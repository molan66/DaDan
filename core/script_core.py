"""Script Core - 脚本核心逻辑

本模块的技术栈与上游完全不同：Python 实现，直连 LCU REST / WebSocket，不涉及任何
DOM 注入或 CEF 环境。但**本模块是基于 sona 项目改写而来的**（见下），并非独立实现。

上游项目：sona —— https://github.com/WJZ-P/sona （AGPL-3.0）
本模块对其进行了移植与改写，修改日期 2026-09-29。

原样保留的上游对应关系（凡下方注释标注「sona」处，即表示该处逻辑/常量来自上游）：
- auto-accept: 每次 ReadyCheck 只接受一次 + 用户拒绝后暂停（双重保护）
  ← 对应上游 src/lib/features/auto-accept.ts
- auto-return: playAgain + retry matchmaking
  ← 对应上游 src/lib/features/auto-return-to-lobby.ts
- bench-swap: 直接调用 API，无冷却限制
  ← 对应上游 src/lib/features/bench-no-cooldown.ts
- auto-honor: 票数分配、随机打散、随机荣誉类别
  ← 对应上游 src/lib/features/auto-honor.ts

本项目的自有部分（上游没有的）：从 lockfile 发现客户端并直连、点卡屏幕兜底、
账号卡与战绩页、毛玻璃界面、单飞/去重锁与重试策略等。

完整的逐项对照与差异说明见仓库根目录 NOTICE.md 与 docs/PROVENANCE.md。
"""

import time
import threading
import random
from typing import Optional, Dict, Callable

# v4.4.0 修复(P2-12): 时序/节流魔法数字集中到 core/constants.py。
# 原因: 原先 "3"(选人节流)、"8"(日志去重)、"15"(接受上限/返回重试) 等数字直接写在
# 比较式与 sleep 里, 语义不同的数字长得一样, 改动时极易串。
from . import constants as C


# v41: 点卡坐标按 ARAM"2-3 张卡"布局标定, 仅在这些队列启用屏幕点卡兜底
# v4.1.57 修: 海克斯大乱斗真实 queueId=2400(2026-09-09 用户真实测试日志证实),
# 原注释"3270=海克斯"是错的。REST 试偏好不限队列。
# 另外大乱斗类模式有 session 特征 allowSubsetChampionPicks=True / benchEnabled,
# 故 _try_initial_pick 额外用特征兜底, 不再只依赖 queueId 白名单(见下)。
ARAM_QUEUE_IDS = {450, 3270, 2400}


def _safe_int(v, default: int, lo: Optional[int] = None,
              hi: Optional[int] = None) -> int:
    """P0-3: 容错地把设置值转成 int。

    用户手改 settings.json 或别的工具写坏时, 三个延时键可能是字符串/None/浮点,
    直接 int() 会抛 ValueError, 被 _set_phase 的兜底吞掉后该阶段动作永久静默失效
    (界面开关仍显示"已开启")。这里失败回退 default, 并夹到 [lo, hi]。
    """
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    if lo is not None and n < lo:
        n = lo
    if hi is not None and n > hi:
        n = hi
    return n


