"""截图取证: 海克斯(3270)选人"卡片"出现瞬间抓全屏 + LOL 客户端窗口矩形。

背景: v31 自动选改为"鼠标点卡"方案 —— 卡片是客户端渲染出来的、必然在服务器
允许子集内, 点它就绕开了"LCU 读不到卡组"的死结。但点哪里需要精确坐标:
本脚本在卡片出现时自动截全屏图, 并记录屏幕分辨率与 LOL 客户端窗口位置,
供 AI 标定点击位置。锁定后再补拍一张(备选池/交换池布局), 一次取证两个界面。

用法(双击 diag_cardshot.bat): 先启动脚本, 再进自定义海克斯房间→开始,
卡片一出现会自动截图保存, 锁定后再截一张, 然后自己退出。
全程保持 LOL 客户端可见, 别切走/别盖住。
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lcu.connector import LCUConnector
from lcu.api import LCUAPI

_TDIR = os.path.dirname(os.path.abspath(__file__))
_OUT_TXT = os.path.join(_TDIR, 'diag_cardshot.txt')
_PNG_CARD = os.path.join(_TDIR, 'diag_cardshot_cards.png')
_PNG_LOCK = os.path.join(_TDIR, 'diag_cardshot_locked.png')


from _tee import _Tee


def _dpi_aware():
    """让本进程按物理像素取屏/取窗口, 保证坐标与鼠标一致。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def _client_rects():
    """枚举可见窗口, 返回所有 LeagueClient 主窗口矩形(按面积降序)。"""
    import psutil
    rects = []
    user32 = ctypes.windll.user32
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                     wintypes.LPARAM)

    def cb(hwnd, _lp):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            name = (psutil.Process(pid.value).name() or '').lower()
        except Exception:
            return True
        if 'league' in name:  # LeagueClient.exe / LeagueClientUx.exe (CEF 渲染层) 都命中
            r = wintypes.RECT()
            if user32.GetWindowRect(hwnd, ctypes.byref(r)):
                w, h = r.right - r.left, r.bottom - r.top
                if w > 300 and h > 200:
                    rects.append({'left': r.left, 'top': r.top,
                                  'right': r.right, 'bottom': r.bottom,
                                  'w': w, 'h': h})
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    rects.sort(key=lambda d: d['w'] * d['h'], reverse=True)
    return rects


def _capture(path):
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=False)  # 主屏全屏(物理像素)
    img.save(path)
    return img.size


def _my_action(s):
    lc = (s or {}).get('localPlayerCellId')
    for g in ((s or {}).get('actions') or []):
        if not isinstance(g, list):
            g = [g]
        for a in g:
            if a.get('actorCellId') == lc:
                return a
    return None


def _snap(s):
    """压缩 session 状态便于落盘。"""
    a = _my_action(s)
    lc = (s or {}).get('localPlayerCellId')
    me = next((p for p in ((s or {}).get('myTeam') or [])
               if p.get('cellId') == lc), None) or {}
    bench = [b.get('championId') for b in ((s or {}).get('benchChampions') or [])
             if isinstance(b, dict)]
    return {
        'act_cid': (a or {}).get('championId'),
        'act_done': (a or {}).get('completed'),
        'act_inp': (a or {}).get('isInProgress'),
        'my_cid': me.get('championId'),
        'my_intent': me.get('championPickIntent'),
        'bench': bench,
        'phase': ((s or {}).get('timer') or {}).get('phase'),
        'queueId': (s or {}).get('queueId'),
        'allowSubset': (s or {}).get('allowSubsetChampionPicks'),
    }


def main():
    try:
        f = open(_OUT_TXT, 'w', encoding='utf-8')
        sys.stdout = _Tee(sys.__stdout__, f)
    except Exception:
        pass
    print(f'记录文件: {_OUT_TXT}\n')

    _dpi_aware()

    # 连接(等客户端最多 30s)
    c = LCUConnector()
    ok = False
    for _ in range(10):
        if c.find_client():
            ok = True
            break
        time.sleep(3)
    if not ok:
        print('❌ 找不到 LOL 客户端, 先启动客户端/脚本再试')
        return 1
    api = LCUAPI(c)
    print('✅ 已连接。现在进自定义海克斯房间 → 开始, 等卡片出现(最多 3 分钟)\n')

    # 1) 等 BAN_PICK 且我的 pick 未完成
    t0 = time.time()
    while time.time() - t0 < 180:
        try:
            s = api.get('/lol-champ-select/v1/session')
            a = _my_action(s)
            if (s and a and not a.get('completed')
                    and ((s.get('timer') or {}).get('phase') == 'BAN_PICK')):
                break
        except Exception:
            pass
        time.sleep(0.3)
    else:
        print('❌ 3 分钟内没等到选人界面(卡片期), 退出。')
        return 1

    ts = time.strftime('%H:%M:%S')
    print(f'[{ts}] 检测到选人(未锁定), 等 2.5s 卡片渲染完再截图...')
    time.sleep(2.5)

    snap1 = _snap(s)
    try:
        size = _capture(_PNG_CARD)
        print(f'[{ts}] ✅ 卡片截图已存: {_PNG_CARD}  (屏幕 {size[0]}x{size[1]})')
    except Exception as e:
        print(f'[{ts}] ❌ 截图失败: {type(e).__name__}: {e}')
        size = None

    # 屏幕/窗口信息
    try:
        rects = _client_rects()
    except Exception:
        rects = []
    print(f'--- 屏幕物理分辨率: {size[0]}x{size[1]} (PIL grab) ---' if size
          else '--- 屏幕分辨率: 截图失败 ---')
    if rects:
        print(f'--- LOL 客户端窗口(最大): {rects[0]} ---')
        for i, r in enumerate(rects[1:4]):
            print(f'--- 其它客户端窗口 #{i+1}: {r} ---')
    else:
        print('--- ⚠️ 没找到 LeagueClient 窗口(客户端可能最小化/全屏独占) ---')

    print(f'--- 截图瞬间 session: {json.dumps(snap1, ensure_ascii=False)} ---')

    # 2) 继续等锁定, 锁定瞬间补拍一张(看备选池布局)
    print('\n等待锁定(点卡或倒计时), 最多 30 秒...')
    t1 = time.time()
    while time.time() - t1 < 30:
        try:
            s2 = api.get('/lol-champ-select/v1/session')
        except Exception:
            s2 = None
        a2 = _my_action(s2)
        snap2 = _snap(s2)
        if a2 and a2.get('completed') is True and snap2.get('my_cid'):
            ts2 = time.strftime('%H:%M:%S')
            print(f'[{ts2}] ✅ 已锁定 英雄#{snap2["my_cid"]}, 补拍一张...')
            time.sleep(1.0)
            try:
                _capture(_PNG_LOCK)
                print(f'[{ts2}] ✅ 锁定截图已存: {_PNG_LOCK}')
            except Exception as e:
                print(f'[{ts2}] 锁定截图失败: {type(e).__name__}: {e}')
            print(f'--- 锁定瞬间 session: '
                  f'{json.dumps(snap2, ensure_ascii=False)} ---')
            print(f'--- 锁后 1s session: '
                  f'{json.dumps(_snap(api.get("/lol-champ-select/v1/session")), ensure_ascii=False)} ---')
            break
        time.sleep(0.3)

    print('\n取证结束。把 tools\\diag_cardshot.png 两张图和 .txt 结果发给 AI')
    print('(也可以直接告诉我 "拍好了", 我会自己读文件)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
