# -*- coding: utf-8 -*-
"""真实平台渲染小窗（选人态）截图。
用法: python tools/shot_overlay.py <outdir>
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from ui.overlay_window import OverlayWindow
from PyQt5.QtCore import QEventLoop, QTimer, QBuffer, QIODevice
from PyQt5.QtGui import QPixmap, QColor


def png_bytes(color):
    pm = QPixmap(52, 52)
    pm.fill(QColor(*color))
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    pm.save(buf, 'PNG')
    return bytes(buf.data())


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


ov = OverlayWindow()
ov.show()
app.processEvents()

ov.set_conn_state('connected', '已连接')
ov.set_live_status('匹配中', '排位 · 单/双排')
ov.sig_champ_name.emit('扭曲树精')

bench_ids = (25, 17, 90, 63, 4, 120, 74, 55, 238, 86, 51, 157)
ov.update_bench_champions(list(bench_ids), current_id=4)
app.processEvents()
# 偏好 + 预输入
ov._preferred = {90, 63}
ov._do_preinput_state(25)
# 图标占位（真实 PNG bytes 走 set_champion_icon）
for i, cid in enumerate(bench_ids):
    ov.set_champion_icon(cid, png_bytes((60 + (i * 37) % 150,
                                         90 + (i * 53) % 120,
                                         130 + (i * 29) % 100)))

for line in ('[匹配] 已进入房间，自动匹配开启',
             '[接受] 检测到对局邀请',
             '[接受] 已自动接受对局',
             '[换英雄] 备选池出现 迅捷斥候',
             '[状态] 等待选人界面…'):
    ov.append_mini_log(line)

ov.sig_ready.emit(True)   # 弹出 ReadyCheck 确认卡
wait(400)

out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'tools')
out = os.path.join(out_dir, 'overlay_pick.png')
ov.grab().save(out)
print('saved:', out, ov.width(), ov.height())