class ScriptCore:
    def __init__(self, api, ws):
        self._api = api
        self._ws = ws
        self._running = False
        self._thread: Optional[threading.Thread] = None

        # 状态
        self._phase = 'None'
        self._last_handled_phase = 'None'  # 上次处理的阶段，防止重复触发
        self._connected = False
        self._summoner = None
        self._champ_data: Dict[int, str] = {}

        # sona 模式的自动接受保护标记
        self._has_accepted_this_check = False  # 本次 ReadyCheck 是否已自动接受
        self._user_declined_this_session = False  # 本次会话中用户是否主动拒绝过

        # 功能开关
        self._settings = {
            'auto_queue': True,
            'auto_queue_delay': 2,
            'auto_accept': True,
            'auto_accept_delay': 1,
            'auto_play_again': True,
            'auto_play_again_delay': 2,
            'auto_pick_champion': True,
            'auto_select_champion': False,  # v41: 与 auto_pick_champion 对称, 进选人自动选英雄(默认关)
            'preferred_champions': [],
            # v31 点卡兜底: 海克斯(3270)"2-3 张卡"模式专用, REST 试错全空时
            # 改用鼠标点卡 → 服务器只接受渲染出来的卡 → 锁定后 bench 出现剩
            # 余卡, 配合自动换回收偏好。比例来自 2026-09-09 1920x1080 截图
            # (客户端约 930x435 窗口化, 左侧卡中心 = 窗口 (0.161, 0.609))。
            'auto_pick_click_fallback': True,
            # v33: x 用候选序列横向扫描, 自适应"2 张或 3 张卡"布局(卡片组
            # 左对齐或居中都能覆盖; 卡片宽约 >20% 窗口, 步长 0.15 保证至少
            # 一个候选点落在某张卡上)。每点后 REST 读回, 一旦 championId
            # 写入(预选或锁定)即停。0.161 是取证那局(2 张卡)第一张卡中心。
            'auto_pick_click_fracX': 0.161,
            'auto_pick_click_xlist': [0.12, 0.27, 0.42, 0.57, 0.72, 0.87],
            'auto_pick_click_fracY': 0.609,
            'auto_honor': True,
            # v41: 移除死配置 'queue_id'(450)——全库零读取, 真实队列从
            # 选人 session 的 queueId 字段获取(见 _handle_champ_select)。
        }

        # 回调
        self._on_phase_changed: Optional[Callable] = None
        self._on_connection_changed: Optional[Callable] = None
        self._on_bench_champions: Optional[Callable] = None
        self._on_champion_data_ready: Optional[Callable] = None
        self._on_status_update: Optional[Callable] = None
        self._on_ready_check_state: Optional[Callable] = None
        self._on_champ_select_info: Optional[Callable] = None  # 选人倒计时/状态回调

        # 冷却
        self._last_swap = 0
        self._champ_dumped = False  # v27: 本局是否已 dump 一次选人 session 结构(诊断)
        # v28:自动选节流与诊断状态
        self._last_pick_t = 0.0        # 上次 pick 成功的时刻(3s 节流)
        self._last_pick_delta = None   # 上次"未锁状态"快照(变化才打日志)
        # v30:自动选"试错法"节流(已证实 pickable 端点返回账号全量英雄, 弃用)
        self._last_noaccept_t = 0.0   # 上次"试锁未成功"提示时刻(8s 去重)
        self._last_no_pref_pool = None  # v4.1.63: 上次"暂不换"打印的备选池快照(池变化才打)
        self._last_no_swap_key = None  # v4.3.14: 上次"当前已是更高优先级"快照(变化才打)

        # v4.4.0 修复(P1-7): 倒计时取消 ID 由"一个全局计数器"拆成"每功能一个"。
        # 原先三个倒计时(自动匹配/自动接受/自动返回)共用一个 _countdown_id, 而
        # 递增它的地方有五处(启动倒计时 ×3 + _ws_ready_check 拒绝 + 手动接受/拒绝
        # + _clear_ready_state)。后果: 任何一处把 ID 抬高, **所有**在途倒计时都会被
        # 判定为"已过期"而中止 —— 例如"等待结算返回房间"的倒计时会被离开 ReadyCheck
        # 时的清理顺手打断(v4.3.11 的 _clear_return_pending 就是在补这个洞)。
        # 现在按功能各持一个计数器, 每个功能只取消自己那一个; 跨功能互不干扰。
        # 兼容: 保留 _countdown_id 属性(外部/旧代码若读过它不会 AttributeError),
        # 但代码内已不再读写它。
        self._countdown_id = 0  # 已弃用(v4.4.0 P1-7): 仅为兼容保留, 不再参与判断
        self._countdown_id_queue = 0   # 自动匹配倒计时
        self._countdown_id_accept = 0  # 自动接受倒计时(含撤销在途接受)
        self._countdown_id_return = 0  # 自动返回房间倒计时
        self._swap_lock = threading.Lock()  # 换英雄线程锁
        # v4.3.10:自动接受"检查+置位"的专用锁,防止 poll 与 WS 双通道叠加倒计时
        self._accept_lock = threading.Lock()
        self._phase_lock = threading.Lock()  # 阶段变更锁（poll 与 WS 线程串行处理，防止竞态）
        # v4.4.2(P1-1): _handle_phase 的 check-then-set 专用锁。原先 _last_handled_phase
        # 在锁外读写, poll 线程与 WS 线程可同时通过检查、重复进入同一 phase
        # (重复开匹配/重复起倒计时)。此锁把"检查 + 置位"收成原子操作。
        self._handle_lock = threading.Lock()
        self._poll_fail_count = 0  # 连续轮询失败次数，达到阈值才断开
        # v23:自动接受互斥 —— 轮询与 WS 双通道都可能触发 _handle_ready_check,
        # 无此标记会叠加多个延迟接受倒计时(重复接受);接受失败时复位允许下轮重试
        self._accept_pending = False
        # v23:自动点赞并发锁 —— 中途启动/状态对账可能在多个结束阶段补点赞,
        # 用非阻塞锁保证同时只有一个点赞线程在跑
        self._honor_lock = threading.Lock()
        # G6:用户主动拒绝后暂停"自动匹配"，直到手动点启动/手动在客户端开始匹配才恢复，
        # 避免"拒绝→回大厅→自动重排→自动接受"的循环违背用户意愿。
        self._auto_queue_paused = False
        # v4.4.0 新增(P2-7): 这次"匹配中"是不是**我们**发起的。
        # _cleanup(停止脚本/退出程序时执行)原先无条件 stop_matchmaking(), 会把用户
        # 自己在客户端点出来的队列一起取消 —— 用户只是想关掉脚本, 队列却被停了。
        # 现在只有自己发起过才在收尾时取消。
        self._queue_started_by_us = False
        # v4.3.11:本局「自动返回房间」流程是否已启动。EndOfGame 与 WaitingForStats
        # 是两个独立 phase, 只靠 3s 节流会启动两次返回流程; 该标记保证一局只
        # 启动一次, 回到 Lobby 时重置(见 _apply_phase_side_effects)。
        self._return_pending = False

    # ==================== 属性 ====================
    @property
    def is_running(self):
        return self._running

    @property
    def current_phase(self):
        return self._phase

    @property
    def is_connected(self):
        return self._connected

    @property
    def summoner_name(self):
        if not self._summoner:
            return ''
        disp = (self._summoner.get('displayName')
                or self._summoner.get('internalName') or '').strip()
        if disp:
            return disp
        # v41: 腾讯 LCU 上 displayName/internalName 恒空, 真名在 gameName#tagLine
        gn = (self._summoner.get('gameName') or '').strip()
        tl = (self._summoner.get('tagLine') or '').strip()
        return f'{gn}#{tl}' if (gn and tl) else (gn or '召唤师')

    # ==================== 回调设置 ====================
    def set_on_phase_changed(self, cb): self._on_phase_changed = cb
    def set_on_connection_changed(self, cb): self._on_connection_changed = cb
    def set_on_bench_champions(self, cb): self._on_bench_champions = cb
    def set_on_champion_data_ready(self, cb): self._on_champion_data_ready = cb
    def set_on_status_update(self, cb): self._on_status_update = cb
    def set_on_ready_check_state(self, cb): self._on_ready_check_state = cb
    def set_on_champ_select_info(self, cb): self._on_champ_select_info = cb

    def update_settings(self, s): self._settings.update(s)
    def get_settings(self): return self._settings.copy()

    # ==================== WS 事件注册 ====================
    def setup_ws_handlers(self):
        self._ws.on('/lol-gameflow/v1/gameflow-phase', self._ws_phase)
        self._ws.on('/lol-champ-select/v1/session', self._ws_champ_select)
        # v41: 非 legacy 模式(新 lobby)走 team-builder 端点, 与 bench_swap 同源
        self._ws.on('/lol-lobby-team-builder/champ-select/v1/session',
                    self._ws_champ_select)
        self._ws.on('/lol-matchmaking/v1/ready-check', self._ws_ready_check)

    # ==================== 启动/停止 ====================
    def start(self):
        if self._running:
            return
        # v4.3.10 修复: 旧线程还没退干净就再 start, 会并存两个 _loop 线程 ——
        # 两个线程同时轮询/处理阶段(可能重复接受、重复开匹配), 且旧线程退出前
        # 还会跑 _cleanup() 把 _connected 置 False 并 stop_matchmaking。
        # 原实现 stop() 只置标志不 join, start() 也只判 _running, 挡不住这种竞态。
        # 这里先等旧线程收工(最多 3s), 再起新线程。
        old = self._thread
        if old is not None and old.is_alive():
            print('[Core] 检测到上一轮线程仍在退出中, 等待其收工…')
            old.join(timeout=3.0)
            if old.is_alive():
                # 还没退出(多半卡在某个 REST 超时里) → 拒绝启动, 避免双线程并存
                print('[Core] 上一轮线程仍未退出, 本次启动取消(请稍后重试)')
                self._emit(self._on_status_update, '⚠️ 上一轮仍在退出，请稍后重试')
                return
        self._running = True
        self._has_accepted_this_check = False
        self._user_declined_this_session = False
        self._auto_queue_paused = False  # 手动点启动即解除拒绝暂停
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._emit(self._on_status_update, '脚本已启动')

    def stop(self):
        self._running = False
        self._emit(self._on_status_update, '脚本已停止')

    # ==================== 主循环 ====================
    def _loop(self):
        self._init_session()
        self._sync_phase()

        while self._running:
            try:
                if not self._connected:
                    self._try_connect()
                    time.sleep(C.RECONNECT_INTERVAL_S)  # v4.4.0(P2-12): 见 constants
                    continue
                self._poll()
            except Exception as e:
                print(f'[Core] 轮询异常，继续运行: {type(e).__name__}: {e}')
                time.sleep(C.POLL_ERROR_SLEEP_S)
            time.sleep(C.POLL_INTERVAL_S)
        self._cleanup()

    def _init_session(self):
        try:
            self._summoner = self._api.get_current_summoner()
            self._connected = True
            self._poll_fail_count = 0
            self._emit(self._on_connection_changed, True, self.summoner_name)
            self._load_champ_data()
            print(f'[Core] 已连接: {self.summoner_name}')
            # v23:连接后立即对账当前客户端状态(取 phase 并执行对应动作),
            # 而不是只等"状态切换"事件 —— 中途任意状态启动脚本也能接管
            self._reconcile_state()
        except Exception as e:
            print(f'[Core] 初始化失败，等待重连: {type(e).__name__}: {e}')
            self._connected = False

    def _load_champ_data(self):
        # v4.4.0(P1-25): 英雄表构建规则与 main.py 的后台刷新共用 lcu/parsing.py
        # 一份实现(原先两处各写一遍 `0 < cid <= 1000` + 名字兜底)。
        # 函数内延迟导入: 与 :748 的 lcu.clicker 同策略, 避免 core/ 在 import
        # 阶段就拉起 lcu 包(连带 requests/websocket-client)。
        from lcu.parsing import is_real_champion, champion_display_name
        try:
            champs = self._api.get_champion_data()
            for c in champs:
                cid = c.get('id')
                if is_real_champion(cid):  # v17: 过滤 60xxx 重做前幽灵英雄
                    self._champ_data[cid] = champion_display_name(c)
            self._emit(self._on_champion_data_ready, self._champ_data)
            print(f'[Core] 加载 {len(self._champ_data)} 个英雄')
            # G9:不再在此 spawn 预下载图标——AppController 侧已有 8 线程下载池 +
            # 磁盘缓存体系(去重/限容/落盘),双体系并发会重复下载同一图标;
            # 需要的图标由 main._dl_icon 按需统一调度。
        except Exception as e:
            print(f'[Core] 加载英雄数据失败: {type(e).__name__}: {e}')

    def _try_connect(self):
        try:
            self._summoner = self._api.get_current_summoner()
            self._connected = True
            self._poll_fail_count = 0  # 连接成功，重置失败计数
            self._emit(self._on_connection_changed, True, self.summoner_name)
            # v23:重连成功同样对账一次(客户端可能已处于任意状态,如已开房间/已在选人)
            self._reconcile_state()
        except Exception:
            self._connected = False

    def _sync_phase(self):
        """连接后首次取阶段（_loop 启动时调一次）。

        v4.4.0 修复(P1-8): 原先本方法直接 `self._phase = p` 并自行 emit,
        绕过了 _set_phase —— 于是**阶段迁移副作用全部被跳过**: 比如启动瞬间
        客户端正处在 ChampSelect, `_has_accepted_this_check` 不会被重置;
        从 ReadyCheck 直接跳到结算时 `_return_pending` 不会被复位; 进入
        Matchmaking 时 `_auto_queue_paused` 不会解除。这些标志卡住会让对应
        功能整局失效, 且因为只发生在"连接那一次", 事后极难复现定位。
        现在统一走 _set_phase(唯一的阶段迁移入口), 副作用/emit/幂等判断
        全部与轮询路径一致。
        allow_handle=False: 保持改动前行为 —— 本方法只同步阶段, 不在这里执行
        阶段动作; 紧接着 _loop 会调 _reconcile_state() 强制执行一次当前阶段动作。
        副作用不会重复: _set_phase 内部有 `new_phase != old` 守卫, 阶段未变时
        只做 Lobby/None 的幂等兜底, 不重复迁移。
        """
        try:
            p = self._api.get_gameflow_phase()
            self._set_phase(p, allow_handle=False)
        except Exception:
            pass

    def _reconcile_state(self):
        """v23 核心:连接/启动后对账客户端当前游戏流程状态。

        原架构的"阶段动作"只在**状态切换**瞬间执行(_set_phase → _handle_phase),
        若脚本在客户端已处于某状态时才启动(如已经创建房间/已进匹配/已在选人/
        已在结算),没有切换事件 → 对应功能永不触发。此处启动即取当前 phase,
        并强制执行一次该阶段的动作(已房间→倒计时自动匹配、已结算→补点赞/自动
        返回等),实现"从任意中间步骤启动脚本都能精准识别当前状态并接管"。
        之后由 poll(1s)维持;同阶段重复执行由 _last_handled_phase 去重防止
        无限倒计时。
        """
        if not self._running:
            return
        try:
            p = self._api.get_gameflow_phase()
        except Exception:
            return
        if not p:
            return
        with self._phase_lock:
            old = self._phase
            changed = p != old
            if changed:
                self._phase = p
                # 迁移副作用与 _set_phase 保持一致
                self._apply_phase_side_effects(old, p)
        if changed:
            self._emit(self._on_phase_changed, p)
        # 强制对当前阶段执行一次动作(重置去重标记;不重复 emit)
        self._last_handled_phase = None
        try:
            self._handle_phase()
        except Exception as e:
            print(f'[Core] 状态对账处理失败: {type(e).__name__}: {e}')

    def _set_phase(self, new_phase, allow_handle=True):
        """统一阶段更新入口：加锁让 poll 线程与 WS 线程串行处理，避免竞态导致重复/漏处理。

        G3:锁内只做状态迁移(毫秒级),锁外才 emit + 执行阶段动作(REST IO),
        避免持 _phase_lock 做网络请求时把 poll/WS 线程全部排队卡住。
        """
        changed = False
        handle_it = False
        with self._phase_lock:
            old = self._phase
            if new_phase and new_phase != old:
                self._phase = new_phase
                self._apply_phase_side_effects(old, new_phase)
                changed = True
                handle_it = allow_handle
            # 阶段未变化：幂等兜底（回到大厅/空闲时解除拒绝暂停）
            elif old in ('Lobby', 'None'):
                self._user_declined_this_session = False
        if changed:
            self._emit(self._on_phase_changed, new_phase)
            if handle_it:
                try:
                    self._handle_phase()
                except Exception as e:
                    print(f'[Core] 阶段处理失败: {type(e).__name__}: {e}')
        return changed

    def _apply_phase_side_effects(self, old, new):
        """阶段迁移时的纯状态副作用(无 REST IO, 只改标志位)。

        v4.3.11: 从 _set_phase / _reconcile_state 抽取, 消除两处重复的迁移逻辑;
        新增「回到 Lobby 重置本局返回标记」—— _return_pending 只用于防止同一局
        结算(EndOfGame / WaitingForStats 两个 phase)重复启动返回流程,
        结算结束回到 Lobby 即失效, 让下一局结算能重新触发。
        """
        # 离开 ChampSelect 时重置单次接受标记
        if old == 'ChampSelect' and new != 'ChampSelect':
            self._has_accepted_this_check = False
        # 回到 Lobby/None = 新匹配会话，解除"主动拒绝"暂停
        if new in ('Lobby', 'None'):
            self._user_declined_this_session = False
            self._return_pending = False
        # 进入 Matchmaking = 用户(手动)开始了匹配,解除自动匹配暂停
        if new == 'Matchmaking':
            self._auto_queue_paused = False

    def _poll(self):
        try:
            p = self._api.get_gameflow_phase()
            # 统一阶段入口（内部加锁串行处理）
            self._set_phase(p)
            self._poll_fail_count = 0
            # ChampSelect 阶段：每次轮询同步选人状态（备选池/倒计时/自动换英雄）
            # 不依赖 WS 推送（WS 可能丢失或延迟），轮询兜底确保自动换英雄生效
            if self._phase == 'ChampSelect':
                try:
                    self._handle_champ_select()
                except Exception as e:
                    print(f'[Core] 选人轮询异常: {type(e).__name__}: {e}')
            # G1:ReadyCheck 阶段同样用轮询兜底——自动接受不再 100% 依赖 WS 推送
            elif self._phase == 'ReadyCheck':
                try:
                    self._handle_ready_check()
                except Exception as e:
                    print(f'[Core] 确认对局轮询异常: {type(e).__name__}: {e}')
        except Exception as e:
            self._poll_fail_count += 1
            if self._connected and self._poll_fail_count >= 3:
                self._connected = False
                self._emit(self._on_connection_changed, False, '')

    def _handle_phase(self):
        p = self._phase
        # v4.4.2(P1-1): 防止重复触发同一阶段 —— 把"检查 + 置位"收进专用锁,
        # 与 poll 线程(1s 轮询)和 WS 线程的并发进入互斥(原先锁外 check-then-set)。
        with self._handle_lock:
            if p == self._last_handled_phase:
                return
            self._last_handled_phase = p
        # 离开 ReadyCheck 时清理定时器和重置标记
        if p != 'ReadyCheck':
            self._clear_ready_state()
        if p == 'Lobby':
            self._handle_lobby()
        elif p == 'ReadyCheck':
            self._handle_ready_check()
        elif p == 'ChampSelect':
            self._handle_champ_select()
        elif p in ('EndOfGame', 'WaitingForStats'):
            # v23:中途在结算界面启动/重连时先补一次自动点赞,再执行自动返回
            self._try_start_honor()
            self._handle_end_of_game()
        elif p == 'PreEndOfGame':
            # sona 模式：点赞界面自动点赞(并发安全入口)
            self._try_start_honor()
            self._emit(self._on_status_update, '⏳ 等待对局结算...')

    # ==================== 自动匹配（房主模式） ====================
    def _handle_lobby(self):
        if not self._settings.get('auto_queue'):
            print('[Core] 自动匹配已关闭')
            return
        # G6:用户刚拒绝过对局时,不自动重排(需手动点启动或手动在客户端开始匹配才恢复)
        if self._auto_queue_paused:
            print('[Core] 自动匹配已暂停(刚拒绝过对局)，手动点启动或客户端开始匹配后恢复')
            return
        try:
            s = self._api.get_matchmaking_search_state()
            if s and s.get('searchState') == 'Searching':
                print('[Core] 已在匹配中，跳过')
                return
        except Exception as e:
            print(f'[Core] 查询匹配状态失败: {type(e).__name__}: {e}')
            # 查询失败不阻止匹配，继续执行

        delay = _safe_int(self._settings.get('auto_queue_delay', 2), 2, lo=0)
        print(f'[Core] 进入房间，{delay}秒后开始匹配')
        if delay > 0:
            # v4.4.0(P1-7): kind='queue' —— 该倒计时独立计数, 不再被离开
            # ReadyCheck 的清理或其他倒计时抢取消
            self._start_countdown(delay, '⏳ 匹配倒计时', self._do_queue, kind='queue')
        else:
            self._do_queue()

    def _do_queue(self):
        if self._phase not in ('Lobby', 'None'):
            return
        try:
            self._api.start_matchmaking()
            # v4.4.0(P2-7): 记下"这个队列是我们发起的", 供 _cleanup 判断是否该收尾
            self._queue_started_by_us = True
            self._emit(self._on_status_update, '🔄 正在匹配...')
            print('[Core] 开始匹配')
        except Exception as e:
            err = str(e)
            if 'INVALID_PERMISSIONS' in err:
                print('[Core] 非房主，等待房主匹配')
                self._emit(self._on_status_update, '⏳ 等待房主匹配...')
            else:
                print('[Core] 匹配失败')

    # ==================== 自动接受（sona 模式） ====================
    def _handle_ready_check(self):
        """sona 模式：每次 ReadyCheck 只接受一次，用户拒绝后暂停直到回到大厅

        v23:增加 _accept_pending 互斥 —— 轮询(1s)与 WS 事件都可能进入本方法,
        若无互斥会叠加多个延迟接受倒计时;原实现在接受前就置 _has_accepted
        导致首次接受请求失败后永不重试,现改为成功才置位、失败复位等下一轮重试。
        """
        try:
            if self._user_declined_this_session:
                return

            check = self._api.get_ready_check()
            if not check:
                return
            state = check.get('state', '')

            if self._on_ready_check_state:
                self._on_ready_check_state(state == 'InProgress')

            if state != 'InProgress' or not self._settings.get('auto_accept'):
                return

            # sona 核心：本次 ReadyCheck 已接受过就不再接受
            if self._has_accepted_this_check:
                return
            # v23:已有一个延迟接受在途(倒计时未触发)时不再叠加。
            # v4.3.10 修复: 原实现"先查后置"无锁 —— poll(1s) 与 WS 推送两个通道
            # 可能同时通过检查, 叠加出两个延迟接受倒计时(重复接受)。
            # WS 事件在本版修好解析后**真的会推送了**, 这条竞态从"理论"变成"常见",
            # 所以用专用锁把"检查 + 置位"做成原子的。
            # (不复用 _swap_lock: 那个锁管选人/换英雄, 语义不同, 混用会造成假耦合。)
            with self._accept_lock:
                if self._accept_pending:
                    return
                self._accept_pending = True

            base = _safe_int(self._settings.get('auto_accept_delay', 1), 1, lo=0)
            # sona 模式：最大延迟保护（15秒上限）
            base = min(base, C.ACCEPT_DELAY_MAX_S)  # v4.4.0(P2-12): 上限见 constants
            delay = self._random_delay(base)

            def do_accept():
                try:
                    c = self._api.get_ready_check()
                    if c and c.get('state') == 'InProgress':
                        self._api.accept_match()
                        self._has_accepted_this_check = True
                        self._emit(self._on_status_update, '✅ 已接受对局!')
                        print(f'[Core] 延迟{delay}秒后接受对局')
                    # 状态已离开 InProgress(如他人接受/超时),放弃本次
                except Exception as e:
                    # v23:接受失败复位标记 → 下一轮 poll(1s)自动重试,
                    # 不再"一次失败本局就永远不接"
                    self._has_accepted_this_check = False
                    print(f'[Core] 接受对局失败(下轮重试): {type(e).__name__}: {e}')
                finally:
                    with self._accept_lock:
                        self._accept_pending = False

            if delay > 0:
                self._start_countdown(delay, '⏳ 接受倒计时', do_accept,
                                      on_cancel=self._clear_accept_pending,
                                      kind='accept')  # v4.4.0(P1-7): 独立计数器
            else:
                do_accept()
        except Exception as e:
            # v4.4.0 修复(P2-6): 原先只打一句"确认对局处理失败", 既没有异常类型也没有
            # 消息。windowed 打包(console=False)下日志文件是唯一现场, 不打类型/消息
            # 等于出事时完全无线索(本方法的异常会让本局自动接受整体失效)。
            print(f'[Core] 确认对局处理失败: {type(e).__name__}: {e}')

    # ==================== 英雄选择（偏好优先 + 随机兜底） ====================
    def _handle_champ_select(self):
        """选人阶段 1s 轮询（WS 推送的双通道兜底）。

        阶段判据 = "我的 pick 动作是否已 complete(locked)"：
          - 自动选英雄(未锁定)：试错法 —— 偏好逐个交 pick_champion 试锁,
            服务器只接受"本局允许子集(屏幕 2-3 张卡)"内的英雄, 其余 204 静默忽略,
            哪个被接受就锁哪个 (v30)
          - 自动换英雄(已锁定)：备选池 = benchChampions(交换池秒换)
        """
        try:
            s = self._api.get_champ_select_session()
            if not s:
                return
            my_id, bench_ids = self._extract_session_state(s)
            self._dump_champ_select_once(s)   # 诊断：本局首次进入选人 dump 结构
            if self._on_bench_champions:
                self._on_bench_champions(bench_ids, my_id)
            locked = self._emit_champ_select_info(s, my_id)
            if not locked:
                self._log_pick_delta(s)     # 诊断:未锁期间状态变化才打
            self._auto_select_pass(bench_ids, my_id, locked, s.get('queueId'),
                                   s.get('allowSubsetChampionPicks'))
        except Exception as e:
            print(f'[Core] 选人处理失败: {type(e).__name__}: {e}')

    def _extract_session_state(self, s):
        """从选人 session 提取 (我的当前英雄, 备选池英雄id列表)。
        兼容新旧字段名：benchChampions(旧) / bench(新)，元素 dict 或裸 int。"""
        my_id = None
        local_cell = s.get('localPlayerCellId')
        for p in s.get('myTeam', []):
            if p.get('cellId') == local_cell:
                my_id = p.get('championId')
                break
        bench = s.get('benchChampions')
        if bench is None:
            bench = s.get('bench') or []
        bench_ids = []
        for b in bench:
            bench_ids.append(b['championId'] if isinstance(b, dict) else b)
        return my_id, bench_ids

    def _dump_champ_select_once(self, s):
        """v27 诊断：一局内首次进入选人时打印 session 结构要点，
        用于确认"初始 2-3 张卡"在 LCU 数据里的真实表示 —— 一次测试局即可定位。"""
        if self._champ_dumped:
            return
        self._champ_dumped = True
        try:
            act_sum = []
            for g in (s.get('actions') or []):
                if not isinstance(g, list):
                    g = [g]
                for a in g:
                    act_sum.append(
                        f"{a.get('type')}#{a.get('id')}"
                        f"(cell{a.get('actorCellId')},inp{a.get('isInProgress')},"
                        f"done{a.get('completed')},cid{a.get('championId')})")
            lc = s.get('localPlayerCellId')
            me = next((p for p in s.get('myTeam', [])
                       if p.get('cellId') == lc), None)
            tm = s.get('timer') or {}
            my_id, b_ids = self._extract_session_state(s)
            print(f'[选人] v30 本局首次进入选人 session keys={sorted(s.keys())}')
            print(f'[选人] v30 legacy={s.get("isLegacyChampSelect")} '
                  f'localCell={lc} 我={me}')
            print(f'[选人] v30 actions=[{" | ".join(act_sum) or "空"}] '
                  f'timer.phase={tm.get("phase")} timeLeft={tm.get("timeLeftInPhase")}')
            print(f'[选人] v30 可用池={sorted(b_ids)} '
                  f'rerolls={s.get("rerollsRemaining")} '
                  f'allowSubset={s.get("allowSubsetChampionPicks")} '
                  f'queueId={s.get("queueId")}  '
                  f'(注: pickable 端点=账号全量英雄, 非本局卡, 已弃用)')
        except Exception as e:
            print(f'[选人] v30 session dump 失败: {type(e).__name__}: {e}')

    def _log_pick_delta(self, s):
        """v30 诊断：未锁定期间, 关键状态每次变化打一行 ——
        观察 PATCH 前后 action 是否被服务器接受/翻转。"""
        try:
            lc = s.get('localPlayerCellId')
            a0 = None
            for g in (s.get('actions') or []):
                if not isinstance(g, list):
                    g = [g]
                for a in g:
                    if a.get('actorCellId') == lc:
                        a0 = a
                        break
                if a0 is not None:
                    break
            me = next((p for p in s.get('myTeam', [])
                       if p.get('cellId') == lc), None)
            if a0 is None and me is None:
                return
            _m = me or {}
            _, bench_ids = self._extract_session_state(s)
            key = (a0.get('championId') if a0 else None,
                   a0.get('completed') if a0 else None,
                   a0.get('isInProgress') if a0 else None,
                   _m.get('championId'),
                   tuple(sorted(bench_ids)))
            if key == self._last_pick_delta:
                return
            self._last_pick_delta = key
            print(f'[选人] v30 未锁状态: action(cid={key[0]},done={key[1]},'
                  f'inp={key[2]}) 我cid={key[3]} 池={list(key[4])}')
        except Exception:
            pass

    def _auto_select_pass(self, bench_ids, my_id, locked, queue_id=None,
                          allow_subset=False):
        """自动选/自动换统一分流（轮询与 WS 共用）。
        未锁定 → 自动选(试错法, 偏好逐个试, 服务器接受谁锁谁);
        已锁定 → 自动换(备选池 benchChampions 秒换)。
        v41: queue_id 传入 _try_initial_pick, 用于把"屏幕点卡兜底"限制在 ARAM 队列。
        v4.1.57: 增加 allow_subset(=session 的 allowSubsetChampionPicks) 作大乱斗
        特征兜底 —— queueId 白名单不可靠(2400 漏网), 靠特征识别更稳。
        """
        # v34: 选/换拆成两个独立开关
        if not locked:
            if self._settings.get('auto_select_champion'):
                self._try_initial_pick(queue_id, allow_subset)
        else:
            if self._settings.get('auto_pick_champion'):
                self._try_auto_pick(bench_ids, my_id)

    def _try_initial_pick(self, queue_id=None, allow_subset=False):
        """v31 自动选英雄: REST 试偏好(快, 命中就锁偏好) → 全空就点卡兜底(必锁一张)。

        v30 认识(监听实验证实): 屏幕那 2-3 张卡 = 服务器下发的"本局允许子集",
        LCU 读不出来; 对非允许英雄, 服务器回 204 但静默忽略(不锁, 也无副作用)。
        v31 兜底思路: 既然"屏幕渲染的卡 = 必被允许", 用鼠标点它就绕开了
        读不到卡组的死结。锁定后剩余卡进 bench, 配合自动换回收偏好, 等于
        把"猜哪个偏好被抽中"变成了"必锁一张 + 偏好可能通过 bench 回来"。

        v41: queue_id 仅用于限制"屏幕点卡兜底"——点卡坐标是按 ARAM(2-3 张卡)
        布局标定的, 排位/匹配等模式的选人界面完全不同, 点卡会误点, 故非 ARAM
        只走 REST 试偏好。

        流程:
          1) 偏好逐个交 pick_champion 试锁(保留 v30 行为, 命中就直接锁偏好)
          2) REST 全 204: 调 _click_pick_card 找客户端窗口 → 置顶 →
             按窗口内相对坐标点卡 → REST 读回验证(若仅预选则 POST complete 补锁)
          3) 全局 3s 节流, 防 1s 轮询打爆请求
        """
        now = time.time()
        if now - self._last_pick_t < C.PICK_THROTTLE_S:
            return
        if not self._swap_lock.acquire(blocking=False):
            return
        try:
            preferred = self._settings.get('preferred_champions', [])

            # 1) 优先 REST 试偏好(命中即锁, 不消耗点卡流程)
            if preferred:
                locked_cid = self._api.pick_champion(list(preferred))
                if locked_cid:
                    self._last_pick_t = now
                    name = self._champ_data.get(locked_cid, str(locked_cid))
                    print(f'[Core] 自动选英雄(REST命中偏好) → {name}')
                    self._emit(self._on_status_update, f'✅ 已选 {name}')
                    return

            # 2) REST 全空 → 点卡兜底(仅在大乱斗类队列且开关开启)
            # v4.1.57: 判断从"queueId 白名单"升级为"queueId 白名单 OR session 特征",
            # 海克斯大乱斗 queueId=2400 曾被漏判(白名单只有 450/3270), 导致点卡
            # 兜底被跳过 → "真实测试没自动选"。特征 allowSubsetChampionPicks=True
            # 是大乱斗独有(召唤师峡谷全英雄可用为 False), 用它对冲 queueId 未知变体。
            is_aram = (queue_id in ARAM_QUEUE_IDS) or bool(allow_subset)
            if not is_aram:
                # 非大乱斗(排位/匹配等): 点卡坐标不适用, 节流后只靠 REST, 不误点
                self._last_pick_t = now
                return
            if not self._settings.get('auto_pick_click_fallback', True):
                # v41: 兜底关闭时也更新节流, 否则每 1s 轮询重跑完整 pick_champion
                self._last_pick_t = now
                if now - self._last_noaccept_t > C.NO_ACCEPT_LOG_INTERVAL_S:
                    print('[选人] v31 偏好未中, 且点卡兜底已关, 继续监测')
                    self._last_noaccept_t = now
                return

            cid = self._click_pick_card()
            self._last_pick_t = now
            if cid:
                name = self._champ_data.get(cid, str(cid))
                print(f'[Core] 自动选英雄(点卡) → {name}')
                self._emit(self._on_status_update, f'✅ 已点选 {name}')
            else:
                if now - self._last_noaccept_t > C.NO_ACCEPT_LOG_INTERVAL_S:
                    print('[Core] v31 点卡兜底未识别为锁定(窗口未找到/坐标偏/被遮), 继续监测')
                    self._last_noaccept_t = now
        finally:
            self._swap_lock.release()

    def _click_pick_card(self):
        """v31: 找 LOL 客户端窗口 → 置顶 → 按相对坐标点卡 → REST 读回验证。

        最多 3 次重试(微调 y 偏移, 处理 click 落点略偏)。
        点完读 session:
          - action.completed && championId: 已锁, 返回 cid
          - championId 已写但未 completed: 预选成功, POST /actions/{id}/complete 补锁
          - 都未变: 点击落空 / 命中空白, 重试
        全失败返回 None。
        """
        try:
            from lcu.clicker import (get_client_rect, bring_to_front,
                                     click_at, window_pid,
                                     process_elevated)
        except ImportError as e:
            print(f'[点卡] 导入 clicker 失败: {type(e).__name__}: {e}')
            return None

        rect = get_client_rect()
        if not rect:
            print('[点卡] 找不到 LOL 客户端窗口(LeagueClient*.exe 都不在可见桌面上)')
            return None
        # v4.3.11: 置顶失败不再整体放弃。cursor/sendinput 按**绝对屏幕坐标**注入,
        # 前台锁挡住时会把点击落到用户正在操作的那个窗口(浏览器/桌面)上 —— 这正是
        # v4.3.10「置顶失败即放弃」要防的风险。但 postmsg 按 hwnd **定向**投递
        # WM_LBUTTONDOWN/UP, 既不依赖前台、也不会点错窗口, 故此时降级为纯 postmsg。
        # v4.3.15: postmsg 对 LOL 的 CEF 客户端**实测完全不生效**(2026-09-24 日志:
        # 13 次 postmsg 全未命中, 唯一"成功"那次其实读到了用户自己手选的英雄)。
        # 真正的判据不是前台, 而是"这个屏幕坐标下最上层的窗口是不是客户端" ——
        # 是则 cursor/sendinput 注入必然落在客户端上, 与前台锁无关。故用
        # require_over 走 click_at(注入前先 WindowFromPoint 校验)。
        front_ok = bring_to_front(rect['hwnd'])
        if not front_ok:
            print('[点卡] 提示: 无法把客户端拉到前台(前台锁), 改用"坐标落入客户端才点击"'
                  '的安全模式')

        # v32 权限侦查(UIPI): 低权限进程向高权限窗口注入鼠标会被系统静默丢弃
        try:
            _me_el = process_elevated()
            _win_el = process_elevated(window_pid(rect['hwnd']))
            if _me_el is False and _win_el is True:
                print('[点卡] ⚠️ 检测到 LOL 客户端是管理员权限运行, 本程序不是 → '
                      '模拟点击会被系统拦截')
                print('[点卡]    处理: 关闭本程序后, 右键 大蛋小助手.exe → '
                      '以管理员身份运行, 再试')
            elif _me_el is True and _win_el is False:
                print('[点卡] 提示: 本程序是管理员权限, LOL 客户端不是(不影响点击)')
        except Exception:
            pass

        fy = self._settings.get('auto_pick_click_fracY', 0.609)
        fx_first = self._settings.get('auto_pick_click_fracX', 0.161)
        xlist = self._settings.get('auto_pick_click_xlist') or [fx_first]
        base_y = int(rect['top'] + fy * rect['h'])
        print(f'[点卡] 窗口 {rect["w"]}x{rect["h"]} @({rect["left"]},{rect["top"]}) '
              f'进程={rect["process"]} y比例={fy:.3f} '
              f'x扫描={["%.2f" % v for v in xlist]}')

        # 找我的未完成 pick action
        s = self._api.get_champ_select_session()
        if not s:
            return None
        lc = s.get('localPlayerCellId')
        actions = s.get('actions') or []
        target_id = None
        is_legacy = s.get('isLegacyChampSelect', True) is not False
        prefix = ('/lol-champ-select/v1/session' if is_legacy
                  else '/lol-lobby-team-builder/champ-select/v1/session')
        for g in actions:
            if not isinstance(g, list):
                g = [g]
            for a in g:
                if (a.get('type') == 'pick'
                        and a.get('actorCellId') == lc
                        and not a.get('completed')):
                    target_id = a.get('id')
                    break
            if target_id is not None:
                break
        if target_id is None:
            print('[点卡] 找不到我的未完成 pick action, 跳过')
            return None

        def _read_my_a():
            try:
                s2 = self._api.get_champ_select_session()
            except Exception:
                return None
            if not s2:
                return None
            for g in (s2.get('actions') or []):
                if not isinstance(g, list):
                    g = [g]
                for a in g:
                    if (a.get('type') == 'pick'
                            and a.get('actorCellId') == lc):
                        return a
            return None

        # v33: x 横向扫描(2/3 张卡布局自适应) × 两档 y, 每点一次立刻读回:
        #   - championId 写入且 completed → 已锁, 收工
        #   - championId 写入但未 completed → 预选命中, POST complete 补锁
        #   - championId 仍是 0 → 点空/点间隙, 试下一个候选
        # v4.3.11: 前台锁挡住时 front_ok=False。当时的做法是降级到 postmsg 定向投递,
        # 但 v4.3.15 已实测 postmsg 对 LOL 的 CEF 客户端完全不生效, 该分支已删除。
        # v4.3.15 修正(2026-09-24 用户实测"点卡显示自动选了, 其实是我自己选的"):
        #   postmsg 对 LOL 的 CEF 客户端**完全不生效**(该局 13 次 postmsg 全未命中),
        #   却返回 True 让调用方接着读回 session —— 读到的其实是用户自己手选的英雄,
        #   于是日志谎报"[点卡] ✅ 锁定 #136 / 自动选英雄(点卡) → 铸星龙王"。
        #   现在统一走 click_at(require_over=True): 注入前先确认该屏幕坐标下最上层
        #   就是客户端窗口。cursor/sendinput 点中的永远是"光标底下那个窗口", 与前台
        #   锁无关 —— 确认过就不会点到用户浏览器, 也就不需要因为拿不到前台而放弃。
        _occl_warned = False
        # v4.4.0(P1-12 配套): 注入被 UIPI 拦截时也只在首轮提示一次
        _uipi_warned = False
        # 点击前基线: 若进入本方法时就已经锁定, 说明是用户/客户端自己锁的,
        # 后续读回的 championId 不能算作"自动点选命中"(防误报, 见 v4.3.15 说明)。
        _pre = _read_my_a()
        pre_locked = bool((_pre or {}).get('completed'))
        if pre_locked:
            print(f'[点卡] 进入点击流程前已锁定 #{(_pre or {}).get("championId")}, '
                  f'跳过自动点卡')
            return None
        # v4.4.2(P1-2): 内层循环加总时限。12 个候选点 × (click + 0.25s + REST
        # 读回 + 命中预选再 0.2s) 最坏分钟级, 全程持 _swap_lock 且阻塞 poll 线程。
        # 超过 deadline 即放弃本轮, 交给下个节流周期重试。
        _deadline = time.monotonic() + C.CLICK_PICK_DEADLINE_S
        for y_off in (0, 12):
            if time.monotonic() > _deadline:
                print('[点卡] 达到本轮总时限, 放弃剩余候选(等下个节流周期)')
                break
            y = base_y + y_off
            for xi in xlist:
                if time.monotonic() > _deadline:
                    print('[点卡] 达到本轮总时限, 放弃剩余候选(等下个节流周期)')
                    break
                x = int(rect['left'] + xi * rect['w'])
                ok, how = click_at(x, y, rect['hwnd'], require_over=True)
                if not ok:
                    if how == 'point-not-over-client':
                        if not _occl_warned:
                            _occl_warned = True
                            print('[点卡] ⚠️ 候选坐标不在客户端窗口上(被其它窗口遮挡或'
                                  '窗口位置偏移), 本轮不点击以免点到别的窗口')
                        continue
                    if how == 'uipi-blocked':
                        # v4.4.0(P1-12 配套): clicker 新增的独立原因。上层 v32 侦查
                        # 只在点卡开头提示一次, 而"侦查之后客户端才被提权"这种时序
                        # (用户中途以管理员重启 LOL)只能靠注入前的这次判定发现。
                        # 这里给出的处置与 :781 的提示一致, 但只打一次, 免得 12 个
                        # 候选点刷 12 行。
                        if not _uipi_warned:
                            _uipi_warned = True
                            print('[点卡] ⚠️ 注入被 UIPI 拦截: LOL 客户端是管理员权限, '
                                  '本程序不是')
                            print('[点卡]    处理: 关闭本程序后, 右键 大蛋小助手.exe → '
                                  '以管理员身份运行, 再试')
                            self._emit(self._on_status_update,
                                       '⚠️ 需以管理员身份运行才能自动选人')
                        continue
                    print(f'[点卡] 点(x={xi:.2f},y+{y_off}) 动作失败: {how}')
                    continue
                time.sleep(C.CLICK_READBACK_SLEEP_S)  # v4.4.0(P2-12): 见 constants
                a = _read_my_a()
                if not a:
                    continue
                cid = a.get('championId') or 0
                done = a.get('completed') or False
                if cid and done:
                    # pre_locked 已在进入循环前判过并 return, 走到这里一定是本次点击生效
                    print(f'[点卡] ✅ 锁定 #{cid} (x={xi:.2f}, y+{y_off}, {how})')
                    return cid
                if cid and not done:
                    # 预选命中 → 补 POST complete 完成锁定
                    try:
                        # v4.4.0 修复(P2-8): 原先直接调私有 self._api._req('POST', ...)。
                        # lcu/api.py 已有公开封装 post(ep, data=None, **kw) —— 它内部
                        # 就是 _req('POST', ...) 再走 _checked(), 且会把 >=400 统一转成
                        # 异常交由下面的 except 处理(原先 _req 直接返回响应, 4xx 被静默
                        # 当成"补锁成功"继续往下读回)。改用公开方法既避免了跨层调私有,
                        # 也让失败能被这里捕获并打日志。
                        self._api.post(f'{prefix}/actions/{target_id}/complete')
                    except Exception as e:
                        print(f'[点卡] 补 complete 异常: {type(e).__name__}: {e}')
                        continue
                    time.sleep(C.COMPLETE_READBACK_SLEEP_S)  # v4.4.0(P2-12)
                    a2 = _read_my_a()
                    if a2 and a2.get('completed') and a2.get('championId'):
                        print(f'[点卡] ✅ 锁定 #{a2["championId"]} '
                              f'(点选+补锁, x={xi:.2f})')
                        return a2['championId']
                # cid 仍是 0 → 未命中卡片, 继续
                print(f'[点卡] 点(x={xi:.2f},y+{y_off}) 未命中, 试下一个…')

        print('[点卡] 横向扫描全部未命中, 放弃本轮(等下个节流周期再试)')
        return None

    def _emit_champ_select_info(self, session, my_champion_id) -> bool:
        """提取选人阶段的倒计时、当前动作、锁定状态；返回是否已锁定"""
        locked = False
        try:
            local_cell = session.get('localPlayerCellId')
            actions = session.get('actions', [])
            # 检查是否已锁定（我方所有 action 都 completed）
            my_actions = []
            for action_group in actions:
                if not isinstance(action_group, list):
                    action_group = [action_group]
                for action in action_group:
                    if action.get('actorCellId') == local_cell:
                        my_actions.append(action)
            if my_actions and all(a.get('completed', False) for a in my_actions):
                locked = True

            if not self._on_champ_select_info:
                return locked
            info = {
                'time_left': 0,
                'phase': '',
                'my_champion_id': my_champion_id,
                'my_champion_name': self._champ_data.get(my_champion_id, '') if my_champion_id else '',
                'is_my_turn': False,
                'current_action_type': '',
                'locked': locked,
            }

            # 计时器信息
            timer = session.get('timer', {})
            info['time_left'] = max(0, int(timer.get('timeLeftInPhase', 0)))
            info['phase'] = timer.get('phase', '')

            # 查找当前正在进行的动作
            for action_group in actions:
                if not isinstance(action_group, list):
                    action_group = [action_group]
                for action in action_group:
                    if action.get('isInProgress') and not action.get('completed'):
                        info['current_action_type'] = action.get('type', '')
                        if action.get('actorCellId') == local_cell:
                            info['is_my_turn'] = True
                        break
                else:
                    continue
                break

            self._on_champ_select_info(info)
        except Exception as e:
            print(f'[Core] 提取选人信息失败: {type(e).__name__}: {e}')
        return locked

    def _try_auto_pick(self, bench_ids, current_id):
        """自动换英雄逻辑（锁定后的备选池阶段）：
        
        规则：
        1. 无偏好英雄 → 不换（尊重用户已锁定的选择，不做不可预测的随机换）
        2. 有偏好英雄 → 找备选池中优先级最高的偏好英雄换上
        3. 当前英雄已是最高优先级 → 不换
        """
        # 使用锁防止多线程同时换英雄
        if not self._swap_lock.acquire(blocking=False):
            return  # 另一个线程正在换英雄
        try:
            self._do_auto_pick(bench_ids, current_id)
        finally:
            self._swap_lock.release()

    def _do_auto_pick(self, bench_ids, current_id):
        """自动换英雄（已锁定后的备选池阶段）：
        1. 备选池出现比当前优先级更高的偏好英雄 → 秒换（无冷却）
        2. 当前英雄优先级已 >= 备选池最优偏好 → 不换（单调升级，不会反复横跳）
        3. 备选池没有偏好英雄 → 不动
        (v27: 初始未锁定的"自动选英雄"已拆到 _try_initial_pick)
        """
        preferred = self._settings.get('preferred_champions', [])
        # v4.4.0 修复(P1-10): 原先 set(bench_ids) 写在 `if not bench_ids` 守卫**之前**。
        # _extract_session_state 在 session 缺字段时可能返回 None, 此时 set(None)
        # 先抛 TypeError, 守卫形同虚设, 异常一路上抛被 _poll 吞成"选人轮询异常"。
        # 先把 None/空集合躲开, 再构造集合。
        if not bench_ids:
            return
        bench_set = set(bench_ids)
        now = time.time()

        # 备选池中优先级最高的偏好英雄（索引最小 = 优先级最高）
        best_cid = None
        best_rank = len(preferred)
        for rank, cid in enumerate(preferred):
            if cid in bench_set and rank < best_rank:
                best_cid = cid
                best_rank = rank

        # 备选池没有偏好英雄 → 不动
        if best_cid is None:
            if preferred:
                # v4.1.63: 只在备选池"变化"时打印一次, 避免每 1s 轮询刷屏
                # (之前每次轮询都打, 选人阶段几十秒打几十行"暂不换"污染日志)
                pool_key = tuple(sorted(bench_ids))
                if pool_key != self._last_no_pref_pool:
                    self._last_no_pref_pool = pool_key
                    print(f'[Core] 暂不换: 备选池无偏好英雄(池={sorted(bench_ids)})')
            return

        # 当前英雄优先级不低于备选池最优偏好 → 不换（不降级、不反复）
        if current_id in preferred:
            current_rank = preferred.index(current_id)
            if current_rank <= best_rank:
                # v4.3.14: 与"备选池无偏好"分支一样去重 —— 之前这里每 1s 轮询
                # 都打一行, 选人几十秒刷几十行"当前英雄已是更高优先级"污染日志。
                key = (current_id, best_cid, best_rank, tuple(sorted(bench_ids)))
                if key != self._last_no_swap_key:
                    self._last_no_swap_key = key
                    print(f'[Core] 暂不换: 当前英雄已是更高优先级'
                          f'(rank#{current_rank+1}<=#{best_rank+1})')
                return

        if best_cid == current_id:
            return

        # 3) 秒换到更高优先级偏好英雄（无冷却）
        ok = self._api.bench_swap(best_cid)
        # v4.4.0(P1-3 配套): bench_swap 现为三态 —— True 成功 / False 明确被 API 拒绝 /
        # None 请求已发出但读回校验失败(未验证)。原先 None 也落进 else 分支打印
        # "API返回False", 属误报: 换英雄很可能已经生效, 只是没能读回确认。
        if ok is None:
            self._last_swap = now  # 未验证也节流, 避免 1s 轮询把同一请求打爆
            print(f'[Core] 秒换结果未验证(请求已发出, REST 读回失败, 可能已生效): '
                  f'championId={best_cid}')
            return
        if not ok:
            print(f'[Core] 秒换失败(API返回False): championId={best_cid}')
            return
        self._last_swap = now
        name = self._champ_data.get(best_cid, str(best_cid))
        print(f'[Core] 自动秒换 → {name}(#{best_rank+1})')
        self._emit(self._on_status_update, f'🔄 秒换 {name}')

    # ==================== 对局结束自动返回（sona 模式） ====================
    def _handle_end_of_game(self):
        if not self._settings.get('auto_play_again'):
            return
        # v4.3.12: EndOfGame 与 WaitingForStats 是两个独立 phase, 会先后各进一次
        # 本方法。去重只靠 _return_pending 一个标记, 不再用 3s 节流 —— 那节流是
        # v4.3.11 引入 _return_pending 之前遗留的旧去重手段, 与新逻辑冲突:
        #   WaitingForStats 启动返回(_return_pending=True, _last_play=now)
        #   → PreEndOfGame 打断倒计时(on_cancel 复位 _return_pending=False)
        #   → EndOfGame 到来时标记已复位, 但距 _last_play 不足 3s → 被旧节流挡住
        #   → EndOfGame 只处理一次(_last_handled_phase 去重) → 返回整局失效。
        # 回到 Lobby 时才重置 _return_pending(见 _apply_phase_side_effects)。
        if self._return_pending:
            return
        # v4.3.11: 倒计时启动前即置位, 防止 EndOfGame→WaitingForStats 二次触发
        self._return_pending = True
        delay = _safe_int(self._settings.get('auto_play_again_delay', 2), 2, lo=0)
        if delay > 0:
            # v4.4.0(P1-7): kind='return' —— v4.3.11 那个"返回倒计时被离开 ReadyCheck
            # 时的清理顺手打断"的事故, 在这一层就根除了(独立计数器)。
            self._start_countdown(delay, '⏳ 返回倒计时', self._do_play_again,
                                  on_cancel=self._clear_return_pending,
                                  kind='return')
        else:
            # v41: delay=0 也走后台线程, 避免 _do_play_again 的 15s 重试循环
            # 阻塞 poll 轮询线程(原 delay=0 直接在 poll 线程跑)
            threading.Thread(target=self._do_play_again, daemon=True).start()

    def _do_play_again(self):
        """playAgain + retry matchmaking（仅房主可操作）"""
        try:
            # playAgain 所有成员都可以调用
            self._api.play_again()
            print('[Core] 返回房间')
            self._emit(self._on_status_update, '🔁 已返回房间')
        except Exception as e:
            print(f'[Core] 返回房间失败: {type(e).__name__}: {e}')
            # v4.3.11 修复: 失败必须复位 _return_pending, 否则若阶段不再变化
            # (停在 EndOfGame), 该标记卡 True 会让本局自动返回永久失效、不再重试。
            self._return_pending = False
            return

        if not self._settings.get('auto_queue'):
            return

        # v4.4.0(P2-12): 15(重试上限)/1(重试间隔)集中到 core/constants.py
        for i in range(1, C.PLAY_AGAIN_RETRY_MAX + 1):
            time.sleep(C.PLAY_AGAIN_RETRY_INTERVAL_S)
            if not self._running:
                return
            try:
                self._api.start_matchmaking()
                # v4.4.0(P2-7): 返回房间后的重排也是"我们发起的", 同样登记
                self._queue_started_by_us = True
                print(f'[Core] 匹配启动（第{i}次）')
                return
            except Exception as e:
                err = str(e)
                if 'INVALID_PERMISSIONS' in err:
                    print('[Core] 非房主，等待房主匹配')
                    self._emit(self._on_status_update, '⏳ 等待房主匹配...')
                    return
                if i >= C.PLAY_AGAIN_RETRY_MAX:
                    print('[Core] 匹配启动失败')

    # ==================== 自动点赞（sona 模式） ====================
    def _try_start_honor(self):
        """v23:并发安全的自动点赞入口。

        结算相关阶段(PreEndOfGame/EndOfGame/WaitingForStats)可能被 WS 事件、
        轮询与状态对账多次触发;用非阻塞锁保证同一时刻只有一个点赞线程,
        且先快速探测 ballot 避免为"没有待点赞的对局"空起线程。
        锁由 worker 线程结束时释放。
        """
        if not self._settings.get('auto_honor'):
            return
        if not self._honor_lock.acquire(blocking=False):
            return  # 已有点赞线程在跑
        try:
            ballot = self._api.get_honor_ballot()
        except Exception:
            ballot = None
        if not ballot:
            self._honor_lock.release()
            return
        allies = list(ballot.get('eligibleAllies') or [])
        opponents = list(ballot.get('eligibleOpponents') or [])
        if not allies and not opponents:
            self._honor_lock.release()
            print('[Core] 没有可点赞的玩家')
            return
        # v4.1.60: 防御 start() 失败(线程资源耗尽等极端情况)导致 _honor_lock
        # 永不释放 —— 锁由 _honor_worker 结束时释放, 若线程根本没起来锁会泄漏,
        # 后续所有自动点赞全被阻塞。这里 start 异常时立即归还锁。
        try:
            threading.Thread(target=self._honor_worker,
                             kwargs={'ballot': ballot, 'allies': allies,
                                     'opponents': opponents},
                             daemon=True).start()
        except Exception as e:
            self._honor_lock.release()
            print(f'[Core] 点赞线程启动失败: {type(e).__name__}: {e}')

    def _honor_worker(self, ballot, allies, opponents):
        """在锁保护下执行点赞,结束(成功/失败/异常)释放锁"""
        try:
            self._do_auto_honor(ballot, allies, opponents)
        except Exception as e:
            print(f'[Core] 自动点赞失败: {type(e).__name__}: {e}')
        finally:
            self._honor_lock.release()

    def _do_auto_honor(self, ballot, allies, opponents):
        """对局结束后自动点赞队友（sona auto-honor 模式）"""
        HONOR_CATEGORIES = ['HEART', 'COOL', 'SHOTCALLER']
        try:
            # v4.4.0 修复(P1-9): 原先 `(ballot.get('votePool') or {}).get('votes', 1)`。
            # 该表达式只防住 votePool 为 None, 防不住 votes **键存在但值为 None**
            # (LCU 在投票池未结算时确实会下发 "votes": null)。此时 votes=None →
            # 下面的切片 [:None] 抛 TypeError → 被本方法的 except 吞掉 →
            # 整局"自动点赞"静默失效(日志里只有一行"自动点赞失败: TypeError")。
            # 现在做显式整型容错: None / 空串 / 非数字一律回落到默认 1 票。
            raw_votes = (ballot.get('votePool') or {}).get('votes')
            try:
                votes = int(raw_votes) if raw_votes is not None else 1
            except (TypeError, ValueError):
                # 非数字(比如客户端给了 '2' 之外的东西)不抛, 退回默认票数
                print(f'[Core] 点赞票数异常({raw_votes!r}), 按 1 票处理')
                votes = 1
            if votes < 0:
                votes = 0
            game_id = ballot.get('gameId', 0)
            print(f'[Core] 自动点赞: 可用票数={votes}, 队友={len(allies)}, 对手={len(opponents)}')
            # 打散顺序
            random.shuffle(allies)
            random.shuffle(opponents)
            # 先给队友，多余的给对手
            targets = (allies + opponents)[:votes]
            for i, target in enumerate(targets):
                category = random.choice(HONOR_CATEGORIES)
                ok = self._api.honor_player(
                    target.get('puuid', ''),
                    target.get('summonerId', 0),
                    game_id,
                    category
                )
                if ok:
                    name = target.get('championName', f'#{target.get("summonerId", "?")}')
                    print(f'[Core] 第{i+1}票 ✓ → [{category}] 给了 {name}')
            self._emit(self._on_status_update, f'👍 已自动点赞 {len(targets)} 人')
            print(f'[Core] 自动点赞完成，共 {len(targets)} 票')
        except Exception as e:
            print(f'[Core] 自动点赞失败: {type(e).__name__}: {e}')

    # ==================== WS 事件（sona 模式） ====================
    def _ws_phase(self, event):
        data = event.get('data')
        if isinstance(data, str):
            new_phase = data
        elif isinstance(data, dict):
            new_phase = data.get('phase', '')
        else:
            return

        # 统一阶段入口（内部加锁串行处理；未连接时不触发任何动作）
        self._set_phase(new_phase, allow_handle=self._connected)

    def _ws_champ_select(self, event):
        data = event.get('data')
        if not data:
            return
        my_id, bench_ids = self._extract_session_state(data)
        if self._on_bench_champions:
            self._on_bench_champions(bench_ids, my_id)

        # WS 事件也更新选人倒计时信息（返回是否已锁定）
        locked = self._emit_champ_select_info(data, my_id)

        # v30:自动选(未锁,试错法)/自动换(已锁,池=bench)统一分流
        try:
            self._auto_select_pass(bench_ids, my_id, locked, data.get('queueId'),
                                   data.get('allowSubsetChampionPicks'))
        except Exception as e:
            print(f'[Core] 选人处理失败(WS): {type(e).__name__}: {e}')
    def _ws_ready_check(self, event):
        """sona 模式：WS 事件触发自动接受
        playerResponse 只代表本机玩家的响应（Declined 只会是自己拒绝），
        无论通过原生按钮还是小窗按钮拒绝，都立即撤销待执行的延迟接受并暂停自动接受，
        防止客户端重新匹配弹新 ReadyCheck 时把玩家的"拒绝"覆盖成"接受"。
        队友的响应在 players 数组中，不会出现在 playerResponse 字段。
        """
        data = event.get('data')
        if not data:
            return
        if self._phase != 'ReadyCheck':
            return
        state = data.get('state', '')
        response = data.get('playerResponse', '')
        if self._on_ready_check_state:
            self._on_ready_check_state(state == 'InProgress')
        # 本机玩家已拒绝（任何途径）：撤销延迟接受 + 暂停自动接受与自动匹配，
        # 直到手动点"启动"或手动在客户端开始匹配(→ _set_phase Matchmaking)才恢复
        if response == 'Declined' and not self._user_declined_this_session:
            self._user_declined_this_session = True
            self._has_accepted_this_check = True
            self._auto_queue_paused = True  # G6:拒绝后连自动匹配一起暂停,防"拒绝→重排→自动接受"循环
            # v4.4.0 修复(P1-7): 只撤销"在途的自动接受倒计时"; 原先共用 _countdown_id,
            # 这一刀会连带打断自动匹配/自动返回的倒计时(自动匹配那种情况下本就被
            # _auto_queue_paused 挡住, 但自动返回会被误伤)。
            self._bump_countdown('accept')
            self._accept_pending = False  # 撤销任何在途延迟接受
            print('[Core] 检测到已拒绝对局，暂停自动接受/自动匹配直到手动恢复')
            self._emit(self._on_status_update, '❌ 已拒绝，暂停自动匹配')
            return
        if state == 'InProgress' and not self._user_declined_this_session:
            self._handle_ready_check()

    # ==================== 公共操作 ====================
    def swap_champion(self, cid: int) -> bool:
        """手动换英雄（UI 按钮）。返回是否真的换成功。

        v4.4.0 修复(P1-6): 原先本方法**不加 _swap_lock**, 只靠 0.5s 节流。
        节流是基于 _last_swap 的"事后"判断, 挡不住并发: 自动选人 _try_initial_pick
        持锁期间正好在 _click_pick_card 里点卡(最坏约 5s), 此时用户点"换英雄",
        两个线程会同时向 LCU 发 bench_swap —— 手动的这一次会插进自动点卡的扫点
        过程中间, 造成"我明明点了英雄却换成了另一个"的错乱, 且日志里看不出原因。
        现在与 _try_initial_pick/_try_auto_pick 共用同一把 _swap_lock:
        拿不到锁 = 自动流程正在写选人状态 → 明确返回 False 并说明原因,
        **绝不静默假装成功**(main.py 只在 ok 为真时刷新 UI 与小窗)。
        仍保留 0.5s 节流作为第二道防线(用户连点)。
        死锁分析: _try_initial_pick 持锁期间只调 _api.pick_champion 与
        _click_pick_card, 二者都不会回调 swap_champion, 故不会自锁。
        """
        now = time.time()
        if now - self._last_swap < C.SWAP_THROTTLE_S:
            print(f'[Core] 手动换英雄被节流拦截(距上次 {now - self._last_swap:.2f}s)')
            return False
        # 不能用阻塞 acquire: 持锁方最坏要 5s(点卡全扫一遍), 阻塞会把 UI 线程卡住,
        # 也会让用户以为"点了没反应"。拿不到就明确拒绝, 让用户下一轮再试。
        if not self._swap_lock.acquire(blocking=False):
            print('[Core] 手动换英雄被拒绝: 自动选英雄正在处理中点卡流程, 请稍后重试')
            self._emit(self._on_status_update, '⏳ 自动选人中，请稍后再换')
            return False
        try:
            ok = self._api.bench_swap(cid)
            self._last_swap = now
            # v4.4.0(P1-3 配套): bench_swap 现在是三态。None = 请求已发出但读回
            # 校验失败(未知)。本方法签名是 -> bool, 且 main.py 只在真值时才刷新
            # 小窗 —— 所以这里返回 False(不谎报成功), 但用状态栏讲清"未验证",
            # 免得用户以为完全没生效而反复点。
            if ok is None:
                print(f'[Core] 手动换英雄结果未验证(请求已发出, REST 读回失败, 可能已生效): '
                      f'championId={cid}')
                self._emit(self._on_status_update, '⚠️ 换英雄结果未确认，请查看客户端')
                return False
            if ok:
                print(f'[Core] 手动换英雄: {cid}')
            return ok
        except Exception as e:
            print(f'[Core] 换英雄失败: {type(e).__name__}: {e}')
            return False
        finally:
            self._swap_lock.release()

    def accept_current_match(self) -> bool:
        try:
            check = self._api.get_ready_check()
            if not check or check.get('state') != 'InProgress':
                return False
            self._bump_countdown('accept')  # v4.4.0(P1-7): 只取消在途自动接受
            self._api.accept_match()
            # sona 模式：手动接受解除拒绝暂停
            self._user_declined_this_session = False
            self._has_accepted_this_check = True
            self._accept_pending = False  # 手动接受:取消在途的自动接受倒计时
            self._auto_queue_paused = False  # 手动接受 = 恢复整条自动化链
            self._emit(self._on_status_update, '✅ 已接受对局!')
            return True
        except Exception as e:
            print(f'[Core] 接受失败: {type(e).__name__}: {e}')
            return False

    def decline_current_match(self) -> bool:
        """sona 模式：拒绝后暂停自动接受与自动匹配（不关闭设置），
        手动点"启动"或手动在客户端开始匹配后自动恢复"""
        try:
            check = self._api.get_ready_check()
            if not check or check.get('state') != 'InProgress':
                return False
            self._bump_countdown('accept')  # v4.4.0(P1-7): 只取消在途自动接受
            self._api.decline_match()
            # sona 核心：用户拒绝后暂停自动接受/自动匹配
            self._user_declined_this_session = True
            self._has_accepted_this_check = True
            self._accept_pending = False  # 取消任何在途自动接受
            self._auto_queue_paused = True  # G6
            print('[Core] 已拒绝对局，暂停自动匹配（手动恢复）')
            self._emit(self._on_status_update, '❌ 已拒绝，暂停自动匹配')
            return True
        except Exception as e:
            print(f'[Core] 拒绝失败: {type(e).__name__}: {e}')
            return False

    def refresh_connection(self) -> bool:
        try:
            self._summoner = self._api.get_current_summoner()
            self._connected = True
            self._poll_fail_count = 0
            self._emit(self._on_connection_changed, True, self.summoner_name)
            return True
        except Exception:
            self._connected = False
            self._emit(self._on_connection_changed, False, '')
            return False

    # ==================== 工具 ====================
    def _clear_ready_state(self):
        """sona 模式：离开 ReadyCheck 时清理定时器和重置标记"""
        # v4.4.0 修复(P1-7): 原先注释写"取消所有倒计时", 靠的是共用 _countdown_id 的
        # 副作用 —— 代价是本方法会把**自动匹配**与**自动返回**的在途倒计时一并打断
        # (v4.3.11 的返回倒计时失效事故即由此而来, 见 _clear_return_pending)。
        # 本方法语义上只该清理 ReadyCheck 自己的状态, 故现在只作废自动接受倒计时。
        # 行为等价性: 自动匹配倒计时到点后 _do_queue 仍会做 `self._phase not in
        # ('Lobby','None') → return` 的守卫, 已离开大厅时不会误发匹配请求。
        self._bump_countdown('accept')
        self._accept_pending = False
        if self._on_ready_check_state:
            self._on_ready_check_state(False)
        # sona 模式：离开 ReadyCheck 重置单次标记（允许下次匹配自动接受）
        self._has_accepted_this_check = False

    def _clear_accept_pending(self):
        """v4.1.64: 接受倒计时被取消(被新倒计时/停止打断)时的复位回调。
        对应 _start_countdown 的 on_cancel 参数, 防止 _accept_pending 卡在
        True 导致本局后续不再自动接受。

        v4.4.0(P1-7): 语义已名副其实 —— 现在只有"自动接受倒计时"这一个倒计时
        会回调本方法, 它只复位 _accept_pending, 不再顺带影响返回/匹配流程。
        """
        self._accept_pending = False

    def _clear_return_pending(self):
        """v4.3.11 修复: 返回倒计时被取消时的复位回调。

        【历史事故记录, 保留】根因: _handle_phase 每次离开 ReadyCheck 阶段都会调
        _clear_ready_state(), 而它会递增 _countdown_id —— WaitingForStats 阶段启动的
        「返回倒计时」会在紧随其后的 PreEndOfGame 阶段迁移时被这个递增打断
        (loop 检测到 id 不匹配 → _cancel() → on_done 永不执行)。若 _return_pending
        不复位, EndOfGame 阶段到来时会被该标记挡住 → 自动返回房间整局失效。
        此回调让它恢复到"结算前一阶段被跳过后, EndOfGame 仍能重新触发一次返回流程"
        的旧行为。

        v4.4.0(P1-7): 上述根因已在 _start_countdown(kind='return') 层面修掉
        (返回倒计时不再被 _clear_ready_state 误伤)。本回调保留 —— 它仍是
        "返回流程自身被停止/被新返回倒计时抢占"时的必要复位, 双保险。
        """
        self._return_pending = False

    @staticmethod
    def _random_delay(base):
        if base <= 0:
            return 0
        # v4.4.0(P2-12): 抖动参数集中到 constants.py, 语义见该文件注释
        half = max(base * 0.5, C.RANDOM_DELAY_MIN_HALF_S)
        return max(C.RANDOM_DELAY_MIN_S, round(base + random.uniform(-half, half)))

    # v4.4.0 新增(P1-7): 三种倒计时各自的取消计数器属性名。
    # 用"功能名 → 属性名"映射而不是下标/元组, 这样加第四种倒计时只需加一行,
    # 且 kind 打错时下面 _bump_countdown 会直接抛 KeyError(启动期暴露), 不会静默失效。
    _COUNTDOWN_ATTRS = {
        'queue': '_countdown_id_queue',
        'accept': '_countdown_id_accept',
        'return': '_countdown_id_return',
    }

    def _bump_countdown(self, kind):
        """让指定功能的**在途倒计时**全部作废(递增其计数器)。

        v4.4.0(P1-7): 取代原先各处直接 `self._countdown_id += 1`。语义要点:
        - 只影响 kind 指定的那一个倒计时, 不再像原先那样"一动就打断全部三个";
        - 不负责复位 pending 标志(_accept_pending/_return_pending): 那是被作废
          的那次倒计时自己的 on_cancel 回调的职责(见 _start_countdown 的 _cancel),
          两者互补 —— 手动接受/拒绝时显式复位, 倒计时被抢时走 on_cancel。
        """
        attr = self._COUNTDOWN_ATTRS[kind]
        setattr(self, attr, getattr(self, attr, 0) + 1)

    def _start_countdown(self, seconds, prefix, on_done, on_cancel=None,
                         kind='accept'):
        """启动一个可见倒计时(状态栏每秒刷新), 到点执行 on_done。

        v4.4.0(P1-7): 新增 kind 参数指定"属于哪个功能", 取值 queue/accept/return。
        默认 'accept' 保持与旧调用点(_start_countdown(秒, 前缀, 回调, on_cancel=..))
        完全兼容。每个 kind 有独立的取消计数器, 因此:
          - 自动匹配倒计时 只被 _do_queue/_handle_lobby 相关路径取消;
          - 自动接受倒计时 只被"用户拒绝/手动接受/手动拒绝/离开 ReadyCheck"取消;
          - 自动返回倒计时 只被"返回流程自身重试/停止"取消。
        这正是 v4.3.11 想解决的问题(返回倒计时被离开 ReadyCheck 顺手打断)的根因修复,
        该历史注释保留在 _clear_return_pending 中作为事故记录。
        """
        attr = self._COUNTDOWN_ATTRS[kind]
        # 用递增 ID 代替 sleep 等待，避免竞态
        setattr(self, attr, getattr(self, attr, 0) + 1)
        my_id = getattr(self, attr)

        def _still_mine():
            return getattr(self, attr) == my_id

        def _cancel():
            # v4.1.64: 倒计时被新倒计时/停止打断时, 通知调用方复位其 pending 标志
            # (如 _accept_pending)。否则通用 _countdown_id 被其他阶段的倒计时递增
            # 时, 本倒计时的 on_done 永不执行、pending 标志永不复位。
            if on_cancel:
                try:
                    on_cancel()
                except Exception:
                    pass

        def loop():
            for r in range(seconds, 0, -1):
                if not _still_mine() or not self._running:
                    _cancel()
                    return
                self._emit(self._on_status_update, f'{prefix} {r}...')
                time.sleep(C.COUNTDOWN_TICK_S)
            if _still_mine() and self._running:
                on_done()
            else:
                _cancel()

        t = threading.Thread(target=loop, daemon=True)
        t.start()

    def _emit(self, cb, *args):
        if cb:
            try:
                cb(*args)
            except Exception as e:
                # v4.4.2(P1-5): 原先 except Exception: pass 完全静默。_emit 是唯一的
                # UI 桥(含 Qt 跨线程信号入口), 一旦出错状态栏/小窗静默停更且日志零线索。
                # 至少打出异常类型与消息(走 print 会被 LogCapture 落盘, 便于事后排查)。
                print(f'[Core] 回调异常(cb={getattr(cb, "__name__", cb)}): '
                      f'{type(e).__name__}: {e}')

    def _cleanup(self):
        """停止脚本/退出程序时的收尾（_loop 退出后执行一次）。

        v4.4.0 修复(P2-7): 原先只要还连着就无条件 stop_matchmaking()。
        这会把**用户自己在客户端点出来的队列**一起取消 —— 场景: 用户手动点
        开始匹配, 顺手点了脚本的"停止"想看个东西, 结果队列被脚本一起停掉。
        现在只在"当前队列确实是我们发起的"(_queue_started_by_us)时才收尾。
        行为变化已在交付报告里登记; 项目内无其它地方依赖"停止脚本必定取消队列"
        (stop_matchmaking 全项目只出现于本方法与 lcu/api.py 的定义处)。
        """
        try:
            if self._connected and self._queue_started_by_us:
                self._api.stop_matchmaking()
                print('[Core] 已取消本脚本发起的匹配队列')
        except Exception as e:
            print(f'[Core] 收尾取消失败: {type(e).__name__}: {e}')
        finally:
            # 无论成功与否都复位: 它描述的是"本次会话"的状态, 收尾后再启动是新会话
            self._queue_started_by_us = False
        self._connected = False
