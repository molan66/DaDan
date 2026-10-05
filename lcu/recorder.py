"""lcu.recorder —— 录制 LOL 登录器上的人工点击, 生成可回放的步骤序列

为什么需要录制(v4.4.12):
    登录器是腾讯 TPF 自绘 UI, 按钮位置只能靠截图逐像素猜。实测从 v4.4.5 到 v4.4.11
    连续 7 版都在"猜坐标 → 用户实测 → 某一步点空"之间反复(用户原话:"我已阅读那里
    没点好, 导致后边也点不到了")。录制把这件事从"猜"变成"抄":
    用户手动完整走一遍, 程序原样记下每次点击的位置与间隔, 之后一键回放。

实现要点:
    - **WH_MOUSE_LL 全局低级鼠标钩子**: 登录器属于另一个进程, 只有系统级钩子才
      收得到它的点击(装在自身窗口上的事件根本收不到)。
    - 钩子必须装在**有消息循环的线程**里: 这里自建后台线程跑 PeekMessage 循环,
      绝不挂到 Qt 主线程(否则钩子回调会把界面拖死)。
    - 停止方式: 录制期间主窗会隐藏, 所以用 **F9** 作停止热键(GetAsyncKeyState
      轮询, 不额外装键盘钩子)。
    - 坐标换算: 记录时先用登录器窗口 rect 把屏幕坐标压成窗口内相对比例(fx, fy),
      与回放侧口径完全一致 —— 换分辨率/窗口位置都成立。
    - 过滤: 点在大蛋小助手自己窗口上的鼠标不进步骤序列。
    - 间隔夹取: 两次点击的间隔夹到 MIN/MAX_WAIT_S —— 太短没意义, 太长通常是用户
      中途走开(回放时不该照等)。最后一步之后固定 3s(留给客户端启动)。

⚠️ 录制期间**不要拖动登录器窗口**: 坐标是按录制开始时的窗口矩形换算的, 中途移动
   窗口会让后续记录的比例全部偏移。
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes
from typing import List, Optional, Tuple

_user32 = ctypes.WinDLL('user32', use_last_error=True)
_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

WH_MOUSE_LL = 14
WM_LBUTTONDOWN = 0x0201
PM_REMOVE = 0x0001
VK_F9 = 0x78

# HHOOK 在 ctypes.wintypes 里没有独立类型, 用 HANDLE 等价表示
_HHOOK = wintypes.HANDLE
_LRESULT = ctypes.c_ssize_t


class _MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('pt', wintypes.POINT),
        ('mouseData', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_size_t),
    ]


_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM,
                               wintypes.LPARAM)

_user32.SetWindowsHookExW.argtypes = [ctypes.c_int, _HOOKPROC,
                                      wintypes.HINSTANCE, wintypes.DWORD]
_user32.SetWindowsHookExW.restype = _HHOOK
_user32.UnhookWindowsHookEx.argtypes = [_HHOOK]
_user32.UnhookWindowsHookEx.restype = wintypes.BOOL
_user32.CallNextHookEx.argtypes = [_HHOOK, ctypes.c_int, wintypes.WPARAM,
                                   wintypes.LPARAM]
_user32.CallNextHookEx.restype = _LRESULT
_user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                 wintypes.UINT, wintypes.UINT, wintypes.UINT]
_user32.PeekMessageW.restype = wintypes.BOOL
_user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
_user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
_user32.GetAsyncKeyState.restype = ctypes.c_short
_user32.WindowFromPoint.argtypes = [wintypes.POINT]
_user32.WindowFromPoint.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                             ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD

_kernel32.GetCurrentThreadId.argtypes = []
_kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class ClickRecorder:
    """全局鼠标点击录制器。

    线程模型: start() 起一条后台线程, 在那条线程里 SetWindowsHookExW + PeekMessage
    循环; stop() 置位停止标志并 join。钩子回调只做"取坐标 + 追加到列表"(加锁),
    不做任何耗时操作 —— 低级钩子回调卡住会拖慢整个系统的鼠标响应。
    """

    MIN_WAIT_S = 0.3      # 两次点击最小间隔(低于此视为误触/连点)
    MAX_WAIT_S = 30.0     # 最大间隔(超过此视为用户中途走开, 回放不该照等)
    TAIL_WAIT_S = 3.0     # 最后一步之后的等待(留给客户端启动)
    # v4.4.14: 允许的越界余量 —— 用户可能点在按钮边缘, 略微出界仍算有效。
    OUT_MARGIN = 0.02

    def __init__(self):
        self._thread: Optional[threading.Thread] = None
        self._hook = None
        self._proc = None            # 必须持引用: 被 GC 回收会导致回调野指针崩溃
        self._lock = threading.Lock()
        self._raw: List[Tuple[int, int, float]] = []   # (x, y, monotonic)
        self._rect = None            # 录制开始时的登录器窗口矩形
        self._running = False
        self._stop_flag = False
        self._ready = threading.Event()
        self._ok = False
        self._skip_pid = os.getpid()
        # v4.4.14: 上一次换算时被丢弃的"落在窗口外"的点击数(供 UI 提示)
        self.last_dropped = 0

    # ---------------- 状态 ----------------
    @property
    def is_running(self) -> bool:
        return self._running

    def count(self) -> int:
        with self._lock:
            return len(self._raw)

    # ---------------- 录制 ----------------
    def start(self, window_rect, skip_pid: Optional[int] = None,
              timeout: float = 3.0) -> bool:
        """开始录制。window_rect = (left, top, right, bottom), 用于换算相对比例。

        返回 True = 钩子装成功, 已开始录制。
        """
        if self._running:
            return True
        try:
            left, top, right, bottom = window_rect
            if right - left <= 0 or bottom - top <= 0:
                return False
        except Exception:
            return False

        with self._lock:
            self._raw = []
        self._rect = (int(left), int(top), int(right), int(bottom))
        self._skip_pid = int(skip_pid if skip_pid is not None else os.getpid())
        self._stop_flag = False
        self._ok = False
        self._ready.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name='click-recorder')
        self._thread.start()
        self._ready.wait(timeout)
        if not self._ok:
            self._running = False
            self._stop_flag = True
        return bool(self._ok)

    def stop(self, window_rect=None) -> List[Tuple[float, float, float]]:
        """停止录制并返回步骤序列 [(fx, fy, wait_s), ...]。

        即使线程已因 F9 自行退出, 这里也能正常取到已录的数据。
        window_rect 可覆盖开始时的矩形(默认沿用开始时那份)。
        """
        self._stop_flag = True
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=3.0)
        self._thread = None
        self._running = False

        rect = window_rect if window_rect else self._rect
        with self._lock:
            raw = list(self._raw)
        return self._to_steps(raw, rect)

    def steps_preview(self) -> List[Tuple[float, float, float]]:
        """只做换算、不改录制状态(供"停止前预览"这类只读场景)。"""
        with self._lock:
            raw = list(self._raw)
        return self._to_steps(raw, self._rect)

    # ---------------- 内部 ----------------
    def _is_own_window(self, x: int, y: int) -> bool:
        """该屏幕点是否落在大蛋小助手自己的窗口上(自己的点击不该被录进去)。"""
        try:
            hwnd = _user32.WindowFromPoint(wintypes.POINT(int(x), int(y)))
            if not hwnd:
                return False
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value) == self._skip_pid
        except Exception:
            return False

    def _run(self):
        def _callback(n_code, w_param, l_param):
            try:
                if n_code == 0 and w_param == WM_LBUTTONDOWN:
                    info = ctypes.cast(l_param,
                                       ctypes.POINTER(_MSLLHOOKSTRUCT)).contents
                    x, y = int(info.pt.x), int(info.pt.y)
                    if not self._is_own_window(x, y):
                        with self._lock:
                            self._raw.append((x, y, time.monotonic()))
            except Exception:
                pass
            # 必须把事件继续传下去, 否则整个系统的鼠标都会失灵
            return _user32.CallNextHookEx(None, n_code, w_param, l_param)

        try:
            self._proc = _HOOKPROC(_callback)
            self._hook = _user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc, None, 0)
            self._ok = bool(self._hook)
        except Exception:
            self._ok = False
        finally:
            self._ready.set()

        if not self._ok:
            self._running = False
            return

        msg = wintypes.MSG()
        try:
            while not self._stop_flag:
                # 泵消息(低级钩子靠它才能收到回调)
                while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))
                if _user32.GetAsyncKeyState(VK_F9) & 0x8000:
                    self._stop_flag = True
                    break
                time.sleep(0.01)
        except Exception:
            pass
        finally:
            try:
                if self._hook:
                    _user32.UnhookWindowsHookEx(self._hook)
            except Exception:
                pass
            self._hook = None
            self._proc = None
            self._running = False

    def _to_steps(self, raw, rect):
        """屏幕坐标序列 → 窗口内相对比例 + 间隔(夹取)。

        v4.4.14: 先**丢弃落在登录器窗口外**的点击再算间隔。实测录到过一次
        frac=(1.0844, 1.2227) —— 那是用户在切换窗口时点到了别处(比如桌面/任务栏),
        这种点回放时必然被 require_over 拦下(报 point-not-over-client), 留在序列里
        只会白白浪费一步并打乱节奏。判定留 OUT_MARGIN 余量: 点在按钮边缘略微出界
        仍算有效, 只有明显跑到窗外才丢。
        """
        self.last_dropped = 0
        if not raw or not rect:
            return []
        left, top, right, bottom = rect
        w = float(right - left)
        h = float(bottom - top)
        if w <= 0 or h <= 0:
            return []

        lo, hi = -self.OUT_MARGIN, 1.0 + self.OUT_MARGIN
        kept = []
        for (x, y, ts) in raw:
            fx = (x - left) / w
            fy = (y - top) / h
            if lo <= fx <= hi and lo <= fy <= hi:
                kept.append((fx, fy, ts))
            else:
                self.last_dropped += 1

        n = len(kept)
        if not n:
            return []
        out = []
        for i, (fx, fy, ts) in enumerate(kept):
            if i + 1 < n:
                wait = kept[i + 1][2] - ts
                wait = max(self.MIN_WAIT_S, min(self.MAX_WAIT_S, wait))
            else:
                wait = self.TAIL_WAIT_S
            # 夹到 [0,1]: 允许的边缘越界在回放时仍按窗口内算, 与 click_frac 口径一致
            out.append((round(max(0.0, min(1.0, fx)), 4),
                        round(max(0.0, min(1.0, fy)), 4),
                        round(wait, 2)))
        return out
