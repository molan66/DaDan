"""LOL 小助手 - 主入口"""

import sys
import os

# ==================== v20:清除代理环境变量(必须在任何网络请求前) ====================
# LCU 通信目标恒为 127.0.0.1 本机,绝不应走代理。用户机器若装有本地代理工具
# (抓包/加速器, 会设置 HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:xxxx),requests 与
# websocket-client 都会读它 → REST 卡在代理隧道数秒(启动 6s 黑洞根因) + WS 反复
# CONNECT 502。本程序无外网需求,直接清空并声明 NO_PROXY。
for _k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
           'all_proxy', 'ALL_PROXY'):
    os.environ.pop(_k, None)
os.environ['no_proxy'] = '*'
os.environ['NO_PROXY'] = '*'

import json
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor

from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QMessageBox, QWidget
from PyQt5.QtCore import Qt, QTimer, QObject, QPropertyAnimation, QThread, pyqtSignal
from PyQt5.QtGui import QIcon, QPixmap, QFont, QPainter, QColor, QBrush, QPainterPath, QLinearGradient

from lcu.connector import LCUConnector
from lcu.api import LCUAPI
from lcu.websocket_listener import WebSocketListener
# v4.4.0(P1-25): 英雄表构建规则(0 < cid <= 1000 + 名字兜底)与 core/script_core.py
# 共用同一份实现, 避免"界面英雄表"与"自动选英雄用的英雄表"两处规则漂移。
from lcu.parsing import is_real_champion, champion_display_name
from ui.main_window import MainWindow
from ui.overlay_window import OverlayWindow
from core.script_core import ScriptCore
from core.logger import log_capture, redact_sensitive
from core.icon_cache import IconCache, prewarm_all
from core.updater import APP_VERSION
from ui.window_base import ui_slot
# v4.4.1(UI-B7): 托盘右键菜单着色 + 菜单项图标。原先裸用系统默认渲染
# (浅色底 + 纯文字), 是全应用唯一一处与深色玻璃观感脱节的地方。
from ui.styles import Glass as _Glass
from ui.icons import (icon_tray_show, icon_tray_play, icon_tray_stop,
                      icon_tray_quit)


