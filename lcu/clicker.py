"""lcu.clicker - 海克斯(3270)等"2-3 张卡"模式用屏幕点击兜底自动选英雄

v31 设计: 屏幕渲染出来的卡必然在服务器允许子集内, 鼠标点它就绕开了
"LCU 读不到卡组"的死结。本模块负责:
  1. 实时找 LOL 客户端窗口(LeagueClient.exe / LeagueClientUx.exe CEF 渲染层)
  2. 把窗口拉到前台
  3. 按窗口内相对比例算绝对屏幕坐标
  4. 模拟鼠标左键单击(多机制降级)
  5. 验证(由调用方做 REST read-back)

v32 升级(v31 实测 SetCursorPos 失败原因不明, 日志无错误码):
  - 点击三机制自动降级: cursor(SetCursorPos+mouse_event) →
    SendInput(官方推荐注入) → postmsg(PostMessage 直发窗口, 不依赖光标/前台)
  - 每步失败都带 GetLastError 错误码
  - 新增权限侦查 process_elevated(): 若 LOL 以管理员运行而本程序不是,
    UIPI 会静默丢弃注入的鼠标消息 → 提示用户以管理员身份运行本程序

坐标策略: 存"窗口内相对比例"(frac_x, frac_y, 范围 0~1), v31 默认 0.161/0.609
来自 2026-09-09 截图标定(1920x1080 屏, 客户端约 930x435 窗口化), 实际点击
坐标 = 窗口当前 rect.left + fracX*w, window.top + fracY*h。窗口位置随便挪
都能算对, 换分辨率也不怕。
"""
import ctypes
import threading
import time
from ctypes import wintypes
from typing import Optional, Tuple, Dict, Any, List

# v4.4.0 修复：窗口尺寸阈值等魔数集中到 lcu/constants.py，避免同一含义的
# 数字散落在多个文件里自行漂移
from .constants import (
    MIN_WINDOW_W, MIN_WINDOW_H,
    CLICK_RESTORE_SLEEP_S, CURSOR_DOWN_SLEEP_S, CURSOR_UP_SLEEP_S,
    POSTMSG_HOVER_SLEEP_S, POSTMSG_UP_SLEEP_S,
)

# Windows API 常量
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
# v4.4.0 修复（P0-6）：SendInput 的绝对坐标默认按**主显示器**映射，虚拟桌面
# (多显示器并集)坐标下会把副屏上的点算到主屏去。必须 OR 上 VIRTUALDESK 才
# 声明"坐标相对整个虚拟桌面"，与 _sendinput_click 里 SM_*VIRTUALSCREEN 的
# 归一化口径一致（原实现两边口径不一致，副屏点卡必然打偏）。
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE = 0
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
SW_RESTORE = 9
GA_ROOT = 2  # GetAncestor: 取顶层(根)窗口, CEF 客户端可见窗口可能是其子窗口
SM_CXSCREEN = 0
SM_CYSCREEN = 1
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ('dx', wintypes.LONG),
        ('dy', wintypes.LONG),
        ('mouseData', wintypes.DWORD),
        ('dwFlags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),  # ULONG_PTR
    ]


class _INPUT_U(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ('u',)
    _fields_ = [
        ('type', wintypes.DWORD),
        ('u', _INPUT_U),
    ]


# use_last_error=True 让 ctypes.get_last_error() 能取到 GetLastError
_user32 = ctypes.WinDLL('user32', use_last_error=True)
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.SetCursorPos.restype = wintypes.BOOL
# v4.4.0(P1-12 收尾): _cursor_click 读回校验需要 GetCursorPos, 显式声明
# argtypes/restype(不声明时 ctypes 默认 c_int 实参, POINT* 会有转换歧义)
_user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
_user32.GetCursorPos.restype = wintypes.BOOL
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL
_user32.GetSystemMetrics.argtypes = [ctypes.c_int]
_user32.GetSystemMetrics.restype = ctypes.c_int
_user32.IsWindowVisible.argtypes = [wintypes.HWND]
_user32.IsWindowVisible.restype = wintypes.BOOL
_user32.IsIconic.argtypes = [wintypes.HWND]
_user32.IsIconic.restype = wintypes.BOOL
_user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
# v4.4.0 修复（P2）：EnumWindows 此前只设了 argtypes 没设 restype —— ctypes
# 默认按 c_int 解释返回值，64 位下 BOOL 虽小仍可能读到高位垃圾；且缺 restype
# 会让 ctypes 无法正确清理返回值。语义上它就是 BOOL，补齐即可。
_user32.EnumWindows.restype = wintypes.BOOL
_user32.SetProcessDPIAware.argtypes = []
_user32.SetProcessDPIAware.restype = wintypes.BOOL
_user32.ScreenToClient.argtypes = [wintypes.HWND,
                                   ctypes.POINTER(wintypes.POINT)]
_user32.ScreenToClient.restype = wintypes.BOOL
_user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                 wintypes.WPARAM, wintypes.LPARAM]
_user32.PostMessageW.restype = wintypes.BOOL
_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT),
                              ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
