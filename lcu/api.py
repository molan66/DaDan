"""LCU API - 英雄联盟客户端 REST API 封装"""

import json
import threading
import requests
import urllib3
from typing import Optional, Any, Dict, List
from urllib3.exceptions import InsecureRequestWarning

# v4.4.0 修复（P2）：session 配置与 connector.py 共用同一工厂，避免两份配置漂移。
# 另：constants.py 只放常量、无第三方依赖，import 方向 connector → constants 单向，无环。
from .connector import make_session
from .constants import (
    TIMEOUT_API, TIMEOUT_ASSET, TIMEOUT_CDN_CDRAGON, TIMEOUT_CDN_GTIMG,
    RETRY_TOTAL, RETRY_BACKOFF, RETRY_STATUS_FORCELIST, RETRY_ALLOWED_METHODS,
    HEALTH_FAIL_THRESHOLD,
    PICK_TRY_MAX, PICK_TRY_DEADLINE_S,
)

requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)

# v4.4.0 修复（P0-8）：**非 loopback** 的第三方 CDN 请求必须校验证书，且不能复用
# LCU 那个 verify=False 的 session。一个模块级懒加载 session 装两者：
#   - verify=True（默认）：走正常 CA 校验；
#   - trust_env=False：沿用 v20 结论，禁止系统代理劫持（否则大陆直连 CDN 会被
#     代理拖到超时）。
# 历史事故提醒：api.py:9 的 disable_warnings 只为 LCU 自签证书保留，不要删。
_CDN_SESSION: Optional[requests.Session] = None
_CDN_LOCK = threading.Lock()


def _cdn_session() -> requests.Session:
    """取第三方 CDN 专用 session（懒加载单例，verify=True）。"""
    global _CDN_SESSION
    if _CDN_SESSION is None:
        with _CDN_LOCK:
            if _CDN_SESSION is None:
                _CDN_SESSION = make_session(verify=True)
    return _CDN_SESSION


# 英雄数字 id → Riot 英文别名(如 4 → 'TwistedFate')。
# gtimg 的图标接口只认别名, 不认数字 id; 映射表来自 data/champion_aliases.py,
# 每项形如 [中文名, 英文别名, 外号...] —— 取下标 1。表和调用方同在 exe 内打包。
_ALIAS_CACHE: Dict[int, str] = {}
# v4.3.11: 多线程并发调 _champion_alias 时, 模块级 dict 靠 GIL 保证单条语句原子、
# 写入值一致, 但"查 → 算 → 写"三步存在重复计算窗口; 加锁消除重复计算与 import 竞争。
_ALIAS_LOCK = threading.Lock()


def _champion_alias(champion_id) -> str:
    """取英雄英文别名。取不到返回 ''(绝不抛异常, 调用方在下载降级链里)。"""
    try:
        cid = int(champion_id)
    except Exception:
        return ''
    with _ALIAS_LOCK:
        if cid in _ALIAS_CACHE:
            return _ALIAS_CACHE[cid]
        alias = ''
        try:
            from data.champion_aliases import CHAMPION_ALIASES
            v = CHAMPION_ALIASES.get(cid)
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                alias = str(v[1]).strip()
        except Exception:
            pass
        # v4.4.2(P2-37): 原无条件写缓存 —— 一次瞬时 import 失败会把该英雄永久
        # 钉成空别名(进程生命周期内), 图标下载链随后一直走不到别名分支。
        # 只在取到非空别名时才缓存; 空值不缓存, 下次重试仍有翻身机会。
        if alias:
            _ALIAS_CACHE[cid] = alias
        return alias