# ==================== v4.4.0(P1-25): 解析辅助函数去重 ====================
# 原先 `_gi`(带默认值的 int 转换)与 `_opt`(缺失返回 None 的 int 转换)在
# `_ConnProbeWorker._parse_recent` 与 `_parse_game_detail` 里**各写了一份**(共 4 份)。
# 这两段解析逻辑干的是同一件事(把 LCU 返回的不确定类型字段压成 int / int|None),
# 散着写的问题是: 修一处类型坑(比如 LCU 有时给字符串 "123")只修到一半。
# 现在提到模块级各一份, 两处解析共用。
# 注意 `_parse_recent` 原本是 `@staticmethod`, 静态方法内引用模块级函数完全合法,
# 不需要改为 classmethod 或传参。
def _gi(v, default=0):
    """尽力把 v 转 int, 失败返回 default(0)。用于"缺字段当 0"的数值。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _opt(v):
    """尽力把 v 转 int, 失败返回 None。用于"缺字段显示为 '—'"的数值(KDA 等)。"""
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# v4.4.0(P1-25): 对局时长单位归一化。LCU 的 gameDuration 各版本单位不一致
# (见过秒, 也见过毫秒), 阈值 10 万: 一场 LOL 最长约 1 小时多(5000s),
# 而毫秒形态最少也有几十万, 故 >100000 判为毫秒。两处解析原先各写一遍。
_DURATION_MS_THRESHOLD = 100000


def _duration_s(v) -> int:
    """把 gameDuration 归一化成秒(自动识别秒/毫秒形态); 无法解析返回 0。"""
    dur = _gi(v)
    if dur > _DURATION_MS_THRESHOLD:
        dur = dur // 1000
    return dur if dur > 0 else 0


# ==================== v4.4.0(P2): 重连退避节奏 ====================
# 原先这两个数字直接写在 AppController.__init__ 里(5000 / [5000,10000,20000,30000])。
# 提为模块级常量: 它们和"重连失败第几次"是同一组语义, 分开写容易出现
# `setInterval(5000)` 改了而 `_RETRY[0]` 没跟着改的错位。
# 注意与 core/constants.py 的 RECONNECT_INTERVAL_S(=3) 不是一回事 ——
# 那个是脚本核心轮询未连接时的重试节奏(秒), 这里是 UI 层"连接失败后重排
# 探活定时器"的毫秒退避序列, 所以留在本文件, 不跨模块混用。
_RECONNECT_BACKOFF_MS = (5000, 10000, 20000, 30000)
_RECONNECT_FIRST_MS = _RECONNECT_BACKOFF_MS[0]


class _ConnProbeWorker(QObject):
    """v8：连接探活后台 worker,避免 REST 调用阻塞主线程 QTimer。

    设计要点（关键修正 v7 的卡顿 bug）：
      - LCU 客户端关闭时,LCUConnector._port/_token 仍非空(进程死了端口不会主动清),
        is_connected() 判定失败后主线程调 REST,但 socket RST + 内核回包兜底要几百 ms,
        v7 的 QTimer.timeout 在主线程跑 → 那几 ms UI 整个不响应 tick,
        表现就是"退出游戏后特别卡"。
      - 现在 worker 在专属 QThread 跑 REST,完成后 emit probe_done(QueuedConnection,
        由 Qt 自动排到 receiver 线程即主线程执行),主线程只负责更新 UI。
      - 信号槽是 Qt 唯一线程安全跨线程通信方式；避免一切手动 threading.Lock/Condition
        与 singleShot 重入保护(v6 死锁教训)。

    v36：probe_done 第二个参数由 str(name) 升级为 dict(info)，除连通性判定外，
    顺带携带召唤师等级/经验/段位/荣誉等账号卡数据 —— summoner 端点每 3s 拉一次
    （轻量、判定连通）；ranked/honor 每 10 次(~30s)才拉一次 + 计数归零时即拉，
    避免对低频变化的段位数据做无意义的高频查询。
    """
    probe_done = pyqtSignal(bool, object, str)  # ok:bool, info:dict, err_msg:str
    trigger = pyqtSignal()  # 主线程 emit,worker 线程消费(connect 须在 moveToThread 后)

    def __init__(self, api):
        super().__init__()
        self._api = api
        self._probe_count = 0
        # 缓存上一次拉到的低频字段(段位/荣誉/资产/熟练度/战绩)以避免每 3s 重发请求
        self._last_solo = None
        self._last_flex = None
        self._last_solo_wins = 0
        self._last_solo_losses = 0
        self._last_flex_wins = 0
        self._last_flex_losses = 0
        self._last_honor = 0
        self._last_wallet_ip = 0
        self._last_wallet_cosmetic = 0
        self._last_wallet_mythic = 0
        self._last_wallet_rp = 0
        self._last_mastery = []
        self._last_recent = None
        self._summoner_id = 0
        # v4.4.0(P1-25): 原名 self._puuid。AppController 上也有一个同名的 self._puuid
        # (探活成功后从中转存,详情/战绩按它反查自己), 两个同名不同对象、分属两个线程,
        # 读代码时极易误判"这个 _puuid 到底是谁在写"。worker 这份是**探活源**,
        # 统一改名 _probe_puuid 明确归属。
        self._probe_puuid = ''
        # v4.3.5: 强制刷新低频字段的信号位。对局结束(EndOfGame)时主控置位,
        # 让下一轮探活**立刻**重拉战绩, 不等"每 10 轮(约 30s)"的周期。
        # v4.3.10: 主线程写 / worker 线程读。CPython 的布尔属性赋值是单条字节码
        # 原子操作, 不会读到撕裂值; 且只有主线程写 True、worker 写 False,
        # 不存在 read-modify-write 竞态, 无需加锁。
        self._force_profile = False

    @staticmethod
    def _parse_recent(matches: dict, my_puuid: str, limit: int = 10) -> dict:
        """解析最近对局胜负(近 N 场)。返回
        {'wins':n,'losses':n,'total':n,'games':[{...}]}。

        match-history 结构: games.games[] 每局含 participantIdentities
        (puuid→participantId) 与 participants(stats.win)。remake/无 win 不计。

        v4.1.56 修:腾讯服 LCU 端点 /lol-match-history/v1/products/lol/{puuid}/matches
        忽略 endIndex 参数,默认返回 20 场(LCU 历史页 page size),导致 UI 显示「9胜12负」
        共 21 场 ≠ 卡片 prefix「近10场」。改为客户端按 limit 截断 games 数组,
        无论服务端返回多少都只统计前 N 场,与 UI 文案严格对齐。

        v4.1.66 增:同一次遍历顺带产出 'games' 明细(战绩页用), 避免二次遍历/二次请求。
        gameDuration 各客户端版本单位不一致(秒/毫秒都有), >10万按毫秒处理。
        """
        wins = losses = 0
        games_out = []
        games = ((matches or {}).get('games') or {}).get('games') or []

        for g in (games[:limit] if limit and limit > 0 else games):
            # v4.4.2(P2-25): 原 try 包住了整个 for —— 某一局脏数据(缺字段/None
            # 下标)会 break 跳出循环, 后面所有对局全部丢失; 而注释自称"单局失败就
            # 跳过这一局", 实现与注释相反。把 try/except 下移到 for 体内,
            # 单局解析失败只跳过这一局, 继续解析后续对局。
            try:
                pid_map = {}
                for pi in g.get('participantIdentities') or []:
                    pl = pi.get('player') or {}
                    puuid = pl.get('puuid')
                    if puuid:
                        pid_map[puuid] = pi.get('participantId')
                my_pid = pid_map.get(my_puuid)
                if my_pid is None:
                    continue
                for part in g.get('participants') or []:
                    if part.get('participantId') != my_pid:
                        continue
                    stats = part.get('stats') or {}
                    win = stats.get('win')
                    if win is True:
                        wins += 1
                    elif win is False:
                        losses += 1
                    else:
                        # remake/无结果: 不计胜负, 也不进明细列表
                        break
                    # 时长: gameDuration 单位不确定(秒/毫秒), 兜底 stats.timePlayed(毫秒)
                    dur = _duration_s(g.get('gameDuration'))
                    if dur <= 0:
                        dur = _gi(stats.get('timePlayed')) // 1000
                    # KDA: 缺字段存 None, 由 UI 显示 '—'
                    lane = str((part.get('timeline') or {}).get('lane') or '')
                    games_out.append({
                        'game_id': _gi(g.get('gameId')),
                        'champion_id': _gi(part.get('championId')),
                        'win': bool(win),
                        'kills': _opt(stats.get('kills')),
                        'deaths': _opt(stats.get('deaths')),
                        'assists': _opt(stats.get('assists')),
                        'level': _gi(stats.get('champLevel')),
                        'mode': str(g.get('gameMode') or ''),
                        'queue_id': _gi(g.get('queueId')),
                        'duration_s': dur,
                        'created_ms': _gi(g.get('gameCreation')),
                        'lane': lane if lane and lane != 'NONE' else '',
                    })
                    break
            except Exception as e:
                # 单局解析失败就跳过这一局, 留痕便于定位"战绩条目对不上/少一局"。
                print(f'[战绩] 解析单局失败(已跳过): {type(e).__name__}: {e}')
        return {'wins': wins, 'losses': losses,
                'total': wins + losses, 'games': games_out}

    @ui_slot
    def do_probe(self):
        # 此方法经 trigger 信号在 worker 所在线程(即 _ProbeQThread)执行
        info = {'name': '', 'game_name': '', 'tag_line': '', 'level': 0,
                'icon_id': 0, 'xp_pct': 0.0, 'puuid': '',
                'solo': None, 'flex': None, 'honor': 0,
                'wallet_ip': 0, 'wallet_cosmetic': 0,
                'wallet_mythic': 0, 'wallet_rp': 0,
                'mastery_top': [], 'recent': None}
        try:
            summoner = self._api.get_current_summoner()
            if not summoner:
                self.probe_done.emit(False, info, 'empty summoner')
                return
            # v37 字段修正:displayName/internalName 在腾讯 LOL LCU 上常为空字符串,
            # 真实名字在 gameName + tagLine(新 Riot ID);三层 fallback 拼接。
            info['game_name'] = (summoner.get('gameName') or '').strip()
            info['tag_line'] = (summoner.get('tagLine') or '').strip()
            disp = (summoner.get('displayName')
                    or summoner.get('internalName') or '').strip()
            if disp:
                info['name'] = disp
            else:
                gn = info['game_name'] or '召唤师'
                tl = info['tag_line']
                info['name'] = f'{gn}#{tl}' if tl else gn
            try:
                info['level'] = int(summoner.get('summonerLevel') or 0)
            except (TypeError, ValueError):
                pass
            try:
                info['icon_id'] = int(summoner.get('profileIconId') or 0)
            except (TypeError, ValueError):
                pass
            # percentCompleteForNextLevel 是 0-100 整数 (距下一级百分比)
            try:
                info['xp_pct'] = float(
                    summoner.get('percentCompleteForNextLevel') or 0)
            except (TypeError, ValueError):
                pass
            # summonerId/puuid:熟练度与最近战绩拉取所需,缓存到 worker 实例
            try:
                self._summoner_id = int(summoner.get('summonerId') or 0)
            except (TypeError, ValueError):
                pass
            new_puuid = (summoner.get('puuid') or '').strip()
            # v4.3.5: **换号检测**。旧实现只看周期(每10轮)决定是否重拉, 且各
            # _last_* 缓存不区分账号 —— 换号后若还没到周期, 界面会继续显示**上一个
            # 账号**的段位/战绩/熟练度(用户表现: "刚打完的战绩里没有")。
            # puuid 变了就立刻清缓存并强制刷新。
            if new_puuid and new_puuid != self._probe_puuid:
                if self._probe_puuid:
                    print(f'[账号] 检测到换号 → 清空账号级缓存并立即重拉')
                self._last_solo = None
                self._last_flex = None
                self._last_solo_wins = 0
                self._last_solo_losses = 0
                self._last_flex_wins = 0
                self._last_flex_losses = 0
                self._last_honor = 0
                self._last_wallet_ip = 0
                self._last_wallet_cosmetic = 0
                self._last_wallet_mythic = 0
                self._last_wallet_rp = 0
                self._last_mastery = []
                self._last_recent = None
                self._force_profile = True
            self._probe_puuid = new_puuid
            info['puuid'] = self._probe_puuid   # v4.3.1: 回传给主控缓存(详情/战绩拉取要用)
        except Exception as e:
            self.probe_done.emit(False, info, f'{type(e).__name__}: {e}')
            return

        # 低频字段(段位/荣誉/资产/熟练度/战绩)：第 1 次 + 之后每 10 次探活(~30s)拉一次。
        # 单端点失败不致命 —— 各自 try,只在成功时更新 _last_* 缓存,失败保留旧值。
        # v4.3.5: 对局结束后主控置 _force_profile, 让本轮无视周期立即重拉
        # (刚打完要看新战绩, 不能等 30s)。
        need_profile = (self._probe_count == 0
                        or self._probe_count % 10 == 0
                        or self._force_profile)
        if need_profile:
            self._force_profile = False
            # v4.1.64 修: qm 必须先初始化。原代码 qm 在 try 内赋值, 若
            # get_current_ranked_stats() 抛异常(网络抖动/LCU 假死), qm 未定义 →
            # 下方 qm.get() 抛 NameError(被内层 except 吞掉) → 胜场被错误重置为 0,
            # 违反"失败保留旧值"语义(段位 chip 后缀"X胜Y负"会偶发消失)。
            qm = None
            try:
                ranked = self._api.get_current_ranked_stats()
                qm = (ranked or {}).get('queueMap') or {}
                self._last_solo = qm.get('RANKED_SOLO_5x5') or None
                self._last_flex = qm.get('RANKED_FLEX_SR') or None
            except Exception:
                pass
            # v4.1.62: 提取赛季胜场(单排/灵活 wins/losses)用于账号卡段位 chip
            # 后缀展示"X胜Y负"。原 get_current_solo/flex 流程没读这俩字段,
            # 现加在 profile 刷新阶段(每 30s 一次), 数据随 _last_solo/flex 走。
            # v4.1.64: 仅当成功拿到 queueMap 时才更新胜场, 失败保留旧值。
            if qm is not None:
                self._last_solo_wins = 0
                self._last_solo_losses = 0
                self._last_flex_wins = 0
                self._last_flex_losses = 0
                for qkey, cache_self in (('RANKED_SOLO_5x5', '_last_solo'),
                                         ('RANKED_FLEX_SR', '_last_flex')):
                    try:
                        q = qm.get(qkey) or {}
                        w = int(q.get('wins') or 0)
                        l = int(q.get('losses') or 0)
                        if qkey == 'RANKED_SOLO_5x5':
                            self._last_solo_wins, self._last_solo_losses = w, l
                        else:
                            self._last_flex_wins, self._last_flex_losses = w, l
                    except Exception:
                        pass
            try:
                hon = self._api.get_honor_profile() or {}
                try:
                    self._last_honor = int(hon.get('honorLevel') or 0)
                except (TypeError, ValueError):
                    pass
            except Exception:
                pass
            # v4.1.43: 资产余额(蓝精粹/橙色精粹/神话精粹/RP)。
            # 腾讯 LCU 钱包数据从 /lol-loot/v1/player-loot 筛 type=CURRENCY,
            # 字段为 count(不是 value,后者是单价)。RP 在腾讯服不计费。
            try:
                w = self._api.get_wallet() or {}
                self._last_wallet_ip = int(w.get('ip') or 0)
                self._last_wallet_cosmetic = int(w.get('cosmetic') or 0)
                self._last_wallet_mythic = int(w.get('mythic') or 0)
                self._last_wallet_rp = int(w.get('rp') or 0)
            except Exception:
                pass
            # v4.1.43: 英雄熟练度 top3(按英雄成就等级降序)。
            # 端点 /lol-champion-mastery/v1/local-player/champion-mastery
            # (原 /lol-collections/v1/... 实测 404)。等级字段 championLevel
            # (= 游戏"X 级英雄成就", 截图中 37/72/16), 排序已由 API 层按
            # (championLevel, championPoints) 降序完成, 这里直接取前3。
            try:
                mlist = self._api.get_champion_mastery(
                    self._summoner_id) or []
                # v4.1.47: API 层已按 championLevel 降序, 直接取前3
                # 字段: championId / level(= 英雄成就等级, 截图中 37/72/16) /
                #       points / grade(最高评分 S+)
                self._last_mastery = [
                    {'champion_id': int(m.get('championId') or 0),
                     'level': int(m.get('level') or 0),
                     'points': int(m.get('points') or 0),
                     'grade': str(m.get('grade') or '')}
                    for m in mlist[:3]
                    if m.get('championId')
                ]
            except Exception:
                pass
            # v4.1.43: 最近战绩(近10场胜负统计)
            try:
                if self._probe_puuid:
                    # v4.1.56: 端点 endIndex 参数在腾讯服被忽略,服务端必须再截一次
                    matches = self._api.get_recent_matches(self._probe_puuid, 20)
                    self._last_recent = self._parse_recent(
                        matches, self._probe_puuid, limit=10)
            except Exception:
                pass
        # 无论是否 need_profile, info 统一沿用缓存(首次 = 初始占位值)
        info['solo'] = self._last_solo
        info['flex'] = self._last_flex
        info['solo_wins'] = self._last_solo_wins
        info['solo_losses'] = self._last_solo_losses
        info['flex_wins'] = self._last_flex_wins
        info['flex_losses'] = self._last_flex_losses
        info['honor'] = self._last_honor
        info['wallet_ip'] = self._last_wallet_ip
        info['wallet_cosmetic'] = self._last_wallet_cosmetic
        info['wallet_mythic'] = self._last_wallet_mythic
        info['wallet_rp'] = self._last_wallet_rp
        info['mastery_top'] = self._last_mastery
        info['recent'] = self._last_recent
        self._probe_count += 1
        self.probe_done.emit(True, info, '')


def _parse_game_detail(raw, my_puuid: str):
    """把 /lol-match-history/v1/games/{gameId} 的原始返回压成详情弹窗要用的结构。

    战绩列表端点每局只给本人 1 条 participant(腾讯服实测), 全场数据必须按 gameId
    单独拉。这里只保留 UI 会渲染的字段, 避免把几十 KB 的原始 JSON(逐项伤害/符文/
    连杀明细)常驻内存。

    返回 {'my_pid', 'players':[...], 'teams':[...], 'duration_s', 'queue_id',
          'mode', 'created_ms'}; 结构不符(记录已过期/被清理)返回 None。
    """
    if not isinstance(raw, dict):
        return None
    participants = raw.get('participants') or []
    if not participants:
        return None

    # pid → 召唤师名(新 Riot ID: gameName#tagLine; 老客户端只有 summonerName)
    names = {}
    for pi in raw.get('participantIdentities') or []:
        pid = _gi(pi.get('participantId'), -1)
        pl = pi.get('player') or {}
        nm = (pl.get('gameName') or '').strip()
        tag = (pl.get('tagLine') or '').strip()
        if nm and tag and tag not in ('0', ''):
            nm = f'{nm}#{tag}'
        if not nm:
            nm = (pl.get('summonerName') or '').strip()
        names[pid] = nm

    dur = _duration_s(raw.get('gameDuration'))   # 老版本返回毫秒, 由 _duration_s 统一识别

    players = []
    for part in participants:
        st = part.get('stats') or {}
        pid = _gi(part.get('participantId'), -1)
        items = [_gi(st.get(f'item{i}')) for i in range(7)]
        multi = ''
        for cnt, label in ((st.get('pentaKills'), '五杀'),
                           (st.get('quadraKills'), '四杀'),
                           (st.get('tripleKills'), '三杀'),
                           (st.get('doubleKills'), '双杀')):
            if _gi(cnt) > 0:
                multi = label
                break
        players.append({
            'pid': pid,
            'team': _gi(part.get('teamId')),
            'cid': _gi(part.get('championId')),
            'name': names.get(pid, ''),
            'champion_name': '',
            'level': _gi(st.get('champLevel')),
            'kills': _opt(st.get('kills')),
            'deaths': _opt(st.get('deaths')),
            'assists': _opt(st.get('assists')),
            'win': bool(st.get('win')),
            'gold': _gi(st.get('goldEarned')),
            'cs': (_gi(st.get('totalMinionsKilled'))
                   + _gi(st.get('neutralMinionsKilled'))),
            'dmg': _gi(st.get('totalDamageDealtToChampions')),
            'taken': _gi(st.get('totalDamageTaken')),
            'vision': _gi(st.get('visionScore')),
            'wards': _gi(st.get('wardsPlaced')),
            'multi': multi,
            'lane': str((part.get('timeline') or {}).get('lane') or ''),
            # v4.3.2: 召唤师技能在 participant **顶层**(不在 stats 里!)。
            # 实测: spell1Id=4(闪现) / spell2Id=32(雪球/mark)。海克斯强化在 stats
            # 的 playerAugment1..6, 大乱斗类玩法才有(召唤师峡谷恒 0)。
            'spell1': _gi(part.get('spell1Id')),
            'spell2': _gi(part.get('spell2Id')),
            'augments': [_gi(st.get(f'playerAugment{i}')) for i in range(1, 7)],
            'items': items,
        })

    teams = []
    for tm in raw.get('teams') or []:
        teams.append({
            'team_id': _gi(tm.get('teamId')),
            'win': tm.get('win'),
            'tower': _gi(tm.get('towerKills')),
            'dragon': _gi(tm.get('dragonKills')),
            'baron': _gi(tm.get('baronKills')),
            'herald': _gi(tm.get('riftHeraldKills')),
            'inhib': _gi(tm.get('inhibitorKills')),
        })

    # 用 puuid 反查我的 participantId(身份表里 puuid 唯一)
    my_pid = -1
    if my_puuid:
        for pi in raw.get('participantIdentities') or []:
            if (pi.get('player') or {}).get('puuid') == my_puuid:
                my_pid = _gi(pi.get('participantId'), -1)
                break

    return {'my_pid': my_pid, 'players': players, 'teams': teams,
            'duration_s': dur, 'queue_id': _gi(raw.get('queueId')),
            'mode': str(raw.get('gameMode') or ''),
            'created_ms': _gi(raw.get('gameCreation'))}


class _ProbeQThread(QThread):
    """v8:QThread 子类化,run() 启动 Qt 事件循环,使 moveToThread 过来的 worker 槽可以被派发。

    默认 QThread().run() 是空的,moveToThread 后 invokeMethod 派发的槽永远没人消费,
    这是 PyQt5 多线程最常见的"线程化不起作用"陷阱。
    """
    def run(self):
        # 子类化 QThread 时,推荐写法是 exec()(等价于 app.exec() 在 worker 线程启动事件循环)
        self.exec()


class AppController(QObject):
    # S1:跨线程 UI 连接状态更新统一经该信号排回主线程。
    # 后台线程(轮询/连接线程)emit → Qt 按 receiver 线程亲和自动 QueuedConnection
    # → _apply_ui_connected 在主线程执行,彻底规避"后台线程直接 setText/setStyleSheet"。
    _ui_cmd = pyqtSignal(bool, str)
    # v4.4.1(UI-B5): 三态连接指示('connecting'/'connected'/'disconnected', 文案)。
    # _ui_cmd 是 bool, 表达不了"正在扫客户端"这个中间态 —— 那会被渲染成和
    # "客户端根本没开"完全相同的红色。后台线程(连接 worker)emit 本信号,
    # 由 Qt 按 receiver 线程亲和排队回主线程再碰控件(与 _ui_cmd 同一套范式)。
    _conn_state_sig = pyqtSignal(str, str)
    # v14.1:延迟隐藏小窗必须排回主线程调度。_on_phase 由 ScriptCore 的
    # poll/WS 后台线程调用,若直接在那里 new QTimer(),timer 绑定到无 Qt
    # 事件循环的后台线程,timeout 永不触发 → 游戏开始后小窗不隐藏。
    _schedule_hide_sig = pyqtSignal()
    # v20:后台线程拉取英雄数据完成后,若选择器尚未初始化,排回主线程补建
    _champ_remote_ready = pyqtSignal()
    # v36:召唤师头像下载完成(icon_id, bytes)。_icon_pool 线程池下载线程 emit
    # → Qt AutoConnection 按 receiver(self,主线程)亲和排队 → _apply_avatar
    # 主线程渲染。v4.1.39:带 icon_id,渲染成功后主窗据此避免被探活轮询覆盖。
    _avatar_done = pyqtSignal(int, bytes)

    # v4.3.0:单局详情拉取完成(独立线程池 → 主线程)。gameId, detail(dict|None), err(str)
    # ⚠️ gameId 必须走 object, **不能用 int**: PyQt 的 `int` 映射到 C++ 32 位 int,
    # 而 LOL 的 gameId 是 64 位大数(如 401111677051)。用 int 会在跨线程投递时被
    # 截断成另一个数(999999999999 → -727379969), 槽函数拿截断后的 key 去
    # _detail_waiters 里 pop 必然找不到 → 回调永不触发 → 弹窗一直读到 40s 硬超时。
    # 这正是"点开详情一直转圈"的真正根因, 别再改回 int / 'qint64' 之外的窄类型。
    _detail_ready = pyqtSignal(object, object, str)

    # v4.3.10:WS 的 on_open / on_close 回调发生在 **websocket-client 的后台线程**,
    # 那里没有 Qt 事件循环。原实现用 `QTimer.singleShot(0, _do)` 想排回主线程,
    # 实测(本次修复前的探针验证)该回调**永不执行**, Qt 只打印
    # "QObject::startTimer: Timers can only be used with threads started with QThread"。
    # 后果是 `_on_ws_disconnected` 里的 `self._conn.reset()` 从不发生 →
    # 关掉游戏再开时 `_conn.is_connected` 仍为 True(留着旧 port/token),
    # `_try_connect_worker` 于是跳过 find_client() 直接拿死端口重连 → 永远连不上,
    # 只有点一次"启动脚本"才能救回来。
    # 改用类级信号: 跨线程 emit 由 Qt 自动走 QueuedConnection 排回主线程。
    # (与 _ui_cmd / _schedule_hide_sig 同一套范式, 别再退回 singleShot。)
    _ws_connected_sig = pyqtSignal()
    _ws_disconnected_sig = pyqtSignal()

    # v4.4.0: 探活"在途"看门狗阈值(秒)。worker 一轮 REST 最坏 ~7s(连接 2s + 读 5s),
    # 留一倍余量取 20s —— 正常轮次绝不会滞留这么久,超过即判定 queued 任务丢失。
    _PROBE_INFLIGHT_TIMEOUT = 20.0

    # v4.4.0(P0-2): 启动流程后台化。原 _start() 直接在 GUI 槽里跑
    # find_client(quick=True) + ws.connect() + refresh_connection() + core.start(),
    # 前两者会 spawn wmic/PowerShell(超时 6~8s), core.start() 里还有
    # old.join(timeout=3.0) —— 未登录/客户端假死时用户点"启动"后界面整个冻住
    # 十几秒,Windows 会直接判定"程序未响应"。现在阻塞段全部移到 daemon 线程,
    # 干完把结果经该信号排回主线程再做 UI 更新。
    _start_done_sig = pyqtSignal(bool, str)

    # v4.4.2(P1-4): 手动接受/拒绝/换英雄原本是 @ui_slot 里同步调 core 的 REST
    # (bench_swap 含 sleep + 读回, 最坏 REST 超时 (2.0,5.0) 级), 主线程假死十秒级。
    # 现把阻塞调用挪后台线程, 结果经本信号排回主线程更新 UI。
    # 参数: kind('accept'|'decline'|'swap'), ok(bool), cid(int, 仅 swap 用)
    _action_done_sig = pyqtSignal(str, bool, int)

    def __init__(self):
        super().__init__()
        self._schedule_hide_sig.connect(self._schedule_hide_impl)
        self._ui_cmd.connect(self._apply_ui_connected)
        self._conn_state_sig.connect(self._apply_conn_state)  # v4.4.1(UI-B5)
        self._champ_remote_ready.connect(self._on_champ_remote_ready)
        self._avatar_done.connect(self._apply_avatar)
        self._detail_ready.connect(self._on_detail_ready)
        self._start_done_sig.connect(self._on_start_done)
        self._action_done_sig.connect(self._on_action_done)  # v4.4.2(P1-4)
        # v4.3.10: WS 回调(后台线程) → 主线程的两条通路, 见类属性处说明
        self._ws_connected_sig.connect(self._on_ws_connected)
        self._ws_disconnected_sig.connect(self._on_ws_disconnected)
        self._app = None
        self._main = None
        self._overlay = None
        self._tray = None
        # v4.4.2(P1-18): 无托盘环境(精简系统/远程会话/Explorer 未运行)下
        # _setup_tray 提前 return, 不会走到 `self._tray_toggle = menu.addAction(...)`,
        # 而 :2072/:2127 两处都 `if self._tray_toggle:` 直接 AttributeError。
        # 补初始化即可 —— _setup_tray 里两处赋值已有 `if self._tray_toggle:` 守卫。
        self._tray_toggle = None
        # v4.1.64: 提权请求去重标志 —— _start(点启动脚本) 与
        # _maybe_elevate_on_startup(启动后 800ms 定时检查) 两个入口都会调
        # _on_auto_select_champion_request, 若时机重合(用户很快就点了启动)会
        # 重复弹提示框 + 重复触发 runas。成功触发一次后置位, 其余入口直接跳过。
        self._elevate_requested = False

        self._conn = LCUConnector()
        self._api = LCUAPI(self._conn)
        self._ws = WebSocketListener(self._conn)
        self._core = ScriptCore(self._api, self._ws)

        self._icon_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix='icon')  # 8线程并发下载图标
        self._champ_data: dict = {}
        self._champ_roles: dict = {}

        # v4.3.0:对局详情 —— **独立线程池**! 上一版把详情请求丢进 _icon_pool(8 worker,
        # 与英雄图标下载共用), 而战绩页/英雄页一次要下几百张图标, 池子长期满载 →
        # 详情任务排在几百个图标下载之后, 弹窗永久 loading。这里单独开 3 个 worker。
        self._detail_pool = ThreadPoolExecutor(max_workers=3,
                                               thread_name_prefix='detail')
        # 装备图标走外部 CDN(不是 LCU), 单独一个池: 一次要拉 7×10 张, 别和详情请求
        # /英雄图标互相堵
        self._item_pool = ThreadPoolExecutor(max_workers=4,
                                             thread_name_prefix='item')
        self._detail_waiters: dict = {}   # gameId -> callback(detail, err)
        # v4.3.1: 本人 puuid 缓存(探活 worker 每轮回传, 见 _on_probe_done)。
        # 空串 = 还没探活过, 此时点详情会给"尚未获取账号信息"而不是崩。
        # v4.4.0(P1-25): 与 _ConnProbeWorker._probe_puuid 区分命名 —— 这一份是
        # **主控侧副本**(消费方), worker 那份是源(生产方); 原来两边都叫 _puuid,
        # 分属两个线程各写, 排查时无法一眼看出谁写谁读。
        self._summoner_puuid = ''
        # 两级兜底, 别让 UI 干等、也别把慢的当成死的:
        #   软超时 → 只告诉弹窗"读得慢, 仍在等", waiter 保留, 结果回来照样回填;
        #   硬超时 → 才摘掉 waiter 并报错。
        # 实测: 同一局第一次拉可能要十几~几十秒(服务端现拼整场), 紧接着再请求
        # 命中缓存 <0.1s —— 所以"读得慢"和"读不到"必须分开表达。
        self._detail_soft_ms = 8000
        self._detail_hard_ms = 40000

        # v4.3.6: 图标缓存统一 —— 英雄/装备/技能/海克斯此前是**四份同构实现**
        # (各自维护 内存字典+LRU order+downloading+failed+MAX+目录), 任何规则
        # 改动都要改四处, 漏一处就是"某类图标在某些机器上刷不出来"这种依赖网络
        # 时序、几乎无法复现的 bug。现统一为 IconCache 四个实例。
        # **key 命名空间由落盘目录名隔离**: 装备 id 与英雄 id 数值会重叠
        # (如 2503/6653), 技能 id(4/32)、海克斯 id(1030…) 同样。
        # 磁盘上限是个兜底(旧实现只有内存 LRU, 磁盘无限增长)。
        # ⚠️ blacklist 只有"数据源里根本不存在这个 id"的类别才开: 装备(6 位特殊
        #    id 两个源都没有) + 技能/海克斯(表里查不到)。**英雄图标必须关** ——
        #    客户端没启动时下载会返回 None, 拉黑的话客户端连上后这批 id 就
        #    整个会话都刷不出图了(旧实现本来就没有英雄黑名单, 这里保持一致)。
        self._cache_champ = IconCache('icon_cache', max_mem=100,
                                      max_disk=800, log_tag='[图标]',
                                      blacklist=False)
        self._cache_item = IconCache('item_cache', max_mem=260,
                                     max_disk=900, log_tag='[详情/装备]',
                                     blacklist=True)
        self._cache_spell = IconCache('spell_cache', max_mem=80,
                                      max_disk=300, log_tag='[详情/技能]',
                                      blacklist=True)
        self._cache_aug = IconCache('augment_cache', max_mem=400,
                                    max_disk=900, log_tag='[详情/海克斯]',
                                    blacklist=True)
        # v4.3.10: 此处原有 `self._icon_lock = threading.Lock()` —— v4.3.6 把四份
        # 缓存实现收进 IconCache 后, 锁已经由各 IconCache 自己持有, 这个成员
        # 全项目零引用, 删掉以免误以为外部还有一把共用锁。

        self._reconnect_timer = QTimer()
        self._reconnect_timer.timeout.connect(self._try_connect)
        self._reconnect_timer.setInterval(_RECONNECT_FIRST_MS)
        self._reconnect_fail = 0
        self._RETRY = list(_RECONNECT_BACKOFF_MS)

        # 小窗日志：线程安全的队列+定时器模式。
        # v4.3.10: _on_log 由 log_capture 回调在**任意调用 print 的线程**里执行
        # (探活 worker / WS 后台线程 / 图标下载池都有可能), 而 _flush_mini_log 在
        # 主线程读+清空 —— 无锁时 list 并发 append/迭代会丢失条目甚至抛异常。
        self._mini_log_queue: list = []
        self._mini_log_lock = threading.Lock()
        self._mini_log_timer = QTimer()
        self._mini_log_timer.timeout.connect(self._flush_mini_log)
        self._mini_log_timer.setInterval(500)

        # UI 连接状态单源真相：WS 事件不稳定，但 REST API 才是脚本功能真正依赖的。
        # 这里用定时器周期性通过 REST 探活来决定 UI 显示什么；WS on_open/on_close
        # 只是辅助信号，避免 UI 在 WS 偶发抖动时被打回"未连接"。
        self._ui_connected = False
        self._ui_conn_refresh_timer = QTimer()
        self._ui_conn_refresh_timer.timeout.connect(self._refresh_ui_conn)
        self._ui_conn_refresh_timer.setInterval(3000)
        self._probe_fail_count = 0
        # v4.3.10: 探活"在途"标志, 防止 QueuedConnection 队列无界堆积(见 _refresh_ui_conn)
        self._probe_inflight = False
        # v4.4.0 看门狗: 记录在途标志置位时刻(monotonic)。_refresh_ui_conn 起手
        # 就 `if self._probe_inflight: return`,而 _probe_inflight 只在 _on_probe_done
        # 里清 —— 一旦 worker 那条 queued 任务被丢弃(线程正在退出/事件循环被销毁/
        # 信号连接在异常后被断开),标志永久 True → 探活彻底停摆,连接绿点永远停在
        # 最后一次的状态(用户表现: 拔了客户端界面还显示已连接)。超过阈值强制复位。
        self._probe_inflight_at = 0.0
        # v4.3.10: 重连线程单飞标志 —— _try_connect 每 5s 一跳, 而
        # find_client(quick=False) 的全盘 BFS 可能跑 30s 以上, 原实现每跳都新起
        # 一个线程 → 多个扫描线程并发, 白耗磁盘 IO。
        self._connecting = False
        # v4.4.0(P0-2): 启动流程单飞标志。_start() 现在把阻塞段丢到后台线程,
        # 期间用户可能连点"启动"(或托盘/小窗同时触发), 必须挡住重复启动。
        self._starting = False
        self._last_icon_id = 0

        # v13 优化项 2:对局即将开始 → 1.5s 后隐藏小窗。
        # v14.1:QTimer 必须在主线程创建(QObject 线程亲和),否则绑定到
        # ScriptCore 后台线程无事件循环,timeout 永不触发。
        self._pending_hide_timer = QTimer(self)
        self._pending_hide_timer.setSingleShot(True)
        self._pending_hide_timer.timeout.connect(self._do_delayed_hide_after_game_start)

        # v4.1.65: 小窗显隐兜底。原实现**完全依赖"阶段切换事件"**隐藏小窗 —— 若脚本
        # 是在对局中才启动的(人在游戏里才开软件、点启动), _reconcile_state 之后再无
        # 阶段变化 → 隐藏逻辑永远等不到 InProgress 事件, 而 _start() 又无条件
        # safe_show() → 小窗永久挂在游戏画面上。这里加周期兜底: 只要"当前真实阶段
        # =游戏中"就强制收起小窗, 与事件无关。
        self._overlay_guard_timer = QTimer(self)
        self._overlay_guard_timer.setInterval(1500)
        self._overlay_guard_timer.timeout.connect(
            lambda: self._enforce_overlay_hidden_in_game('周期兜底'))
        # "对局即将开始" 1.5s 预告窗口的截止时刻(monotonic); 兜底在该窗口内不干预
        self._overlay_preview_deadline = 0.0

        # v8：探活后台线程化。worker 在 QThread 跑 REST,Qt QueuedConnection
        # 自动跨线程派发 probe_done 信号到主线程,主线程只负责 UI 更新。
        # 这样 LCU 客户端关闭时 REST 超时不会卡主线程 QTimer。
        self._probe_worker = _ConnProbeWorker(self._api)
        self._probe_thread = _ProbeQThread()
        self._probe_thread.setObjectName('ConnProbeThread')
        self._probe_worker.moveToThread(self._probe_thread)
        # 重要:trigger.connect(do_probe) 必须在 moveToThread 之后,此时 receiver(worker)
        # 线程亲和 = probe_thread,Qt 自动选 QueuedConnection,do_probe 在 worker 线程跑
        self._probe_worker.trigger.connect(self._probe_worker.do_probe)
        self._probe_worker.probe_done.connect(self._on_probe_done)
        self._probe_thread.start()



    def _make_icon(self) -> QIcon:
        """生成应用图标 - 从文件加载"""
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
            base2 = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
            base2 = base
        for b in [base, base2]:
            for p in ['data/app_icon.png', 'data/app_icon.ico']:
                path = os.path.join(b, p)
                if os.path.exists(path):
                    icon = QIcon(path)
                    if not icon.isNull():
                        return icon
        # 兜底绘制
        pm = QPixmap(64, 64)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor('#6366f1')))
        p.drawRoundedRect(2, 2, 60, 60, 14, 14)
        p.setBrush(QBrush(QColor(255, 255, 255)))
        path = QPainterPath()
        path.moveTo(38, 8)
        path.lineTo(22, 34)
        path.lineTo(32, 34)
        path.lineTo(28, 56)
        path.lineTo(48, 28)
        path.lineTo(36, 28)
        path.closeSubpath()
        p.drawPath(path)
        p.end()
        return QIcon(pm)

    def initialize(self):
        icon = self._make_icon()
        self._app.setWindowIcon(icon)

        # ===== 小窗日志过滤策略（v11 增强）=====
        # 主窗日志面板是开发者/诊断向的，仍接受所有内容；
        # 小窗 mini_log 只展示面向用户的友好状态。
        _HIDE_PATTERNS = ['hwnd', 'shadow=', 'port=', 'token=', 'Acrylic', 'Glass',
                          'Exception', 'Traceback', 'Error', 'Timeout', 'ConnectionError',
                          'ImportError', 'ModuleNotFoundError', 'FileNotFoundError',
                          # v11：明确拦截监控类日志(原来靠"无中文"过滤),
                          # 但 [WS] 关闭: code=None msg=None manual=False 里有"关闭"二字会漏进来,
                          # 这里直接拦截主入口与典型监控字串:
                          '] 关闭:', '] 已连接:', '] 连接中', '] on_open',
                          'code=None', 'msg=None', 'manual=',
                          '[探活]', '[监控]']
        # v11：监控类日志关键字大集合 —— 任一命中即不进 mini_log
        # 即便有中文字符(像 "[WS] 关闭: code=None msg=None manual=False" 含"关闭")
        _MONITOR_PATTERNS = _HIDE_PATTERNS + ['[WS]', '[Core]', '[App]', '[选人]',
                                              'GET ', 'POST ', 'PUT ', 'DELETE ',
                                              'wss://', 'ws://', 'https://', 'http://',
                                              'Connection:', 'User-Agent']

        def _on_log(text):
            try:
                if self._main:
                    self._main.append_log(text)
                # 过滤技术细节和英文/监控类日志
                if any(p in text for p in _MONITOR_PATTERNS):
                    return
                # 过滤纯英文行（无中文字符）
                if text.strip() and not any('\u4e00' <= c <= '\u9fff' for c in text):
                    return
                # v11：相邻相同文本去重,避免反复刷"已选 X"
                if text == getattr(self, '_last_mini_log_text', None):
                    return
                self._last_mini_log_text = text
                with self._mini_log_lock:
                    self._mini_log_queue.append(text)
                    if len(self._mini_log_queue) > 6:
                        self._mini_log_queue = self._mini_log_queue[-6:]
            except Exception:
                pass
        log_capture.add_callback(_on_log)
        # v4.4.2(P2-32): 必须**先** start() 再 _setup_tray()/_setup_callbacks()。
        # 原先顺序是 _setup_tray() 在前, 而 _setup_tray 内 `:855 menu.setStyleSheet`
        # 失败时的 `except: print(...)` 跑在 _Writer 接管 stdout **之前** —— frozen
        # console=False 下 print 是静默 no-op, 托盘菜单样式降级无任何记录。提前后,
        # 该 print 也会进 app.log。
        log_capture.start()
        self._mini_log_timer.start()

        self._setup_tray()
        self._setup_callbacks()

        self._ws.set_on_disconnected(self._emit_ws_disconnected)
        self._ws.set_on_connected(self._emit_ws_connected)
        self._core.setup_ws_handlers()
        print(f'[启动] 大蛋小助手 v{APP_VERSION} 已启动')
        # 延迟到事件循环/窗口显示后再加载英雄数据，避免启动时在主线程做网络探测导致卡顿
        QTimer.singleShot(0, self._try_load_champ_data)
        # v18:图标预热改为线程池后台立即执行 —— 不再用 singleShot(200) 在主线程跑,
        # 数百张 PNG 读盘+魔数校验在 frozen 冷启动下可达上百 ms,会卡顿 splash→主窗的过渡。
        # 方法本身线程安全:IconCache 内部锁串行化,不触碰任何 UI。
        # v4.3.6: 顺带清理磁盘超额缓存(此前磁盘无上限,长期使用会无限增长)。
        try:
            self._icon_pool.submit(self._prewarm_icon_cache_async)
        except Exception as e:
            print(f'[App] 预热任务提交失败: {type(e).__name__}: {e}')

        self._reconnect_timer.start()
        self._ui_conn_refresh_timer.start()
        self._try_connect()

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self._main)
        self._tray.setToolTip('大蛋小助手')
        self._tray.setIcon(self._make_icon())

        menu = QMenu()
        # v4.4.1(UI-B7): 深色菜单 QSS + 菜单项图标。
        # ⚠️ QMenu 是系统弹出的顶层窗口, 部分系统主题/高对比度模式下 Qt 会忽略
        # 自定义样式 —— 那是可接受的降级(回落原生菜单, 不会渲染坏), 所以这里
        # 只做锦上添花, 不依赖它承重。
        try:
            menu.setStyleSheet(_Glass.MENU_QSS)
        except Exception as e:
            print(f'[App] 托盘菜单样式应用失败: {type(e).__name__}: {e}')
        show = menu.addAction(icon_tray_show(), '显示主界面')
        show.triggered.connect(self._show_main)
        menu.addSeparator()
        self._tray_toggle = menu.addAction(icon_tray_play(), '启动脚本')
        self._tray_toggle.setCheckable(True)
        self._tray_toggle.triggered.connect(self._toggle_from_tray)
        menu.addSeparator()
        quit_a = menu.addAction(icon_tray_quit(), '退出')
        quit_a.triggered.connect(self._quit)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray)
        self._tray.show()

    def _setup_callbacks(self):
        self._core.set_on_phase_changed(self._on_phase)
        self._core.set_on_connection_changed(self._on_conn)
        self._core.set_on_bench_champions(self._on_bench)
        self._core.set_on_champion_data_ready(self._on_champ_data)
        # v4.4.2(P2-31): 修注释 —— 原称"core 侧不再预下载图标,此回调已无触发源",
        # 与事实相反: core/script_core.py:280 真实 _emit(self._on_champion_data_ready,
        # self._champ_data), 而本文件 _on_champ_data 是活代码(触发 _champ_remote_ready)。
        # 该回调触发的是"英雄数据就绪"(供界面刷新), 与图标下载(main._dl_icon 线程池)是两条链路。
        self._core.set_on_status_update(self._on_status)
        self._core.set_on_ready_check_state(self._on_ready)
        self._core.set_on_champ_select_info(self._on_champ_select_info)

    def _flush_mini_log(self):
        """定时刷新小窗日志（主线程安全）"""
        try:
            if not self._overlay:
                return
            # v4.3.10: 加锁一次性消费所有队列项, 避免与后台 _on_log 并发读写
            with self._mini_log_lock:
                if not self._mini_log_queue:
                    return
                lines = list(self._mini_log_queue)
                self._mini_log_queue.clear()
            for text in lines:
                self._overlay.append_mini_log(text)
        except Exception:
            pass

    @ui_slot
    def _try_load_champ_data(self):
        """v20 重构:启动时只在主线程做毫秒级缓存读取,立即排期构建选择器。

        根因修复:旧实现把 find_client()(客户端未开时可能全盘扫描数秒)与
        get_champion_data()(REST 经代理隧道可能卡数秒)放在事件循环首个事件里
        同步执行 → 阻塞 splash 淡出与主窗首帧,表现为"蛋标出现后 ~6s 才见主界面"
        (与 exe 体积无关,故 v17-v19 瘦身无感)。联网刷新现整体移至后台线程。
        """
        # 1) 本地缓存(毫秒级),先行出 UI —— 有缓存即有英雄表可搜索
        if not self._champ_data:
            self._load_cache()
        if self._champ_data:
            QTimer.singleShot(120, self._auto_init_picker)
        # 2) 联网刷新交给后台线程(连接发现可能全盘扫;拉取后整体替换 dict)
        self._start_remote_champ_refresh()

    def _start_remote_champ_refresh(self):
        if getattr(self, '_champ_refresh_started', False):
            return
        self._champ_refresh_started = True
        threading.Thread(target=self._refresh_champ_remote, daemon=True).start()

    def _refresh_champ_remote(self):
        """后台线程:完整连接发现 + 拉取官方英雄数据。绝不在主线程跑。
        完成后整体替换 _champ_data/_champ_roles(引用赋值原子,主线程读旧或读新,
        不会撕裂),写缓存,并在选择器尚未初始化时发信号回主线程补建。
        v23:任何失败路径都复位 _champ_refresh_started —— 否则"客户端后开"场景
        首次刷新失败后 flag 恒 True,连接恢复时永远不再重试(仅靠缓存直到重启)。
        """
        try:
            if not self._conn.find_client():
                self._champ_refresh_started = False
                return
            champs = self._api.get_champion_data()
            if not champs:
                self._champ_refresh_started = False
                return
            new_data, new_roles = {}, {}
            for c in champs:
                cid = c.get('id')
                roles = c.get('roles', [])
                if is_real_champion(cid):  # v17: 过滤 60xxx 重做前幽灵英雄
                    new_data[cid] = champion_display_name(c)
                    new_roles[cid] = roles
            if not new_data:
                self._champ_refresh_started = False
                return
            self._champ_data = new_data
            self._champ_roles = new_roles
            print(f'[App] 后台加载 {len(new_data)} 个英雄(已写缓存)')
            self._save_cache()
            if not getattr(self, '_picker_initialized', False):
                self._champ_remote_ready.emit()
        except Exception as e:
            print(f'[App] 后台刷新英雄数据失败: {type(e).__name__}: {e}')
            self._champ_refresh_started = False

    @ui_slot
    def _on_champ_remote_ready(self):
        """后台数据就绪且 picker 尚未初始化时,由主线程补建选择器"""
        if not getattr(self, '_picker_initialized', False):
            self._auto_init_picker()

    def _save_cache(self):
        """保存英雄数据缓存（压缩格式，减小文件大小）
        v16:frozen 时写到 exe 同级 data/（_MEIPASS 临时目录只读,退出即消失,
        写那里下次启动读不到）。与 _get_icon_cache_dir 的落盘策略保持一致。
        """
        try:
            if getattr(sys, 'frozen', False):
                base = os.path.dirname(sys.executable)
            else:
                base = os.path.dirname(os.path.abspath(__file__))
            d = os.path.join(base, 'data')
            os.makedirs(d, exist_ok=True)
            # 使用紧凑格式，不缩进
            with open(os.path.join(d, 'champion_cache.json'), 'w', encoding='utf-8') as f:
                json.dump({
                    'd': {str(k): v for k, v in self._champ_data.items()},
                    'r': {str(k): v for k, v in self._champ_roles.items()},
                }, f, ensure_ascii=False, separators=(',', ':'))
        except Exception as e:
            # 写缓存失败不致命(下次启动联网重拉), 但留痕 —— 否则"英雄名一直
            # 是 #123 这种没缓存的样子"会查不出原因(多半是 exe 放在只读目录)。
            print(f'[App] 英雄数据缓存写入失败: {type(e).__name__}: {e}')

    def _load_cache(self):
        """加载英雄数据缓存（兼容新旧格式）
        v16:多路径回退 —— 单文件 exe 的 __file__ 指向 _MEIPASS 临时目录,
        但 champion_cache.json 可能位于 _MEIPASS/data(已打包)、exe 同级 data、
        或源码 data;逐一尝试,任一命中即加载。
        """
        candidates = []
        if getattr(sys, 'frozen', False):
            mei = getattr(sys, '_MEIPASS', None)
            # v4.3.10 修复: **exe 同级优先**。原顺序把 _MEIPASS 排在前面, 而
            # _save_cache 写的是 exe 同级那份 —— 于是打包后永远读包内那份
            # "构建时的旧快照", 联网刷新 + 落盘的更新**永远读不到**
            # (交付版日志 `缓存加载 173 个英雄 <- C:\...\_MEI...\data\...` 可印证)。
            candidates.append(os.path.join(
                os.path.dirname(sys.executable), 'data', 'champion_cache.json'))
            if mei:
                candidates.append(os.path.join(mei, 'data', 'champion_cache.json'))
        candidates.append(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'data', 'champion_cache.json'))
        for p in candidates:
            try:
                if not os.path.exists(p):
                    continue
                with open(p, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 兼容旧格式（champion_data）和新格式（d）
                champ_raw = data.get('d') or data.get('champion_data', {})
                roles_raw = data.get('r') or data.get('champion_roles', {})
                self._champ_data = {int(k): v for k, v in champ_raw.items() if 0 < int(k) <= 1000}
                self._champ_roles = {int(k): v for k, v in roles_raw.items() if 0 < int(k) <= 1000}
                print(f'[App] 缓存加载 {len(self._champ_data)} 个英雄 <- {p}')
                return
            except Exception as e:
                # v4.4.2(P2-26): 原 `except Exception: continue` 无日志 —— 缓存文件被
                # 写坏时静默滑到下一候选、最终回落打包内旧快照, 用户看到旧数据却无
                # 任何线索。补打印异常类型与消息, 便于定位。
                print(f'[App] 缓存候选取用失败: {type(e).__name__}: {e}')
                continue

    # ==================== 回调 ====================
    def _on_phase(self, phase):
        try:
            # 去重：同一阶段不重复处理
            if phase == getattr(self, '_last_phase', None):
                return
            self._last_phase = phase
            _phase_cn = {'None': '等待中', 'Lobby': '房间中', 'Matchmaking': '匹配中', 'ReadyCheck': '确认对局', 'ChampSelect': '英雄选择', 'InProgress': '游戏中', 'EndOfGame': '对局结束', 'WaitingForStats': '等待统计', 'PreEndOfGame': '即将结束'}
            print(f'[App] 阶段变化: {_phase_cn.get(phase, phase)}')
            if self._main:
                self._main.update_phase(phase)
            if self._overlay:
                phase_info = {
                    'None': ('', '等待连接', ''),
                    'Lobby': ('', '等待匹配', ''),
                    'Matchmaking': ('🔄', '匹配中', ''),
                    'ReadyCheck': ('', '已找到对局', '等待玩家确认'),
                    'ChampSelect': ('', '英雄选择', '选择英雄中'),
                    'InProgress': ('', '游戏中', ''),
                    'EndOfGame': ('', '对局结束', ''),
                    'WaitingForStats': ('', '等待统计', ''),
                    'PreEndOfGame': ('', '即将结束', ''),
                }
                icon, text, detail = phase_info.get(phase, ('', '未知状态', ''))
                _live_cn = {'等待玩家确认': '等待确认', '选择英雄中': '选择中'}
                self._overlay.set_live_status(icon, text, _live_cn.get(detail, detail))
                # 离开 ReadyCheck 时隐藏面板并清除状态文字
                if phase != 'ReadyCheck':
                    self._overlay.show_ready_check_buttons(False)
                    # 清除旧的倒计时状态文字
                    if phase not in ('EndOfGame', 'WaitingForStats', 'PreEndOfGame'):
                        self._overlay.update_status('')
                if phase == 'InProgress':
                    print('[App] 游戏开始，隐藏小窗')
                    self._overlay.clear_champions()
                    self._overlay.update_current_champion('')
                    # v13 优化项 2:"对局即将开始" 1.5s 预告 → 让用户多看一眼备选结果
                    self._overlay.set_live_status('⚔️', '对局即将开始', '')
                    # v4.1.65: 标记预告窗口截止时刻, 让周期兜底在此期间不误收小窗
                    self._overlay_preview_deadline = _time.monotonic() + 1.7
                    # v14.1:延迟隐藏必须经信号排回主线程调度(QTimer 线程亲和),
                    # 后台线程 new QTimer 会导致 timeout 永不触发、小窗不隐藏。
                    self._schedule_hide_sig.emit()
                elif phase in ('EndOfGame', 'WaitingForStats', 'PreEndOfGame'):
                    print('[App] 游戏结束，显示小窗')
                    self._overlay.safe_show()
        except Exception as e:
            print(f'[App] 阶段处理异常: {type(e).__name__}: {e}')

        # v4.3.5: 对局结束时立刻 + 分阶段补拉战绩 —— 解决"刚打完战绩里不显示"。
        # ⚠️ 刻意放在 `if self._overlay:` **外面**: 万一小窗构造失败,
        # 战绩刷新不该跟着一起不工作(两者本无依赖)。
        if phase == 'EndOfGame':
            self._force_profile_refresh('对局结束')
            self._schedule_recent_refresh()

    def _on_conn(self, connected, name=''):
        """ScriptCore 连接事件回调（来自 _init_session / _poll 失败 / refresh_connection）

        注：UI 连接状态的最终显示由 `_refresh_ui_conn`（REST 周期探活）作为单源
        真相，ScriptCore 的这个回调也参与，但仅在状态确实变化时推送到 UI，
        避免和 REST 探活产生抖动冲突。
        """
        try:
            conn_txt = '连接成功' if connected else '连接失败'
            print(f'[App][Core] {conn_txt} {name}')
            # v7：直接同步走 _set_ui_connected
            self._set_ui_connected(connected, name)
        except Exception as e:
            print(f'[App] 连接处理异常: {type(e).__name__}: {e}')

    def _on_bench(self, ids, cur_id=None):
        try:
            if not self._overlay:
                return
            self._overlay.update_bench_champions(ids, cur_id)
            for cid in ids:
                data = self._cache_champ.hit(cid)
                if data:
                    self._overlay.set_champion_icon(cid, data)
                else:
                    self._dl_icon(cid)
            if cur_id:
                name = self._champ_data.get(cur_id, '')
                self._overlay.update_current_champion(name)
        except Exception as e:
            print(f'[App] 备选英雄处理异常: {type(e).__name__}: {e}')

    def _schedule_hide_impl(self):
        """v14.1:主线程执行 —— 重排 1.5s 后隐藏小窗。

        由 _schedule_hide_sig 从后台线程 emit 后经 QueuedConnection 排到主线程。
        避免后台线程 new/start QTimer(绑定到无事件循环线程 → 永不触发)。
        """
        try:
            if self._pending_hide_timer is not None:
                self._pending_hide_timer.stop()
            self._pending_hide_timer.start(1500)
        except Exception as e:
            print(f'[App] 排定延迟隐藏异常: {type(e).__name__}: {e}')

    def _do_delayed_hide_after_game_start(self):
        """v13 优化项 2:对局正式开始 1.5s 后再隐藏小窗(给"对局即将开始"预告一次被扫到的机会)
        二次校验 phase:若 1.5s 内玩家强退→phase 变 EndOfGame/None,小窗已 safe_show,此时不再 hide。
        """
        try:
            if not self._overlay:
                return
            # v4.1.65: 以"当前真实阶段"为准。原实现只看 _last_phase(事件快照), 而
            # _on_phase 有"同阶段去重", 游戏中又可能夹带 'None' 等过渡值 —— 一旦快照
            # 不是 InProgress, 这里的二次校验就会放行 → 小窗留在游戏画面上。
            cur = self._core.current_phase
            if cur == 'InProgress' or getattr(self, '_last_phase', None) == 'InProgress':
                self._overlay.safe_hide()
        except Exception as e:
            print(f'[App] 延迟隐藏异常: {type(e).__name__}: {e}')

    def _enforce_overlay_hidden_in_game(self, reason=''):
        """v4.1.65 兜底: 只要"当前真实阶段=游戏中"就强制收起小窗。

        独立于阶段事件, 覆盖所有异常路径 —— 最典型的是"人在对局中才打开软件、
        点启动": 此后阶段不再变化, 事件驱动的隐藏逻辑永远不会被触发, 小窗就
        永久挂在游戏画面上。此方法由 _overlay_guard_timer 每 1.5s 调用一次。
        """
        try:
            if not self._overlay or not self._core.is_running:
                return
            if self._core.current_phase != 'InProgress':
                return
            # "对局即将开始" 1.5s 预告窗口内不干预(让用户看一眼备选结果)
            if _time.monotonic() < self._overlay_preview_deadline:
                return
            if self._overlay.isVisible():
                print(f'[App][小窗] 身处对局 → 收起小窗({reason})')
                self._overlay.safe_hide()
        except Exception as e:
            print(f'[App][小窗] 兜底隐藏异常: {type(e).__name__}: {e}')

    def _show_overlay_after_start(self):
        """v4.1.65: 启动脚本后按"当前真实阶段"决定是否显示小窗。

        原实现在 _start() 末尾**无条件** safe_show(): 若启动时人已经在对局中,
        阶段不会再变化(不会再有 InProgress 事件) → 隐藏逻辑等不到触发,
        小窗就永久留在游戏画面上。这里改为先看阶段再决定。
        """
        try:
            if not self._overlay:
                return
            if self._core.is_running and self._core.current_phase == 'InProgress':
                print('[App][小窗] 启动时已在对局中 → 不显示小窗')
                self._overlay.safe_hide()
                return
            self._overlay.safe_show()
        except Exception as e:
            print(f'[App][小窗] 启动显示判断异常: {type(e).__name__}: {e}')

    def _get_icon_cache_dir(self):
        """英雄图标缓存目录(v4.3.6: 统一走 IconCache)。"""
        return self._cache_champ.dir

    # v4.3.10 清理: 删掉 v4.3.6 迁移时留下的 13 个"兼容层"薄封装
    # (_load_icon_from_disk / _save_icon_to_disk / _do_dl_icon / _do_dl_item /
    #  _do_dl_aux / _aux_cache_hit / _aux_cache_put / _aux_cache_dir /
    #  _get_item_cache_dir / _item_cache_hit / _item_cache_put / _PNG_MAGIC /
    #  _icon_lock)。当时以为"旧调用点还在", 实际全项目零引用(AST 扫描确认),
    # 留着只会让人以为还存在第二套缓存实现。
    # ⚠️ 仍在使用的是: _get_icon_cache_dir(头像落盘)、_item_icon_cb/_dl_item、
    #    _aux_icon_cb/_aux_dl_cb/_aux_fetch、_icon_cb_for_picker、_icon_dl_inflight。

    def _prewarm_icon_cache_async(self):
        """v16:启动期后台预热磁盘图标缓存到内存。

        旧的图标加载是纯被动触发（_on_bench / _tick_icon_load 才会 _dl_icon），
        离线启动时没有备选池/选择器事件 → 磁盘上已有的头像永远不被读入内存。
        现在扫一遍缓存目录全部读入,选择器/备选池第一帧即见图。
        v4.3.6: 顺带清理磁盘超额文件(内存有 LRU, 磁盘此前没有上限)。
        """
        try:
            added = prewarm_all(
                [self._cache_champ, self._cache_item,
                 self._cache_spell, self._cache_aug])
            if added:
                print(f'[App] 预热图标缓存: 共 {added} 张')
        except Exception as e:
            print(f'[App] 预热图标缓存失败: {type(e).__name__}: {e}')

    def _dl_icon(self, cid):
        """注入给备选池/选择器: cid -> 触发加载(命中即回填小窗, 未命中下载)。"""
        def _on_ready(key, data):
            if self._overlay:
                self._overlay.set_champion_icon(key, data)
        self._cache_champ.request(cid, self._api.download_champion_icon,
                                  on_ready=_on_ready, pool=self._icon_pool)

    # ==================== v36 召唤师头像(账号卡) ====================
    def _avatar_disk_path(self, icon_id):
        """头像落盘路径。英雄图标是 {cid}.png(带 PNG 魔数校验);头像来源格式
        不定(jpg/png/webp),统一存 avatar_{id}.img,靠 QPixmap.loadFromData
        识别,不做魔数假设。"""
        return os.path.join(self._get_icon_cache_dir(),
                            f'avatar_{icon_id}.img')

    def _dl_avatar(self, icon_id):
        """主线程调用:把"查磁盘缓存 → 未命中下载"整段丢进线程池。

        v4.4.0(P0-2): 原实现在这里同步 `open(path,'rb').read()` —— 头像文件
        可达几十~上百 KB, 而本方法由 `_on_probe_done`(主线程)每轮探活调用;
        机械盘/杀软实时扫描下这一读会明显卡帧。磁盘探测一并移入 worker,
        主线程只负责信号回投(命中与下载走同一条 `_avatar_done` 通路,
        渲染侧无需区分来源)。
        """
        try:
            self._icon_pool.submit(self._do_dl_avatar, icon_id)
        except Exception as e:
            print(f'[App] 头像下载任务提交失败: {type(e).__name__}: {e}')

    def _do_dl_avatar(self, icon_id):
        """线程池执行:先查磁盘缓存 → 未命中再下载 → 落盘 → 信号回主线程渲染。"""
        try:
            # 1. 磁盘缓存命中(含格式魔数交给 QPixmap 识别, 这里只做长度过滤)
            try:
                path = self._avatar_disk_path(icon_id)
                if os.path.exists(path):
                    with open(path, 'rb') as f:
                        data = f.read()
                    if data and len(data) > 200:
                        self._avatar_done.emit(icon_id, data)
                        return
            except Exception as e:
                print(f'[App] 头像缓存读取失败 icon={icon_id}: '
                      f'{type(e).__name__}: {e}')
            # 2. 缓存未命中 → 下载
            data = self._api.download_profile_icon(icon_id)
            if not data:
                return
            try:
                path = self._avatar_disk_path(icon_id)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'wb') as f:
                    f.write(data)
            except Exception:
                pass
            self._avatar_done.emit(icon_id, data)
        except Exception as e:
            print(f'[App] 头像下载失败 icon={icon_id}: '
                  f'{type(e).__name__}: {e}')

    def _apply_avatar(self, icon_id, data):
        """主线程:头像 bytes → 主窗账号卡头像。"""
        try:
            if self._main:
                self._main.set_account_avatar(data, icon_id)
        except Exception as e:
            print(f'[App] 头像渲染失败: {type(e).__name__}: {e}')


    # ==================== v4.3.0 对局详情(战绩页点击) ====================
    def fetch_game_detail(self, game_id, callback):
        """主线程调用: 后台拉单局 10 人详情, 完成后在主线程回调 callback(detail, err)。

        v4.3.0 修掉上一版"点开永久转圈"的三个根因:
        1. **信号类型截断(真正的元凶)**: `_detail_ready` 原声明成 `pyqtSignal(int, …)`,
           PyQt 的 int = C++ 32 位 int, 而 gameId 是 64 位大数 → 跨线程投递时被截断
           (401111677051 → 另一个数), 槽函数拿错的 key pop 不到回调 → 永远不触发。
           已改为 object, 详见 `_detail_ready` 处的注释。
        2. 详情请求走**独立线程池**(见 __init__ 的 _detail_pool), 不再和几百张英雄
           图标下载抢同一批 worker;
        3. 整条链路加**超时兜底**: 到点未回也走 callback, 弹窗能给出"读取超时/重试",
           而不是让 UI 干等。
        """
        try:
            gid = int(game_id or 0)
        except (TypeError, ValueError):
            gid = 0
        if gid <= 0:
            self._safe_detail_cb(callback, None, '无效的对局 id')
            return
        # v4.4.0(P1): 同一 gameId 重复点开(用户连点/软超时后重试)时, 原实现
        # 直接覆盖 `_detail_waiters[gid]`, 旧 callback 从此再无引用、永不触发 ——
        # 第一个弹窗就永久停在"读取中"(它连超时兜底都收不到, 因为 hard timeout
        # 是按 gid pop 的, 只会唤醒最后一个)。这里显式给旧回调一个终止答复。
        old_cb = self._detail_waiters.get(gid)
        if old_cb is not None:
            print(f'[详情] 该对局已有在途请求, 旧回调先收到"已被新请求取代" '
                  f'gameId={gid}')
            self._safe_detail_cb(old_cb, None, '该对局详情已重新请求，本次读取被取代')
        self._detail_waiters[gid] = callback
        QTimer.singleShot(self._detail_soft_ms,
                          lambda g=gid: self._detail_slow(g))
        QTimer.singleShot(self._detail_hard_ms,
                          lambda g=gid: self._detail_timeout(g))
        try:
            self._detail_pool.submit(self._do_fetch_game_detail, gid)
        except Exception as e:
            self._detail_waiters.pop(gid, None)
            print(f'[详情] 排入线程池失败: {type(e).__name__}: {e}')
            self._safe_detail_cb(callback, None, f'线程池繁忙({type(e).__name__})')

    @ui_slot
    def _detail_slow(self, gid):
        """软超时: 还在读。只提示, **不摘 waiter** —— 结果回来照样回填。"""
        cb = self._detail_waiters.get(int(gid))
        if cb is None:
            return
        print(f'[详情] 读取较慢 gameId={gid} (>{self._detail_soft_ms}ms, 继续等待)')
        self._safe_detail_cb(cb, None, '__slow__')

    @ui_slot
    def _detail_timeout(self, gid):
        """硬超时: waiter 还在 = 真的没结果(客户端无响应/端点挂了)。"""
        cb = self._detail_waiters.pop(int(gid), None)
        if cb is None:
            return
        print(f'[详情] 读取失败 gameId={gid} (>{self._detail_hard_ms}ms 无结果)')
        self._safe_detail_cb(cb, None, '读取超时（客户端可能正忙，可重试）')

    def _do_fetch_game_detail(self, gid):
        """详情线程池执行: 拉详情 + 解析 + 补英雄名(只读 _champ_data, 字典读安全)。"""
        detail, err = None, ''
        try:
            # v4.3.1: puuid 缺失就明确报错 —— 没它无法从 10 人里认出"我"。
            # 正常流程下 _on_probe_done 已经填过了(探活 3s 一轮, 点详情时必然就绪)。
            my_puuid = (getattr(self, '_summoner_puuid', '') or '').strip()
            if not my_puuid:
                err = '尚未获取账号信息，请等客户端连接后重试'
            else:
                # 单局详情要让服务端现拼整场数据, 比普通接口慢, 读超时放宽
                raw = self._api.get(f'/lol-match-history/v1/games/{int(gid)}',
                                    timeout=(3.0, 20.0))
                detail = _parse_game_detail(raw, my_puuid)
                if detail:
                    for p in detail.get('players') or []:
                        cid = p.get('cid') or 0
                        p['champion_name'] = self._champ_data.get(
                            cid, f'英雄#{cid}' if cid else '')
                        # v4.3.2: 技能/海克斯中文名(表已在进程内缓存, 这里只是字典读;
                        # 图标本身由弹窗按 id 走 _aux_icon_cb 另外取, 不在解析里做)
                        p['spell1_name'] = self._api.spell_info(p.get('spell1'))[0]
                        p['spell2_name'] = self._api.spell_info(p.get('spell2'))[0]
                        p['augment_names'] = [
                            self._api.augment_info(a)[0]
                            for a in (p.get('augments') or []) if a
                        ]
                else:
                    err = '该对局记录已不可用'
        except Exception as e:
            err = f'{type(e).__name__}: {e}'
            print(f'[详情] 拉取失败 gameId={gid}: {err}')
        self._detail_ready.emit(int(gid), detail, err)

    @ui_slot
    def _on_detail_ready(self, gid, detail, err=''):
        """主线程槽: 把结果派回发起方(弹窗)。"""
        cb = self._detail_waiters.pop(int(gid), None)
        if cb is not None:
            self._safe_detail_cb(cb, detail, err)

    @staticmethod
    def _safe_detail_cb(cb, detail, err=''):
        """回调可能指向已关闭/已销毁的弹窗, 吞掉运行时错误。"""
        try:
            cb(detail, err)
        except RuntimeError as e:
            print(f'[详情] 弹窗已关闭, 丢弃结果: {e}')
        except Exception as e:
            print(f'[详情] 回填异常: {type(e).__name__}: {e}')

    # -------- 装备图标(v4.3.6: 统一走 IconCache) --------
    def _item_icon_cb(self, iid):
        """注入给详情弹窗: iid -> bytes|None(仅读缓存, 不发起下载)。"""
        try:
            iid = int(iid)
        except (TypeError, ValueError):
            return None
        if iid <= 0 or self._cache_item.is_failed(iid):
            return None
        return self._cache_item.hit(iid)

    def _dl_item(self, iid):
        """注入给详情弹窗: 触发后台下载(去重 + 失败黑名单)。

        黑名单很重要: 大乱斗/竞技场那批 6 位特殊装备 id(如 226668/220013)在
        gtimg 与 ddragon 上都没有图, 不做黑名单的话弹窗每几百毫秒的轮询会反复
        提交同一个必失败的下载, 白白空打网络。
        """
        self._cache_item.request(iid, self._api.download_item_icon,
                                 pool=self._item_pool)

    # -------- v4.3.2: 召唤师技能 / 海克斯强化(走 LCU 本地资源) --------
    # 两类结构完全一样(整数 id + 表里的 iconPath)。v4.3.6 起连同装备/英雄一起
    # 收进 IconCache, 这里只负责"把 iconPath 查出来再取图"这一步 —— 缓存本身
    # 的目录/命中/去重/黑名单/LRU 全部由 IconCache 负责。
    _AUX_CACHE = {'spell': '_cache_spell', 'augment': '_cache_aug'}
    _AUX_INFO = {'spell': 'spell_info', 'augment': 'augment_info'}

    def _cache_for(self, kind):
        return getattr(self, self._AUX_CACHE.get(kind, '_cache_aug'))

    def _aux_info(self, kind, key):
        """表查询: 返回 (中文名, iconPath)。表未加载/查不到时为 ('', '')。"""
        try:
            return getattr(self._api, self._AUX_INFO.get(kind, 'augment_info'))(key)
        except Exception:
            return ('', '')

    def _aux_icon_cb(self, kind):
        """注入给弹窗: key -> bytes|None(仅读缓存, 不发起下载)。"""
        cache = self._cache_for(kind)

        def _cb(key):
            try:
                key = int(key)
            except (TypeError, ValueError):
                return None
            if key <= 0 or cache.is_failed(key):
                return None
            return cache.hit(key)
        return _cb

    def _aux_dl_cb(self, kind):
        """注入给弹窗: key -> 触发后台下载(去重 + 失败黑名单)。"""
        return self._aux_fetch(kind)

    def _aux_fetch(self, kind):
        """返回一个 key -> None 的回调: 触发该 kind 图标的后台下载。"""
        cache = self._cache_for(kind)

        def _fetch(key):
            """线程池执行: 查表拿 iconPath → LCU 本地资源取图 → 缓存。"""
            name, icon_path = self._aux_info(kind, key)
            if not icon_path:
                print(f'[详情] {kind} 表内无此 id={key}')
                return None
            return self._api.download_lcu_asset(icon_path)

        def _cb(key):
            try:
                key = int(key)
            except (TypeError, ValueError):
                return
            cache.request(key, _fetch, pool=self._item_pool)
        return _cb

    def _on_champ_data(self, data):
        self._champ_data = data
        if self._overlay:
            self._overlay.set_champion_data(data)
        # v23:core 在脚本启动时加载成功、而应用侧缓存/远程刷新都尚未建选择器时,
        # 排回主线程补建(幂等;由后台线程调用,经信号回主线程)
        if not getattr(self, '_picker_initialized', False) and self._champ_data:
            self._champ_remote_ready.emit()

    def _on_status(self, msg):
        """状态更新（从后台线程调用，用信号转发到主线程）
        S3:统一走 set_live_status 显示原文。旧 if/elif 分支文案与 core 实际输出
        （🔄 秒换 X / ✅ 已选 X 等）不匹配导致多数消息连小窗都不显示;现在全部直达。
        """
        try:
            print(f'[App] 状态: {msg}')
            # set_live_status 内部使用 sig_live_status 信号，线程安全
            if self._overlay:
                self._overlay.set_live_status('', msg, '')
        except Exception as e:
            print(f'[App] 状态处理异常: {type(e).__name__}: {e}')

    def _on_ready(self, visible):
        try:
            # 只在 ReadyCheck 阶段才显示面板
            if self._overlay:
                if visible and self._core.current_phase == 'ReadyCheck':
                    self._overlay.show_ready_check_buttons(True)
                elif not visible:
                    self._overlay.show_ready_check_buttons(False)
        except Exception as e:
            print(f'[App] 确认对局处理异常: {type(e).__name__}: {e}')

    def _on_champ_select_info(self, info):
        """选人阶段信息回调"""
        try:
            if not self._overlay:
                return
            name = info.get('my_champion_name', '')
            locked = info.get('locked', False)
            time_left = info.get('time_left', 0)
            if name:
                status = f'{name}（已锁定）' if locked else f'{name}（{time_left}s）'
                self._overlay.update_current_champion(status)
            else:
                self._overlay.update_current_champion('')
        except Exception as e:
            print(f'[App] 选人信息处理异常: {type(e).__name__}: {e}')

    def _emit_ws_connected(self):
        """WS 后台线程 → 主线程的适配层(on_open)。

        绝不能在这里直接碰 UI, 也不能用 QTimer.singleShot(跨线程不触发, 见类属性注释);
        emit 类级信号, Qt 会按 receiver(主线程) 亲和自动选 QueuedConnection。
        """
        try:
            self._ws_connected_sig.emit()
        except Exception as e:
            print(f'[App] WS 连接信号派发失败: {type(e).__name__}: {e}')

    def _emit_ws_disconnected(self):
        """WS 后台线程 → 主线程的适配层(on_close)。同上。"""
        try:
            self._ws_disconnected_sig.emit()
        except Exception as e:
            print(f'[App] WS 断开信号派发失败: {type(e).__name__}: {e}')

    def _on_ws_disconnected(self):
        """WS断开回调（在 websocket-client 的后台线程触发）

        注意：这里**不再立刻把 UI 置为"未连接"**。
        原先的逻辑会让一个偶发的 WS 闪断（比如握手失败、SSL 错误、空闲超时）
        直接把 UI 打回红色，即使客户端依旧可达（ScriptCore 的 REST 轮询还在
        正常进行），造成"日志说连上了但界面显示未连接"的撕裂。

        新的做法：仅清理连接器内部状态，让 AppController 的定期 REST 探活
        （_ui_conn_refresh_timer）来判断 UI 应当显示什么。
        WS 事件流是否可用是次要的——脚本功能主要走 REST API，WS 只是用来
        接收推送事件。WS 断开期间 REST 仍可正常工作，UI 就应保持绿色。

        v4.3.10: 本函数现在**直接在主线程执行**（由 _ws_disconnected_sig 排回），
        不再需要 singleShot 包装 —— 跨线程 singleShot 实测永不触发,
        曾导致这里的 _conn.reset() 从不执行 → 关游戏重开后无法自动重连。
        """
        try:
            self._conn.reset()
            self._core.refresh_connection()
            # 不再调用 self._main.update_connection_status(False)，
            # 改由 _ui_conn_refresh_timer 周期探活来刷新 UI。
        except Exception as e:
            # WS 断开后的重连准备失败 —— 留痕,否则表现为"断线后永远重连不上"
            print(f'[App] WS 断开后清理/重连准备失败: '
                  f'{type(e).__name__}: {e}')

    # ==================== 连接（后台线程，不阻塞UI） ====================
    def _try_connect(self):
        if self._ws.is_connected:
            self._reconnect_fail = 0
            self._reconnect_timer.setInterval(self._RETRY[0])
            return
        # v4.3.10: 单飞 —— 上一轮后台连接(可能含全盘 BFS, 30s+)还没跑完就不再起新线程
        if self._connecting:
            return

        # v4.4.1(UI-B5): 起手就把小窗打成"连接中"(琥珀)。这段后台全盘探测(最坏
        # 30s+)以前在界面上与"客户端根本没开"渲染得一模一样 —— 都是红色"未连接",
        # 用户会以为软件坏了。放在单飞判断之后: 已在连接中就不必重复推。
        self._set_conn_state('connecting', '连接中')

        self._reconnect_fail += 1
        idx = min(self._reconnect_fail - 1, len(self._RETRY) - 1)
        self._reconnect_timer.setInterval(self._RETRY[idx])

        # 后台线程执行连接，避免阻塞UI
        self._connecting = True
        threading.Thread(target=self._try_connect_worker, daemon=True).start()

    def _try_connect_worker(self):
        """后台线程：执行find_client和WebSocket连接

        注意：`self._ws.connect()` 是异步的——它只启动了一个后台线程去
        run_forever，函数立即返回，此时 WS 尚未真正建立（is_connected 仍为
        False）。所以这里"找到客户端并启动 WS"后，绝不能立刻把 UI 置为已连接；
        UI 的"已连接"状态统一交给 WS 真正 on_open 时触发的 _on_ws_connected()
        来置绿，否则会出现"connect 一返回就假连上、随后又被断开打回未连接"的
        抖动，导致不点启动就一直显示未连接。
        """
        try:
            # v4.4.0(P1): 原先这里分"已缓存参数 / 未缓存"两支, 前者直接穿透
            # connector 的私有 `_validate_cached_connection()` 探活、失败再 reset。
            # 而 find_client() 入口本来就做同一件事(缓存参数先探活, 通过即 return
            # True; 失败 reset 后继续走完整发现链路), 两支完全等价 —— 合并成一次
            # 公开调用, 顺带消掉跨模块私有穿透。本函数在后台线程, 全链路扫描不卡 UI。
            if not self._conn.find_client():
                # v7：直接同步,不用 singleShot
                self._set_ui_connected(False, '')
                self._settle_connecting()   # v4.4.1(UI-B5): 连接中 → 未连接
                return
            print('[App] 已找到客户端')

            if not self._ws.is_connected:
                # v4.4.0(P0-1b 配套): connect() 不再抛 ConnectionError, 改为返回
                # 状态串('ok'/'already-running'/'no-client')。这里读返回值区分
                # "客户端还没就绪"与"连接真的失败", 而不是无差别 reset 掉刚发现的
                # 端口/令牌 —— 客户端正处于启动中时 reset 会让下一轮重头 BFS。
                try:
                    st = self._ws.connect()
                except Exception as e:
                    st = f'exc:{type(e).__name__}'
                if st == 'no-client':
                    self._set_ui_connected(False, '')
                    self._settle_connecting()   # v4.4.1(UI-B5)
                    return
                if st not in ('ok', 'already-running'):
                    self._conn.reset()
                    print(f'[App] 连接失败({st})')
                    self._settle_connecting()   # v4.4.1(UI-B5)
                    return
            # 注意：不再在这里调用 _on_connect_success()，
            # 已连接状态的 UI 刷新由 WS on_open 回调 _on_ws_connected() 负责。
        except Exception as e:
            print(f'[App] 连接线程异常: {type(e).__name__}: {e}')
            self._settle_connecting()   # v4.4.1(UI-B5): 异常也不能把琥珀永久留在界面上
        finally:
            self._connecting = False

    def _on_ws_connected(self):
        """WS 真正建立连接(on_open)后回调 —— 在 websocket-client 后台线程触发

        与 _on_ws_disconnected 对称：只有此刻才是真正连上，刷新 UI 为已连接，
        并在此后一次性加载英雄数据（避免 connect() 刚返回、客户端就绪前就加载）。

        v4.3.10: 改为由 _ws_connected_sig 排回主线程后**直接执行**。
        原实现用 QTimer.singleShot(0, _do) 包装, 而本函数在后台线程被调用,
        跨线程 singleShot 实测永不触发 → 这条"连上后补拉数据"的路径其实一直没生效。
        """
        try:
            self._reconnect_fail = 0
            self._reconnect_timer.setInterval(self._RETRY[0])
            self._on_connect_success()
        except Exception as e:
            print(f'[App] WS 连接成功处理异常: {type(e).__name__}: {e}')

    def _force_profile_refresh(self, reason=''):
        """v4.3.5: 立刻重拉一次低频字段(重点是战绩), 不等探活的 30s 周期。

        背景: 探活的低频字段是"每 10 轮(约 30s)"拉一次, 且 **Python 侧没有任何
        '对局刚结束' 的触发点** —— 于是刚打完的一局最长要等 30s 才出现在战绩页。
        对局结束(EndOfGame/WaitingForStats)时调这里, 让下一轮探活立即重拉。

        ⚠️ LCU 的 match-history 还有**服务端入库延迟**(实测刚结束 1~3 分钟内可能
        仍查不到), 所以调用方要按 [15s, 45s, 90s] 分几次补拉, 单次不够。
        """
        try:
            w = getattr(self, '_probe_worker', None)
            if w is None:
                return
            w._force_profile = True
            print(f'[战绩] 触发强制刷新({reason}), 下一轮探活立即重拉')
        except Exception as e:
            print(f'[战绩] 强制刷新置位失败: {type(e).__name__}: {e}')

    def _schedule_recent_refresh(self):
        """对局结束后按 [15s, 45s, 90s] 补拉战绩 —— 对抗 LCU 服务端入库延迟。"""
        for delay in (15_000, 45_000, 90_000):
            try:
                QTimer.singleShot(delay, lambda: self._force_profile_refresh('对局结束后补拉'))
            except Exception:
                pass

    def _refresh_ui_conn(self):
        """v8：主线程只做轻量校验 + 派发任务,实际 REST 在 _ConnProbeWorker (QThread) 跑。

        关键修正：v7 在主线程同步跑 REST,关游戏时 socket RST 阻塞主线程 QTimer
        几百 ms → UI 假死卡顿。改为后台线程后,主线程立即返回,~3s 后 probe_done
        在主线程派发,主线程再调 _set_ui_connected 翻 UI。

        v4.3.10 修复: 增加"在途"保护。worker 每轮 REST 最坏要 ~7s(连接 2s + 读 5s),
        而本定时器 3s 一跳且**无条件 emit trigger**。客户端假死时任务在
        QueuedConnection 队列里**无界堆积**, 每个堆积任务稍后都会再打一次真实的
        网络请求 —— 既浪费又让"关游戏后探测"雪上加霜。
        现在同一时刻最多只有一个探活任务在途。

        fail_count 保留在主线程(只在 UI 更新时修改),保证线程安全:主线程独占读写。
        """
        if self._probe_inflight:
            # v4.4.0 看门狗: 在途标志若超时仍未清(见 __init__ 的 _probe_inflight_at),
            # 说明本轮 queued 任务丢失 —— 强制复位让下一轮重新派发, 不能永久停摆。
            stuck_s = _time.monotonic() - self._probe_inflight_at
            if stuck_s > self._PROBE_INFLIGHT_TIMEOUT:
                print(f'[探活] 在途标志已滞留 {stuck_s:.1f}s '
                      f'(> {self._PROBE_INFLIGHT_TIMEOUT:.0f}s), 强制复位重派')
                self._probe_inflight = False
            else:
                return
        if not self._probe_thread.isRunning():
            # 线程挂了(理论不该发生) —— 绝不能回主线程同步跑 do_probe(里面是
            # 最坏 ~7s 的 REST, 会把主线程 QTimer/UI 整段卡死)。安全降级为:
            # 判未连接即可, 等重连定时器把线程救回(实际 _ProbeQThread 一旦 start
            # 就长期存活, 此分支几乎走不到)。
            self._probe_fail_count += 1
            if self._probe_fail_count >= 2:
                self._set_ui_connected(False, '')
            return
        if not self._conn.is_connected:
            # 无端口/token 直接判未连,无 REST 必要
            self._probe_fail_count = 0
            self._set_ui_connected(False, '')
            return
        # 跨线程调用 worker.do_probe():用 trigger 信号派发,Qt AutoConnection
        # 会按 receiver 线程亲和自动用 QueuedConnection,把 do_probe 排到
        # worker 所在线程(_ProbeQThread.run() 里 exec() 的事件循环)。
        # 不用 QMetaObject.invokeMethod 是因为后者要求被调方法是 @pyqtSlot。
        try:
            self._probe_inflight = True
            self._probe_inflight_at = _time.monotonic()
            self._probe_worker.trigger.emit()
        except Exception as e:
            self._probe_inflight = False
            # trigger emit 异常: 不落回主线程跑 REST, 只留痕等下一轮 3s 重试
            print(f'[探活] trigger 信号失败: {type(e).__name__}: {e}, 下轮重试')

    @ui_slot
    def _on_probe_done(self, ok: bool, info, err_msg: str):
        """worker 完成后由 Qt 排回主线程执行,只负责 UI 状态机。

        v36:info 为 dict(见 _ConnProbeWorker),同时驱动连接状态与账号卡。
        v4.3.10: 第一件事就是清"在途"标志 —— 无论成功/失败/异常路径都必须清,
        否则探活会永久停摆(比堆积更糟)。
        """
        self._probe_inflight = False
        self._probe_inflight_at = 0.0
        if ok and isinstance(info, dict):
            self._probe_fail_count = 0
            name = info.get('name', '') or ''
            # v4.3.1: 缓存 puuid —— 详情/战绩请求要按 puuid 反查自己的 participantId。
            # 它本来只存在探活 worker 实例上(worker 的 self._puuid, 现名 _probe_puuid),
            # AppController 自己没有, 于是"点开对局详情"一取就 AttributeError。
            # 这里随每轮探活同步过来。v4.4.0(P1-25): 本副本改名 _summoner_puuid,
            # 与 worker 那份彻底分开(两份同名不同对象, 曾是排查陷阱)。
            pu = (info.get('puuid') or '').strip()
            if pu and pu != getattr(self, '_summoner_puuid', ''):
                if getattr(self, '_summoner_puuid', ''):
                    # v4.3.5: 换号 → 让 UI 的战绩去重签名失效, 使新账号的战绩
                    # 立即重建(否则 sig 相同 → 界面继续显示上一个账号的列表)。
                    # v4.3.6: 这里**不能静默失败** —— 它是换号刷新的唯一开关,
                    # 吞掉异常会让"换号后战绩不刷新"以完全无痕的方式复现。
                    if self._main:
                        try:
                            self._main.mark_recent_dirty()
                        except Exception as e:
                            print(f'[账号] 战绩签名置脏失败(换号后可能仍显示旧账号'
                                  f'战绩): {type(e).__name__}: {e}')
                self._summoner_puuid = pu
            if not self._ui_connected:
                print(f'[App] 已连接客户端（{name or "未知召唤师"}）')
            self._set_ui_connected(True, name)
            # 账号卡:主线程直接更新(3s 一轮,数据全量,UI 侧有去重)
            if self._main:
                try:
                    # v4.1.43: 熟练度 champion_id → 英雄名映射(champ_data 在主线程)
                    mt = info.get('mastery_top')
                    if mt:
                        info['mastery_top'] = [
                            dict(m, name=self._champ_data.get(
                                m.get('champion_id'),
                                f"#{m.get('champion_id')}"))
                            for m in mt
                        ]
                    # v4.1.66: 战绩页明细同样补英雄名(UI 只负责渲染)
                    recent = info.get('recent')
                    if isinstance(recent, dict) and recent.get('games'):
                        for g in recent['games']:
                            cid = g.get('champion_id') or 0
                            if not g.get('champion_name'):
                                g['champion_name'] = self._champ_data.get(
                                    cid, f'英雄#{cid}' if cid else '')
                    self._main.update_account_info(info)
                    # v4.1.66: 战绩页图标回调只注入一次(与选择器同源, 走内存缓存)
                    if not getattr(self, '_recent_icon_wired', False):
                        self._main.set_recent_icon_cb(
                            self._icon_cb_for_picker, self._dl_icon,
                            self._item_icon_cb, self._dl_item,
                            # v4.3.2: 召唤师技能 / 海克斯强化图标(LCU 本地资源)
                            self._aux_icon_cb('spell'), self._aux_dl_cb('spell'),
                            self._aux_icon_cb('augment'), self._aux_dl_cb('augment'))
                        self._recent_icon_wired = True
                        # v4.3.0: 战绩详情数据回调(点击行 → 独立线程池拉 10 人明细)
                        self._main.set_recent_detail_cb(self.fetch_game_detail)
                except Exception as e:
                    print(f'[App] 账号卡更新失败: {type(e).__name__}: {e}')
            # 头像:icon_id 变化才重新下载
            icon_id = info.get('icon_id') or 0
            if icon_id and icon_id != self._last_icon_id:
                self._last_icon_id = icon_id
                self._dl_avatar(icon_id)
            if not self._champ_data:
                # v4.4.0(P1): 这里原调 _try_load_champ_data(), 内含 _load_cache()
                # 的磁盘读 —— 而本函数是主线程 3s 一拍的探活回调, 缓存文件缺失时
                # 等于每拍都读盘一次。本地缓存启动时已读过一次(singleShot(0)),
                # 此处只需把"还没拿到数据"这件事交给后台联网刷新(自带单飞与
                # 失败复位), 主线程不做 IO。
                self._start_remote_champ_refresh()
        else:
            self._probe_fail_count += 1
            # 连续失败 ≥2 才判未连接,关游戏场景通常失败 1 次(socket 还在断)
            # + 关掉那一刻 RST,第 2 次 timeout 才真正确认
            if self._ui_connected and self._probe_fail_count >= 2:
                # v4.4.0(P1-2 接线): 探活失败但进程/端口还在时, 两种原因完全
                # 不同 —— "客户端假死(请求超时)" 与 "端口还在但鉴权/网关已废"。
                # LCUAPI 一直维护着最后一处 REST 失败原因(原先无人读取, 等于
                # 白记), 这里在读它出来一起打进日志, 排障不用再靠猜。
                print('[App] 客户端连接已断开')
                try:
                    if not self._api.is_healthy and self._api.last_error:
                        print(f'[App] REST 健康度: 连续失败 {self._api.consecutive_failures} 次, '
                              f'最后一处错误: {self._api.last_error}')
                except Exception:
                    pass
                self._set_ui_connected(False, '')

    def _set_ui_connected(self, connected: bool, name: str = ''):
        """线程安全的连接状态入口：主线程直跑，后台线程经信号排回主线程。

        S1 修复：_on_conn(ScriptCore 轮询 daemon 线程)/_try_connect_worker(threading)
        都在非主线程调用本函数,原实现直接 setText/setStyleSheet 主窗控件,违反 Qt
        线程模型。现按调用线程分流,渲染永远发生在主线程。
        """
        if QThread.currentThread() is not self.thread():
            self._ui_cmd.emit(connected, name)
            return
        self._apply_ui_connected(connected, name)

    def _set_conn_state(self, state: str, text: str = ''):
        """v4.4.1(UI-B5): 线程安全的三态连接指示入口(主线程直跑, 后台线程经信号排回)。

        与 `_set_ui_connected` 的分工: 后者管**主窗**的已连接/未连接二元状态,
        本条只管**小窗**的三态指示器。二者互不依赖。
        """
        if QThread.currentThread() is not self.thread():
            self._conn_state_sig.emit(state, text)
            return
        self._apply_conn_state(state, text)

    def _apply_conn_state(self, state: str, text: str = ''):
        """v4.4.1(UI-B5): 主线程内把三态推给小窗(浮层可能还没建, 静默跳过)。"""
        if not self._overlay:
            return
        try:
            self._overlay.set_conn_state(state, text)
        except Exception as e:
            print(f'[App] 小窗连接指示更新失败: {type(e).__name__}: {e}')

    def _settle_connecting(self):
        """v4.4.1(UI-B5): 连接尝试失败收尾时, 把"连接中"落回"未连接"。

        为什么必须显式做: `_set_ui_connected(False, '')` 只在 True→False 的**跃迁**
        上生效, 而"连接中"不是连接态 —— 若连接线程失败时只调它, 琥珀提示会永久
        停在"连接中"。反过来, 若期间 WS 已真正 on_open, 这里也不能把绿点压回红。
        """
        try:
            if self._ui_connected or self._ws.is_connected:
                return
        except Exception:
            pass
        self._set_conn_state('disconnected', '未连接')

    def _apply_ui_connected(self, connected: bool, name: str = ''):
        """主线程内直接同步设置 UI 连接状态（已连接/未连接）

        v4.4.0(P1): 原先直接写 `self._main._conn_label` / `_summoner_label` /
        调 `_update_dot()` —— 三个都是 MainWindow 的私有成员, 一旦 ui 侧重命名
        这里就是运行时 AttributeError(被 except 吞掉只剩一行日志, 界面永远
        不更新)。改用 ui 侧公开的 apply_connection_ui()。
        """
        if connected and not self._ui_connected:
            self._ui_connected = True
            if self._main:
                try:
                    self._main.apply_connection_ui(True, name)
                except Exception as e:
                    print(f'[App] 主窗连接状态更新失败: {type(e).__name__}: {e}')
            if self._overlay:
                try:
                    self._overlay.set_conn_indicator(True, '已连接')
                except Exception as e:
                    print(f'[App] 小窗连接指示更新失败: {type(e).__name__}: {e}')
            # v23:客户端(重新)连上时若英雄数据尚未联网刷新成功(flag 已复位),
            # 立即后台补拉一次 —— 修复"先开脚本后开游戏,数据永是旧缓存"场景
            if not getattr(self, '_champ_refresh_started', False):
                self._start_remote_champ_refresh()
        elif not connected and self._ui_connected:
            self._ui_connected = False
            # v4.1.39:重置头像下载去重标记 —— 否则同账号重连后 icon_id 不变,
            # 永不重新走缓存/下载链路,界面头像停在 🎮/旧状态
            self._last_icon_id = 0
            if self._main:
                try:
                    # 断开态文案由 ui 侧决定(原先在这里硬编码 '未连接到客户端')
                    self._main.apply_connection_ui(False, '')
                    # v36:账号卡回到等待态(名字/等级/段位/头像全部清空)
                    self._main.update_account_info(None)
                except Exception as e:
                    print(f'[App] 主窗连接状态更新失败: {type(e).__name__}: {e}')
            if self._overlay:
                try:
                    self._overlay.set_conn_indicator(False, '未连接')
                except Exception as e:
                    print(f'[App] 小窗连接指示更新失败: {type(e).__name__}: {e}')

    def _on_connect_success(self):
        """连接成功后更新UI（主线程）—— 立即反馈绿色，后续由定时器持续校准"""
        # v7：直接同步走 _set_ui_connected,绕开 singleShot
        self._set_ui_connected(True, '')
        # 立即触发一次探活（不等 3s）让 summoner 名称尽快显示
        self._refresh_ui_conn()

    # ==================== 英雄选择器自动初始化 ====================
    def _auto_init_picker(self):
        """英雄数据加载后自动初始化偏好选择器(v20:幂等 —— 只允许初始化一次)"""
        if getattr(self, '_picker_initialized', False):
            return
        print(f'[App] 初始化英雄选择器: 数据={len(self._champ_data)}个')
        if not self._champ_data or not self._main:
            print('[App] 跳过: 数据或窗口未就绪')
            return
        try:
            settings = self._main.get_settings()
            self._main.init_champion_picker(
                self._champ_data, settings.get('preferred_champions', []),
                self._icon_cb_for_picker, champion_roles=self._champ_roles,
                download_cb=self._dl_icon,
                # v4.3.10: 让选择器能区分"下载慢"与"下载失败"
                is_dl_inflight=self._icon_dl_inflight
            )
            self._picker_initialized = True
            print('[App] 英雄选择器初始化完成')
        except Exception as e:
            print(f'[App] 初始化选择器失败: {type(e).__name__}: {e}')

    def _icon_cb_for_picker(self, cid):
        """选择器图标回调（仅返回缓存，不阻塞UI）"""
        return self._cache_champ.hit(cid)

    def _icon_dl_inflight(self, cid):
        """选择器用: 该英雄图标是否有下载在途。

        v4.3.10: 选择器的"失败计数"据此避免把慢速下载误判成永久失败。
        """
        try:
            return self._cache_champ.is_downloading(cid)
        except Exception:
            return False

    # ==================== 控制 ====================
    def _start(self):
        """启动脚本(GUI 线程,必须立即返回)。

        v4.4.0(P0-2): 本方法原先把整条"查客户端 → 连 WS → 刷连接 → 起核心"
        同步跑在 GUI 槽里,其中 find_client(quick=True) 要 spawn wmic/PowerShell
        (connector.py 里超时 6~8s),core.start() 里还有 old.join(timeout=3.0)。
        客户端没开/假死时用户点"启动"后主界面十几秒不响应,Windows 直接报
        "程序未响应"。现在阻塞段全部移入 _start_worker 后台线程,本方法只做
        "能在 GUI 线程安全秒回"的事(提权检查 + 单飞标志 + 派发)。
        """
        # v4.1.58: 启动脚本前先查"自动选英雄"权限。实测(2026-09-09 真实日志):
        # 非管理员进程对管理员权限的 LOL 客户端(leagueclientux.exe)模拟点击会被
        # UIPI 静默丢弃(cursor/sendinput/postmsg 三机制全失效), 点卡 12 个点全
        # "未命中"。settings 已持久化 auto_select=True 时, 设置页点开关信号不会
        # 再触发提权, 所以必须在启动脚本这里补查。
        try:
            if (not self._elevate_requested
                    and self._main
                    and self._main.get_settings().get('auto_select_champion')):
                from lcu.clicker import is_admin
                if not is_admin():
                    print('[启动] 自动选英雄已开启但当前非管理员, 引导提权')
                    if self._on_auto_select_champion_request():
                        # 已触发提权重启(旧实例稍后退出), 不再继续启动脚本
                        return
                    # 用户取消 UAC → 自动选英雄已回退关闭, 继续启动其余功能
        except Exception as e:
            print(f'[启动] 权限检查异常(忽略): {type(e).__name__}: {e}')

        # v4.4.0(P0-2): 单飞 —— 后台启动期间重复点"启动"直接忽略
        if self._starting:
            print('[App] 正在启动中, 忽略重复的启动请求')
            return
        self._starting = True
        print('[App] 正在启动, 查找客户端…')
        threading.Thread(target=self._start_worker, daemon=True,
                         name='app-start').start()

    def _start_worker(self):
        """启动流程的阻塞部分(后台 daemon 线程,绝不碰 Qt 控件)。

        结果一律经 _start_done_sig 排回主线程, 由 _on_start_done 做 UI 更新。
        """
        ok, err = False, ''
        try:
            # 1. 查找客户端（quick：不跑全盘扫描，失败由后台定时器持续重试）
            if not self._conn.is_connected:
                try:
                    found = self._conn.find_client(quick=True)
                except Exception as e:
                    print(f'[App] 查找客户端异常: {type(e).__name__}: {e}')
                    found = False
                if not found:
                    self._start_done_sig.emit(False, 'notfound')
                    return
                try:
                    self._ws.connect()
                except Exception as e:
                    print(f'[App] 连接异常: {type(e).__name__}: {e}')

            # 2. 刷新核心连接
            if not self._core.is_connected:
                try:
                    self._core.refresh_connection()
                except Exception as e:
                    print(f'[App] 刷新连接失败: {type(e).__name__}: {e}')

            # 3. 启动脚本(内部可能 join 上一轮线程, 最多 3s —— 这正是必须放
            #    后台线程的原因之一)
            try:
                self._core.start()
            except Exception as e:
                print(f'[App] 启动核心失败: {type(e).__name__}: {e}')

            ok = True
        except Exception as e:
            import traceback
            traceback.print_exc()
            err = f'{type(e).__name__}: {e}'
        self._start_done_sig.emit(ok, err)

    def _on_start_done(self, ok, err):
        """_start_worker 完成 → 主线程收尾(设置同步 / 控件状态 / 小窗)。"""
        try:
            # 用户在启动过程中点了"停止"(或已退出) → 别再把 UI 拉回运行态,
            # 并把可能已经起来的核心停掉, 避免"界面显示已停止但脚本在跑"。
            if not self._starting:
                print('[App] 启动完成前已收到停止请求, 回滚: 停止核心')
                try:
                    self._core.stop()
                except Exception:
                    pass
                return
            self._starting = False

            if not ok:
                if err == 'notfound':
                    try:
                        QMessageBox.warning(self._main, '未找到客户端',
                                            '未找到英雄联盟客户端。\n\n请确认已启动并登录游戏（客户端开着即可，'
                                            '无需进入对局），本程序会在后台持续自动重试连接。')
                    except Exception:
                        print('[App] 未找到客户端，请先启动游戏')
                else:
                    print(f'[App] 启动失败: {err}')
                    if self._main:
                        try:
                            self._main.set_script_running(False)
                        except Exception as e:
                            print(f'[App] 启动失败后回退按钮状态也失败: '
                                  f'{type(e).__name__}: {e}')
                return

            # 3. 同步设置
            if self._main:
                try:
                    s = self._main.get_settings()
                    self._core.update_settings(s)
                    self._main.update_phase(self._core.current_phase)
                    self._main.set_script_running(True)
                    _yn = lambda v: '开启' if v else '关闭'
                    print(f'[启动] 设置: 匹配={_yn(s.get("auto_queue"))}, 接受={_yn(s.get("auto_accept"))}, 返回={_yn(s.get("auto_play_again"))}, 换英雄={_yn(s.get("auto_pick_champion"))}')
                    if s.get('preferred_champions'):
                        print(f'[启动] 偏好英雄: {len(s["preferred_champions"])}个')
                except Exception as e:
                    print(f'[App] 同步设置失败: {type(e).__name__}: {e}')

            # 5. 更新UI
            if self._tray_toggle:
                self._tray_toggle.setText('停止脚本')
                self._tray_toggle.setChecked(True)
                # v4.4.1(UI-B7): 图标跟文案一起切(自定义 QSS 下 QAction 的
                # 对勾指示器不一定渲染, 用图标 + 文案表达运行态更可靠)
                self._tray_toggle.setIcon(icon_tray_stop())
            if self._overlay:
                try:
                    # v4.3.10: 用 set_conn_indicator 而不是 update_connection_status。
                    # 后者只驱动底部 _conn_dot/_conn_lbl, 顶部 _live_dot 仍是旧的
                    # → 点启动的瞬间会出现"底部已连接、顶部未连接"的自相矛盾显示。
                    self._overlay.set_conn_indicator(
                        self._conn.is_connected,
                        '已连接' if self._conn.is_connected else '未连接')
                    self._overlay.set_script_running(True)
                    # v4.1.65: 不再无条件 safe_show()。启动这一瞬间核心线程正在跑
                    # _reconcile_state 取真实阶段, 让 400ms 后按阶段决定显隐
                    # (对局中启动就不显示)。原实现无条件显示是"游戏中不隐藏"的根因。
                    QTimer.singleShot(400, self._show_overlay_after_start)
                except Exception as e:
                    print(f'[App] 更新小窗失败: {type(e).__name__}: {e}')
            # v4.1.65: 启动"游戏中必隐藏"周期兜底
            try:
                self._overlay_guard_timer.start()
            except Exception as e:
                print(f'[App] 启动小窗兜底定时器失败: {type(e).__name__}: {e}')

            QTimer.singleShot(500, lambda: self._main.hide() if self._main else None)
            print('[App] 脚本已启动')
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._starting = False
            print('[App] 启动失败')
            if self._main:
                try:
                    self._main.set_script_running(False)
                except Exception as e:
                    print(f'[App] 启动失败后回退按钮状态也失败: '
                          f'{type(e).__name__}: {e}')

    def _stop(self):
        print('[停止] 脚本已停止')
        # v4.4.0(P0-2): 若"启动"还在后台跑, 清掉单飞标志让 _on_start_done
        # 走回滚分支(停核心 + 不把 UI 拉回运行态), 否则界面会显示"已停止"
        # 而脚本在后台真的跑起来。
        self._starting = False
        self._core.stop()
        # v4.1.65: 脚本停了就不必再兜底收小窗
        try:
            self._overlay_guard_timer.stop()
        except Exception:
            pass
        if self._main:
            self._main.set_script_running(False)
        if self._tray_toggle:
            self._tray_toggle.setText('启动脚本')
            self._tray_toggle.setChecked(False)
            self._tray_toggle.setIcon(icon_tray_play())   # v4.4.1(UI-B7)
        if self._overlay:
            self._overlay.set_script_running(False)
            self._overlay.safe_hide()
            self._overlay.clear_champions()

    @ui_slot
    def _toggle_script(self):
        if self._core.is_running:
            self._stop()
        else:
            self._start()

    @ui_slot
    def _minimize_to_tray(self):
        """最小化到托盘"""
        if self._main:
            self._main.hide()
        if self._core.is_running and self._overlay:
            # v4.1.65: 游戏中不弹小窗(否则游戏画面上多一个悬浮窗)
            if self._core.current_phase == 'InProgress':
                self._overlay.safe_hide()
            else:
                self._overlay.safe_show()
        print('[窗口] 最小化到托盘')

    def _quit(self):
        # G7:按依赖顺序收尾——先停所有定时器与核心线程,再等探活线程与图标下载池
        # 收工(配合 S2 的 REST 默认超时,最坏等待 ~5s),最后才关窗,避免
        # "QThread: Destroyed while thread is still running" 与后台线程向已关窗口发信号。
        # v4.4.0(P0-2): 清掉启动单飞标志 —— 若启动还在后台跑, 收尾后
        # _on_start_done 会走回滚分支, 不会往已关闭的窗口上发信号。
        self._starting = False
        for t in (self._ui_conn_refresh_timer, self._reconnect_timer, self._mini_log_timer,
                  self._overlay_guard_timer, self._pending_hide_timer):
            try:
                t.stop()
            except Exception:
                pass
        # v4.4.2(P2-28): 原这两行是 _quit() 里唯一没包 try 的收尾 —— 同函数其余
        # 收尾(定时器/探活线程/图标池/日志)全包了 try, 这里一抛就跳过末尾的
        # QApplication.quit(), 进程留在半退出态。概率低但成本为零, 顺手包一下。
        try:
            self._core.stop()
        except Exception:
            pass
        try:
            self._ws.disconnect()
        except Exception:
            pass
        try:
            self._probe_thread.quit()
            # worker 单轮最坏 ~7s(连接 2s + 读 5s), 且可能刚触发新一轮探活,
            # 等 8s 覆盖"最后一次在途探测"的完整周期, 避免 QThread 销毁时 worker
            # 仍在跑 REST(原 3s 会在假死客户端下截断在途请求)。
            self._probe_thread.wait(8000)
        except Exception:
            pass
        try:
            self._icon_pool.shutdown(wait=True)
        except Exception:
            pass
        # 详情/装备图标池: 不等(wait=False) —— 有意设计: 让 _quit() 立即关窗返回,
        # 在途外网请求由 CPython 的 threading._register_atexit 在解释器退出时 join,
        # 最终退出时间与 wait=True 等价, 但 UI 先关、不卡用户。见 P2-29。
        for pool in (getattr(self, '_detail_pool', None),
                     getattr(self, '_item_pool', None)):
            try:
                if pool is not None:
                    pool.shutdown(wait=False)
            except Exception:
                pass
        if self._tray:
            self._tray.hide()
        if self._overlay:
            self._overlay.close()
        if self._main:
            self._main.close()
        # v4.4.0(P2-10 配套): 关闭日志文件句柄并还原 sys.stdout/stderr。
        # 原先 _quit() 从不调用它 —— 进程退出时文件句柄靠操作系统回收, 而
        # sys.stdout/stderr 被 _Writer 换过之后也没还原。stop() 已幂等(内部判空),
        # 放在 close() 之后是因为窗口的 closeEvent 里可能还在打日志。
        try:
            log_capture.stop()
        except Exception:
            pass
        QApplication.quit()

    # ==================== 托盘 ====================
    @ui_slot
    def _on_tray(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_main()
        elif reason == QSystemTrayIcon.Trigger:
            if self._main and self._main.isVisible():
                self._main.hide()
            else:
                self._show_main()

    @ui_slot
    def _toggle_from_tray(self, checked):
        if checked:
            self._start()
        else:
            self._stop()

    def _show_main(self):
        if self._main:
            self._main.show()
            self._main.raise_()
            self._main.activateWindow()

    # ==================== 信号连接 ====================
    def _connect_signals(self):
        if not self._main or not self._overlay:
            return
        self._main.start_script_signal.connect(self._start)
        self._main.stop_script_signal.connect(self._stop)
        self._main.minimize_signal.connect(self._minimize)
        self._main.close_signal.connect(self._minimize_to_tray)
        self._main.settings_changed_signal.connect(self._on_settings)
        # v34: 设置页打开"自动选英雄" → 触发提权重启流程
        self._main.auto_select_champion_request_signal.connect(self._on_auto_select_champion_request)
        # v4.1.60: 须知确认后补查提权(升级场景 agreement 缺失)
        self._main.agreement_confirmed_signal.connect(self._maybe_elevate_on_startup)

        self._overlay.swap_signal.connect(self._on_swap)
        self._overlay.accept_signal.connect(self._on_accept)
        self._overlay.decline_signal.connect(self._on_decline)
        self._overlay.stop_signal.connect(self._stop)
        self._overlay.toggle_script_signal.connect(self._toggle_script)
        self._overlay.show_main_signal.connect(self._show_main)
        self._overlay.close_signal.connect(lambda: (self._stop(), self._show_main()))
        self._overlay.toggle_setting_signal.connect(self._on_overlay_toggle)

    @ui_slot
    def _minimize(self):
        if self._core.is_running:
            self._main.hide()
            # v4.1.65: 游戏中收起小窗(与"进游戏自动隐藏"语义一致), 否则会挂在画面上
            if self._core.current_phase == 'InProgress':
                self._overlay.safe_hide()
            else:
                self._overlay.safe_show()
            print('[窗口] 隐藏主窗口，显示小窗')
        else:
            if self._overlay and self._overlay.isVisible():
                self._overlay.hide()
            else:
                self._overlay.safe_show()

    @ui_slot
    def _on_settings(self, settings):
        self._core.update_settings(settings)
        if self._overlay:
            self._overlay.update_settings(settings)
        label = {
            'auto_queue': '自动匹配', 'auto_queue_delay': '匹配延迟',
            'auto_accept': '自动接受', 'auto_accept_delay': '接受延迟',
            'auto_play_again': '自动返回', 'auto_play_again_delay': '返回延迟',
            'auto_select_champion': '自动选英雄', 'auto_pick_champion': '自动换英雄',
            'preferred_champions': '偏好英雄',
        }
        for k, v in settings.items():
            if k in label and k not in ('preferred_champions',):
                print(f'[设置] {label[k]} = {v}')

    def _maybe_elevate_on_startup(self):
        """v4.1.59: 启动时兜底检查「自动选英雄」权限。

        用户痛点(2026-09-09): 首次提权成功后关闭软件, 再次双击启动是普通权限,
        settings 已持久化 auto_select=True → 点卡被 UIPI 拦截, 但此时既没点开关
        也没点启动脚本, 无人触发提权。故在启动后延迟检查一次: 若 auto_select 开着
        且非管理员, 直接弹提权引导(复用 _on_auto_select_champion_request)。
        首次启动(agreement_accepted=False)跳过 —— 须知弹窗优先, 且此时尚未开
        auto_select。
        """
        try:
            s = self._main.get_settings() if self._main else {}
            if not s.get('auto_select_champion'):
                return
            if not s.get('agreement_accepted'):
                return  # 首次启动: 须知优先, auto_select 尚未开启
            from lcu.clicker import is_admin
            if is_admin():
                return
            print('[启动] 自动选英雄已开启但当前非管理员, 启动时引导提权')
            self._on_auto_select_champion_request()
        except Exception as e:
            print(f'[启动] 启动时提权检查异常(忽略): {type(e).__name__}: {e}')

    def _on_auto_select_champion_request(self):
        """v35: 设置页开启「自动选英雄」→ 权限判定 + (必要时)提权重启。

        v34 实测教训: 用户已是管理员进程时旧 process_elevated() 误报非管理员
        → 走了 runas, 而 Windows 对已提权进程再 runas 不弹 UAC 直接静默重启,
        用户看到"点开关没弹窗自己就重启"。修复:
        1. 用 shell32 IsUserAnAdmin() 权威判定(不易误报)
        2. 判定非管理员才提权; 提权前弹中文提示框, 不无声重启

        v4.1.58: 改为返回 bool —— True=已是管理员或已触发提权重启(调用方应
        return 不再继续); False=用户取消 UAC(自动选英雄开关已回退关闭)。供
        _start 启动脚本时复用(settings 已持久化 auto_select=True 但双击普通
        启动的场景, 点开关信号不会触发, 必须在这里补查)。
        """
        # v4.1.64: 去重 —— 已发起过提权请求则不再重复弹框/runas
        if self._elevate_requested:
            print('[设置] 提权请求已发起过, 跳过重复触发')
            return True
        try:
            from lcu.clicker import is_admin
            elevated = is_admin()
        except Exception:
            elevated = False
        print(f'[设置] 自动选英雄请求: 当前进程管理员={elevated}')
        if elevated:
            print('[设置] 已是管理员权限, 自动选英雄直接生效(不重启)')
            try:
                if self._main:
                    self._main.append_log('[设置] 自动选英雄已开启(管理员权限, 直接生效)')
            except Exception:
                pass
            return True

        # 真非管理员 → 先停脚本避免双实例并发, 再弹提示, 再 runas
        if self._core and self._core.is_running:
            print('[设置] 提权前先停止当前脚本实例')
            try:
                self._stop()
            except Exception as e:
                print(f'[设置] 停止脚本异常: {type(e).__name__}: {e}')

        # 弹中文提示, 让用户知道接下来要做什么(不再无声重启)
        try:
            from PyQt5.QtWidgets import QMessageBox
            if self._main:
                box = QMessageBox(self._main)
                box.setWindowTitle('需要管理员权限')
                box.setText('「自动选英雄」需要以管理员身份运行才能模拟点击选卡。')
                box.setInformativeText(
                    '接下来会弹出 Windows 用户账户控制(UAC)窗口。\n'
                    '请点「是」允许 → 程序将以管理员权限自动重启。')
                box.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
                box.setDefaultButton(QMessageBox.Ok)
                ret = box.exec_()
                if ret != QMessageBox.Ok:
                    print('[设置] 用户取消了管理员授权, 开关保持关闭')
                    self._revert_auto_select_champion()
                    return False
        except Exception as e:
            print(f'[设置] 提示框异常: {type(e).__name__}: {e}')

        # 启动管理员实例
        try:
            import ctypes
            import os as _os
            shell32 = ctypes.WinDLL('shell32', use_last_error=True)
            shell32.ShellExecuteW.argtypes = [
                ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
            shell32.ShellExecuteW.restype = ctypes.c_size_t
            exe = sys.executable
            cwd = _os.path.dirname(exe) if getattr(sys, 'frozen', False) else _os.getcwd()
            r = shell32.ShellExecuteW(None, 'runas', exe, '', cwd, 1)
            print(f'[设置] ShellExecuteW(runas) 返回 {r}')
        except Exception as e:
            print(f'[设置] 提权异常: {type(e).__name__}: {e}')
            self._revert_auto_select_champion()
            return False

        if r <= 32:
            # 用户在 UAC 弹窗选了「否」, 撤销开关
            print(f'[设置] UAC 未授权(返回值 {r}), 自动选英雄回退为关闭')
            self._revert_auto_select_champion()
            return False

        # v4.1.64: 已成功请求提权, 置位去重标志(防止旧实例退出前被再次触发)
        self._elevate_requested = True
        print('[设置] 已请求管理员权限, 旧实例 1.5s 后退出, 由管理员实例接管')
        # 给新实例启动时间, 再退出旧进程
        QTimer.singleShot(1500, lambda: self._quit_for_admin_handoff())
        return True

    def _revert_auto_select_champion(self):
        # 让 UI 开关弹回 False, 同步 settings 并落盘
        try:
            # v4.4.0(P1): 原为 sync_setting() + 直接写 `self._main._settings[...]`
            # + 调私有 `_save_settings()` —— 三处都踩在 MainWindow 内部实现上
            # (而且 sync_setting 自己就会落盘, 那次 _save_settings 是多余的)。
            # 换成公开的 persist_setting()。
            self._main.persist_setting('auto_select_champion', False)
            print('[设置] 自动选英雄开关已恢复为关闭')
        except Exception as e:
            print(f'[设置] 撤销开关异常: {type(e).__name__}: {e}')

    def _quit_for_admin_handoff(self):
        # v4.4.0(P0-3): 原实现 `try: print(...) finally: os._exit(0)` 会绕过全部
        # 收尾 —— logger 的文件句柄不 flush/close(data/logs/app.log 丢掉最后几十行,
        # 恰恰是"为什么退出"这条最该留下的日志)、stdout/stderr 重定向不还原、
        # 探活 QThread 不停(wait 被跳过)、WS 不 disconnect、定时器不停。
        # 正确顺序: 先走完整 _quit() 收尾, 再 _exit(0) 兜底强杀(防止 Qt 事件循环
        # 残留让进程卡住, 那正是原来用 _exit 的原因 —— 这里两者都要)。
        try:
            print('[设置] 旧实例退出, 让位给管理员实例')
            self._quit()
        except Exception as e:
            print(f'[设置] 退出收尾异常(继续强退): {type(e).__name__}: {e}')
        finally:
            import os as _os
            # 强制落盘标准流(crash 场景下 logger 可能还没装上)
            try:
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception:
                pass
            _os._exit(0)

    # ==================== v4.3.8: 版本检查功能已整体移除 ====================
    # 用户明确表示"根本不需要检查更新的功能"。原先的 _check_update/_do_check_update/
    # _on_update_ready/_auto_check_update_once 与设置页「检查更新」卡片、UpdateDialog
    # 弹窗一并删除。core/updater.py 只保留 APP_VERSION(启动日志与界面版本号用)。

    @ui_slot
    def _on_overlay_toggle(self, key, value):
        settings = self._core.get_settings()
        settings[key] = value
        self._core.update_settings(settings)
        if self._main:
            # v4.4.0(P1): 原为 sync_setting() + 私有 `_save_settings()` 两连击
            # (sync_setting 自己就落盘)。改用公开的 persist_setting(key, value),
            # 一次调用完成"改内存 + 同步控件 + 落盘"。
            self._main.persist_setting(key, value)
        label = {
            'auto_queue': '自动匹配', 'auto_accept': '自动接受',
            'auto_play_again': '自动返回', 'auto_pick_champion': '自动换英雄',
        }
        print(f'[小窗] {label.get(key, key)} → {"开启" if value else "关闭"}')

    @ui_slot
    def _on_accept(self):
        # v4.4.2(P1-4): 同步 REST 挪后台线程, 结果经 _action_done_sig 排回主线程。
        threading.Thread(target=self._accept_worker, daemon=True,
                         name='app-accept').start()

    @ui_slot
    def _on_decline(self):
        # v4.4.2(P1-4): 同上, 拒绝走后台。
        threading.Thread(target=self._decline_worker, daemon=True,
                         name='app-decline').start()

    @ui_slot
    def _on_swap(self, cid):
        # v4.4.2(P1-4): 换英雄含 bench_swap 的 sleep+读回, 最坏 REST 超时级,
        # 必须移出主线程。
        threading.Thread(target=self._swap_worker, args=(cid,), daemon=True,
                         name='app-swap').start()

    def _accept_worker(self):
        """后台执行 accept_current_match(含 get_ready_check + accept 两次 REST)。"""
        try:
            ok = bool(self._core.accept_current_match())
        except Exception as e:
            print(f'[操作] 手动接受异常: {type(e).__name__}: {e}')
            ok = False
        self._action_done_sig.emit('accept', ok, 0)

    def _decline_worker(self):
        """后台执行 decline_current_match(含 get_ready_check + decline 两次 REST)。"""
        try:
            ok = bool(self._core.decline_current_match())
        except Exception as e:
            print(f'[操作] 手动拒绝异常: {type(e).__name__}: {e}')
            ok = False
        self._action_done_sig.emit('decline', ok, 0)

    def _swap_worker(self, cid):
        """后台执行 swap_champion(含 bench_swap 的 sleep(0.1) + 读回 GET)。"""
        try:
            ok = bool(self._core.swap_champion(cid))
        except Exception as e:
            print(f'[操作] 手动换英雄异常: {type(e).__name__}: {e}')
            ok = False
        self._action_done_sig.emit('swap', ok, cid)

    @ui_slot
    def _on_action_done(self, kind, ok, cid):
        """后台动作完成 → 主线程更新 UI(不碰任何阻塞调用)。"""
        if kind == 'accept':
            if self._overlay:
                if ok:
                    self._overlay.set_ready_response('Accepted')
                    self._overlay.update_status('✅ 已接受对局!')
                    print('[操作] 手动接受对局')
                else:
                    self._overlay.show_ready_check_buttons(False)
                    print('[操作] 接受失败 - 无进行中的匹配')
        elif kind == 'decline':
            if self._overlay:
                if ok:
                    self._overlay.set_ready_response('Declined')
                    self._overlay.update_status('❌ 已拒绝，匹配已关闭')
                    print('[操作] 手动拒绝对局')
                else:
                    self._overlay.show_ready_check_buttons(False)
                    print('[操作] 拒绝失败 - 无进行中的匹配')
        elif kind == 'swap':
            if ok and self._overlay:
                name = self._champ_data.get(cid, str(cid))
                self._overlay.update_current_champion(name)
                print(f'[操作] 手动换英雄 → {name}')

    # ==================== 运行 ====================
    def run_with_app(self, app, splash=None):
        """使用已创建的 QApplication 启动"""
        self._app = app
        icon = self._make_icon()
        self._app.setWindowIcon(icon)
        self._main = MainWindow()
        # v18:构造之间穿插 processEvents —— 让 splash 的三点动画在重活间隙真实转动,
        # 用户看到"正在加载"而非一整段卡死帧后才跳变主窗
        if splash:
            app.processEvents()
        self._overlay = OverlayWindow()
        # 同步主窗口设置到小窗
        self._overlay.update_settings(self._main.get_settings())
        self._connect_signals()
        self.initialize()
        self._main.show()
        # v4.1.59: 启动后延迟检查"自动选英雄"权限(持久化 auto_select=True 但双击
        # 普通启动的场景, 点卡会被 UIPI 拦截)。延迟 800ms 避开首帧渲染, 且须知弹窗
        # (首次启动)已在前面 singleShot 触发, 若 auto_select 开着会在此引导提权。
        QTimer.singleShot(800, self._maybe_elevate_on_startup)
        # v4.1.55: 首次启动强制阅读须知(30s)。splash 淡出后再弹，避免视觉叠加。
        if splash:
            QTimer.singleShot(600, self._main.show_first_launch_agreement)
        else:
            QTimer.singleShot(200, self._main.show_first_launch_agreement)
        if splash:
            # splash 最小展示 ~280ms(动画至少转过一轮)后开始 220ms 淡出
            shown_at = getattr(splash, '_shown_at', None)
            age = (_time.perf_counter() - shown_at) * 1000 if shown_at else 0.0
            QTimer.singleShot(max(0, int(280 - age)), splash.fade_finish)

        rc = self._app.exec_()
        sys.exit(rc)

class SplashWindow(QWidget):
    """启动闪屏：液态玻璃风格（渐变底 + 发光蛋标 + 三点加载动画）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(320, 200)
        self._dots = 0
        self._icon = None
        # 加载应用图标（蛋标）
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
            base2 = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
            base2 = base
        for b in [base, base2]:
            p = os.path.join(b, 'data', 'app_icon.png')
            if os.path.exists(p):
                self._icon = QPixmap(p)
                break
        if self._icon is None:
            self._icon = QPixmap(96, 96)
            self._icon.fill(Qt.transparent)
        # v18:预缩放缓存 —— paintEvent 每帧(400ms tick)不再重复做平滑缩放
        self._icon_drawn = self._icon.scaled(72, 72, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        self._dot_timer = QTimer(self)
        self._dot_timer.timeout.connect(self._tick)
        self._dot_timer.start(400)
        self._fade = None

    @ui_slot
    def _tick(self):
        self._dots = (self._dots + 1) % 4
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 圆角裁剪
        path = QPainterPath()
        path.addRoundedRect(0, 0, self.width(), self.height(), 18, 18)
        p.setClipPath(path)
        # 渐变底
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0.0, QColor('#1e1b4b'))
        g.setColorAt(0.5, QColor('#111827'))
        g.setColorAt(1.0, QColor('#0b0f1a'))
        p.fillRect(self.rect(), QBrush(g))
        # 顶部柔光
        g2 = QLinearGradient(0, 0, 0, self.height() * 0.4)
        g2.setColorAt(0.0, QColor(99, 102, 241, 40))
        g2.setColorAt(1.0, QColor(99, 102, 241, 0))
        p.fillRect(0, 0, self.width(), int(self.height() * 0.4), QBrush(g2))
        # 蛋标（带外发光）
        icon_size = 72
        ix = (self.width() - icon_size) // 2
        iy = 28
        glow = QColor(99, 102, 241)
        for i in range(3, 0, -1):
            alpha = 40 - i * 8
            if alpha <= 0:
                continue
            glow.setAlpha(alpha)
            p.setBrush(QBrush(glow))
            p.setPen(Qt.NoPen)
            p.drawEllipse(ix - i * 3, iy - i * 3, icon_size + i * 6, icon_size + i * 6)
        p.drawPixmap(ix, iy, self._icon_drawn)
        # 标题
        p.setPen(QColor('#f1f5f9'))
        f = QFont('Microsoft YaHei UI', 12, QFont.DemiBold)
        p.setFont(f)
        p.drawText(self.rect().adjusted(0, 112, 0, 0), Qt.AlignHCenter | Qt.AlignTop, '大蛋小助手')
        # 副标题 + 三点动画
        p.setPen(QColor(148, 163, 184))
        f2 = QFont('Microsoft YaHei UI', 9)
        p.setFont(f2)
        p.drawText(self.rect().adjusted(0, 142, 0, 0), Qt.AlignHCenter | Qt.AlignTop,
                   '加载中' + '.' * self._dots + ' ' * (3 - self._dots))
        p.end()

    @ui_slot
    def fade_finish(self):
        """主窗口显示后调用：淡出并关闭"""
        self._dot_timer.stop()
        if self._fade is None:
            self._fade = QPropertyAnimation(self, b'windowOpacity')
            self._fade.setDuration(220)
            self._fade.setStartValue(1.0)
            self._fade.setEndValue(0.0)
            self._fade.finished.connect(self._on_fade_done)
        self._fade.start()

    @ui_slot
    def _on_fade_done(self):
        """淡出动画完成 —— 蛋标真正从屏幕消失的时刻(用户开始看到主窗内容)"""
        self.close()