# v4.3.15 修复: GetForegroundWindow 此前**没设 restype** —— ctypes 默认按 c_int
# (32 位有符号)返回, 而 HWND 在 64 位 Windows 上是 8 字节指针 → 高位被截断,
# 拿到的句柄必然不等于真实 hwnd。bring_to_front 里 v4.3.10 加的"复核当前前台
# 窗口是不是目标窗口"因此**永远判定失败**, 即使客户端就在最前面也返回 False,
# 于是点卡被降级成 postmsg(对 LOL 的 CEF 客户端实测完全不生效) → 自动选英雄
# 整局不动, 还会把用户自己手选的英雄误报成"自动点选成功"(2026-09-24 日志)。
_user32.GetForegroundWindow.argtypes = []
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL
_user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                      wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL
_user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
_user32.GetAncestor.restype = wintypes.HWND
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                wintypes.DWORD, wintypes.DWORD,
                                ctypes.c_size_t]


# v41: process_elevated 用的 WinDLL/结构体/签名提升到模块级, 避免每次调用
# 重新 WinDLL + 定义结构体(原 process_elevated 每次点卡权限侦查都重做一遍)。
class TOKEN_ELEVATION(ctypes.Structure):
    _fields_ = [('TokenIsElevated', wintypes.DWORD)]


_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                  wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       ctypes.POINTER(wintypes.HANDLE)]
_advapi32.OpenProcessToken.restype = wintypes.BOOL
_advapi32.GetTokenInformation.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_advapi32.GetTokenInformation.restype = wintypes.BOOL

# v4.4.0 修复（P2）：is_admin() 原先**每次调用**都新建 WinDLL('shell32') 并重设
# restype —— 该函数被启动流程与每次提权侦查调用，重复加载 DLL 是纯浪费。
# 提到模块级只做一次；加载失败时置 None，由 is_admin 走 process_elevated 回退。
try:
    _shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    _shell32.IsUserAnAdmin.restype = wintypes.BOOL
except Exception:
    _shell32 = None

# v4.4.0 修复（P1-12）：click_at 前置提权侦查需要知道"客户端进程是否已提权 vs
# 本进程是否提权"。两者都是进程级属性、一局内不会变，但 click_at 是**热路径**
# （点卡重试连点），不能每次都调 OpenProcess/OpenProcessToken。
# 这里按 pid 缓存判定结果，只缓存"已确定"的结果，None(=无法判定) 不缓存以免把临时失败固化。
# v4.4.2(P0-2): 原注释"pid 为进程身份, 不会复用"是错的 —— Windows 会复用 PID。
# 客户端重启若拿到同一 PID, 旧缓存会把"已提权"错误命中, 导致整局点卡静默失效。
# 改为带 TTL 的缓存: 提权状态在单个进程生命周期内不变, 秒级 TTL 即可兼顾
# 热路径性能与 PID 复用的正确性(客户端重启到再次进选人点击远超过 TTL)。
_ELEV_CACHE_TTL_S = 30.0
_elev_cache: Dict[int, Tuple[float, bool]] = {}
_elev_cache_lock = threading.Lock()

# v4.4.2(P2-40): 本进程是否管理员的判定结果 —— 自身权限在运行期不会变,
# 但 uiPi_blocked 在点卡热路径上每轮被 12 个候选点各调一次 is_admin(), 每次都
# 走 shell32.IsUserAnAdmin()(甚至 process_elevated 的 token 查询)。进程内缓存
# 一次即可。None = 尚未判定(与"判定失败"区分开, 失败才回退重试)。
_admin_cache: Optional[bool] = None


