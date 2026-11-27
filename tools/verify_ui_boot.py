# -*- coding: utf-8 -*-
"""UI 启动级验证（仅验证 UI 层构造与交互，不跑 AppController 主流程）。

用法: <venv python> tools/verify_ui_boot.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

app = QApplication(sys.argv)

from ui.main_window import MainWindow
from ui.overlay_window import OverlayWindow

ok = True


def check(name, fn):
    global ok
    try:
        fn()
        print('[OK]', name)
    except Exception as e:
        ok = False
        print('[FAIL]', name, '->', type(e).__name__, e)


def main():
    mw = MainWindow()
    check('MainWindow 构造 (期望 720x540)',
          lambda: assert_size(mw, 720, 540))
    for i in range(5):
        check('主窗切页 #%d' % i, lambda i=i: mw._switch_page(i))
    check('账号卡更新', lambda: mw.update_account_info({
        'name': '测试召唤师', 'level': 30, 'xp_pct': 62,
        'solo': {'tier': 'GOLD', 'division': 'II', 'leaguePoints': 53},
        'solo_wins': 30, 'solo_losses': 40,
        'flex': None,
        'wallet_ip': 52000, 'wallet_cosmetic': 980}))
    check('连接态三态', lambda: (
        mw.apply_connection_ui(True, '测试'),
        mw.apply_connection_ui(False)))

    ow = OverlayWindow()
    check('OverlayWindow 构造 (期望 300x420)',
          lambda: assert_size(ow, 300, 420))
    check('小窗备选 12 个 + 三态',
          lambda: (ow.update_bench_champions(
              list(range(1, 13)), current_id=5),
              ow.update_settings({'preferred_champions': [3, 7]}),
              ow.set_preinput(2)))
    check('小窗连接三态', lambda: (
        ow.set_conn_state('connected'),
        ow.set_conn_state('connecting'),
        ow.set_conn_state('disconnected')))
    check('小窗 ReadyCheck 出现/收起',
          lambda: (ow.show_ready_check_buttons(True),
                   ow.show_ready_check_buttons(False)))
    check('小窗日志 5 行', lambda: [ow.append_mini_log('行 %d' % i)
                                    for i in range(6)])
    check('小窗运行态切换', lambda: ow.set_script_running(True))

    QTimer.singleShot(800, app.quit)
    sys.exit(app.exec_())


def assert_size(w, ww, hh):
    if (w.width(), w.height()) != (ww, hh):
        raise AssertionError('尺寸 %dx%d != %dx%d' % (
            w.width(), w.height(), ww, hh))


if __name__ == '__main__':
    main()
