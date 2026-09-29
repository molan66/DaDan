"""Glass Effect - 液态玻璃效果（方案E: 分层半透明 + 系统Backdrop + 系统圆角）

路线选择（Win11 22H2 / build 22621+）:
  使用 Windows 11 官方路线: 窗口保持分层半透明(WA_TranslucentBackground),
  通过 DWM 系统级背景 DWMWA_SYSTEMBACKDROP_TYPE=3(Acrylic) 提供模糊,
  DWMWA_WINDOW_CORNER_PREFERENCE=2(ROUND) 提供系统圆角(8px),
  并清除 WS_EX_WINDOWEDGE 等扩展样式去除系统阴影。

  之前反复出现的"黑角/白边/阴影/直角"根因:
  1. 旧代码显式设置了 DONOTROUND + NCRP_DISABLED, 等于要求系统不圆角
  2. DwmExtendFrameIntoClientArea(-1) 把玻璃框架扩展到整个矩形,
     任何 Qt 侧圆角都会被这个矩形玻璃盖住, 产生四角伪影
  3. 老式 SWCA Acrylic(SetWindowCompositionAttribute) 只支持矩形模糊,
     无法被裁成圆角

  Win10 及老版本自动回退旧方案(方形+老式Acrylic), 不破坏兼容。
"""

import ctypes
from ctypes import wintypes

_win_ver = getattr(__import__('sys'), 'getwindowsversion', lambda: None)()
_WIN11_BACKDROP = bool(_win_ver) and _win_ver.build >= 22621  # Win11 22H2+

user32 = ctypes.WinDLL('user32', use_last_error=True)
dwmapi = ctypes.WinDLL('dwmapi', use_last_error=True)

# v4.4.2(P1-14/P2-3): 补 argtypes/restype —— 对照 lcu/clicker.py 的严格声明。
# 64 位下 HWND/LONG 是 64 位, 不声明会走 ctypes 默认 c_int(32 位) 截断,
# 高位被丢弃后 SetWindowLongW/SetWindowPos 拿到错误句柄。use_last_error=True
# 只有配合 restype 才能正确捕获失败(见 _dwm_attr 的返回值检查)。
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_uint]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowCompositionAttribute.argtypes = [wintypes.HWND, ctypes.c_void_p]
user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
dwmapi.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD,
                                         ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

# 常量
GWL_EXSTYLE = -20
WS_EX_WINDOWEDGE = 0x00010000
WS_EX_DLGMODALFRAME = 0x00000001
WS_EX_STATICEDGE = 0x00020000
SWP_FRAMECHANGED = 0x0020
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004

# DWM 属性
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_SYSTEMBACKDROP_TYPE = 38
DWMSBT_NONE = 0              # 无背景（用于重置）
DWMSBT_TRANSIENTWINDOW = 3   # Acrylic
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_ROUND = 2             # 系统圆角 8px

# 老式 SWCA Acrylic 属性 ID
WCA_ACCENT_POLICY = 19
ACCENT_ENABLE_ACRYLICBLURBEHIND = 3

# v4.4.2(P1-14/P2-3): 结构体提升到模块级 —— 原在 _apply_legacy 函数体内定义,
# 每次调用都重建三个 ctypes 类(重复类定义 + 每次调用重建, 纯浪费且不利于
# 后续类型复用)。
class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", wintypes.DWORD),
        ("AccentFlags", wintypes.DWORD),
        ("GradientColor", wintypes.DWORD),
        ("AnimationId", wintypes.DWORD),
    ]


class WINDOW_COMPOSITION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Attribute", wintypes.DWORD),
        ("Data", ctypes.POINTER(ACCENT_POLICY)),
        ("SizeOfData", wintypes.ULONG),
    ]


class MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


def _dwm_attr(hwnd, attr, val):
    """设置 DWM 属性。返回 HRESULT(0=成功); 任何异常返回 -1。

    v4.4.2(P1-14): 原实现把异常吞成 -1、正常调用也返回 HRESULT, 但调用方
    _apply_native_rounded 四次调用全丢弃返回值 → 失败被污染成"已应用"。
    """
    try:
        v = ctypes.c_int(val)
        return int(dwmapi.DwmSetWindowAttribute(
            hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)))
    except Exception:
        return -1