def _cached_process_elevated(pid: int) -> Optional[bool]:
    """带 TTL 缓存的 process_elevated(pid)，避免点击热路径重复查询 token。"""
    if not pid:
        return None
    now = time.monotonic()
    with _elev_cache_lock:
        ent = _elev_cache.get(int(pid))
        if ent is not None and now - ent[0] < _ELEV_CACHE_TTL_S:
            return ent[1]
    res = process_elevated(int(pid))
    if res is not None:
        with _elev_cache_lock:
            _elev_cache[int(pid)] = (now, bool(res))
    return res


def uiPi_blocked(hwnd: Optional[int]) -> bool:
    """判断对目标窗口注入鼠标是否会被 UIPI 静默丢弃。

    v4.4.0 修复（P1-12）：原先 process_elevated 只在 core/script_core.py:730-742
    做过一次"侦查并打印提示"，click_at 完全不检查 —— 客户端以管理员运行时本程序
    若未提权，SendInput/PostMessage 会被 UIPI 静默丢弃（全部失败但无错误码），
    表现为"点了 13 次全没反应、日志看不出原因"。这里在点击前判定：
    目标进程已提权 且 本进程未提权 → True（调用方应返回 'uipi-blocked'）。
    本进程已提权时对未提权目标一定可注入；任一判定失败(None)则视为不阻塞，
    保持原行为（宁可尝试失败，也不要因为侦查不到而拒绝点击）。
    """
    try:
        if not hwnd:
            return False
        if not is_admin():
            pid = window_pid(hwnd)
            if pid and _cached_process_elevated(pid) is True:
                return True
        return False
    except Exception:
        return False


def _dpi_aware() -> None:
    """让本进程按物理像素取屏/取窗口, 保证坐标与鼠标一致。

    v4.3.10 修复: 原实现调用 SetProcessDpiAwareness(2) 后**不看返回值就 return**
    —— 该 API 在已设过 DPI 感知时会返回 E_ACCESSDENIED(0x80070005), 此时其实
    需要回退到 SetProcessDPIAware(); 原写法让回退分支变成死代码, 进程可能停留在
    非 DPI 感知状态 → 缩放 != 100% 时点卡坐标整体偏移。
    """
    try:
        # 0 = S_OK 才算成功; 非 0(E_ACCESSDENIED 等)继续尝试回退方案
        hr = ctypes.windll.shcore.SetProcessDpiAwareness(2)
        if hr == 0:
            return
    except Exception:
        pass
    try:
        # v4.4.2(P2-41): 原 `ctypes.windll.user32.SetProcessDPIAware()` 走的是
        # windll.user32(另一个 DLL 实例), 而 :112-113 在 _user32 上声明的
        # argtypes/restype 从未生效(ctypes.windll.user32 is _user32 实测 False)。
        # 改走 _user32, 让声明真正受控。
        if _user32.SetProcessDPIAware():
            return
    except Exception:
        pass


# 进程启动即生效, 不依赖调用方
_dpi_aware()


def _last_err() -> int:
    """取最近一次 Win32 调用的错误码。"""
    try:
        return int(ctypes.get_last_error() or 0)
    except Exception:
        return 0


def window_pid(hwnd: int) -> Optional[int]:
    """取窗口所属进程 PID, 失败返回 None。"""
    try:
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) or None
    except Exception:
        return None


def process_elevated(pid: Optional[int] = None) -> Optional[bool]:
    """检测进程是否管理员权限(TokenElevation)。

    pid=None → 检测自身进程。返回 True/False; 无法判定返回 None。
    v32 用途: LOL 以管理员运行而本程序不是时, UIPI 会静默丢弃注入的鼠标
    消息(SetCursorPos 仍能移动光标, 但 down/up 点击到不了对方窗口)。
    v41: WinDLL/结构体/签名已提升到模块级(见上 _kernel32/_advapi32),
    这里只做查询逻辑。
    """
    try:
        TOKEN_QUERY = 0x0008
        TokenElevation = 20
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

        if pid is None:
            hproc = _kernel32.GetCurrentProcess()
            need_close = False
        else:
            hproc = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                          False, int(pid))
            need_close = True
            if not hproc:
                return None
        htok = wintypes.HANDLE()
        if not _advapi32.OpenProcessToken(hproc, TOKEN_QUERY,
                                          ctypes.byref(htok)):
            if need_close:
                _kernel32.CloseHandle(hproc)
            return None
        te = TOKEN_ELEVATION()
        ret_len = wintypes.DWORD()
        ok = _advapi32.GetTokenInformation(
            htok, TokenElevation, ctypes.byref(te),
            ctypes.sizeof(te), ctypes.byref(ret_len))
        _kernel32.CloseHandle(htok)
        if need_close:
            _kernel32.CloseHandle(hproc)
        return bool(te.TokenIsElevated) if ok else None
    except Exception:
        return None


