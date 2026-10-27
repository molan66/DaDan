"""lcu.launcher —— 启动 LOL(跳过 WeGame 主面板)

背景(2026-10 需求): 用户希望不打开 WeGame 主程序, 直接从大蛋小助手启动英雄联盟。

v4.8.0 行为定义(用户明确要求, 只有一种):
    **只把登录器打开**(跳过 WeGame), 然后什么都不做 —— 界面完全交给用户自己操作。
    ⚠️ 本模块**不含任何模拟点击代码**: 录制回放功能已于 v4.8.0 整体移除
    (F9 录制器、lcu/recorder.py、回放分支、相关设置项与提权原因一并删除)。
    ⚠️ 因此本模块也不再需要管理员权限: 只启动登录器是 ShellExecuteW 的正常启动路径。

v4.4.6 关键修正(用户实测反馈, 本模块最重要的教训):
    v4.4.5 启动的是 `WeGameLauncher\\launcher.exe` —— 用户点了之后 **WeGame 主程序
    照样被拉起来**(实测 wegame.exe 与 launcher.exe 同一秒启动), 等于没跳过 WeGame。
    根因: 那个文件的文件描述就叫"启动器", 它本身就是 WeGame 的壳。

    正确目标是 **`TCLS\\client.exe`** —— 文件描述"腾讯游戏登录程序"、产品"腾讯游戏
    统一登录系统", 即社区教程里说的"原生 client.exe":
        进游戏目录 → TCLS 文件夹 → client.exe → 双击即可免 WeGame 进 LOL。

启动链路对比:
    ❌ <根>\\WeGameLauncher\\launcher.exe → wegame.exe(主面板) → tcls_core → 客户端
    ✅ <根>\\TCLS\\client.exe             → (腾讯统一登录) → tcls_core → 客户端

为什么最终选择"只启动"而不是内置坐标或录制回放(重要教训):
    v4.4.9~v4.4.13 曾内置一套靠**截图逐像素实测**得出的默认步骤(QQ登录 → 切换快速
    登录 → 我已阅读 → 进入游戏 → 服务器列表 → 大区 → 确认选择)。结果连续多版都卡在
    "某一步点空"上 —— 用户原话:"我已阅读那里没点好, 导致后边也点不到了"。原因不是
    坐标测不准, 而是**界面状态无法判定**: 同一坐标在不同页面含义完全不同, 盲点必然
    会在某一步错位。v4.4.12 曾改成"录制回放"绕开猜坐标, 但 v4.8.0 用户明确要求
    把这个功能整个去掉 —— 自动点击这条路彻底放弃, 只保留"把登录器打开"。

其它实现要点:
    - 登录器是腾讯 TPF 自绘 UI(**无标准 Win32 控件**, UIA 也拿不到按钮)。
    - 启动必须走 ShellExecuteW: 登录器清单要求管理员权限, subprocess.Popen 走的
      CreateProcess **不会自动提权**, 非管理员下会直接抛 WinError 740。
    - 大厅是否就绪用 LCUConnector 探活判断(连上 LCU = 已进大厅)。
    - 进程识别带**路径校验**: `client.exe` 是极常见的进程名, 只按名字匹配会
      误抓到系统里别的 client.exe 窗口, 因此要求 exe 路径落在 LOL 安装目录下。

边界(诚实声明):
    - 凭证过期时腾讯会强制重新登录/扫码, 本模块不破解也不伪造凭证, 此时
      流程停下并提示用户手动登录这一次。
"""
import os
import time
import ctypes
from ctypes import wintypes
from typing import Optional, Dict, Callable, Tuple, List

# 复用它做窗口置顶(三机制降级 + 前台锁绕过), 让登录器出现在用户眼前。
# v4.8.0: 不再复用 click_at —— 本模块已无任何点击。
from .clicker import bring_to_front

# v4.4.6: LOL 安装根下的登录器候选(按优先级)。
# ⚠️ 明确**不用** WeGameLauncher\launcher.exe —— 它启动后会拉起 wegame.exe 主程序,
#    等于没跳过 WeGame(v4.4.5 实测踩坑, 详见模块 docstring)。
_LAUNCHER_REL_CANDIDATES = (
    os.path.join('TCLS', 'client.exe'),       # 腾讯游戏统一登录程序(原生登录器)★首选
    os.path.join('Launcher', 'Client.exe'),   # 英雄联盟登录程序(备选, 老版式样)
)

