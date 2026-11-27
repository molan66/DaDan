# -*- coding: utf-8 -*-
"""Enumerate all top-level windows whose title contains 大蛋."""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
GetWindowTextW = user32.GetWindowTextW
GetWindowRect = user32.GetWindowRect
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
IsWindowVisible = user32.IsWindowVisible
IsIconic = user32.IsIconic

results = []

@ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
def cb(hwnd, lparam):
    buf = ctypes.create_unicode_buffer(256)
    GetWindowTextW(hwnd, buf, 256)
    t = buf.value
    if "大蛋" in t:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        r = RECT()
        GetWindowRect(hwnd, ctypes.byref(r))
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((hwnd, t, pid.value, IsWindowVisible(hwnd), IsIconic(hwnd),
                        (r.left, r.top, r.right - r.left, r.bottom - r.top)))
    return True

EnumWindows(cb, 0)
for hwnd, t, pid, vis, iconic, rect in results:
    print(f"hwnd={hwnd} pid={pid} visible={vis} iconic={iconic} rect={rect} title={t}")
if not results:
    print("NO_MATCHING_WINDOWS")
