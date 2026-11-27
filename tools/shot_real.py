# -*- coding: utf-8 -*-
"""真实平台渲染 MainWindow 并截图（非 offscreen，文字正常渲染）。
用法: python tools/shot_real.py [out.png]
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from ui.main_window import MainWindow

w = MainWindow()
w.show()
app.processEvents()

info = {
    'name': '你们也会被审判的#90061',
    'level': 452,
    'xp_pct': 63.0,
    'solo': {'tier': 'SILVER', 'division': 'I', 'leaguePoints': 60},
    'flex': None,
    'honor': 5,
    'wallet_ip': 258477,
    'wallet_cosmetic': 1254,
    'wallet_mythic': 56,
    'mastery_top': [
        {'champion_id': 25, 'name': '堕落天使', 'level': 72, 'points': 760424},
        {'champion_id': 17, 'name': '迅捷斥候', 'level': 37, 'points': 374248},
        {'champion_id': 90, 'name': '虚空先知', 'level': 16, 'points': 152552},
    ],
    'recent': {'wins': 3, 'losses': 8, 'total': 11},
}
w.update_account_info(info)

# 模拟已连接状态
w._do_conn(True, '你们也会被审判的#90061')
w._do_phase('等待启动')

# 塞几张熟练度占位头像（纯色 pixmap）确认头像链路渲染
from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtCore import Qt
for i, cid in enumerate((25, 17, 90)):
    pm = QPixmap(64, 64)
    pm.fill(QColor(90 + i * 30, 140, 180))
    w._do_mastery_icon(i, None) if False else None
    chip = (w._chip_m1, w._chip_m2, w._chip_m3)[i]
    chip._avatar.setPixmap(pm.scaled(32, 32, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation))

app.processEvents()
out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'tools', 'shot_real.png')
w.grab().save(out)
print('saved:', out)