# 登录器相关进程名(用于"已在运行"判断与窗口枚举)。
# v4.4.6: **去掉 launcher.exe** —— 那是 WeGameLauncher 的壳(WeGame 主程序的前置),
# 它残留时会让 is_launcher_running() 误判"登录器已在运行"从而跳过启动。
# 真正要认的是 TCLS 登录器(client.exe)及其登录核心(tcls_core.exe)。
_LAUNCHER_PROC_NAMES = ('client.exe', 'tcls_core.exe')

# v4.4.6: 判定"该进程属于 LOL"的路径片段(小写比较)。
# 进程名 client.exe 太通用, 必须同时校验 exe 路径, 否则会误抓别的程序窗口。
_LOL_PATH_HINTS = ('wegameapps', '英雄联盟', 'leagueclient', '\\tcls\\', '\\launcher\\')

# 登录器窗口最小尺寸(小于此视为登录小窗/隐形窗/WebView 子窗, 跳过)
_MIN_W = 300
_MIN_H = 200

# v4.4.10: 从"点一键启动"到"开始点第一个按钮"的最小等待(秒)。
# 用户实测: 登录器窗口虽然很快就出现, 但客户端启动/资源加载还没完成, 界面上
# 的按钮此时点不动(点空)。从点按钮算起留足 15s, 界面才真正可点。
LAUNCH_SETTLE_S = 15.0

# v4.6.2: "窗口已出现"之后**至少**再等的秒数。
# 起因(日志实测): 原实现只按"从点一键启动起算总时长"倒扣, 于是 launch()+等窗口
# 若已耗掉 10s, 就只再等 5s —— 日志里出现过「稳定(5s)」「稳定(3s)」, 紧接着就是
# 「『录制步骤2』点击失败(point-not-over-client)」。登录器是自绘 UI, 按钮可点性
# 与进程存活时长不严格相关, 慢机器上窗口出现≠界面就绪, 点早了必然落空。
SETTLE_MIN_AFTER_WINDOW_S = 10.0

# v4.6.2: 单步点击落空(point-not-over-client)时的重试次数。
# 该失败几乎都是"点击瞬间登录器不在最上层"(被其它窗口/系统提示条遮住),
# 而不是坐标错。原来直接跳过该步 —— 关键步骤一丢, 整个流程就断。
# 现在先置顶再重试, 仍失败才放弃(并记明原因, 便于区分"被遮挡"与"坐标不对")。
#
# v4.8.0: 已无任何点击, 这两个常量随之失效。**保留为空占位会误导后来人**
# (看起来还有重试机制), 故明确删除。


# ==================== 窗口枚举(精简自 clicker, 独立模块不耦合其私有成员) ====================
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
_user32.EnumWindows.restype = wintypes.BOOL
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetWindowTextW.argtypes = [wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
_user32.GetWindowTextW.restype = ctypes.c_int


def _launcher_pids() -> Dict[int, str]:
    """返回 {pid: 进程名} —— 当前运行中的登录器相关进程(带路径校验)。

    v4.4.6: 增加 exe 路径校验。`client.exe` 是通用进程名, 只按名字匹配会把
    系统里别的 client.exe 也算进来, 导致后续窗口枚举抓错窗口。拿不到路径时
    (权限不足)宽松接受 —— 宁可多试, 也不漏掉真正的登录器。
    """
    out: Dict[int, str] = {}
    try:
        import psutil
    except ImportError:
        return out
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            name = (proc.info.get('name') or '').lower()
        except Exception:
            continue
        if name not in _LAUNCHER_PROC_NAMES:
            continue
        exe = ''
        try:
            exe = (proc.exe() or '').lower()
        except Exception:
            exe = ''
        if exe and not any(h in exe for h in _LOL_PATH_HINTS):
            continue   # 是别处的同名程序, 不是 LOL 的
        try:
            out[int(proc.info['pid'])] = name
        except Exception:
            continue
    return out


def find_window() -> Optional[Dict]:
    """找登录器可见主窗口(按面积降序取最大)。返回 {hwnd,left,top,right,bottom,w,h,proc}。"""
    pids = _launcher_pids()
    if not pids:
        return None
    rects = []

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        if not _user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) not in pids:
            return True
        r = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        w = r.right - r.left
        h = r.bottom - r.top
        if w >= _MIN_W and h >= _MIN_H:
            rects.append({
                'hwnd': int(hwnd or 0),
                'left': r.left, 'top': r.top,
                'right': r.right, 'bottom': r.bottom,
                'w': w, 'h': h,
                'proc': pids.get(int(pid.value), ''),
            })
        return True

    try:
        _user32.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        return None
    if not rects:
        return None
    rects.sort(key=lambda d: d['w'] * d['h'], reverse=True)
    return rects[0]


