# -*- coding: utf-8 -*-
"""真实平台逐页截图：首页/设置/英雄/战绩/日志 各一张。
用法: python tools/shot_pages.py <outdir>
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from ui.main_window import MainWindow
from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtCore import Qt

w = MainWindow()
w.show()
app.processEvents()

info = {
    'name': '你们也会被审判的#90061',
    'level': 452, 'xp_pct': 63.0,
    'solo': {'tier': 'SILVER', 'division': 'I', 'leaguePoints': 60},
    'flex': None, 'honor': 5,
    'wallet_ip': 258477, 'wallet_cosmetic': 1254, 'wallet_mythic': 56,
    'mastery_top': [
        {'champion_id': 25, 'name': '堕落天使', 'level': 72, 'points': 760424},
        {'champion_id': 17, 'name': '迅捷斥候', 'level': 37, 'points': 374248},
        {'champion_id': 90, 'name': '虚空先知', 'level': 16, 'points': 152552},
    ],
    'recent': {'wins': 3, 'losses': 8, 'total': 11},
}
w.update_account_info(info)
w._do_conn(True, '你们也会被审判的#90061')
w._do_phase('等待启动')
for i, cid in enumerate((25, 17, 90)):
    pm = QPixmap(64, 64)
    pm.fill(QColor(90 + i * 30, 140, 180))
    chip = (w._chip_m1, w._chip_m2, w._chip_m3)[i]
    chip._avatar.setPixmap(pm.scaled(32, 32, Qt.KeepAspectRatio,
                                     Qt.SmoothTransformation))

# 战绩页塞两行模拟数据
try:
    games = [
        {'game_id': 1, 'champion_id': 25, 'champion_name': '堕落天使',
         'win': True, 'kills': 8, 'deaths': 3, 'assists': 12,
         'gold': 24500, 'cs': 210, 'dmg': 41000,
         'queue_id': 450, 'duration_s': 1520, 'created_ms': 0},
        {'game_id': 2, 'champion_id': 17, 'champion_name': '迅捷斥候',
         'win': False, 'kills': 2, 'deaths': 9, 'assists': 4,
         'gold': 14200, 'cs': 96, 'dmg': 18000,
         'queue_id': 450, 'duration_s': 1180, 'created_ms': 0},
    ]
    w.update_recent(games)
    for g in games:
        pass
except Exception as e:
    print('recent mock fail:', e)

outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'tools')
os.makedirs(outdir, exist_ok=True)

from PyQt5.QtCore import QEventLoop, QTimer


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


for idx, name in enumerate(('home', 'settings', 'heroes', 'recent', 'log')):
    w._switch_page(idx)
    wait(320)  # 等切页淡出动画(180ms)结束
    out = os.path.join(outdir, f'page_{name}.png')
    w.grab().save(out)
    print('saved:', out)