def _crash_dir():
    """崩溃日志目录:包外(exe 同级) data/logs —— 与 core.logger 落盘策略一致。

    _MEIPASS 临时目录退出即消失,写那里的日志用户永远看不到,所以 frozen 时
    只认 exe 同级。
    """
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, 'data', 'logs')


_UNCAUGHT_MAX_BYTES = 512 * 1024


def _append_uncaught(header, text):
    """把一条未捕获异常追加到 data/logs/uncaught.log。

    追加而非覆盖:原 main() 的 crash.log 用 'w' 打开,连续崩溃只会留下最后一次,
    而"上次崩在哪、这次又崩在哪"往往正是定位线索。写入失败一律降级为只打印
    (绝不能因为日志写不进去而把异常吞掉)。

    超过 _UNCAUGHT_MAX_BYTES 就轮转一次(留 .1 一代),与 core.logger 的 app.log
    策略一致:一个每轮轮询都抛异常的槽能让这里无限增长,没有上限的文件迟早
    变成用户硬盘上的垃圾。
    """
    try:
        d = _crash_dir()
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, 'uncaught.log')
        try:
            if os.path.getsize(p) >= _UNCAUGHT_MAX_BYTES:
                os.replace(p, p + '.1')
        except OSError:
            pass
        # v4.4.2(P1-10): 落盘前先脱敏 —— uncaught.log 直写原文会让令牌/用户名
        # 明文留盘(与 app.log 的脱敏口径不一致)。header 是标题行(不含敏感值),
        # 只有 text 需要过 redact_sensitive。
        with open(p, 'a', encoding='utf-8') as f:
            f.write(f'\n===== {_time.strftime("%Y-%m-%d %H:%M:%S")} {header} =====\n')
            f.write(redact_sensitive(text))
            if not text.endswith('\n'):
                f.write('\n')
    except Exception as e:
        # v4.4.2(P2-27): docstring 承诺"写入失败一律降级为只打印", 而原先这里是
        # 真正静默的 `except Exception: pass`。改为打印异常类型与消息。
        try:
            print(f'[崩溃] uncaught.log 写入失败: {type(e).__name__}: {e}')
        except Exception:
            pass


