"""LCU Connector - 查找英雄联盟客户端进程，获取连接参数

查找策略（跨电脑通用，不依赖固定安装路径/盘符）:
1. 从运行中的 LeagueClient 进程命令行解析 app-port/remoting-auth-token
   （wmic 优先 → PowerShell 兜底；只要客户端在运行且命令行为同权限可见，
    一定能拿到，不依赖装在哪里）
2. 从进程 exe 路径向上逐层找 lockfile（精确探测，不猜目录名）
3. RiotClientInstalls.json / 注册表 → 安装目录 → lockfile
4. 常见安装根目录快扫 lockfile（固定根名，快路径）
5. 真·全盘 lockfile 扫描（每盘 BFS + 目录名启发式剪枝 + 遍历预算，
   覆盖"自定义游戏库目录/任意盘符/任意目录名"场景 —— 换电脑的最终兜底）
6. 从 LeagueClientUx 日志读取 app-port/remoting-auth-token
7. psutil（仅开发模式，兜底）

设计要点：
    - 来源可信度：进程命令行 > lockfile > 日志。三者解析出 (port, token) 后
    **信任并接受**——但日志兜底路径 v21 修复为立即 REST 探活校验,避免
    "脚本先启动、LOL 后启动"场景下拿历史日志里的过期值反复 WS 10061。
    UI 周期探活继续负责已连接后的真伪校准与自动重置。
- lockfile 预检内容前缀必须是 'LeagueClient'，避免对无关软件的 lockfile
  发网络请求。
- 进程查询合并为一次调用（同时匹配两个进程名），不再为每个名字各 spawn
  一次 PowerShell。
"""

import subprocess
import sys
import os
import re
import json
from collections import deque
from typing import Optional, Tuple, List, Dict

if sys.platform == 'win32':
    _CREATE_NO_WINDOW = 0x08000000
    _STARTUP = subprocess.STARTUPINFO()
    _STARTUP.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUP.wShowWindow = 0
    _KW = {'creationflags': _CREATE_NO_WINDOW, 'startupinfo': _STARTUP}
else:
    _KW = {}

import requests
import urllib3
# v4.4.2(P2-35): 原 `urllib3.disable_warnings()` 无参 = 进程级关掉**全部**
# InsecureRequestWarning, 波及 verify=True 的第三方 CDN session, 将来某个主机
# 证书异常会完全无声。只关本文件 loopback LCU 需要的那一类。
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# v4.4.0 修复（P2）：超时/预算/缓存时长等魔数集中到 lcu/constants.py
from .constants import (
    TIMEOUT_PROBE, TIMEOUT_WMIC, TIMEOUT_PS,
    BFS_BUDGET, BFS_MAX_DEPTH, WALK_MAX_DEPTH,
)


def make_session(verify: bool = False) -> requests.Session:
    """创建统一配置的 requests.Session。

    v4.4.0 修复（P2）：原先 connector.py:48-51 与 api.py:44-48 各写一份
    Session 配置（verify/trust_env），两处必然漂移 —— 例如 v20 清代理那次
    只改了 connector 侧。收敛到本工厂，两个模块复用同一份语义。

    verify=False: 仅用于 LCU 本地回环地址（自签证书），**绝不可**用于
    第三方主机；第三方一律 verify=True（见 api.py 的 _cdn_session）。
    trust_env=False: LCU 目标恒为 127.0.0.1，绝不读系统代理（本地抓包/加速器
    会设置 HTTP_PROXY，曾导致 REST 卡在代理隧道数秒 + WS 反复 502）。
    """
    s = requests.Session()
    s.verify = bool(verify)
    s.trust_env = False
    return s


# 共享Session，复用TCP连接（仅用于 LCU 回环地址）
_shared_session = make_session(verify=False)

# LCU 相关进程名（两个都匹配，覆盖官方版 / WeGame 版 / Riot Client 版）
_LCU_PROC_NAMES = "Name='LeagueClientUx.exe' or Name='LeagueClient.exe'"

# 全盘扫描时，命中即深挖的目录名关键词（小写匹配）
_HOT_DIR = ('league', 'lol', 'wegame', 'wegam', 'riot', 'tencent', 'qqgame',
            'hero', '英雄', '腾讯', '游戏', 'client', 'game')