def is_admin() -> bool:
    """权威检测: 当前进程是否以管理员权限运行(v35)。

    用 shell32 IsUserAnAdmin() —— Windows 官方"本进程是否管理员"判定,
    语义直白、无 token 查询的层层 api 组合, 不易误报。
    该调用异常时回退 process_elevated(); 两者都失败按 False 处理
    (宁可误走一次提权, 也不要管理员身份被误判而跳过提权)。

    背景(v4.1.34 实测): 用户已是管理员进程, process_elevated() 却误报
    非管理员 → 触发了 runas, 而 Windows 对已提权进程再 runas 不弹 UAC
    直接静默重启 → 用户看到"点开关没弹窗自己就重启了"。

    v4.4.2(P2-40): 结果进程内缓存 —— 自身权限运行期不变, 而本函数在点卡
    热路径被反复调用。仅缓存**确定性**的 shell32 判定; 回退 process_elevated 的
    路径不缓存(None 会被当 False, 若缓存会把一次瞬时失败钉死)。
    """
    global _admin_cache
    if _admin_cache is not None:
        return _admin_cache
    try:
        # v4.4.0 修复（P2）：WinDLL 已提到模块级，这里只调用
        if _shell32 is not None:
            _admin_cache = bool(_shell32.IsUserAnAdmin())
            return _admin_cache
        raise OSError('shell32 未加载')
    except Exception:
        try:
            return bool(process_elevated())
        except Exception:
            return False


def find_client_windows() -> List[Dict[str, Any]]:
    """枚举所有 league* 进程的可见顶层窗口(按面积降序)。

    返回: [{'hwnd', 'left', 'top', 'right', 'bottom', 'w', 'h', 'process'}, ...]
    """
    try:
        import psutil
    except ImportError:
        return []
    rects: List[Dict[str, Any]] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL,
                                     wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _lp):
        if not _user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            name = (psutil.Process(pid.value).name() or '').lower()
        except Exception:
            return True
        if 'league' in name:  # LeagueClient.exe / LeagueClientUx.exe 都命中
            # v4.3.15: 必须排除游戏本体 "League of Legends.exe" —— 它的进程名同样
            # 含 'league', 且窗口通常是 1920x1080 全屏(面积最大), 一旦这局同时开着
            # 游戏窗口, 就会把游戏当成客户端, 点卡按客户端坐标去点游戏界面。
            # 旧版日志(v4.3.9)里唯一那次点卡 12 连未命中正是猎到了它。
            if 'league of legends' in name:
                return True
            r = wintypes.RECT()
            if _user32.GetWindowRect(hwnd, ctypes.byref(r)):
                w = r.right - r.left
                h = r.bottom - r.top
                if w > MIN_WINDOW_W and h > MIN_WINDOW_H:
                    rects.append({
                        'hwnd': int(hwnd or 0),
                        'left': r.left, 'top': r.top,
                        'right': r.right, 'bottom': r.bottom,
                        'w': w, 'h': h,
                        'process': name,
                    })
        return True

    try:
        _user32.EnumWindows(WNDENUMPROC(cb), 0)
    except Exception:
        return []
    rects.sort(key=lambda d: d['w'] * d['h'], reverse=True)
    return rects


def get_client_rect() -> Optional[Dict[str, Any]]:
    """取最大 league* 窗口 rect, 失败返回 None。"""
    rects = find_client_windows()
    return rects[0] if rects else None