def _install_excepthooks():
    """v4.4.0(P1-1): 安装全局异常兜底钩子。

    为什么必须有:
      1) **主线程**:PyQt5 默认把槽内未捕获异常交给 qFatal(),一个脏数据引发的
         ValueError 会**直接 abort 进程**,连托盘/日志都来不及收尾 —— 用户看到
         的就是"程序莫名其妙消失"。ui/ 侧用 ui_slot 装饰器包了**一部分**槽(局部
         加固 + 把"这里不该外泄"写在定义处),这里是**覆盖全部槽的那一道**,也是
         真正保命的那一道:任何槽漏包,都会走到这里落盘 + 打印,而不是静默暴毙。
         注:ui_slot 装饰器内部也会把异常转交给本 hook(见 window_base._report_slot_exception),
         所以两道防线信息只增不减,不会互相屏蔽。
      2) **子线程**:daemon 线程里未捕获异常默认只打到 stderr(日志文件里什么都
         没有)。本程序大量使用后台线程(连接发现/英雄刷新/详情拉取),线程静默
         死亡会让功能"用着用着就不动了"且无从查起。threading.excepthook 把它们
         全部记进 uncaught.log。

    ⚠️ 这里**不重抛**(不调用原 hook):目标是"这次刷新失败但程序继续可用"。
    """
    def _hook(exc_type, exc, tb):
        try:
            import traceback as _tb
            text = ''.join(_tb.format_exception(exc_type, exc, tb))
        except Exception:
            text = f'{exc_type}: {exc}'
        try:
            sys.stderr.write(text)
        except Exception:
            pass
        _append_uncaught('[未捕获异常/主线程]', text)
        try:
            print(f'[崩溃] 未捕获异常已拦下(日志见 data/logs/uncaught.log): '
                  f'{exc_type.__name__}: {exc}')
        except Exception:
            pass

    def _thread_hook(args):
        try:
            import traceback as _tb
            text = ''.join(_tb.format_exception(args.exc_type, args.exc_value,
                                               args.exc_traceback))
            where = getattr(getattr(args, 'thread', None), 'name', '?')
        except Exception:
            text = f'{args.exc_type}: {args.exc_value}'
            where = '?'
        _append_uncaught(f'[未捕获异常/线程 {where}]', text)
        try:
            print(f'[崩溃] 线程 {where} 未捕获异常已记录'
                  f'(日志见 data/logs/uncaught.log): '
                  f'{args.exc_type.__name__}: {args.exc_value}')
        except Exception:
            pass

    sys.excepthook = _hook
    try:
        threading.excepthook = _thread_hook   # Python 3.8+
    except Exception:
        pass


