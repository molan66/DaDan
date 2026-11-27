# -*- coding: utf-8 -*-
"""v4.1.39 头像状态机 offscreen 冒烟测试。
验证:真头像渲染后,探活轮询 update_account_info(同 icon_id)不再覆盖成首字占位;
换 icon_id → 重新占位等待;断线(None) → 回 🎮 且 _avatar_real_id 清零。
"""
import os, sys
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
# 项目根目录 = 本文件上两级(tools/ -> dadan-build/), 不写死绝对路径, 换机可用
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJ_ROOT)

from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter, QColor, QPixmap
from PyQt5.QtCore import QBuffer, QByteArray, Qt

app = QApplication([])

from ui.main_window import MainWindow

def make_real_avatar_bytes(color='#ff8800'):
    """造一张 100x100 纯色 PNG bytes 模拟下载到的真头像。"""
    img = QImage(100, 100, QImage.Format_RGB32)
    img.fill(QColor(color))
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.WriteOnly)
    img.save(buf, 'PNG')
    buf.close()
    return bytes(ba.data())

def avatar_is_real(w):
    """真头像特征:非空 pixmap 且非 '🎮' 文本。生成式占位也是 pixmap,
    用 _avatar_real_id 状态 + pixmap cacheKey 差分做判断。"""
    return w._avatar_real_id != 0

results = []
def check(name, ok, extra=''):
    results.append((name, ok, extra))
    print(f'{"PASS" if ok else "FAIL"} | {name} {extra}')

w = MainWindow()
w.show()  # offscreen 下 show 使 isVisible 语义正确
app.processEvents()

# ---- 1. 探活首轮:只有名字 → 生成式占位,real=0 ----
info_base = dict(name='你们也会被审判的#90061', level=452, icon_id=7084,
                 xp_pct=63,
                 solo=dict(tier='SILVER', division='I', leaguePoints=60),
                 flex=None,
                 solo_high=dict(tier='SILVER', division='I', leaguePoints=60),
                 flex_high=None, honor=5)
w.update_account_info(info_base)
app.processEvents()
check('首轮探活(无真头像):显示占位且 real=0', w._avatar_real_id == 0)
ck_placeholder = w._avatar_lbl.pixmap().cacheKey() if w._avatar_lbl.pixmap() else 0

# ---- 2. 真头像 bytes 到达(模拟下载线程 emit→set_account_avatar) ----
w.set_account_avatar(make_real_avatar_bytes(), 7084)
app.processEvents()
check('真头像渲染:real=7084', w._avatar_real_id == 7084)
ck_real = w._avatar_lbl.pixmap().cacheKey() if w._avatar_lbl.pixmap() else 0
check('真头像 pixmap 与占位不同', ck_real != ck_placeholder)
check('真头像 pixmap 非空', ck_real != 0)

# ---- 3. 下一轮探活(同 icon_id):不得覆盖真头像 ----
for i in range(3):
    w.update_account_info(info_base)
    app.processEvents()
still_real = (w._avatar_real_id == 7084 and
              w._avatar_lbl.pixmap() and
              w._avatar_lbl.pixmap().cacheKey() == ck_real)
check(f'探活刷新×3 后真头像仍在屏(不被占位覆盖)', still_real)

# ---- 4. 游戏内换头像 8000:应重新占位等待(real 清零) ----
info_8000 = dict(info_base); info_8000['icon_id'] = 8000
w.update_account_info(info_8000)
app.processEvents()
check('换头像(7084→8000):占位等待 real=0', w._avatar_real_id == 0 and w._avatar_lbl.pixmap())
ck_ph2 = w._avatar_lbl.pixmap().cacheKey() if w._avatar_lbl.pixmap() else 0
check('换头像后显示生成式占位(与真头像不同)', ck_ph2 != 0 and ck_ph2 != ck_real)

# ---- 5. 新头像下载成功 → real=8000 ----
w.set_account_avatar(make_real_avatar_bytes('#22aa66'), 8000)
app.processEvents()
check('新头像渲染:real=8000', w._avatar_real_id == 8000)

# ---- 6. 断线:update_account_info(None) → 🎮 + real=0 ----
w.update_account_info(None)
app.processEvents()
check('断线:回 🎮 且 real=0', w._avatar_real_id == 0 and w._avatar_lbl.text() == '🎮')

# ---- 7. 重连(同 7084):再次占位→真头像 全链路 ----
w.update_account_info(info_base)
app.processEvents()
w.set_account_avatar(make_real_avatar_bytes(), 7084)
app.processEvents()
check('重连后再渲染:real=7084', w._avatar_real_id == 7084)

fails = [r for r in results if not r[1]]
print('\n==== 结果:', 'ALL PASS' if not fails else f'{len(fails)} FAIL ====')
sys.exit(1 if fails else 0)
