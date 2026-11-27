# -*- coding: utf-8 -*-
"""ULTRA 综合截图：英雄选择页（独立渲染）+ 对局详情弹窗。
用法: python tools/shot_ultra.py <outdir>
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from PyQt5.QtGui import QPixmap, QColor
from PyQt5.QtCore import Qt, QEventLoop, QTimer, QBuffer, QIODevice


def png_bytes(color):
    pm = QPixmap(64, 64)
    pm.fill(QColor(*color))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, 'PNG')
    return bytes(buf.data())


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'tools')
os.makedirs(out_dir, exist_ok=True)

# ================= 英雄选择页 =================
from ui.champion_picker import ChampionPickerWidget

names = {1: '黑暗之女', 2: '狂战士', 3: '正义巨像', 4: '卡牌大师', 5: '德邦总管',
         6: '无畏战车', 7: '诡术妖姬', 8: '猩红收割者', 9: '远古恐惧', 10: '正义天使',
         11: '无极剑圣', 12: '牛头酋长', 13: '符文法师', 14: '亡灵战神', 15: '战争女神',
         16: '众星之子', 17: '迅捷斥候', 18: '麦林炮手', 19: '祖安怒兽', 20: '雪原双子'}
roles = {1: ['mage'], 2: ['fighter'], 4: ['mage'], 5: ['fighter'], 7: ['assassin'],
         11: ['assassin'], 12: ['tank'], 16: ['support'], 17: ['marksman'],
         18: ['marksman'], 6: ['tank'], 3: ['tank']}

picker = ChampionPickerWidget(names, preferred=[17, 4, 11],
                              champion_roles=roles)
picker.resize(720, 470)
picker.show()
wait(150)
# 给几个英雄塞头像，观察网格质感
for cid, rgb in ((17, (40, 70, 110)), (4, (90, 50, 40)), (11, (30, 80, 60)),
                 (16, (120, 70, 130)), (5, (140, 100, 50)), (1, (70, 60, 110))):
    picker._apply_icon(cid, png_bytes(rgb))
picker._set_card_selected(17, True)
picker._set_card_selected(4, True)
wait(150)
picker.grab().save(os.path.join(out_dir, 'ultra_heroes.png'))
print('saved: ultra_heroes.png')

# ================= 对局详情弹窗 =================
from ui.recent_detail import RecentDetailDialog

game = {'game_id': 12345, 'champion_id': 17, 'champion_name': '迅捷斥候',
        'win': True, 'queue_id': 450, 'duration_s': 1620, 'created_ms': 0,
        'kills': 8, 'deaths': 4, 'assists': 11}
detail = {
    'players': [
        {'pid': 'me', 'team': 100, 'cid': 17, 'champion_name': '迅捷斥候',
         'name': '你们也会被审判的', 'level': 18, 'kills': 8, 'deaths': 4,
         'assists': 11, 'gold': 18600, 'cs': 242, 'dmg': 38600, 'taken': 24100,
         'items': [3078, 3153, 3006, 3031, 0, 0], 'spells': [4, 7], 'augments': [1, 5]},
        {'pid': 'p2', 'team': 100, 'cid': 4, 'champion_name': '卡牌大师',
         'name': '队友二号', 'level': 17, 'kills': 3, 'deaths': 7, 'assists': 13,
         'gold': 13200, 'cs': 188, 'dmg': 21800, 'taken': 19600,
         'items': [3115, 3089, 0, 0, 0, 0], 'spells': [4, 14], 'augments': [2]},
        {'pid': 'p3', 'team': 100, 'cid': 5, 'champion_name': '德邦总管',
         'name': '队友三号', 'level': 16, 'kills': 5, 'deaths': 6, 'assists': 9,
         'gold': 14900, 'cs': 174, 'dmg': 23200, 'taken': 28700,
         'items': [3071, 3065, 0, 0, 0, 0], 'spells': [14, 12], 'augments': []},
        {'pid': 'p4', 'team': 100, 'cid': 16, 'champion_name': '众星之子',
         'name': '队友四号', 'level': 15, 'kills': 0, 'deaths': 5, 'assists': 19,
         'gold': 9800, 'cs': 32, 'dmg': 6400, 'taken': 11200,
         'items': [3504, 0, 0, 0, 0, 0], 'spells': [14, 21], 'augments': [3]},
        {'pid': 'p5', 'team': 100, 'cid': 11, 'champion_name': '无极剑圣',
         'name': '队友五号', 'level': 17, 'kills': 9, 'deaths': 5, 'assists': 6,
         'gold': 16800, 'cs': 209, 'dmg': 31400, 'taken': 18400,
         'items': [3031, 3078, 3006, 0, 0, 0], 'spells': [4, 11], 'augments': [1]},
        {'pid': 'e1', 'team': 200, 'cid': 9, 'champion_name': '远古恐惧',
         'name': 'Lucifer 良', 'level': 18, 'kills': 6, 'deaths': 3, 'assists': 10,
         'gold': 17400, 'cs': 220, 'dmg': 29800, 'taken': 22100,
         'items': [3157, 3001, 0, 0, 0, 0], 'spells': [4, 7], 'augments': [5]},
        {'pid': 'e2', 'team': 200, 'cid': 1, 'champion_name': '黑暗之女',
         'name': '敌方二号', 'level': 17, 'kills': 7, 'deaths': 6, 'assists': 8,
         'gold': 15100, 'cs': 197, 'dmg': 27600, 'taken': 17900,
         'items': [3089, 3100, 0, 0, 0, 0], 'spells': [4, 14], 'augments': [2]},
        {'pid': 'e3', 'team': 200, 'cid': 6, 'champion_name': '无畏战车',
         'name': '敌方三号', 'level': 16, 'kills': 4, 'deaths': 5, 'assists': 9,
         'gold': 13900, 'cs': 183, 'dmg': 21400, 'taken': 33100,
         'items': [3068, 3075, 0, 0, 0, 0], 'spells': [14, 12], 'augments': []},
        {'pid': 'e4', 'team': 200, 'cid': 3, 'champion_name': '正义巨像',
         'name': '敌方四号', 'level': 15, 'kills': 2, 'deaths': 7, 'assists': 12,
         'gold': 11200, 'cs': 142, 'dmg': 17800, 'taken': 26400,
         'items': [3068, 0, 0, 0, 0, 0], 'spells': [14, 21], 'augments': [3]},
        {'pid': 'e5', 'team': 200, 'cid': 18, 'champion_name': '麦林炮手',
         'name': '敌方五号', 'level': 17, 'kills': 8, 'deaths': 4, 'assists': 6,
         'gold': 17800, 'cs': 231, 'dmg': 33500, 'taken': 17600,
         'items': [3031, 3006, 3078, 0, 0, 0], 'spells': [4, 7], 'augments': [1]},
    ],
    'teams': [
        {'team_id': 100, 'tower': 6, 'dragon': 2, 'baron': 1, 'herald': 1, 'inhib': 1},
        {'team_id': 200, 'tower': 3, 'dragon': 1, 'baron': 0, 'herald': 0, 'inhib': 0},
    ],
}

dlg = RecentDetailDialog(game)
dlg.set_detail(detail)
wait(150)
# 塞图标：英雄/装备/技能占位
for p in detail['players']:
    cid = p['cid']
    dlg._grab_cache = getattr(dlg, '_grab_cache', {})
    try:
        dlg._swap_big_icon(cid, p['champion_name'][:1])
    except Exception:
        pass
wait(200)
dlg.grab().save(os.path.join(out_dir, 'ultra_detail.png'))
print('saved: ultra_detail.png', dlg.width(), dlg.height())