def _fg_hwnd() -> int:
    """取当前前台窗口句柄(已设 restype=HWND, 64 位下不会被截断)。"""
    try:
        return int(_user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def bring_to_front(hwnd: int) -> bool:
    """把指定窗口拉到前台(若最小化先还原)。

    v4.3.10 修复: 原实现丢弃 SetForegroundWindow 的返回值**恒返回 True** ——
    Windows 有前台锁(用户正在操作别的程序时该调用会失败), 此时调用方以为已置顶,
    却仍按绝对屏幕坐标注入点击 → **点到用户浏览器/桌面上**。
    现在: 返回真实结果, 并在失败时用 GetForegroundWindow 复核一次当前前台窗口。

    v4.3.15 修复(2026-09-24 实测"点卡显示成功、实际是我自己选的"):
      ① GetForegroundWindow 未设 restype, 64 位 HWND 被截成 c_int, 复核永远失败;
      ② 只有"复核"不够 —— 前台锁生效时 SetForegroundWindow 必然失败, 需要真正
         绕过它: AttachThreadInput 把自己的输入队列接到当前前台线程, 之后再
         SetForegroundWindow/BringWindowToTop 即可成功(标准做法);
      ③ 复核用 **GA_ROOT 归一化**后再比 —— CEF 客户端的可见窗口可能是子窗口,
         GetForegroundWindow 返回的是根窗口, 直接比句柄会不相等而误判失败。
    """
    if not hwnd:
        return False
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(CLICK_RESTORE_SLEEP_S)
        if _user32.SetForegroundWindow(hwnd):
            return True
        # 复核: 目标窗口是否已在最前(GA_ROOT 归一化, 兼容 CEF 子窗口)
        target = int(_user32.GetAncestor(wintypes.HWND(int(hwnd)), GA_ROOT) or 0) \
            or int(hwnd)
        if _fg_hwnd() == target:
            return True
        # 前台锁绕过: 附加到当前前台线程的输入队列, 再置顶
        fg = _fg_hwnd()
        if fg:
            tid_fg = _user32.GetWindowThreadProcessId(wintypes.HWND(fg), None)
            tid_me = _kernel32.GetCurrentThreadId()
            attached = bool(tid_fg and tid_fg != tid_me and
                            _user32.AttachThreadInput(tid_me, tid_fg, True))
            try:
                _user32.BringWindowToTop(wintypes.HWND(int(hwnd)))
                _user32.SetForegroundWindow(wintypes.HWND(int(hwnd)))
            finally:
                if attached:
                    _user32.AttachThreadInput(tid_me, tid_fg, False)
        if _fg_hwnd() == target:
            return True
        return False
    except Exception:
        return False


def _cursor_click(x: int, y: int) -> bool:
    """机制A: SetCursorPos 移动真实光标 + mouse_event 按下/抬起。

    v4.4.0(P1-12 收尾): SetCursorPos 成功≠点击落地, 所以三个可查的返回值
    全部检查: SetCursorPos 的 BOOL、两次 GetCursorPos 的 BOOL、以及点击前后
    光标位置是否一致(注入瞬间被别的进程抢走光标时位置会漂, 此时不报成功)。
    注意 SetCursorPos 会把越界坐标**钳制**到桌面范围, 所以基准取 SetCursorPos
    之后的读回值(钳制后的有效点), 而不是请求的原始坐标 —— 否则副屏边缘会误判失败。

    **本层检测不到什么**: UIPI 对 `mouse_event` 的静默丢弃不会改变光标位置,
    也返回不了错误码 —— 那种情况只能靠上层的 `uiPi_blocked()` 提权侦查与
    REST 读回(championId 是否真的锁上)判定, 不能指望这里的读回。

    读回不一致只记 False 而不抛, 上层 click_at 会继续降级到 SendInput/postmsg。
    """
    try:
        if not _user32.SetCursorPos(x, y):
            return False
        pt = wintypes.POINT()
        if not _user32.GetCursorPos(ctypes.byref(pt)):
            return False
        ex, ey = int(pt.x), int(pt.y)     # 钳制后的实际落点
        time.sleep(CURSOR_DOWN_SLEEP_S)
        _user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        time.sleep(CURSOR_UP_SLEEP_S)
        _user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        # 读回: 光标被别的进程抢走/注入被丢弃时位置会漂, 此时不算成功
        cur = wintypes.POINT()
        if not _user32.GetCursorPos(ctypes.byref(cur)):
            return False
        return int(cur.x) == ex and int(cur.y) == ey
    except Exception:
        return False


def _root_of(hwnd: int) -> int:
    """取窗口的顶层(GA_ROOT)句柄 —— CEF 客户端可见窗口可能是子窗口。"""
    try:
        r = int(_user32.GetAncestor(wintypes.HWND(int(hwnd)), GA_ROOT) or 0)
        return r or int(hwnd)
    except Exception:
        return int(hwnd or 0)


def point_over_window(hwnd: int, x: int, y: int) -> bool:
    """屏幕坐标 (x, y) 处最上层的窗口是否属于目标客户端窗口。

    v4.3.15 新增: cursor/sendinput 注入的是**绝对屏幕坐标**, 实际点中的是
    "光标底下那个窗口"——与前台无关。所以只要确认该点落在客户端窗口上,
    注入就是安全的(既不会点到浏览器, 也不受前台锁影响)。
    这比 v4.3.10 的"拿不到前台就整体放弃"更准: 放弃会让自动选英雄彻底失效。
    """
    if not hwnd:
        return False
    try:
        pt = wintypes.POINT(int(x), int(y))
        under = int(_user32.WindowFromPoint(pt) or 0)
        if not under:
            return False
        return _root_of(under) == _root_of(int(hwnd))
    except Exception:
        return False


def _sendinput_click(x: int, y: int) -> bool:
    """机制B: SendInput 注入绝对坐标鼠标输入(官方推荐, 替代已废弃的 mouse_event)。

    v4.3.10 修复: 原来用主屏 SM_CXSCREEN/SM_CYSCREEN 并且把坐标 `max(0, ...)`
    夹到非负 —— 多显示器环境下(副屏在主屏左侧/上方时坐标是负数, 或副屏坐标
    超出主屏范围)会把点击**算到主屏左上角**, 表现为"点卡点不动/点到别处"。
    现在改用虚拟桌面(所有显示器并集)的起点与尺寸做归一化, Windows 的
    绝对坐标本来就是相对虚拟桌面的。
    """
    try:
        vx = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if vw <= 1 or vh <= 1:
            # 取不到虚拟桌面(异常) → 退回主屏, 至少不比原实现差
            vx = vy = 0
            vw = _user32.GetSystemMetrics(SM_CXSCREEN)
            vh = _user32.GetSystemMetrics(SM_CYSCREEN)
        if vw <= 1 or vh <= 1:
            return False
        # 相对虚拟桌面左上的偏移, 再归一化到 0..65535
        nx = int((x - vx) * 65535 // max(1, vw - 1))
        ny = int((y - vy) * 65535 // max(1, vh - 1))
        nx = max(0, min(65535, nx))
        ny = max(0, min(65535, ny))

        def _mk(flags):
            return INPUT(type=INPUT_MOUSE,
                         mi=MOUSEINPUT(dx=nx, dy=ny, mouseData=0,
                                       dwFlags=flags, time=0,
                                       dwExtraInfo=0))

        # v4.4.0 修复（P0-6）：三个 INPUT 全部 OR 上 MOUSEEVENTF_VIRTUALDESK ——
        # 上面 nx/ny 是按虚拟桌面(所有显示器并集)归一化的，若不声明 VIRTUALDESK，
        # Windows 会按主显示器解释绝对坐标 → 副屏(尤其负坐标副屏)点卡整体打偏。
        # 三个事件必须用同一 flag 组合，否则 move/down/up 会落在不同物理点。
        arr = (INPUT * 3)(
            _mk(MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_MOVE | MOUSEEVENTF_VIRTUALDESK),
            _mk(MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTDOWN | MOUSEEVENTF_VIRTUALDESK),
            _mk(MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_LEFTUP | MOUSEEVENTF_VIRTUALDESK),
        )
        sent = _user32.SendInput(3, arr, ctypes.sizeof(INPUT))
        return int(sent) == 3
    except Exception:
        return False


def _postmsg_click(hwnd: int, x: int, y: int) -> bool:
    """机制C: 直接把鼠标消息投递给窗口(不依赖光标/前台, UIPI 下对同权限窗口可用)。

    CEF(LeagueClientUx) 通常需要先 hover(WM_MOUSEMOVE) 再 down/up。
    坐标需转为窗口客户区坐标。

    v4.4.0(P1-12 收尾): 三个 PostMessageW 都是 BOOL 返回值, 原实现完全不看
    —— UIPI 拦截/窗口已销毁时它们返回 0 但函数照样 `return True`, 让上层误以为
    点成功了。现在逐条检查: 任意一条失败(或消息队列满导致投递失败)即返回 False,
    由 click_at 报告真实机制名, 上层就知道这次点击没落地。
    """
    if not hwnd:
        return False
    try:
        pt = wintypes.POINT(int(x), int(y))
        if not _user32.ScreenToClient(hwnd, ctypes.byref(pt)):
            return False
        cx, cy = int(pt.x), int(pt.y)
        if cx < 0 or cy < 0:
            return False
        lparam = ((cy & 0xFFFF) << 16) | (cx & 0xFFFF)
        if not _user32.PostMessageW(hwnd, WM_MOUSEMOVE, 0, lparam):
            return False
        time.sleep(POSTMSG_HOVER_SLEEP_S)
        if not _user32.PostMessageW(hwnd, WM_LBUTTONDOWN, MK_LBUTTON, lparam):
            return False
        time.sleep(POSTMSG_UP_SLEEP_S)
        if not _user32.PostMessageW(hwnd, WM_LBUTTONUP, 0, lparam):
            return False
        return True
    except Exception:
        return False


def click_at(x: int, y: int, hwnd: Optional[int] = None,
             require_over: bool = False) -> Tuple[bool, str]:
    """在绝对屏幕坐标 (x, y) 处模拟鼠标左键单击, 多机制自动降级。

    v32 返回 (ok, detail): ok=True 表示点击动作已执行; detail 说明用到的
    机制或失败原因(含 GetLastError)。调用方仍需 REST read-back 验证是否
    真的锁上了英雄。

    v4.3.15 新增 require_over: 注入前先用 point_over_window 确认该屏幕坐标
    处最上层的窗口就是目标客户端 —— cursor/sendinput 注入的是绝对屏幕坐标,
    点中的永远是"光标底下那个窗口", 与前台无关。确认过之后再注入, 既不会
    点到用户浏览器(v4.3.10 担心的风险), 也不受前台锁影响(不再需要因为
    SetForegroundWindow 失败而放弃点击 —— 放弃会让自动选英雄彻底失效)。

    v4.4.0 修复:
      P0-7  去掉 `if x >= 0 and y >= 0` 这个 gate —— 副屏在主屏左侧/上方时
            屏幕坐标本就是负数, 原写法直接跳过全部注入机制, errs 为空,
            返回 (False, '') (2026-10-xx 副屏用户"点卡毫无反应且日志无原因")。
            负坐标交给 _sendinput_click 内部已有的 clamp(0..65535) 处理。
      P1-13 机制顺序改为 SendInput 优先、cursor(SetCursorPos 会抢用户光标)
            降为兜底; require_over 下"宁可报失败不报假成功"的既有语义不变。
      P1-12 注入前增加 UIPI 提权侦查, 被阻断时返回独立原因 'uipi-blocked'。
    """
    errs = []
    if require_over and hwnd and not point_over_window(hwnd, x, y):
        return False, 'point-not-over-client'
    # v4.4.0 修复（P1-12）：客户端已提权而本进程未提权时，注入的鼠标消息会被
    # UIPI 静默丢弃（无错误码、无异常），必须显式返回独立原因让上层提示用户
    # 以管理员身份重启，而不是静默失败或被误判为"坐标不对"。
    if uiPi_blocked(hwnd):
        return False, 'uipi-blocked'
    if _sendinput_click(x, y):
        return True, 'sendinput'
    errs.append(f'sendinput(err={_last_err()})')
    if _cursor_click(x, y):
        return True, 'cursor'
    errs.append(f'cursor(err={_last_err()})')
    # v4.3.15: require_over 模式下**不**降级到 postmsg —— PostMessage 只是"消息已投递",
    # 对 CEF 渲染的客户端实测完全不生效, 却会返回 True 让调用方误以为点中了
    # (2026-09-24 日志: 13 次 postmsg 全未命中, 最终把用户自己手选的英雄误报成
    #  "自动选英雄(点卡) → 铸星龙王")。宁可报失败, 也不要假成功。
    if hwnd and not require_over:
        if _postmsg_click(hwnd, x, y):
            return True, 'postmsg'
        errs.append(f'postmsg(err={_last_err()})')
    return False, '; '.join(errs)
