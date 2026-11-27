# -*- coding: utf-8 -*-
"""离屏渲染 MainWindow 主页并截图, 用于布局迭代快速目检。
用法: python tools/preview_layout.py [out.png]
"""
import os, sys
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from ui.main_window import MainWindow
from ui.styles import Glass

w = MainWindow()
w.show()
app.processEvents()

# 模拟账号数据(用户真实形态) 观察完整排版
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
        {'champion_id': 25, 'name': '堕落天使', 'level': 72, 'points': 760424, 'grade': 'S+'},
        {'champion_id': 17, 'name': '迅捷斥候', 'level': 37, 'points': 374248, 'grade': 'S+'},
        {'champion_id': 90, 'name': '虚空先知', 'level': 16, 'points': 152552, 'grade': 'S+'},
    ],
    'recent': {'wins': 3, 'losses': 8, 'total': 11},
}
w.update_account_info(info)
app.processEvents()

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'tools', 'layout_preview.png')
w.grab().save(out)
print('saved:', out)
print('window size:', w.width(), w.height())

# 几何侦查:找 fab 按钮与账号卡的绝对位置
fab = w._fab
print('fab abs y,x:', fab.mapTo(w, fab.rect().topLeft()).y(), fab.mapTo(w, fab.rect().topLeft()).x(),
      'size', fab.width(), fab.height())
ac = w._acct_card
tl = ac.mapTo(w, ac.rect().topLeft())
br = ac.mapTo(w, ac.rect().bottomRight())
print('acct abs top-left:', tl.y(), tl.x(), ' bottom-right:', br.y(), br.x(),
      'size', ac.width(), ac.height())

# 验证各 chip 的 value 已正确写入(确认 update_account_info 数据流)
print('--- chip states ---')
for name, chip in [('solo', w._chip_solo), ('flex', w._chip_flex),
                   ('wallet_ip', w._chip_wallet_ip),
                   ('wallet_cos', w._chip_wallet_cosmetic),
                   ('wallet_my', w._chip_wallet_mythic),
                   ('honor', w._chip_honor),
                   ('m1', w._chip_m1), ('m2', w._chip_m2), ('m3', w._chip_m3),
                   ('recent', w._chip_recent)]:
    val = chip._value.text()
    vis = chip.isVisible()
    pf = chip._prefix.text() if name.startswith('m') else ''
    print(f'  {name:10s} visible={vis} prefix={pf!r:12s} value={val!r}')