def main():
    # 高DPI环境变量（必须在任何Qt调用之前）
    os.environ['QT_ENABLE_HIGHDPI_SCALING'] = '1'
    os.environ['QT_SCALE_FACTOR_ROUNDING_POLICY'] = 'PassThrough'
    # v4.4.0(P1-1): 必须在 QApplication 之前装 —— 槽异常从第一个事件循环 tick
    # 起就可能发生,晚一步装就等于放过启动阶段那批最可疑的异常。
    _install_excepthooks()
    try:
        # QObject 必须在 QApplication 之后才能创建
        # 所以 QApplication 在 run() 中创建，然后才能创建 AppController
        QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        _app = QApplication(sys.argv)
        _app.setStyle('Fusion')
        _app.setApplicationName('大蛋小助手')
        _app.setQuitOnLastWindowClosed(False)
        font = QFont('Microsoft YaHei UI', 10)
        font.setStyleStrategy(QFont.PreferAntialias)
        font.setHintingPreference(QFont.PreferFullHinting)
        _app.setFont(font)

        # 启动闪屏（液态玻璃风格：渐变+发光蛋标+三点加载动画）
        splash = SplashWindow()
        splash.show()
        # 记录闪屏真正上屏的时刻, 供 run_with_app 计算最小展示时长(280ms 后淡出)
        splash._shown_at = _time.perf_counter()
        _app.processEvents()

        ctrl = AppController()
        ctrl.run_with_app(_app, splash)
    except Exception as e:
        import traceback
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
        log_path = os.path.join(base, 'crash.log')
        # v4.4.2(P1-10/P2-27): ① 直写原文会让令牌明文落盘, 先脱敏; ② 改用 'a'
        # 追加(与 _append_uncaught docstring 一致, 'w' 只留最后一次会丢失线索)。
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(redact_sensitive(''.join(
                    traceback.format_exception(*sys.exc_info()))))
        except Exception:
            pass
        raise


if __name__ == '__main__':
    main()
