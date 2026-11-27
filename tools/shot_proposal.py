# -*- coding: utf-8 -*-
"""效果图：现状(真实渲染) vs 建议版(mockup) 并排对比。

建议版落地三条调研结论：
  1. 圆角收敛：控件 6px / 卡片 8px / 弹层 10px（对齐 Fluent 克制感）
  2. 层级靠 1px 描边 + 背景阶差，无玻璃渐变、无阴影光斑
  3. 主色深青绿 + on-brand 白字；胜负语义色统一（胜=红/负=绿）
输出 outdir/compare.png（单张并排大图）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import (
    QPixmap, QColor, QPainter, QFont, QPainterPath, QFontDatabase,
    QLinearGradient, QBrush, QPen,
)

app = QApplication(sys.argv)

# ---- 字体 ----
_fam = 'Microsoft YaHei UI'
if _fam not in QFontDatabase().families():
    QFontDatabase.addApplicationFont(r'C:\Windows\Fonts\msyh.ttc')
    _fam = 'Microsoft YaHei'
assert _fam in QFontDatabase().families(), 'no yahei'


def F(px, bold=False):
    f = QFont(_fam)
    f.setPixelSize(px)
    f.setBold(bold)
    return f


# ---- ULTRA 建议版色板（= 调研结论）----
BG_WIN = QColor('#F2F4F1')          # 窗口底（雾白）
BG_SIDE = QColor('#E9EFEC')         # 侧栏
BG_CARD = QColor('#FAFBF8')         # 卡片（暖白）
BG_CARD_H = QColor('#FFFFFF')       # 卡片 hover
BG_INSET = QColor('#EDF1EE')        # 内凹（输入/进度槽）
BRAND = QColor('#087F73')           # 主色深青绿
BRAND_D = QColor('#06574F')         # pressed
ON_BRAND = QColor('#FFFFFF')
DANGER = QColor('#C34F55')
DANGER_BG = QColor('#FBEAEA')
SUCCESS = QColor('#19845F')
SUCCESS_BG = QColor('#E7F3EE')
TXT1 = QColor('#202D35')
TXT2 = QColor('#52616D')
TXT3 = QColor('#5E6C75')
BORDER = QColor('#D8E1DC')
BORDER_H = QColor('#C2CFC8')

WIN_W, WIN_H = 720, 540


def draw_window_base(p, x, y, w, h):
    p.setPen(Qt.NoPen)
    p.setBrush(BG_WIN)
    p.drawRoundedRect(x, y, w, h, 12, 12)


def rounded(p, x, y, w, h, r, fill, border=None, border_w=1):
    p.setPen(Qt.NoPen)
    p.setBrush(fill)
    p.drawRoundedRect(QRectF(x, y, w, h), r, r)
    if border is not None:
        p.setPen(QPen(border, border_w))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(x + 0.5, y + 0.5, w - 1, h - 1), r, r)


def chip(p, x, y, w, h, text, fg, bg, r=4, bold=True, size=12):
    rounded(p, x, y, w, h, r, bg)
    p.setPen(fg)
    p.setFont(F(size, bold))
    p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)


def stat_block(p, x, y, w, h, label, value, label_color=TXT3, value_color=TXT1,
               mono=False):
    rounded(p, x, y, w, h, 6, BG_INSET, BORDER)
    p.setPen(label_color)
    p.setFont(F(12))
    p.drawText(QRectF(x, y, w, 20), Qt.AlignHCenter | Qt.AlignBottom, label)
    f = QFont('Consolas') if mono else QFont(_fam)
    f.setPixelSize(13)
    f.setBold(True)
    p.setFont(f)
    p.setPen(value_color)
    p.drawText(QRectF(x, y + 22, w, h - 26), Qt.AlignCenter, value)


def stat_value_only(p, x, y, w, h, value, color=TXT1, bold=True, size=13):
    p.setPen(color)
    p.setFont(F(size, bold))
    p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, value)


def btn_primary(p, x, y, w, h, text):
    rounded(p, x, y, w, h, 6, BRAND)
    p.setPen(ON_BRAND)
    p.setFont(F(14, True))
    p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)


def btn_outline(p, x, y, w, h, text, fg, border):
    rounded(p, x, y, w, h, 6, BG_CARD, border)
    p.setPen(fg)
    p.setFont(F(14, True))
    p.drawText(QRectF(x, y, w, h), Qt.AlignCenter, text)


def btn_danger_outline(p, x, y, w, h, text):
    btn_outline(p, x, y, w, h, text, DANGER, DANGER)


def sidebar(p, x, y, w, h, active_idx):
    p.setPen(Qt.NoPen)
    p.setBrush(BG_SIDE)
    p.drawRoundedRect(QRectF(x, y, w, h), 12, 12)
    p.drawRect(x, y + 12, w, h - 24)  # 盖掉左侧圆角（贴边）
    items = ['首页', '设置', '英雄', '战绩', '日志']
    for i, name in enumerate(items):
        iy = y + 76 + i * 52
        if i == active_idx:
            rounded(p, x + 6, iy - 2, w - 12, 44, 6, QColor('#087F73'), None)
            fg = ON_BRAND
        else:
            fg = TXT3
        p.setPen(fg)
        p.setFont(F(12, i == active_idx))
        p.drawText(QRectF(x, iy - 2, w, 44), Qt.AlignCenter, name)


def titlebar(p, x, y, w, h):
    p.setPen(TXT1)
    p.setFont(F(13, True))
    p.drawText(QRectF(x + 16, y, 200, h), Qt.AlignVCenter | Qt.AlignLeft, '大蛋小助手')
    # 关闭按钮（红底 hover 态演示）
    rounded(p, x + w - 42, y + (h - 26) // 2, 26, 26, 6, DANGER)
    p.setPen(ON_BRAND)
    p.setFont(F(12, True))
    p.drawText(QRectF(x + w - 42, y + (h - 26) // 2, 26, 26), Qt.AlignCenter, '×')


def render_current():
    """现状：真实 MainWindow 渲染。"""
    from ui.main_window import MainWindow
    w = MainWindow()
    w.show()
    app.processEvents()
    info = {
        'name': '你们也会被审判的#90061', 'level': 452, 'xp_pct': 63.0,
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
        (w._chip_m1, w._chip_m2, w._chip_m3)[i]._avatar.setPixmap(
            pm.scaled(32, 32, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    app.processEvents()
    pm = w.grab()
    w.close()
    return pm


def render_proposed():
    """建议版 mockup：720x540 首页。"""
    pm = QPixmap(WIN_W, WIN_H)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)

    draw_window_base(p, 0, 0, WIN_W, WIN_H)
    titlebar(p, 0, 0, WIN_W, 40)
    # 标题栏下分隔线
    p.setPen(BORDER)
    p.drawLine(16, 40, WIN_W - 16, 40)

    # 侧栏
    sidebar(p, 0, 40, 60, WIN_H - 40, 0)

    cx = 76  # 内容区起点
    cw = WIN_W - 76 - 16

    # ===== 顶部状态条 =====
    sy = 56
    sh = 40
    # 连接 pill
    rounded(p, cx, sy, 190, sh, 8, BG_INSET, BORDER)
    p.setPen(SUCCESS)
    p.setBrush(SUCCESS)
    p.drawEllipse(QRectF(cx + 14, sy + 16, 8, 8))
    p.setPen(TXT2)
    p.setFont(F(13, True))
    p.drawText(QRectF(cx + 30, sy, 160, sh), Qt.AlignVCenter, '客户端已连接')
    # 当前状态 pill（右）
    rounded(p, cx + cw - 150, sy, 150, sh, 8, BG_INSET, BORDER)
    p.setPen(TXT3)
    p.setFont(F(12))
    p.drawText(QRectF(cx + cw - 150, sy + 2, 150, 16), Qt.AlignHCenter, '当前状态')
    p.setPen(TXT1)
    p.setFont(F(16, True))
    p.drawText(QRectF(cx + cw - 150, sy + 18, 150, 20), Qt.AlignCenter, '等待启动')

    # ===== 中部 =====
    my = sy + sh + 14
    # 账号卡（左，弹性）
    acw = cw - 170 - 12
    ach = 300
    rounded(p, cx, my, acw, ach, 8, BG_CARD, BORDER)
    # 头像
    av = 48
    ax, ay = cx + 16, my + 16
    rounded(p, ax, ay, av, av, 6, QColor('#B4D8D2'))
    p.setPen(TXT1)
    p.setFont(F(16, True))
    p.drawText(QRectF(ax, ay, av, av), Qt.AlignCenter, '审')
    # 昵称 + 游戏ID
    p.setPen(TXT1)
    p.setFont(F(15, True))
    p.drawText(QRectF(ax + av + 12, ay, 300, 22), Qt.AlignVCenter, '你们也会被审判的#90061')
    p.setPen(TXT3)
    p.setFont(F(12))
    p.drawText(QRectF(ax + av + 12, ay + 24, 300, 20), Qt.AlignVCenter, '等级 452')
    # 升级进度
    px, py, pw, ph = cx + 16, ay + av + 12, acw - 32, 16
    p.setPen(TXT3)
    p.setFont(F(11))
    p.drawText(QRectF(px, py, pw, 12), Qt.AlignLeft, '升级进度 63%')
    rounded(p, px, py + 13, pw, 3, 2, BORDER)
    rounded(p, px, py + 13, int(pw * 0.63), 3, 2, BRAND)

    # 统计块：段位一行
    syy = py + 30
    sbh = 44
    sbw = (acw - 32 - 8) // 2
    stat_block(p, px, syy, sbw, sbh, '单双排位', '白银 I · 60 胜点', TXT3, TXT1)
    stat_block(p, px + sbw + 8, syy, sbw, sbh, '灵活组排', '未定级', TXT3, TXT3)
    # 资产一行（三等分）
    syy2 = syy + sbh + 8
    sbw3 = (acw - 32 - 16) // 3
    stat_block(p, px, syy2, sbw3, sbh, '蓝色精粹', '258,477', TXT3, TXT1, mono=True)
    stat_block(p, px + sbw3 + 8, syy2, sbw3, sbh, '橙色精粹', '1,254', TXT3, TXT1, mono=True)
    stat_block(p, px + 2 * (sbw3 + 8), syy2, sbw3, sbh, '神话精粹', '56', TXT3, TXT1, mono=True)
    # 熟练度一行
    syy3 = syy2 + sbh + 10
    p.setPen(TXT3)
    p.setFont(F(12))
    p.drawText(QRectF(px, syy3 - 2, 100, 18), Qt.AlignLeft, '熟练度')
    syy3 += 18
    for i, (nm, lv) in enumerate((('堕落天使', '72级'), ('迅捷斥候', '37级'), ('虚空先知', '16级'))):
        bx = px + i * (sbw3 + 8)
        rounded(p, bx, syy3, sbw3, 40, 6, BG_INSET, BORDER)
        # 头像小块（与文字同排，左侧）
        rounded(p, bx + 7, syy3 + 7, 26, 26, 4, QColor(90 + i * 30, 140, 180))
        p.setPen(TXT2)
        p.setFont(F(11))
        p.drawText(QRectF(bx + 38, syy3 + 4, sbw3 - 42, 16), Qt.AlignVCenter, nm)
        p.setPen(TXT1)
        p.setFont(F(12, True))
        p.drawText(QRectF(bx + 38, syy3 + 19, sbw3 - 42, 16), Qt.AlignVCenter, lv)

    # 操作卡（右，固定）
    ox = cx + acw + 12
    ow = 170
    rounded(p, ox, my, ow, ach, 8, BG_CARD, BORDER)
    p.setPen(TXT3)
    p.setFont(F(11, True))
    p.drawText(QRectF(ox + 12, my + 10, 100, 16), Qt.AlignLeft, '操作')
    by = my + 34
    btn_primary(p, ox + 12, by, ow - 24, 40, '启动 LOL')
    btn_outline(p, ox + 12, by + 50, ow - 24, 36, '启动脚本', BRAND, BRAND)
    p.setPen(TXT3)
    p.setFont(F(11))
    p.drawText(QRectF(ox + 12, by + 92, ow - 24, 16), Qt.AlignCenter, '海克斯大乱斗 · 自动挂机')
    btn_danger_outline(p, ox + 12, by + 160, ow - 24, 36, '全部停止')

    # ===== 底部近期战绩 =====
    ry = my + ach + 14
    rh = WIN_H - ry - 16
    rounded(p, cx, ry, cw, rh, 8, BG_CARD, BORDER)
    # 标题行：左标题 + chip + 右侧场次/胜率
    head_h = 34
    p.setPen(TXT3)
    p.setFont(F(12, True))
    p.drawText(QRectF(cx + 14, ry, 90, head_h), Qt.AlignVCenter, '近期战绩')
    chip(p, cx + 110, ry + (head_h - 24) // 2, 56, 24, '3 胜', DANGER, DANGER_BG, 12)
    chip(p, cx + 174, ry + (head_h - 24) // 2, 56, 24, '8 负', SUCCESS, SUCCESS_BG, 12)
    p.setPen(TXT3)
    p.setFont(F(12))
    p.drawText(QRectF(cx + cw - 150, ry, 136, head_h), Qt.AlignVCenter | Qt.AlignRight,
               '近 11 场 · 胜率 27%')
    # 两行最近对局摘要
    row_h = 30
    rows = [
        ('堕落天使', '极地大乱斗 · 25分20秒', '8 / 3 / 12', True),
        ('迅捷斥候', '极地大乱斗 · 19分40秒', '2 / 9 / 4', False),
    ]
    rows_top = ry + head_h + 4
    for i, (nm, meta, kda, win) in enumerate(rows):
        yy = rows_top + i * (row_h + 6)
        rounded(p, cx + 14, yy, cw - 28, row_h, 6, BG_INSET)
        # 胜负竖条
        p.setPen(Qt.NoPen)
        p.setBrush(DANGER if win else SUCCESS)
        p.drawRoundedRect(QRectF(cx + 15, yy + 4, 3, row_h - 8), 2, 2)
        p.setPen(TXT1)
        p.setFont(F(12, True))
        p.drawText(QRectF(cx + 30, yy, 90, row_h), Qt.AlignVCenter, nm)
        p.setPen(TXT3)
        p.setFont(F(11))
        p.drawText(QRectF(cx + 124, yy, 180, row_h), Qt.AlignVCenter, meta)
        p.setPen(TXT2)
        p.setFont(F(12, True))
        p.drawText(QRectF(cx + cw - 220, yy, 90, row_h), Qt.AlignVCenter | Qt.AlignRight, kda)
        chip(p, cx + cw - 116, yy + 4, 40, 22, '胜' if win else '负',
             DANGER if win else SUCCESS,
             DANGER_BG if win else SUCCESS_BG, 11)

    p.end()
    return pm


def main():
    outdir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools')
    os.makedirs(outdir, exist_ok=True)

    cur = render_current()
    prop = render_proposed()

    # 并排合成：标题 + 两图
    gap = 24
    label_h = 40
    W = cur.width() + gap + prop.width()
    H = label_h + max(cur.height(), prop.height())
    canvas = QPixmap(W, H)
    canvas.fill(QColor('#E4E9E6'))
    p = QPainter(canvas)
    p.setPen(QColor('#202D35'))
    p.setFont(F(14, True))
    p.drawText(0, 4, cur.width(), 28, Qt.AlignCenter, '现状（v5.0.2 已上线）')
    p.drawText(cur.width() + gap, 4, prop.width(), 28, Qt.AlignCenter,
               '建议版（Fluent 克制 + 描边分层）')
    p.drawPixmap(0, label_h, cur)
    p.drawPixmap(cur.width() + gap, label_h, prop)
    p.end()

    out = os.path.join(outdir, 'compare.png')
    canvas.save(out)
    print('saved:', out)

    out_cur = os.path.join(outdir, 'proposal_current.png')
    out_prop = os.path.join(outdir, 'proposal_new.png')
    cur.save(out_cur)
    prop.save(out_prop)
    print('saved:', out_cur)
    print('saved:', out_prop)


if __name__ == '__main__':
    main()