def apply_liquid_glass(widget, enable_shadow=False):
    """应用液态玻璃效果（自动选择方案）"""
    try:
        if _WIN11_BACKDROP:
            return _apply_native_rounded(widget, enable_shadow)
        return _apply_legacy(widget, enable_shadow)
    except Exception as e:
        print(f'[Glass] 失败: {e}')
        return False


def _hwnd(widget):
    try:
        return int(widget.winId())
    except Exception:
        return 0


def _clear_shadow_styles(hwnd):
    """清除扩展样式里产生阴影/边框的位, 并强制刷新窗口框架"""
    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex_style &= ~WS_EX_WINDOWEDGE
    ex_style &= ~WS_EX_DLGMODALFRAME
    ex_style &= ~WS_EX_STATICEDGE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex_style)
    user32.SetWindowPos(
        hwnd, 0, 0, 0, 0, 0,
        SWP_FRAMECHANGED | SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
    )


def _apply_native_rounded(widget, enable_shadow=False):
    """Win11: 系统 Acrylic 模糊 + 系统圆角(8px), 无阴影。

    v4.4.2(P1-14): 原实现四次 _dwm_attr 全丢弃返回值、无条件 return True,
    失败被污染成"已应用"。现检查每个 HRESULT, 关键属性(Acrylic)失败即返回 False。
    """
    hwnd = _hwnd(widget)
    if not hwnd:
        return False
    _clear_shadow_styles(hwnd)
    # 深色模式/圆角失败不致命(降级为原生外观), 但 Acrylic 失败要如实上报。
    _dwm_attr(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1)
    # 先重置再应用：窗口 hide/show 后 DWM 会清除 backdrop，
    # 不重置直接设 Acrylic 可能不生效（表现为关闭再打开后玻璃效果丢失）
    _dwm_attr(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_NONE)
    hr = _dwm_attr(hwnd, DWMWA_SYSTEMBACKDROP_TYPE, DWMSBT_TRANSIENTWINDOW)  # Acrylic
    _dwm_attr(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)            # 圆角
    if hr not in (0,):
        print(f'[Glass] Acrylic 应用失败: hwnd={hwnd}, HRESULT={hr}')
        return False
    return True


def _apply_legacy(widget, enable_shadow=False):
    """老系统(Win10): 方形 + 老式 SWCA Acrylic(矩形模糊)"""
    hwnd = _hwnd(widget)
    if not hwnd:
        return False

    accentPolicy = ACCENT_POLICY()
    accentPolicy.AccentState = ACCENT_ENABLE_ACRYLICBLURBEHIND
    # v4.4.2(P1-14): 原 0x292929 是 ABGR, 最高字节 alpha=0 → Win10 回退
    # Acrylic 完全透明无着色(看不见磨砂)。补 alpha=0x99(约 60% 不透明),
    # 其余 RGB 保持原 0x29 灰。ABGR 布局 = 0xAABBGGRR。
    accentPolicy.GradientColor = 0x99292929
    accentPolicy.AccentFlags = (0x20 | 0x40 | 0x80 | 0x100) if enable_shadow else 0

    winCompAttrData = WINDOW_COMPOSITION_ATTRIBUTES()
    winCompAttrData.Attribute = WCA_ACCENT_POLICY
    winCompAttrData.SizeOfData = ctypes.sizeof(accentPolicy)
    winCompAttrData.Data = ctypes.pointer(accentPolicy)
    ok = user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(winCompAttrData))

    # v4.4.2(P1-14): 原无条件 return True, SetWindowCompositionAttribute 失败也
    # 谎称成功。现按 BOOL 返回值如实上报。
    if not ok:
        print(f'[Glass] 旧式Acrylic失败: hwnd={hwnd}')
        return False
    print(f'[Glass] 旧式Acrylic已设置(Win10兼容): hwnd={hwnd}, shadow={enable_shadow}')
    return True
