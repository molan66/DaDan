# -*- coding: utf-8 -*-
"""复现侧边导航 hover 文字消失问题：渲染三种状态局部放大截图。
用法: python tools/diag_sidebar.py <out.png>
"""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt5.QtWidgets import QApplication
app = QApplication(sys.argv)

from ui.main_window import MainWindow
from PyQt5.QtCore import QEventLoop, QTimer

w = MainWindow()
w.show()


def wait(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec_()


wait(200)
# 模拟：第 0 项激活，第 1 项 hover
for i, btn in enumerate(w._nav_buttons):
    btn.set_active(i == 0)
w._nav_buttons[1]._hovered = True
w._nav_buttons[1].update()
wait(100)

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'tools', 'diag_sidebar.png')
w.grab().save(out)
print('saved:', out)
# 顺带打印按钮实际几何，检查内容居中计算
for i, btn in enumerate(w._nav_buttons):
    print(i, btn._label, 'size:', btn.width(), 'x', btn.height(),
          'active:', btn._active, 'hovered:', btn._hovered)