def wait_window(timeout_s: float = 30.0) -> Optional[Dict]:
    """轮询等登录器窗口出现。返回窗口信息或 None。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        w = find_window()
        if w:
            return w
        time.sleep(0.5)
    return None


def is_launcher_running() -> bool:
    return bool(_launcher_pids())


# ==================== 定位登录器 exe ====================
def _list_drives() -> List[str]:
    """枚举所有存在的盘符。"""
    drives = []
    for letter in 'CDEFGHIJKLMNOPQRSTUVWXYZ':
        d = f'{letter}:\\'
        if os.path.exists(d):
            drives.append(d)
    return drives


def _find_lol_roots() -> List[str]:
    """返回各盘可能的 WeGame 安装根(游戏目录的父目录)。

    命中 `E:\\WeGame\\WeGameApps` 这类目录后, 其下的"英雄联盟（含经典模式）"
    等子目录就是游戏根, 由 find_launcher_exe 逐个探测登录器。
    """
    roots: List[str] = []
    seen = set()
    candidates = []
    for drive in _list_drives():
        candidates.append(os.path.join(drive, 'WeGame', 'WeGameApps'))
        candidates.append(os.path.join(drive, 'WeGameApps'))
        candidates.append(os.path.join(drive, 'WeGame'))
    local = os.environ.get('LOCALAPPDATA', '')
    if local:
        candidates.append(os.path.join(local, 'WeGame', 'WeGameApps'))
        candidates.append(os.path.join(local, 'WeGameApps'))
    for p in candidates:
        try:
            p = os.path.normpath(p)
        except Exception:
            continue
        if p in seen or not os.path.isdir(p):
            continue
        seen.add(p)
        roots.append(p)
    return roots


def _iter_game_dirs(root: str):
    """产出可能的 LOL 游戏根目录(root 自身 + 其一级子目录)。"""
    yield root
    try:
        for name in os.listdir(root):
            p = os.path.join(root, name)
            if os.path.isdir(p):
                yield p
    except Exception:
        return


def find_launcher_exe() -> Optional[str]:
    """定位 LOL 原生登录器 exe(跳过 WeGame 主面板)。

    v4.4.6 修正: 目标改为 `TCLS\\client.exe`(腾讯游戏统一登录程序)。
    v4.4.5 误用 WeGameLauncher\\launcher.exe —— 那是 WeGame 的壳, 启动后照样
    拉起 wegame.exe 主程序(用户实测"还是启动的 WeGame")。
    """
    for root in _find_lol_roots():
        for game_dir in _iter_game_dirs(root):
            for rel in _LAUNCHER_REL_CANDIDATES:
                p = os.path.join(game_dir, rel)
                if os.path.isfile(p):
                    return p
    return None


# ==================== 启动 / 点击 ====================
# v4.4.15: 必须用 ShellExecuteW 而不是 subprocess.Popen 启动登录器。
#   实测 TCLS\client.exe 的清单要求管理员权限, 而 CreateProcess(Popen 走的路径)
#   **不会自动提权**, 非管理员身份下直接抛 OSError WinError 740("请求的操作需要提升")。
#   ShellExecuteW 等价于"资源管理器里双击": 需要提权时由 Windows 自己弹 UAC, 用户
#   点「是」即正常启动 —— 也就不会因为"默认不模拟点击所以不提权"而打不开登录器。
_shell32 = ctypes.WinDLL('shell32', use_last_error=True)
_shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
_shell32.ShellExecuteW.restype = ctypes.c_size_t


# ==================== 关闭 LOL ====================
# v4.4.16: 「全部停止」要关掉 LOL。优先**优雅退出**, 不要一上来就强杀:
#   ① 首选 LCU 的 /process-control 或 LeagueClientUx.exe --quit —— 让客户端自己走
#      正常关闭流程(保存设置、退出账号), 也不会留下 lockfile 残骸;
#   ② 兜底才用 taskkill 强杀 LeagueClientUx/LeagueClient/tcls_core/client。
# 强杀客户端是有副作用的(可能残留 lockfile 让下次连接读到死端口), 所以顺序不能反。
_LOL_CLIENT_PROCS = ('LeagueClientUx.exe', 'LeagueClient.exe',
                     'tcls_core.exe', 'client.exe', 'launcher.exe')


def quit_client(connector=None) -> Tuple[bool, str]:
    """关闭 LOL 客户端(先优雅、后强杀)。返回 (ok, 说明)。

    实测结论(决定了这个函数的结构):
      - LOL 客户端/登录器都以**管理员权限**运行, 非管理员进程 taskkill 一律
        `Access is denied` —— 强杀这条路非管理员走不通。
      - 但**客户端完整运行时**(LCU 可用), 走官方 `/process-control/v1/process/quit`
        可以让它**自己退出** —— 不需要管理员, 只是退出需要时间(实测可超过 6s,
        故这里把等待窗口放宽到 8s)。

    所以调用方应该"先调这个函数静默试一次, 只有失败了才去要管理员权限",
    别一上来就弹 UAC。
    """
    # 已经没在跑了就直接返回
    if not _any_lol_client_running():
        return True, 'LOL 未在运行'

    # ① 优雅: LCU 官方退出接口(客户端自己收尾, 无需管理员)
    #    认证用 connector.auth —— 它是 ('riot', password) 元组, 不是单独的 token。
    graceful_tried = False
    if connector is not None:
        try:
            if connector.is_connected or connector.find_client(quick=True):
                base = connector.base_url
                auth = connector.auth
                if base and auth and auth[1]:
                    graceful_tried = True
                    s = requests_session_for_quit()
                    s.post(f'{base}/process-control/v1/process/quit',
                           auth=auth, timeout=(2.0, 3.0), verify=False)
                    # 给客户端足够时间自己退(实测可能要 6s 以上)
                    for _ in range(16):
                        time.sleep(0.5)
                        if not _any_lol_client_running():
                            return True, 'LOL 已退出（客户端正常关闭）'
        except Exception:
            pass

    # ② 兜底: 强杀(含提权场景 —— 非管理员时这一步必然 Access denied)
    killed = _taskkill_lol()
    for _ in range(8):
        time.sleep(0.5)
        if not _any_lol_client_running():
            return True, 'LOL 已关闭（强制结束）'

    if not _any_lol_client_running():
        return True, 'LOL 已关闭'
    why = ('客户端未响应退出指令' if graceful_tried else '未登录状态, 无法走官方退出接口')
    return False, (f'LOL 仍在运行（{why}）—— 它以管理员权限运行, '
                   f'需要管理员权限才能强制关闭')


def requests_session_for_quit():
    """关闭 LOL 用的最小 requests.Session(仅本机回环, 不读系统代理)。"""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    s = requests.Session()
    s.verify = False
    s.trust_env = False
    return s


def _any_lol_client_running() -> bool:
    """是否有 LOL 客户端/登录器进程在跑(带路径校验, 避免误判同名的 client.exe)。"""
    try:
        import psutil
    except ImportError:
        return False
    for proc in psutil.process_iter(['name']):
        try:
            n = (proc.info.get('name') or '').lower()
        except Exception:
            continue
        if n not in [x.lower() for x in _LOL_CLIENT_PROCS]:
            continue
        # LeagueClientUx/LeagueClient 名字足够独特, 直接认; client.exe 太通用,
        # 必须校验路径属于 LOL 安装目录。
        if n in ('leagueclientux.exe', 'leagueclient.exe'):
            return True
        exe = ''
        try:
            exe = (proc.exe() or '').lower()
        except Exception:
            exe = ''
        if not exe or any(h in exe for h in _LOL_PATH_HINTS):
            return True
    return False


def _taskkill_lol() -> bool:
    """taskkill 强杀 LOL 相关进程。返回是否执行过(不代表都成功)。"""
    import subprocess
    ok_any = False
    for name in _LOL_CLIENT_PROCS:
        try:
            r = subprocess.run(['taskkill', '/F', '/IM', name],
                               capture_output=True, timeout=10,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if r.returncode == 0:
                ok_any = True
        except Exception:
            continue
    return ok_any


def launch() -> Tuple[bool, str]:
    """启动原生登录器(已运行则返回 True)。返回 (ok, 说明)。"""
    if is_launcher_running():
        return True, '登录器已在运行'
    exe = find_launcher_exe()
    if not exe or not os.path.isfile(exe):
        return False, '未找到 LOL 原生登录器(TCLS\\client.exe, 请确认已安装英雄联盟)'
    try:
        # 工作目录必须是 exe 所在目录: TCLS\client.exe 依赖同目录的
        # Tenio\ 、config\ 、mmog_data.xml 等资源, 换目录会启动失败。
        # verb 用 "open"(默认动作) —— 别用 "runas": 对已经通过清单声明需要提权的
        # 程序, "open" 同样会触发 UAC, 而且是系统认定的正常启动路径。
        # SW_SHOWNORMAL = 1
        r = _shell32.ShellExecuteW(None, 'open', exe, None,
                                   os.path.dirname(exe), 1)
        if r <= 32:
            return False, (f'启动登录器失败(ShellExecute 返回 {int(r)}); '
                           f'若刚弹过 UAC 且被拒绝, 请重试并在弹窗里点「是」')
        sub = os.path.join(os.path.basename(os.path.dirname(exe)),
                           os.path.basename(exe))
        return True, f'已启动原生登录器: {sub}'
    except Exception as e:
        return False, f'启动登录器失败: {type(e).__name__}: {e}'


def lcu_ready(connector) -> bool:
    """大厅是否就绪(LCU 已真正连上)。**必须真探活, 不能只看有没有参数。**

    v4.6.2 修复(用户反馈"一键启动有时能成功有时不能"):

    原实现是
        if connector.is_connected:      # ← 只判 port/token 非空, 不发请求
            return True
    而 `is_connected` 是纯属性判空。后果: 本会话里只要**曾经**连上过 LOL, 之后
    用户把 LOL 关掉, 连接器仍留着旧 port/token, 这里就恒为 True →
    run_flow 第 0 步直接报"已在游戏大厅, 无需启动"并 return True,
    **一次都不尝试启动**。用户看到 ✅ 成功, 但 LOL 压根没开。

    日志实锤(app.log:716-726): 同一进程里一边打印"已在游戏大厅", 紧接着又连打
    5 行"日志兜底参数已失效(LOL 未启动或端口已变)" —— 自相矛盾。共出现 4 次。

    这解释了"有时成功有时不能": 差别只在于**本次会话是否曾连上过客户端**。

    修法: 有参数时必须真发一次 REST 探活(probe_alive)。探活不过就**继续走
    find_client** —— 它会 reset 掉脏参数并重新发现(即"自愈"): 客户端真的开着
    时照样立刻找回, 真的没开时正确返回 False, 而不是拿旧参数谎报就绪。
    """
    try:
        if connector.is_connected and connector.probe_alive():
            return True
        # 参数不存在, 或存在但已失效(残留/客户端刚重启) → 走完整发现链路自愈
        return connector.find_client(quick=True)
    except Exception:
        return False


# ==================== 一键流程 ====================
# v4.8.0(用户明确要求): **彻底取消一切自动点击**, 只保留"打开登录器"。
#     点「启动 LOL」= 把登录器拉起来(跳过 WeGame), 然后什么都不做,
#     界面完全交给用户自己操作。
#
# 历史: v4.4.9~4.4.13 曾内置一套"靠截图像素实测猜出来"的默认步骤, 连续多版都卡在
#     "某一步点空"(用户原话:"我已阅读那里没点好, 导致后边也点不到了");
#     v4.4.12 改成"录制回放"来绕开猜坐标; v4.8.0 用户要求把这个功能整个去掉 ——
#     录制器(F9 全局钩子 + lcu/recorder.py)、回放分支、相关设置项与提权原因全部移除。
#
# ⚠️ 为什么"只启动"这件事仍需要 _wait_settle: 它不是为了点击, 而是为了让
#     run_flow 在窗口出现后能稳定地探测一次"客户端是否自己起来了", 避免
#     窗口一出现就误判。见该函数说明。


def _wait_settle(t_start: float, say=None, total_s: float = LAUNCH_SETTLE_S) -> None:
    """确保从"点启动"起至少过了 total_s 秒, 再去探测客户端状态。

    v4.4.10: 用户实测 —— 登录器窗口很快出现, 但客户端启动/资源加载尚未完成。
    v4.6.2: 按"从启动起算总时长"补齐, 并把最小值抬到 SETTLE_MIN_AFTER_WINDOW_S
    (登录器是腾讯 TPF 自绘 UI, 按钮可点性与"进程跑了多久"并不严格相关)。

    v4.8.0: 不再有点击了, 但这段等待**仍然保留** —— 作用变成: 窗口刚出现时
    `lcu_ready()` 一定还是 False(客户端进程尚未拉起), 若不等就立即探测会得到
    一个必然为假的结论, 让 run_flow 过早返回"未进入大厅"。等一段之后探测才有意义。
    """
    remain = total_s - (time.monotonic() - t_start)
    if remain < SETTLE_MIN_AFTER_WINDOW_S:
        remain = SETTLE_MIN_AFTER_WINDOW_S
    if remain > 0:
        if say:
            say(f'等待登录器界面稳定({remain:.0f}s)…')
        time.sleep(remain)


def run_flow(connector,
             on_status: Optional[Callable[[str], None]] = None,
             timeout_s: float = 150.0) -> Tuple[bool, str]:
    """启动 LOL：只把原生登录器打开(跳过 WeGame), **不做任何模拟点击**。

    v4.8.0 语义(用户要求):
        登录器打开后流程就结束, 界面交给用户自己操作。
        ⚠️ 没有任何点击步骤参数 —— 录制回放功能已整体移除。

    为什么仍要等一段(见 _wait_settle 与下面第 3 步):
        登录器可能记住登录态, 自己就把客户端拉起来了。等一段再探测, 才能如实
        报告"已进入大厅"还是"停在登录器"。这段探测**只读状态, 不点任何东西**。

    on_status: 进度回调(线程安全由调用方保证), 报告中文进度。

    返回 (ok, msg)。
    """
    def say(msg):
        if on_status:
            try:
                on_status(msg)
            except Exception:
                pass

    # 0. 已在大厅?
    if lcu_ready(connector):
        say('已在游戏大厅, 无需启动')
        return True, '已在游戏大厅'

    # 1. 启动原生登录器(跳过 WeGame)
    t_start = time.monotonic()   # v4.4.10: 从"点启动"起算界面稳定期
    ok, msg = launch()
    say(msg)
    if not ok:
        return False, msg

    # 2. 等登录器窗口(原生登录器要过更新检查/TP 加载, 给 60s)
    w = wait_window(60.0)
    if w:
        say(f'登录器窗口已就绪({w["w"]}x{w["h"]})')
        try:
            bring_to_front(int(w['hwnd']))
        except Exception:
            pass
    else:
        # 窗口没出现不一定失败: 登录器可能识别到已登录, 直接拉起客户端
        say('未等到登录器窗口, 继续等客户端连接…')

    # 3. 界面稳定期 —— 窗口出现 ≠ 客户端就绪。等完再探测一次真实状态。
    _wait_settle(t_start, say)

    say('登录器已打开, 后续操作交给你自己完成')
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if lcu_ready(connector):
            say('已连接客户端, 进入大厅')
            return True, '已进入大厅'
        if not find_window():
            # 登录器窗口消失通常意味着它把客户端拉起并让位了
            break
        time.sleep(1.0)

    return True, '登录器已打开(不做任何模拟点击, 后续请自行操作)'

