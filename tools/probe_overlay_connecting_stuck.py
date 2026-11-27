# -*- coding: utf-8 -*-
r"""probe_overlay_connecting_stuck.py —— 复现「小窗永远停在黄色『连接中』」。

【现象】用户实测报障(v4.7.2): 日志里 `[App] 已连接客户端` 与
`[WS] on_open 成功` 都在, 主窗/功能都正常, 但小窗顶部**和**底部一直显示琥珀色
「连接中」, 等多久都不翻绿。

【为什么 v4.6.13 的修复没能盖住它】
v4.6.13 补上了 `_set_conn_state('connected')`(在那之前 'connected' 是 0 次调用),
但补的位置在 `_apply_ui_connected` 的**跃迁分支里**:

    if connected and not self._ui_connected:      # ← 只有 False→True 才进去
        ...
        self._set_conn_state('connected', '已连接')

于是「小窗三态」不是"由当前事实重算", 而是"由状态跃迁顺带触发的一次性动作"。
只要有一次 interleaving 把琥珀色盖上去, 之后所有 `_set_ui_connected(True)` 都是
**非跃迁的 no-op**, 三态通道再没有任何机会被纠正。

【v4.7.3 的修法】把三态收口成一个函数 `_sync_conn_state()`: 按当前事实
(已连接 > 连接中 > 未连接)算出目标值, 与"屏上当前值"比较, 幂等推送。
所有写点(`_try_connect` / `_settle_connecting` / `_apply_ui_connected` /
`_on_start_done`)都改成调它, 不再各自 `_set_conn_state`。

【真实的 interleaving(本项目每天都在发生)】
REST 探活与 WS 是两条独立通道, 而且 REST 通常**先**成功:
    t0  `_on_probe_done`(3s 周期探活) → `_set_ui_connected(True)` → 跃迁 → 绿
    t1  5s 周期的 `_try_connect` 触发 —— 此时 `_ws.is_connected` **仍是 False**
        (on_open 还没到), 而 `_try_connect` 只判 ws/单飞,**根本不看 `_ui_connected`**
        → 往小窗打「连接中」琥珀
    t2  `[WS] on_open` → `_on_connect_success` → `_set_ui_connected(True)`
        → `not self._ui_connected` 为假 → **不推三态** → 小窗永远停在琥珀
    t3  之后每 3s 的探活同样是非跃迁 no-op; 每 5s 的 `_try_connect` 还在反复补琥珀。

dadan-build\dist\data\logs\app.log 里两行的先后正是这个顺序(3771 先于 3789):
    [App] 已连接客户端（你们也会被审判的#90061）
    ...
    [WS] on_open 成功: port=54808

【本探针做什么】
用**真实** AppController + MainWindow + OverlayWindow(离屏)按上面 t0→t1→t2→t3
走一遍, 逐步打印小窗顶部文案。修复前: t2/t3 之后仍是「连接中」(退出码 1);
修复后: 「已连接」(退出码 0)。

用法(离屏, 不碰真实客户端):
    E:\HuanJing\01_Runtimes\Python\envs\dadan\Scripts\python.exe ^
        tools\probe_overlay_connecting_stuck.py
"""
import os
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FAILS = []


def check(label, got, want):
    ok = (got == want)
    print(f'  {"OK  " if ok else "FAIL"} {label}: 得到 {got!r} / 期望 {want!r}')
    if not ok:
        FAILS.append(f'{label}: 得到 {got!r} / 期望 {want!r}')
    return ok


def main():
    from PyQt5.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    import main as M
    from ui.overlay_window import OverlayWindow
    from ui.main_window import MainWindow

    ctrl = M.AppController()
    ov = OverlayWindow()
    ctrl._overlay = ov
    # 真实 MainWindow —— apply_connection_ui 内部要 emit 信号, 假替身会掩盖行为
    ctrl._main = MainWindow()
    # 屏蔽与本缺陷无关的副作用(自动启动脚本 / 联网补拉英雄)
    ctrl._maybe_auto_start_on_connect = lambda: None
    ctrl._start_remote_champ_refresh = lambda: None
    # 探活线程在离屏探针里不起, 避免真实 REST; 置成"已在跑"让 _refresh_ui_conn
    # 走正常分支而不是"线程挂了"的降级分支。
    ctrl._probe_thread.isRunning = lambda: True

    def text():
        app.processEvents()
        return ov._live_text.text()

    print('[t0] REST 探活先成功 → _on_probe_done 走 _set_ui_connected(True)')
    ctrl._set_ui_connected(True, '你们也会被审判的#90061')
    check('t0 小窗文案', text(), '已连接')
    if not check('t0 _ui_connected', ctrl._ui_connected, True):
        pass

    print('[t1] 5s 重连 tick: WS 尚未 on_open (is_connected=False)')
    print('     ⚠️ 此时界面**已经**是"已连接"(t0), 所以绝不能被重新打回琥珀 ——')
    print('        这正是缺陷的起点。修复后 t1 应保持"已连接"。')
    ctrl._ws._connected = False
    # 只保留 _try_connect 的"打点"逻辑, 不起真实扫描线程(worker 全盘 BFS 会很慢)
    ctrl._try_connect_worker = lambda: None
    ctrl._try_connect()
    check('t1 小窗文案(不得被琥珀盖回)', text(), '已连接')
    # 对照: 真的没连上时, 打点必须照旧生效(不能把"连接中"这个中间态修没了)
    # ⚠️ worker 被上面替成了空壳, 不会走到它的 `finally: self._connecting = False`,
    # 单飞标志会一直是 True —— 而"在途连接"本来就该显示「连接中」。要验"断开"必须
    # 先把这个标志落下来, 否则测的是另一回事。
    ctrl._connecting = False
    ctrl._set_ui_connected(False, '')
    check('t1b 断开后回到', text(), '未连接')
    ctrl._try_connect()
    check('t1c 未连接时打点仍生效', text(), '连接中')

    print('[t2] [WS] on_open → _on_ws_connected → _on_connect_success 置绿')
    ctrl._ws._connected = True
    ctrl._on_connect_success()
    check('t2 _ui_connected', ctrl._ui_connected, True)
    ok_t2 = check('t2 小窗文案(核心断言)', text(), '已连接')

    print('[t3] 之后 3s 周期的探活再置一次绿(非跃迁 no-op) —— 能否自愈?')
    ctrl._set_ui_connected(True, '你们也会被审判的#90061')
    check('t3 小窗文案', text(), '已连接')

    print()
    if FAILS:
        print(f'[FAIL] 缺陷已复现(共 {len(FAILS)} 项): 小窗停在下发时的琥珀色, '
              f'后续置绿全部是 no-op')
        for f in FAILS:
            print(f'   - {f}')
        return 1
    print('[OK] 小窗三态已能随真实连接态归正(不再停在「连接中」)')
    if not ok_t2:
        print('   (注: t2 已通过, 但前面有其它步骤未过)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
