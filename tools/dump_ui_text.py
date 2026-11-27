# -*- coding: utf-8 -*-
"""离屏渲染 MainWindow 并把可见控件按坐标输出成文字版界面,用于无法直接看图时核对 UI。
用法: python tools/dump_ui_text.py
"""
import os, sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QAbstractButton, QProgressBar
app = QApplication(sys.argv)

from ui.main_window import MainWindow
w = MainWindow()
w.show()
app.processEvents()

# 模拟"已连接 + 已就绪 + 你的账号数据" 与截图时运行状态一致
w._update_dot(True)
w._conn_label.setText('客户端已连接')
w._phase_label.setText('等待启动')
info = {
    'name': '你们也会被审判的#90061',
    'level': 452,
    'xp_pct': 63.0,
    'solo': {'tier': 'SILVER', 'division': 'I', 'leaguePoints': 60},
    'flex': None,
    'solo_high': {'tier': 'SILVER', 'division': 'I', 'leaguePoints': 60},
    'flex_high': None,
    'honor': 5,
}
w.update_account_info(info)
app.processEvents()
w._fab.setText('⚡  启动脚本')
app.processEvents()

out = os.path.join(ROOT, 'tools', 'layout_live.png')
w.grab().save(out)

W, H = w.width(), w.height()
print(f'window {W}x{H}')

# 头像 pixmap 是否有内容
if w._avatar_lbl.pixmap() is not None:
    pm = w._avatar_lbl.pixmap()
    print(f'avatar pixmap: {pm.width()}x{pm.height()} null={pm.isNull()}')
else:
    print('avatar pixmap: None')

rows = []
for cls in (QLabel, QAbstractButton):
    for obj in w.findChildren(cls):
        if not obj.isVisible():
            continue
        text = (obj.text() or '').strip()
        # QProgressBar 是 QWidget 但没 text;跳过按钮里的 icon 文字叠加
        if cls is QAbstractButton and isinstance(obj, QPushButton) and not text:
            continue
        if not text:
            continue
        tl = obj.mapTo(w, obj.rect().topLeft())
        rows.append((tl.y(), tl.x(), text))

rows.sort(key=lambda r: (r[0], r[1]))
for y, x, t in rows:
    print(f'({y:4d},{x:4d}) {t}')
print('COUNT', len(rows))