# 盘根一级目录直接跳过（系统/无用目录）
_SKIP_TOP = ('windows', 'program files', 'program files (x86)', 'programdata',
             '$recycle.bin', 'system volume information', 'recovery',
             'perflogs', 'msocache', 'intel', 'amd', 'nvidia',
             'documents and settings', 'boot', 'sources', 'python',
             'nodejs', 'workbuddy', '.workbuddy', 'appdata')


class LCUConnector:
    def __init__(self):
        self._port: Optional[int] = None
        self._token: Optional[str] = None
        self._pid: Optional[int] = None
        # v4.4.0 修复：全盘 BFS 上限允许注入覆盖（默认值见 lcu/constants.py）
        self.bfs_budget: int = BFS_BUDGET
        self.bfs_max_depth: int = BFS_MAX_DEPTH

    @property
    def is_connected(self) -> bool:
        return self._port is not None and self._token is not None

    @property
    def base_url(self) -> str:
        return f'https://127.0.0.1:{self._port}' if self._port else ''

    @property
    def port(self) -> Optional[int]:
        """v4.4.2(P2-9): LCU 端口的只读公开入口 —— 原来 websocket_listener 直读
        私有 `_port`(跨文件读私有属性, 破坏封装且 grep 困难)。"""
        return self._port

    @property
    def auth(self) -> Tuple[str, str]:
        return ('riot', self._token or '')

    def reset(self):
        self._port = self._token = self._pid = None

    def _accept(self, why: str, validate: bool = True) -> bool:
        """统一"接受本次发现的连接参数"出口。

        v4.4.0 修复（P1-4）：原先进程命令行 / exe 反推 lockfile 等路径一解析出
        (port, token) 就 `return True`，**不做探活**。实际遇到过 LeagueClient
        进程是上一次残留（或参数被别的工具覆写）时，拿到死端口却报"已连接"，
        后续所有 REST/WS 全部失败。改为：解析成功先跑一次已有的
        _validate_cached_connection()（超时 (1.5, 2.0)，见 constants），
        通过才接受；不通过则 reset 并返回 False，让调用方继续尝试下一级方法。

        validate=False 仅用于"探活本就不适用"的场合（如进程刚被枚举到时
        客户端可能正在启动中）——保持与原行为一致，仍返回 True。
        """
        if not self.is_connected:
            return False
        if not validate or self._validate_cached_connection():
            return True
        print(f'[连接] {why} 参数探活失败(port={self._port}),继续尝试其它方法')
        self.reset()
        return False

    def _validate_cached_connection(self) -> bool:
        """v14:验证缓存的 (port, token) 是否仍能连上 LOL 客户端。

        用于 find_client 入口短路前的真伪判定：避免"日志兜底拿到旧参数"或
        "LOL 重启后端口变化"场景下,大蛋小助手拿着过期参数反复 WS 失败。
        """
        try:
            r = _shared_session.get(
                f'{self.base_url}/lol-summoner/v1/current-summoner',
                auth=self.auth, timeout=TIMEOUT_PROBE,
            )
            # v4.4.2(P2-8): 原只认 200 —— LCU 启动期/网关未就绪返回 5xx, 或重定向
            # 类 3xx 都判缓存失效并 reset() 触发全盘重扫。接受 2xx/3xx 更稳。
            return 200 <= r.status_code < 400
        except Exception:
            return False

    # ==================== 主入口 ====================
    def find_client(self, quick: bool = False) -> bool:
        """查找并保存 LCU 连接参数。

        quick=True:只走轻量方法（进程/注册表/固定根），供主线程"点启动"时调用，
                    避免全盘扫描卡 UI；失败由后台 5s 重连定时器继续完整查找。
        quick=False:完整方法链，供后台重连线程调用。

        v14 关键修复：旧参数(尤其是日志兜底残留的过期 port/token)必须先做
        REST 探活校验才能短路返回。否则会出现"先开脚本后开 LOL"场景下，
        拿着旧参数反复 WS 10061 拒绝,但 is_connected 仍 True,UI 显示
        未连接但脚本能启动的撕裂 bug。
        """
        if self._port and self._token:
            if self._validate_cached_connection():
                return True  # 缓存参数仍可用,直接接受
            # 缓存参数已失效(LOL 重启/换端口/换 token),重置走完整链路
            print(f'[连接] 缓存参数已失效(port={self._port}),重新发现')
            self.reset()

        # 1. 进程命令行（最通用：不依赖安装路径/文件）
        if sys.platform == 'win32':
            procs = self._query_lcu_processes()
            for proc in procs:
                args = proc.get('cmdline')
                # v4.1.64: 只解析一次(原代码 if 判断与解包各调一次 _parse_cmdline,
                # 重复跑正则)
                parsed = self._parse_cmdline(args) if args else None
                if parsed:
                    self._port, self._token, self._pid = parsed
                    port, token, pid = parsed
                    # v4.4.0 修复（P1-4）：解析成功不再无条件 return True，
                    # 先探活；失败则丢弃参数继续看下一个进程/下一级方法
                    if self._accept('进程命令行'):
                        print(f'[连接] 进程命令行成功: port={port}, pid={pid or proc.get("pid")}')
                        return True
                    continue
                exe = proc.get('exe')
                if exe and self._find_lockfile_near_exe(exe):
                    # v4.4.0 修复（P1-4）：lockfile 解析出参数后同样先探活
                    if self._accept('exe 反推 lockfile'):
                        print(f'[连接] 进程 exe 反推 lockfile 成功: {exe}')
                        return True

        # 2. Riot 官方安装信息文件
        if self._find_by_riot_installs() and self._accept('RiotClientInstalls'):
            return True

        # 3. 注册表
        if sys.platform == 'win32' and self._find_by_registry() and \
                self._accept('注册表'):
            return True

        # 4. 常见安装根固定路径快扫（quick 模式只扫 LOCALAPPDATA, 不遍历盘符）
        if self._find_lockfile_fixed_roots(quick=quick) and self._accept('固定根快扫'):
            return True

        # 5. 真·全盘 lockfile 扫描（慢，仅完整模式）
        if not quick and self._scan_all_drives_lockfile() and self._accept('全盘扫描'):
            return True

        # 6. 日志 + 端口（慢，仅完整模式）—— 该方法内部已自行探活
        if sys.platform == 'win32' and not quick and self._find_by_log_and_scan():
            return True

        # 7. psutil（仅开发模式）
        if not getattr(sys, 'frozen', False):
            try:
                import psutil
                for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                    try:
                        if proc.info['name'] and 'LeagueClient' in proc.info['name']:
                            result = self._parse_cmdline(proc.info['cmdline'])
                            if result:
                                self._port, self._token, self._pid = result
                                # v4.4.0 修复（P1-4）：psutil 路径同样先探活
                                if self._accept('psutil'):
                                    return True
                                continue
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        continue
            except ImportError:
                pass

        return False

    # ==================== 进程查询（PS/CIM 优先 → wmic 兜底） ====================
    def _query_lcu_processes(self) -> List[Dict]:
        """一次调用拿到全部 LCU 进程的 (pid, exe, cmdline)。

        v4.4.0 修复（P2）：顺序反转为 **PowerShell Get-CimInstance 优先、wmic
        兜底** —— Win11 24H2 起 wmic 已从系统移除（旧版先试 wmic 会每次白白
        浪费一次失败 spawn）。

        v4.4.2(P2-36): 删除 PROC_QUERY_TTL 短缓存 —— 本方法是全仓唯一调用点
        (find_client:177), 真实调用方间隔全部 ≥5s(重连定时器 5s 起步), 而 TTL
        只有 1.5s, 缓存**永远不可能命中**, 纯属死逻辑(且空结果被缓存还会在客户端
        刚启动时返回过期空表)。去掉后每次真查, 行为与"缓存永不命中"完全等价。
        """
        if sys.platform != 'win32':
            return []
        raw = self._query_powershell()
        if raw is None:
            raw = self._query_wmic()
        if not raw:
            raw = []
        return raw

    @staticmethod
    def _decode_out(b: bytes, utf8_first: bool = True) -> str:
        """字节流解码：优先 utf-8（PS 已强制），失败回退 gbk/mbcs（wmic OEM）。"""
        if not b:
            return ''
        encs = ['utf-8', 'gbk', 'mbcs'] if utf8_first else ['gbk', 'mbcs', 'utf-8']
        for enc in encs:
            try:
                return b.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return b.decode('utf-8', errors='replace')

    def _query_wmic(self) -> Optional[List[Dict]]:
        """wmic /format:list 拿 LCU 进程记录；返回 None 表示 wmic 不可用。"""
        try:
            cmd = ['wmic', 'process', 'where',
                   "name='LeagueClientUx.exe' or name='LeagueClient.exe'",
                   'get', 'ProcessId,ExecutablePath,CommandLine', '/format:list']
            r = subprocess.run(cmd, capture_output=True, timeout=TIMEOUT_WMIC, **_KW)
        except Exception:
            return None  # wmic 不存在/被禁
        if r.returncode != 0:
            return None
        text = self._decode_out(r.stdout, utf8_first=False)
        return self._parse_keyed_records(text, ('ProcessId', 'ExecutablePath', 'CommandLine'))

    def _query_powershell(self) -> Optional[List[Dict]]:
        """PowerShell Get-CimInstance 拿 LCU 进程记录。

        v4.4.0 修复（P2）：本方法现在是**首选**（wmic 在 Win11 24H2+ 已被移除）。
        """
        try:
            ps = ("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                  "Get-CimInstance Win32_Process -Filter \"" + _LCU_PROC_NAMES + "\" | "
                  "ForEach-Object { 'PID=' + $_.ProcessId; 'EXE=' + $_.ExecutablePath; "
                  "'CMD=' + $_.CommandLine; '' }")
            r = subprocess.run(
                ['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                capture_output=True, timeout=TIMEOUT_PS, **_KW)
        except Exception:
            return None
        if r.returncode != 0:
            return None
        text = self._decode_out(r.stdout, utf8_first=True)
        return self._parse_keyed_records(text, ('PID', 'EXE', 'CMD'))

    def _parse_keyed_records(self, text: str, keys: Tuple[str, ...]) -> List[Dict]:
        """解析 键=值 块格式输出（wmic /format:list 或 PS 自打印）。

        不依赖空行分记录：同一键在同一记录内只出现一次，键重复即视为新记录
        开始（PS 每记录以 PID 开头、wmic 各版本字段顺序不同均适用）。
        CommandLine 折行内容会续接到当前记录。
        """
        recs: List[Dict] = []
        cur: Dict = {}
        for line in text.splitlines():
            line = line.rstrip()
            # v4.4.2(P2-39): 剥 BOM —— PowerShell 5.1 输出若带 UTF-8 BOM, 首行会
            # 变成 '\ufeffPID=...', startswith('PID=') 永远不匹配 → 首条记录缺字段。
            line = line.lstrip('\ufeff')
            if not line.strip():
                continue
            matched = False
            for key in keys:
                if line.startswith(key + '='):
                    if key in cur and cur:
                        recs.append(cur)
                        cur = {}
                    cur[key] = line[len(key) + 1:]
                    matched = True
                    break
            if not matched:
                # 值续行（CommandLine 可能被折行）
                for ck in ('CommandLine', 'CMD'):
                    if ck in cur:
                        cur[ck] += ' ' + line.strip()
                        break
        if cur:
            recs.append(cur)

        out: List[Dict] = []
        for rec in recs:
            item: Dict = {}
            pid = rec.get('ProcessId') or rec.get('PID')
            if pid:
                try:
                    item['pid'] = int(pid.strip())
                except ValueError:
                    pass
            exe = rec.get('ExecutablePath') or rec.get('EXE')
            if exe and os.path.isfile(exe.strip()):
                item['exe'] = exe.strip()
            cmd = rec.get('CommandLine') or rec.get('CMD')
            if cmd:
                item['cmdline'] = self._tokenize(cmd.strip())
            if item:
                out.append(item)
        return out

    @staticmethod
    def _tokenize(cmd: str) -> List[str]:
        """Windows 命令行分词：引号内的空格不拆分，并剥除引号。"""
        tokens = []
        for m in re.finditer(r'"([^"]*)"|(\S+)', cmd):
            tokens.append(m.group(1) if m.group(1) is not None else m.group(2))
        return tokens

    def _parse_cmdline(self, cmdline) -> Optional[Tuple[int, str, int]]:
        """从进程参数列表解析 app-port / remoting-auth-token / pid。

        兼容 '--k=v' 与 '--k v' 两种写法；值剥除引号。
        """
        if not cmdline:
            return None
        port = token = pid = None
        i = 0
        n = len(cmdline)
        while i < n:
            arg = cmdline[i].strip('"')  # 双保险：list 来源也可能带引号
            if arg == '--app-port' and i + 1 < n:
                try:
                    port = int(cmdline[i + 1].strip('"'))
                except (IndexError, ValueError):
                    pass
                i += 2
                continue
            if arg.startswith('--app-port='):
                try:
                    port = int(arg.split('=', 1)[1].strip('"'))
                except (IndexError, ValueError):
                    pass
            elif arg == '--remoting-auth-token' and i + 1 < n:
                token = cmdline[i + 1].strip('"')
                i += 2
                continue
            elif arg.startswith('--remoting-auth-token='):
                token = arg.split('=', 1)[1].strip('"')
            elif arg == '--pid' and i + 1 < n:
                try:
                    pid = int(cmdline[i + 1].strip('"'))
                except (IndexError, ValueError):
                    pass
                i += 2
                continue
            elif arg.startswith('--pid='):
                try:
                    pid = int(arg.split('=', 1)[1].strip('"'))
                except (IndexError, ValueError):
                    pass
            i += 1
        if port and token:
            return (port, token, pid or 0)
        return None

    # ==================== exe 反推 lockfile ====================
    def _find_lockfile_near_exe(self, exe: str) -> bool:
        """从客户端 exe（或安装目录）向上逐层精确探测 lockfile。

        lockfile 一定在 LeagueClient 进程 exe 同目录或安装根目录，
        无需递归 walk，向上探测 0..3 层即可。
        """
        base = exe if os.path.isdir(exe) else os.path.dirname(exe)
        for _ in range(5):  # 当前目录 + 向上 4 层
            if not base or not os.path.isdir(base):
                break
            for rel in ('lockfile', os.path.join('Config', 'lockfile')):
                p = os.path.join(base, rel)
                if os.path.isfile(p) and self._try_lockfile(p):
                    return True
            parent = os.path.dirname(base)
            if parent == base:
                break
            base = parent
        return False

    # ==================== 安装信息文件 / 注册表 ====================
    def _find_by_riot_installs(self) -> bool:
        """从 RiotClientInstalls.json 读取关联客户端路径 → 探测 lockfile。"""
        try:
            candidates = []
            progdata = os.environ.get('ProgramData', r'C:\ProgramData')
            p = os.path.join(progdata, 'Riot Games', 'RiotClientInstalls.json')
            if os.path.isfile(p):
                with open(p, encoding='utf-8-sig') as f:
                    data = json.load(f)
                ac = data.get('associated_client') or {}
                candidates.extend(ac.keys())
                rc = data.get('rc_default') or ''
                if rc:
                    candidates.append(rc)
            for raw in candidates:
                if not raw:
                    continue
                base = os.path.normpath(raw.replace('/', os.sep))
                for d in (base, os.path.dirname(base)):
                    if self._find_lockfile_near_exe(d):
                        return True
        except Exception:
            pass
        return False

    def _find_by_registry(self) -> bool:
        """从注册表读取安装位置 → 探测 lockfile。"""
        if sys.platform != 'win32':
            return False
        try:
            import winreg
        except Exception:
            return False
        keys = [
            (winreg.HKEY_CURRENT_USER, r'Software\Tencent\WeGame\Apps\LeagueClient'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Tencent\WeGame\Apps\LeagueClient'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\WOW6432Node\Riot Games\League of Legends'),
            (winreg.HKEY_LOCAL_MACHINE, r'SOFTWARE\Riot Games\League of Legends'),
        ]
        base_dirs = []
        for hive, key in keys:
            try:
                with winreg.OpenKey(hive, key) as k:
                    for val in ('InstallDir', 'InstallPath', 'Path', 'Location'):
                        try:
                            v, _ = winreg.QueryValueEx(k, val)
                            if v and os.path.isdir(str(v)):
                                base_dirs.append(str(v))
                        except OSError:
                            continue
            except OSError:
                continue
        for base in base_dirs:
            if self._find_lockfile_near_exe(base):
                return True
        return False

    # ==================== lockfile 扫描 ====================
    def _find_lockfile_fixed_roots(self, quick: bool = False) -> bool:
        """常见安装根固定路径快扫（各盘 WeGameApps / Riot Games 等）。

        v4.4.2(P1-13): 加 quick 参数 —— 点启动(quick=True)时只扫 LOCALAPPDATA
        两个固定路径, 跳过"遍历所有盘符"的循环。原实现 quick 与完整模式都走
        同一段 `for drive in self._get_drives()`, 而 `_get_drives()` 用
        `os.path.exists()` 判存在, 对断连的网络映射盘会阻塞数十秒 → 点启动卡死。
        """
        try:
            local = os.environ.get('LOCALAPPDATA', '')
            if local:
                for p in (
                        os.path.join(local, 'Riot Games', 'League of Legends', 'lockfile'),
                        os.path.join(local, 'Riot Games', 'LeagueClient', 'lockfile')):
                    if os.path.isfile(p) and self._try_lockfile(p):
                        return True
            if quick:
                # quick 模式到此为止 —— 盘符遍历交给后台重连线程(完整模式)。
                return False
            for drive in self._get_drives():
                roots = [
                    os.path.join(drive, 'WeGameApps'),
                    os.path.join(drive, 'WeGame'),
                    os.path.join(drive, 'Riot Games'),
                ]
                for root in roots:
                    if not os.path.isdir(root):
                        continue
                    if self._walk_find_lockfile(root, max_depth=WALK_MAX_DEPTH):
                        return True
        except Exception:
            pass
        return False

    def _scan_all_drives_lockfile(self) -> bool:
        """真·全盘 lockfile 扫描（自定义游戏库目录的最终兜底）。

        BFS + 启发式剪枝 + 遍历预算，避免扫系统目录与无限深：
          - 盘根(depth0)：跳过系统保留目录，其余全部进入(depth1)
          - depth1：全部进入(depth2)
          - depth2 起：目录名命中关键词才继续深挖，最深 BFS_MAX_DEPTH 层
          - 每盘最多访问 BFS_BUDGET 个目录，超出放弃该盘
        v4.4.0 修复（P2）：上限集中到 lcu/constants.py，并允许通过实例属性
        self.bfs_budget / self.bfs_max_depth 注入覆盖（默认值不变）。
        """
        try:
            for drive in self._get_drives():
                if self._bfs_lockfile(drive, budget=self.bfs_budget):
                    return True
        except Exception:
            pass
        return False

    def _bfs_lockfile(self, root: str, budget: Optional[int] = None) -> bool:
        if budget is None:
            budget = self.bfs_budget
        max_depth = self.bfs_max_depth
        try:
            q = deque([(root, 0)])
            visited = 0
            while q and visited < budget:
                d, dep = q.popleft()
                visited += 1
                try:
                    names = os.listdir(d)
                except Exception:
                    continue
                subdirs = []
                for name in names:
                    if name == 'lockfile':
                        p = os.path.join(d, name)
                        if self._try_lockfile(p):
                            return True
                        continue
                    if dep >= max_depth:
                        continue
                    p = os.path.join(d, name)
                    if not os.path.isdir(p):
                        continue
                    low = name.lower()
                    if dep == 0:
                        if low in _SKIP_TOP:
                            continue
                        subdirs.append(p)
                    elif dep == 1:
                        subdirs.append(p)
                    elif any(k in low for k in _HOT_DIR):
                        subdirs.append(p)
                q.extend((p, dep + 1) for p in subdirs)
        except Exception:
            pass
        return False

    def _walk_find_lockfile(self, root: str, max_depth: int) -> bool:
        """在已知根下递归找 lockfile（限深，用于固定根快扫）。"""
        try:
            for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
                depth = dirpath[len(root):].count(os.sep)
                if depth > max_depth:
                    dirnames[:] = []
                    continue
                if 'lockfile' in filenames:
                    p = os.path.join(dirpath, 'lockfile')
                    if self._try_lockfile(p):
                        return True
        except Exception:
            pass
        return False

    @staticmethod
    def _get_drives() -> List[str]:
        """枚举所有存在的盘符"""
        drives = []
        for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
            d = f'{letter}:\\'
            if os.path.exists(d):
                drives.append(d)
        return drives

    def _try_lockfile(self, path: str) -> bool:
        """解析 lockfile（LeagueClient:PID:PORT:PASSWORD:protocol）并接受。

        内容格式正确即信任接受（REST 探活后续负责真伪判定），避免在
        客户端启动早期/未登录时把有效候选误杀。
        """
        try:
            with open(path, 'r', encoding='ascii', errors='replace') as f:
                content = f.read().strip()
            if not content:
                return False
            parts = content.split(':')
            if len(parts) < 4 or parts[0] != 'LeagueClient':
                return False
            pid = int(parts[1])
            port = int(parts[2])
            token = parts[3]
            if not (port and token):
                return False
            self._port = port
            self._token = token
            self._pid = pid
            return True
        except Exception:
            return False

    # ==================== 日志兜底 ====================
    def _find_by_log_and_scan(self) -> bool:
        """递归搜索 LeagueClientUx 日志，读取 app-port + remoting-auth-token。

        覆盖安装版式差异：WeGame 版日志在 ...英雄联盟/LeagueClient/ 根目录，
        官方/Riot 版在 ...League of Legends/Logs/LeagueClientUx/ 子目录。
        """
        try:
            scan_roots = []
            local = os.environ.get('LOCALAPPDATA', '')
            if local:
                scan_roots.append(os.path.join(local, 'Riot Games', 'League of Legends'))
            for drive in self._get_drives():
                for sub in ('WeGameApps', 'WeGame', 'Riot Games'):
                    r = os.path.join(drive, sub)
                    if os.path.isdir(r):
                        scan_roots.append(r)
            log_files = []
            for root in scan_roots:
                try:
                    for dirpath, dirnames, filenames in os.walk(root, onerror=lambda e: None):
                        depth = dirpath[len(root):].count(os.sep)
                        if depth > WALK_MAX_DEPTH:
                            dirnames[:] = []
                            continue
                        for f in filenames:
                            if (f.endswith('.log') and 'LeagueClientUx' in f
                                    and 'Helper' not in f):
                                fp = os.path.join(dirpath, f)
                                try:
                                    log_files.append((os.path.getmtime(fp), fp))
                                except Exception:
                                    pass
                except Exception:
                    continue
            port = token = None
            for _, fp in sorted(log_files, reverse=True):
                try:
                    with open(fp, encoding='utf-8', errors='replace') as fh:
                        content = fh.read(8000)
                except Exception:
                    continue
                m = re.search(r'--remoting-auth-token=(\S+)', content)
                m2 = re.search(r'--app-port=(\d+)', content)
                if m and m2:
                    token = m.group(1).strip('"')
                    port = int(m2.group(1))
                    break
            if not port or not token:
                return False
            self._port = port
            self._token = token
            # v21:日志兜底写入后必须立即 REST 探活校验 —— 历史日志里的 port/token
            # 是上次 LOL 启动时写的,本次启动若 LOL 还没开/换端口,这就是无效值。
            # v14 修复只覆盖了"缓存已有值"场景,日志兜底这条路径是盲区(被用户
            # "先开脚本后开游戏" 10061 复测暴露)。校验失败则丢弃,让后台重连
            # 定时器在用户真正启动 LOL 后再走进程/锁文件拿到真值。
            if not self._validate_cached_connection():
                print('[连接] 日志兜底参数已失效(LOL 未启动或端口已变),丢弃')
                self.reset()
                return False
            # v4.3.14: 去掉"校验通过(旧)"的成功打印 —— 每次重连探活都打一行
            # 且成对出现, 纯噪音(成功与否由 WS on_open / 失败分支体现)。
            return True
        except Exception:
            return False

    def disconnect(self):
        self.reset()