class LCUAPI:
    def __init__(self, connector):
        self._c = connector
        # v4.4.0 修复（P2）：改用 connector.make_session 共享工厂（原先这里手写
        # verify/trust_env，与 connector.py 那份会漂移）。
        self._s = make_session(verify=False)
        # v4.4.0 修复（P1-2）：连接健康度观察点。上层（重连定时器 / UI）可读
        # consecutive_failures 或 is_healthy 判断"REST 是不是真的在通"，
        # 而不用自己去数异常。
        self.consecutive_failures: int = 0
        self.last_error: Optional[str] = None
        # v4.4.0 修复（P1-2）：只给幂等请求挂重试。requests 的 Retry 必须挂在
        # HTTPAdapter 上（Session.request 无 retries 参数），而 adapter 的作用域
        # 是整个 session，因此额外建一个**仅幂等方法使用**的 session。
        # 绝不能把 Retry 挂到 self._s 上 —— 那样 POST accept_match / bench_swap
        # 也会被重发，产生真实的重复副作用。
        self._s_idem = make_session(verify=False)
        self._retry = urllib3.util.Retry(
            total=RETRY_TOTAL,
            backoff_factor=RETRY_BACKOFF,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=RETRY_ALLOWED_METHODS,
            raise_on_status=False,
        )
        _adapter = requests.adapters.HTTPAdapter(max_retries=self._retry)
        self._s_idem.mount('https://', _adapter)
        self._s_idem.mount('http://', _adapter)

    @property
    def is_healthy(self) -> bool:
        """连续失败未达阈值即视为健康（P1-2，供上层观察）。"""
        return self.consecutive_failures < HEALTH_FAIL_THRESHOLD

    def _note_ok(self):
        self.consecutive_failures = 0
        self.last_error = None

    def _note_fail(self, msg: str):
        self.consecutive_failures += 1
        self.last_error = msg

    def _session_for(self, method: str) -> requests.Session:
        """幂等方法用挂了 Retry 的 session，其余用无重试 session（P1-2）。

        v4.4.0 修复（P1-2）：重试**只能**挂在 HTTPAdapter 上（requests 的
        Session.request() 没有 retries 参数，传了会 TypeError）。而 Retry 挂在
        adapter 上会作用于该 session 的全部请求，所以必须分成两个 session：
        非幂等动词（POST/PATCH/DELETE）走无重试的那个，绝不因网络抖动重发
        accept_match / bench_swap / honor_player（会造成真实副作用）。
        """
        if method.upper() in RETRY_ALLOWED_METHODS:
            return self._s_idem
        return self._s

    def _req(self, method: str, ep: str, **kw) -> requests.Response:
        if not self._c.is_connected:
            raise ConnectionError('未连接到客户端')
        # 默认超时（连接 2s / 读取 5s）：LCU 假死/防火墙弹窗时防止调用线程无限阻塞
        kw.setdefault('timeout', TIMEOUT_API)
        hdr = kw.pop('headers', {})
        hdr.setdefault('Content-Type', 'application/json')
        hdr.setdefault('Accept', 'application/json')
        # v4.1.61: 请求头声明 Accept-Encoding: identity, 尽量让 LCU 返回明文,
        # 规避腾讯 LCU 某些端点间歇性返回"gzip 声明但 body 明文"的解压炸裂问题。
        hdr.setdefault('Accept-Encoding', 'identity')
        # v4.4.0 修复（P1-2）：超时 / 连接错误不再裸抛，包一层保留原始异常文本，
        # 上层日志能区分"LCU 卡住"（Timeout，进程假死）与"端口不通"（ConnectionError，
        # 客户端已退出/换端口）—— 这两种情况的处理策略完全不同。
        try:
            r = self._session_for(method).request(
                method, f'{self._c.base_url}{ep}',
                auth=self._c.auth, headers=hdr, **kw)
        except requests.exceptions.Timeout as e:
            msg = (f'API超时 [{method} {ep}] timeout={kw.get("timeout")}: '
                   f'{type(e).__name__}: {e}')
            self._note_fail(msg)
            raise Exception(msg) from e
        except requests.exceptions.RequestException as e:
            # ConnectionError 是 RequestException 的子类；SSL/Proxy 等也落这里。
            # 不要只列 ConnectionError —— 会把其它子类漏成裸异常。
            msg = (f'API连接失败 [{method} {ep}] base={self._c.base_url}: '
                   f'{type(e).__name__}: {e}')
            self._note_fail(msg)
            raise Exception(msg) from e
        return r

    def _parse_body(self, r: requests.Response, method: str, ep: str) -> Any:
        """解析响应 JSON, 容错 Content-Encoding 头错误。

        v4.1.61 根因: 腾讯 LCU 某些端点(champ-select session)间歇性返回错误的
        Content-Encoding: gzip(实际 body 是明文 JSON), requests 解压时抛 zlib.error
        "Error -3 ... incorrect header check", 冒到选人处理 → _handle_champ_select
        提前退出 → 点卡兜底被跳过, 自动选英雄失效。此处捕获解压异常后, 重发一次
        stream 请求手动读原始字节(不解压)再 json.loads, 绕过 requests 自动解压。
        """
        try:
            return r.json()
        except Exception as first_err:
            # v4.3.10: 必须先把异常存进局部变量。Python 3 在 except 块结束时
            # 删除异常变量名, 末尾那句 `raise first_err` 会变成
            # UnboundLocalError, 把真实的解析错误(如 zlib.error)盖掉。
            parse_err = first_err
        # v4.3.10 修复: 仅对**安全/幂等**方法(GET)重发。POST/PATCH/DELETE 若
        # 首次请求实际已生效(只是响应体解析炸了), 再重发一次会重复执行副作用
        # (如 accept_match / honor_player / bench_swap 被触发两次)。非幂等方法
        # 直接抛原始解析错误, 绝不重发。
        if method.upper() not in ('GET', 'HEAD', 'OPTIONS'):
            raise parse_err
        try:
            # v4.1.64: 重发也带 Accept-Encoding: identity(否则又可能拿到
            # "声明 gzip / 实际明文"的坏响应); read 显式 decode_content=False
            # 读原始字节, 彻底绕开 requests 的自动解压。若原始字节仍解不出 JSON
            # (LCU 合法 gzip 压缩的情形), 再 gzip 解压兜底。
            hdr = {'Accept': 'application/json',
                   'Content-Type': 'application/json',
                   'Accept-Encoding': 'identity'}
            r2 = self._s.request(method, f'{self._c.base_url}{ep}',
                                 auth=self._c.auth, stream=True,
                                 headers=hdr, timeout=TIMEOUT_API)
            # v4.4.2(P1-8): 重发后先校验状态码 —— 原实现不校验, 4xx/5xx 的响应体
            # 也会被当正常 JSON 解析, 错误被错报成"解析失败"。非 2xx 直接走错误详情。
            if not (200 <= r2.status_code < 300):
                try:
                    r2.close()
                except Exception:
                    pass
                raise Exception(f'重发请求 HTTP {r2.status_code}: '
                                f'{self._err_detail(r2)}')
            try:
                raw = r2.raw.read(decode_content=False)
            finally:
                # v4.3.10: stream=True 的响应必须显式关闭, 否则连接池里的这条
                # 连接一直挂着(本分支所属端点正是 1s 轮询且已知会间歇性返回坏响应)。
                try:
                    r2.close()
                except Exception:
                    pass
            if raw:
                try:
                    return json.loads(raw.decode('utf-8', errors='replace'))
                except Exception:
                    import gzip as _gz
                    return json.loads(
                        _gz.decompress(raw).decode('utf-8', errors='replace'))
        except Exception:
            pass
        raise parse_err

    def _err_detail(self, r) -> str:
        """提取错误响应体(容错解压失败, 避免 r.text 也炸)。"""
        try:
            return r.text
        except Exception:
            return '(响应体解析失败)'

    def _cdn_get(self, url: str, timeout=TIMEOUT_CDN_GTIMG) -> requests.Response:
        """访问**非 loopback** 的第三方 CDN（P0-8）。

        v4.4.0 修复：原先第三方 CDN 也走 self._s（verify=False），等于对
        communitydragon / gtimg / ddragon 全部放弃证书校验，中间人可替换图标
        字节。现在第三方一律走独立 session 且 verify=True；LCU 本地回环请求
        （由 base_url 拼出来的）继续用 self._s（自签证书必须 verify=False）。
        """
        return _cdn_session().get(url, timeout=timeout)

    def _checked(self, r: requests.Response, method: str, ep: str) -> Optional[Any]:
        """统一处理 204 / 4xx-5xx / 正常 JSON 解析，并维护健康度计数。

        v4.4.0 修复（P1-2）：把四个动词里重复的四段判断收敛到一处，同时把
        HTTP 层失败记进 consecutive_failures（原先只在异常文本里体现，上层
        无法在不 try/except 的前提下观察连接健康）。
        **返回约定与改动前完全一致**：204 → None；>=400 → raise Exception；
        否则返回解析后的 body。上层"静默转空数据"的 try/except 行为不受影响。

        v4.4.0 修复（P1-2 语义修正）：**常规 4xx 不再计入健康度失败**。
        健康标志要回答的是"REST 到底通不通"，而 LCU 对"资源不存在"正是用
        404 回答的 —— 例：不在选人阶段时 /lol-champ-select/v1/session 返回
        404、战绩还没入库时 /lol-match-history/v1/games/{id} 返回 404，
        这些都是**完全正常的业务答复**（服务端在线且响应了），若一并计入
        连续失败，is_healthy 在正常游戏过程中就会频繁翻假，这个标志也就
        失去了报告里"区分『没有选人会话』与『LCU 挂了/鉴权错』"的意义。
        现在只把 **5xx（网关没就绪/后端崩）与 401/403（token 过期、鉴权错）**
        计为失败；其余 4xx 视为"服务端已应答"。
        注意：raise 行为一字未改，调用方的异常处理路径不变。
        """
        if r.status_code == 204:
            self._note_ok()
            return None
        if r.status_code >= 400:
            msg = f'API错误 [{r.status_code}]: {self._err_detail(r)}'
            if r.status_code >= 500 or r.status_code in (401, 403):
                self._note_fail(msg)
            else:
                # 4xx 业务性答复：REST 通道是好的，不能算"连接不健康"
                self._note_ok()
            raise Exception(msg)
        try:
            body = self._parse_body(r, method, ep)
        except Exception as e:
            # 解析失败也算一次失败（LCU 返回坏响应同样是不可用信号）
            self._note_fail(f'API响应解析失败 [{method} {ep}]: '
                            f'{type(e).__name__}: {e}')
            raise
        self._note_ok()
        return body

    def get(self, ep: str, **kw) -> Any:
        return self._checked(self._req('GET', ep, **kw), 'GET', ep)

    def post(self, ep: str, data=None, **kw) -> Any:
        if data is not None and not isinstance(data, str):
            data = json.dumps(data)
        return self._checked(self._req('POST', ep, data=data, **kw), 'POST', ep)

    def patch(self, ep: str, data=None, **kw) -> Any:
        # v4.4.2(P2-10): 有意保留(协议完整性) —— REST 客户端补全 PATCH 动词。
        # 项目内零调用, 但 Riot LCU 确有 PATCH 端点, 删掉会让动词族不完整。
        if data is not None and not isinstance(data, str):
            data = json.dumps(data)
        return self._checked(self._req('PATCH', ep, data=data, **kw), 'PATCH', ep)

    def delete(self, ep: str, **kw) -> Any:
        return self._checked(self._req('DELETE', ep, **kw), 'DELETE', ep)

    # ==================== 召唤师 ====================
    def get_current_summoner(self) -> Dict:
        """当前召唤师信息：displayName / summonerLevel / profileIconId /
        percentCompleteForNextLevel 等"""
        return self.get('/lol-summoner/v1/current-summoner')

    def get_current_ranked_stats(self) -> Dict:
        """当前赛季排位数据。

        queueMap.RANKED_SOLO_5x5 / RANKED_FLEX_SR 各含
        tier(GOLD/PLATINUM...)/division(I-IV)/leaguePoints;
        queueMap 为空 = 本模式未定级。
        """
        return self.get('/lol-ranked/v1/current-ranked-stats')

    def get_honor_profile(self) -> Dict:
        """荣誉档案：honorLevel 0-5"""
        return self.get('/lol-honor-v2/v1/profile')

    def get_wallet(self) -> Dict:
        """账号资产余额:蓝精粹/橙色精粹/神话精粹/RP。

        v4.1.43 修正:腾讯 LCU 没有 /lol-store/v1/wallet(实测 404),真实数据在
        /lol-loot/v1/player-loot 中 type=CURRENCY 的项。**关键字段是 count 不是 value**
        (value 是每个单位的精粹价值,count 才是余额)。返回结构:
            {'ip':258477,'cosmetic':1254,'mythic':56,'rp':0}
        RP 在腾讯服不计费(原 LOL 客户端无 RP),固定 0。"""
        try:
            loot = self.get('/lol-loot/v1/player-loot') or []
        except Exception:
            return {'ip': 0, 'cosmetic': 0, 'mythic': 0, 'rp': 0}
        out = {'ip': 0, 'cosmetic': 0, 'mythic': 0, 'rp': 0}
        if not isinstance(loot, list):
            return out
        for item in loot:
            if item.get('type') != 'CURRENCY':
                continue
            ln = item.get('lootName') or ''
            try:
                cnt = int(item.get('count') or 0)
            except (TypeError, ValueError):
                cnt = 0
            if ln == 'CURRENCY_champion':
                out['ip'] = cnt           # 蓝精粹
            elif ln == 'CURRENCY_cosmetic':
                out['cosmetic'] = cnt     # 橙色精粹
            elif ln == 'CURRENCY_mythic':
                out['mythic'] = cnt       # 神话精粹
        return out

    def get_champion_mastery(self, summoner_id: int) -> List[Dict]:
        """英雄熟练度列表(全英雄)。

        v4.1.47 修正(对照用户游戏截图): 之前 v4.1.43 用 championSeasonMilestone
        (本赛季里程碑 1-7) 当等级, 截图显示游戏里写的是"X 级英雄成就"(37/72/16),
        数值对不上。实测 LCU mastery 字段:
          - championLevel         = 英雄成就等级(截图中 37/72/16) ← 用户最熟悉的数字
          - championPoints        = 熟练度点数
          - championSeasonMilestone = 本赛季里程碑 1-7
          - highestGrade          = 最高评分 S+/S/A/B
        排序键用 championLevel(对应游戏"英雄成就"页面"最高英雄成就"展示),
        返回每项: {championId, level(int 英雄成就等级), points, grade}
        """
        try:
            ms = self.get('/lol-champion-mastery/v1/local-player/champion-mastery')
        except Exception:
            return []
        out = []
        if not isinstance(ms, list):
            return out
        for m in ms:
            try:
                cid = int(m.get('championId') or 0)
            except (TypeError, ValueError):
                cid = 0
            if not cid:
                continue
            try:
                pts = int(m.get('championPoints') or 0)
            except (TypeError, ValueError):
                pts = 0
            # v4.1.47: 等级字段改用 championLevel(= 游戏"X 级英雄成就")
            lv = m.get('championLevel')
            try:
                lv_i = int(lv) if lv is not None else 0
            except (TypeError, ValueError):
                lv_i = 0
            grade = (m.get('highestGrade') or '').strip() or ''
            out.append({'championId': cid,
                        'level': lv_i,
                        'points': pts,
                        'grade': grade})
        # v4.1.47: 按英雄成就等级降序(与游戏"最高英雄成就"排序一致)
        out.sort(key=lambda x: (x['level'], x['points']), reverse=True)
        return out

    def get_recent_matches(self, puuid: str, count: int = 10) -> Dict:
        """最近对局(games.games 数组)。"""
        return self.get(
            f'/lol-match-history/v1/products/lol/{puuid}/matches'
            f'?begIndex=0&endIndex={int(count)}')

    def download_profile_icon(self, icon_id: int) -> Optional[bytes]:
        """下载召唤师头像 bytes（首页账号卡用）。

        v4.1.39 修复:旧腾讯 CDN(act/img/profileicon)头像库 2023 年起停更,
        仅覆盖到 ~5xxx 号段,新头像 id(如 7084)一律 404 —— 这就是 v4.1.37
        实测"CDN 404"的根因。现改用 CommunityDragon(latest 全量 1..最新,
        profile-icons 复数连字符 + .jpg),大陆直连可达;腾讯旧 CDN 仅作末位
        兜底(老号段还有资源)。LCU 本地资产仍是第一优先(离线可用)。
        """
        # 1) LCU 本地资产(离线):部分客户端版本 .jpg 有的是 .png,逐个尝试
        if self._c.is_connected:
            for ext in ('.jpg', '.png'):
                try:
                    url = (f'{self._c.base_url}/lol-game-data/assets/v1/'
                           f'profileicons/{int(icon_id)}{ext}')
                    r = self._s.get(url, auth=self._c.auth, timeout=5)
                    if r.status_code == 200 and len(r.content) > 200:
                        return r.content
                except Exception:
                    continue
        # 2) CommunityDragon 全量头像库(2026-09-09 实测对 7084 HTTP 200)
        try:
            url = (f'https://raw.communitydragon.org/latest/plugins/'
                   f'rcp-be-lol-game-data/global/default/v1/'
                   f'profile-icons/{int(icon_id)}.jpg')
            # v4.4.0 修复（P0-8）：第三方主机 → 独立 verify=True session
            r = self._cdn_get(url, timeout=TIMEOUT_CDN_CDRAGON)
            if r.status_code == 200 and len(r.content) > 200:
                return r.content
        except Exception:
            pass
        # 3) 腾讯旧 CDN(仅老号段有图,末位兜底)
        try:
            url = (f'https://game.gtimg.cn/images/lol/act/img/'
                   f'profileicon/{int(icon_id)}.png')
            # v4.4.0 修复（P0-8）：第三方主机 → 独立 verify=True session
            r = self._cdn_get(url, timeout=TIMEOUT_CDN_GTIMG)
            if r.status_code == 200 and len(r.content) > 200:
                return r.content
        except Exception:
            pass
        return None

    # ==================== 游戏流程 ====================
    def get_gameflow_phase(self) -> str:
        return self.get('/lol-gameflow/v1/gameflow-phase')

    # ==================== 匹配 ====================
    def start_matchmaking(self):
        self.post('/lol-lobby/v2/lobby/matchmaking/search')

    def stop_matchmaking(self):
        try:
            self.delete('/lol-lobby/v2/lobby/matchmaking/search')
        except Exception:
            pass

    def get_matchmaking_search_state(self) -> Dict:
        return self.get('/lol-lobby/v2/lobby/matchmaking/search-state')

    # ==================== 接受/拒绝 ====================
    def accept_match(self):
        self.post('/lol-matchmaking/v1/ready-check/accept')

    def decline_match(self):
        self.post('/lol-matchmaking/v1/ready-check/decline')

    def get_ready_check(self) -> Optional[Dict]:
        try:
            return self.get('/lol-matchmaking/v1/ready-check')
        except Exception:
            return None

    # ==================== 对局结束 ====================
    def play_again(self):
        self.post('/lol-lobby/v2/play-again')

    # ==================== 自动点赞 ====================
    def get_honor_ballot(self) -> Optional[Dict]:
        """获取可点赞的玩家列表"""
        try:
            return self.get('/lol-honor-v2/v1/ballot')
        except Exception:
            return None

    def honor_player(self, puuid: str, summoner_id: int, game_id: int, honor_category: str) -> bool:
        """点赞玩家 (HEART/COOL/SHOTCALLER)"""
        try:
            self.post('/lol-honor-v2/v1/honor-player', data={
                'puuid': puuid,
                'summonerId': summoner_id,
                'gameId': game_id,
                'honorCategory': honor_category,
            })
            return True
        except Exception:
            return False

    # ==================== 英雄选择 ====================
    def get_champ_select_session(self) -> Optional[Dict]:
        try:
            return self.get('/lol-champ-select/v1/session')
        except Exception:
            return None

    def pick_champion(self, candidates: list) -> object:
        """v30 自动选英雄：按偏好顺序逐个试锁(试错法), 返回锁定的 championId 或 None。

        v24-v29 六版全灭的根因(多轮监听实验证实): 本局屏幕那 2-3 张卡 = 服务器下发的
        "本局允许子集", LCU 的 REST 端点读不到(候选端点要么空, 要么返回账号全量英雄);
        服务器对"非允许英雄"的 PATCH/POST 一律回 204 但静默忽略(action 永不翻转)。
        此前每次都只试偏好第一名, 它不在当局卡里就永远锁不上。

        v30 试错法: 把整个偏好列表交进来逐个试, 每次 read-back 验证 action 的
        championId/completed 是否真的翻转 —— 翻转 = 该英雄在本局允许子集里 → 锁定返回。
        全部未翻转 = 当局 2-3 张卡里没有偏好 → 返回 None(不硬锁, 尊重"只在偏好里选")。
        非允许英雄反复试锁无副作用(204 静默忽略), 也不会误锁。
        5v5 回合制: 未轮到我时首个 PATCH 通常 4xx → 本轮放弃, 下轮轮到再锁。

        v4.4.2(P1-3/P2-44): 加候选截断(PICK_TRY_MAX)与总时限(PICK_TRY_DEADLINE_S);
        单候选失败改 continue(可能是该候选特有的 204/4xx), 只有超时或试完才返回,
        不再因首个候选的 PATCH 异常/非 2xx 就放弃整个列表。
        """
        import json as _json
        import time as _t
        try:
            s = self.get_champ_select_session()
            if not s:
                return None
            local_cell = s.get('localPlayerCellId')
            actions = s.get('actions') or []
            is_legacy = s.get('isLegacyChampSelect', True) is not False
            prefix = ('/lol-champ-select/v1/session'
                      if is_legacy else
                      '/lol-lobby-team-builder/champ-select/v1/session')

            # 找我的第一个未完成的 pick action
            target_id = None
            for group in actions:
                if not isinstance(group, list):
                    group = [group]
                for a in group:
                    if (a.get('type') == 'pick'
                            and a.get('actorCellId') == local_cell
                            and not a.get('completed')):
                        target_id = a.get('id')
                        break
                if target_id is not None:
                    break
            if target_id is None:
                print('[选人] v30 尚未就绪(找不到我的未完成 pick action), 下轮重试')
                return None

            def _read_my_action():
                s2 = self.get_champ_select_session()
                if not s2:
                    return None
                for g in (s2.get('actions') or []):
                    if not isinstance(g, list):
                        g = [g]
                    for a in g:
                        if (a.get('type') == 'pick'
                                and a.get('actorCellId') == local_cell):
                            return a
                return None

            def _locked(a, cid):
                return bool(a and a.get('completed')
                            and a.get('championId') == cid)

            _deadline = _t.monotonic() + PICK_TRY_DEADLINE_S
            _tried = 0
            for cid in candidates:
                if not cid:
                    continue
                if _tried >= PICK_TRY_MAX:
                    print(f'[选人] v30 已达候选上限 {PICK_TRY_MAX}, 本轮放弃')
                    break
                if _t.monotonic() > _deadline:
                    print('[选人] v30 达到总时限, 本轮放弃(等下轮)')
                    break
                _tried += 1
                # 载荷A: 单步 PATCH {championId, completed:True}
                try:
                    r1 = self._req('PATCH', f'{prefix}/actions/{target_id}',
                                   data=_json.dumps({'championId': cid,
                                                     'completed': True}))
                except Exception as e:
                    print(f'[选人] v30 cid={cid} PATCH 异常: '
                          f'{type(e).__name__}: {e}')
                    continue
                if not (200 <= r1.status_code < 300):
                    # v4.4.2(P2-44): 4xx/5xx 可能是该候选特有的(非可操作时机/
                    # 不在本局子集), 不再整体放弃, 试下一个候选。
                    print(f'[选人] v30 cid={cid} PATCH HTTP {r1.status_code} '
                          f'(该候选未接受), 试下一个')
                    continue
                _t.sleep(0.08)
                a = _read_my_action()
                if _locked(a, cid):
                    print(f'[选人] v30 ✅ 自动锁 {cid}: 单步 PATCH HTTP '
                          f'{r1.status_code}, action 翻转为 done')
                    return cid
                if a and a.get('championId') == cid and not a.get('completed'):
                    # 预选被接受但未 done → 补 POST complete
                    try:
                        r2 = self._req('POST',
                                       f'{prefix}/actions/{target_id}/complete')
                        _t.sleep(0.08)
                        a = _read_my_action()
                        if _locked(a, cid):
                            print(f'[选人] v30 ✅ 自动锁 {cid}: 单步PATCH+complete '
                                  f'HTTP {r1.status_code}/{r2.status_code}')
                            return cid
                    except Exception as e:
                        print(f'[选人] v30 cid={cid} complete 异常: '
                              f'{type(e).__name__}: {e}')
                # 载荷B: 两步 PATCH {championId} + POST complete
                try:
                    r1 = self._req('PATCH', f'{prefix}/actions/{target_id}',
                                   data=_json.dumps({'championId': cid}))
                except Exception as e:
                    print(f'[选人] v30 cid={cid} PATCH 异常: '
                          f'{type(e).__name__}: {e}')
                    continue
                if 200 <= r1.status_code < 300:
                    _t.sleep(0.05)
                    a = _read_my_action()
                    if _locked(a, cid):
                        print(f'[选人] v30 ✅ 自动锁 {cid}: 两步 PATCH HTTP '
                              f'{r1.status_code}, 已 done')
                        return cid
                    if a and a.get('championId') == cid and not a.get('completed'):
                        try:
                            r2 = self._req('POST',
                                           f'{prefix}/actions/{target_id}/complete')
                            _t.sleep(0.08)
                            a = _read_my_action()
                            if _locked(a, cid):
                                print(f'[选人] v30 ✅ 自动锁 {cid}: 两步PATCH+complete '
                                      f'HTTP {r1.status_code}/{r2.status_code}')
                                return cid
                        except Exception:
                            pass
                # 走到这里 = 该候选 204 静默忽略(不在本局允许集) → 试下一个
            print(f'[选人] v30 试完候选均未被本局接受'
                  f'(2-3 张卡里无偏好) → 不硬锁')
            return None
        except Exception as e:
            print(f'[选人] v30 异常: {type(e).__name__}: {e}')
            return None

    def bench_swap(self, champion_id: int) -> Optional[bool]:
        """ARAM 备选池换英雄，带 read-back 验证（非阻塞版）。

        v4.4.2(P2-34) 返回值语义（与 docstring 对齐）：
          True  = 已确认换到目标英雄（read-back 命中）
          False = POST 抛异常，明确失败
          None  = POST 没报错，但 read-back 读不到 / 读回的不是目标英雄
                  （未知，不能谎报成功——含慢同步下"其实已生效"的情形）
        两个调用方都已显式处理 None（v4.4.0 同步）：
          - `core/script_core.py` 自动秒换：None 单独打"结果未验证"并返回，
            不再冒充"API返回False"
          - `core/script_core.py` 手动换英雄：签名仍为 -> bool，None 记状态栏
            "换英雄结果未确认"并返回 False
        """
        is_legacy = True
        try:
            s = self.get_champ_select_session()
            if s is not None:
                is_legacy = s.get('isLegacyChampSelect', True) is not False
        except Exception:
            pass

        try:
            if is_legacy:
                self.post(f'/lol-champ-select/v1/session/bench/swap/{champion_id}')
            else:
                self.post(f'/lol-lobby-team-builder/champ-select/v1/session/bench/swap/{champion_id}')
        except Exception as e:
            # v41: 与上方 get 的容错对齐, 连接中断不向上抛
            print(f'[选人] bench_swap POST 异常: {type(e).__name__}: {e}')
            return False

        # 非阻塞 read-back：只检查一次当前状态
        try:
            import time as _t
            _t.sleep(0.1)
            s = self.get_champ_select_session()
            if s:
                local_cell = s.get('localPlayerCellId')
                for p in s.get('myTeam', []):
                    if p.get('cellId') == local_cell:
                        my = p.get('championId')
                        # v4.4.2(P2-34): 原 `return my == champion_id` 与 docstring
                        # 矛盾 —— 慢同步下"真的换成了但读回还没刷新"会被误报成
                        # "API 明确拒绝(False)", 且消费方不更新 _last_swap。
                        # 改为: 读回命中 → True; 读回成别的英雄 → None(未验证,
                        # 可能是慢同步); 只有 POST 异常才返回 False。
                        if my == champion_id:
                            return True
                        return None
        except Exception:
            pass
        # v4.4.0 修复（P1-3）：原为 return True。读回校验失败/读不到会话时
        # 真实结果未知，返回 None 让上层决定（绝不谎报成功）。
        return None

    # ==================== 英雄数据 ====================
    def get_champion_data(self) -> List[Dict]:
        """取全部英雄基础数据(选人界面/英雄选择器用)。

        v4.4.0 修复(P1-2 残留): 原实现两层 `except Exception` 最终静默
        `return []`, 调用方无法区分"该客户端没有这份数据"与"LCU 连不上/
        超时", 会把网络故障当成空列表吞掉(表现为英雄选择器永远"加载中")。

        现在: 首选端点失败→打印真实原因并回退到旧端点; 旧端点也失败→
        记下 last_error 后**抛出**, 由调用方决定提示/重试, 不再谎报空表。
        (两个调用方 `main.py` 后台刷新线程与 `core/script_core._load_champ_data`
        都已用 try/except 包住并有各自日志, 传播安全。)
        """
        try:
            return self.get('/lol-game-data/assets/v1/champion-summary.json')
        except Exception as e:
            print(f'[API] champion-summary.json 取用失败({type(e).__name__}: {e}), '
                  f'回退 owned-champions-minimal')
            return self.get('/lol-champions/v1/owned-champions-minimal')

    def download_item_icon(self, item_id: int) -> Optional[bytes]:
        """下载装备图标 bytes(对局详情装备栏用)。

        LCU 本地没有"按 id 直取"的装备图路径(items.json 的 iconPath 带中文后缀,
        如 ASSETS/Items/Icons2D/1001_Boots.png), 要额外拉一份 1MB+ 的 items.json
        才能解析; 实测腾讯 CDN 按 id 直取即可(https://game.gtimg.cn/images/lol/
        act/img/item/1001.png → 200 image/png), 大陆直连可达, 故直接用 CDN。

        v4.4.2(P2-38): 删除 ddragon 兜底 —— 原 URL 把 CDN 版本钉死成 14.1.1,
        对版本更高才上线的新装备 id 必然 404, 而它又是兜底链路的最后一环, 等于
        新装备永远显示空槽; 且该兜底只有在 gtimg 也失败时才走到, 保留一个"新装备
        必 404"的兜底没有意义。gtimg 按 id 直取即可, 无版本号问题。

        返回 None 表示源拿不到(新装备/6 位特殊装备 id), UI 显示空槽位。
        """
        try:
            iid = int(item_id)
        except (TypeError, ValueError):
            return None
        if iid <= 0:
            return None
        url = f'https://game.gtimg.cn/images/lol/act/img/item/{iid}.png'
        try:
            # v4.4.0 修复（P0-8）：gtimg 是第三方主机，
            # 必须走 verify=True 的独立 session（原先复用 LCU 的 verify=False）
            r = self._cdn_get(url, timeout=TIMEOUT_ASSET)
            if r.status_code == 200 and len(r.content) > 100:
                return r.content
        except Exception:
            pass
        return None

    # ---------------- v4.3.2: 召唤师技能 / 海克斯强化 ----------------
    # 两者都能从 LCU 本地资源直接取图(不走外网, 最快最稳):
    #   技能表   /lol-game-data/assets/v1/summoner-spells.json (39 条)
    #            → [{'id':4,'name':'闪现','iconPath':'/lol-game-data/assets/DATA/
    #               Spells/Icons2D/Summoner_flash.png'}, ...]
    #   海克斯表 /lol-game-data/assets/v1/cherry-augments.json (552 条, 含竞技场/
    #            海克斯大乱斗全部强化) → id / nameTRA / augmentSmallIconPath
    #            ⚠️ `/augments.json`(不带 cherry-) 会 400, 必须用 cherry-augments。
    #            ⚠️ KIWI 特有强化(如 1151 面包和奶酪)iconPath 指向 Kiwi/Augments
    #               目录, 同样在本地资源里, 一并可取。
    # 映射表与图标都在进程内缓存 —— 表只拉一次。

    _SPELL_TABLE = None      # {id: (中文名, iconPath)}
    _AUGMENT_TABLE = None    # {id: (中文名, iconPath)}
    # v4.3.10: 保护两张类级表的并发加载。首次加载含网络请求(连接2s/读5s),
    # 详情弹窗可能多行同时触发 spell_info/augment_info → 多个线程并发进入
    # _load_*_table, 无锁时可能重复拉取同一份表并竞争写类级缓存。
    _TABLE_LOCK = threading.Lock()

    def _load_spell_table(self):
        if LCUAPI._SPELL_TABLE is not None:
            return LCUAPI._SPELL_TABLE
        with LCUAPI._TABLE_LOCK:
            # 双检: 拿到锁后另一线程可能已完成加载
            if LCUAPI._SPELL_TABLE is not None:
                return LCUAPI._SPELL_TABLE
            table = {}
            try:
                for s in self.get('/lol-game-data/assets/v1/summoner-spells.json') or []:
                    try:
                        sid = int(s.get('id') or 0)
                    except (TypeError, ValueError):
                        continue
                    icon = str(s.get('iconPath') or '')
                    if sid > 0 and icon:
                        table[sid] = (str(s.get('name') or ''), icon)
            except Exception as e:
                # v4.3.10 修复: 失败时**不能写类级缓存**。原实现无论成败都执行
                # `LCUAPI._SPELL_TABLE = table`, 而守卫是 `is not None` —— 首次调用
                # 若赶上 LCU 忙/网络抖动, 空表被当成"已加载"永久缓存, 本进程内
                # 再也不重试 → 详情弹窗的技能名称与图标整个会话都是空的。
                print(f'[详情] 召唤师技能表读取失败(不缓存, 下次重试): '
                      f'{type(e).__name__}: {e}')
                return table
            if not table:
                # 拿到了但为空(客户端尚未就绪) → 同样不缓存, 让下次调用重试
                return table
            LCUAPI._SPELL_TABLE = table
            return table

    def _load_augment_table(self):
        if LCUAPI._AUGMENT_TABLE is not None:
            return LCUAPI._AUGMENT_TABLE
        with LCUAPI._TABLE_LOCK:
            # 双检: 拿到锁后另一线程可能已完成加载
            if LCUAPI._AUGMENT_TABLE is not None:
                return LCUAPI._AUGMENT_TABLE
            table = {}
            try:
                for a in self.get('/lol-game-data/assets/v1/cherry-augments.json') or []:
                    try:
                        aid = int(a.get('id') or 0)
                    except (TypeError, ValueError):
                        continue
                    icon = str(a.get('augmentSmallIconPath') or '')
                    if aid > 0 and icon:
                        table[aid] = (str(a.get('nameTRA') or ''), icon)
            except Exception as e:
                # v4.3.10: 同 _load_spell_table —— 失败/空表都不写类级缓存, 保留重试机会
                print(f'[详情] 海克斯强化表读取失败(不缓存, 下次重试): '
                      f'{type(e).__name__}: {e}')
                return table
            if not table:
                return table
            LCUAPI._AUGMENT_TABLE = table
            return table

    def spell_info(self, spell_id) -> tuple:
        """技能 id → (中文名, iconPath); 查不到返回 ('', '')。"""
        try:
            return self._load_spell_table().get(int(spell_id), ('', ''))
        except (TypeError, ValueError):
            return ('', '')

    def augment_info(self, augment_id) -> tuple:
        """海克斯 id → (中文名, iconPath); 查不到返回 ('', '')。"""
        try:
            return self._load_augment_table().get(int(augment_id), ('', ''))
        except (TypeError, ValueError):
            return ('', '')

    def download_lcu_asset(self, path: str, timeout=(2.0, 6.0)) -> Optional[bytes]:
        """按 LCU 本地资源路径取图(技能/海克斯图标等)。

        path 来自各类表的 iconPath 字段, 形如
        `/lol-game-data/assets/DATA/Spells/Icons2D/Summoner_flash.png` ——
        直接拼到 base_url 上用同一个已鉴权 session 取(实测 200 image/png)。
        """
        if not path or not self._c.is_connected:
            return None
        try:
            r = self._s.get(self._c.base_url + path, auth=self._c.auth,
                            timeout=timeout)
            if r.status_code == 200 and len(r.content) > 100:
                return r.content
        except Exception:
            pass
        return None

    def download_champion_icon(self, champion_id: int) -> Optional[bytes]:
        # 优先从 LCU 客户端下载
        try:
            if self._c.is_connected:
                url = f'{self._c.base_url}/lol-game-data/assets/v1/champion-icons/{champion_id}.png'
                r = self._s.get(url, auth=self._c.auth, timeout=5)
                if r.status_code == 200:
                    return r.content
        except Exception:
            pass
        # CDN 备用 —— 客户端没启动时唯一可用的来源, 必须走通。
        # ⚠️ v4.3.9 修: 原 URL 是 /act/img/js/champion/{数字id}.png, 实测**恒 404**
        #    (js 前缀是抄错了数据接口的路径, 且该接口只认**英雄英文别名**不认数字 id)。
        #    结果: 凡是"缓存为空 + 客户端没启动"的场景图标全变灰, 且永远补不回来 ——
        #    因为唯一的备用源是死的, 只有开客户端才能下载。改用别名后实测 173/173 命中。
        try:
            alias = _champion_alias(champion_id)
            if alias:
                url = f'https://game.gtimg.cn/images/lol/act/img/champion/{alias}.png'
                # v4.4.0 修复（P0-8）：第三方主机 → 独立 verify=True session
                r = self._cdn_get(url, timeout=TIMEOUT_CDN_GTIMG)
                if r.status_code == 200 and len(r.content) > 100:
                    return r.content
        except Exception:
            pass
        return None
