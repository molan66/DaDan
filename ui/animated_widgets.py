"""Animated Widgets - 液态玻璃动效组件 v3

小米风格核心：边框外圈柔光扩散
- 每张卡片边框绘制多层扩散光晕（3层递减透明度）
- 顶部折射高光
- hover 时光晕增强
"""

import math as _math
import time as _time

from PyQt5.QtWidgets import (
    QStackedWidget, QWidget, QGraphicsOpacityEffect, QLabel,
    QVBoxLayout, QHBoxLayout,
)
from PyQt5.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty, QPointF
from PyQt5.QtGui import QColor, QPainter, QPainterPath, QLinearGradient

from .window_base import ui_slot
from .styles import Glass


def paint_window_backdrop(p, w, h):
    """主窗/小窗统一的顶层玻璃背景: 深色半透明底色 + 顶部柔光
    （底层由 DWM 系统 Acrylic 模糊提供, 此处只画半透明着色层）"""
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(8, 10, 18, 155))
    p.drawRect(0, 0, w, h)
    # 顶部柔光线
    cr, cg, cb = Glass.NEON_QCOLOR
    p.setPen(QColor(cr, cg, cb, 18))
    p.drawLine(0, 0, w, 0)


class SpringStackedWidget(QStackedWidget):
    """页面切换 - 快照淡出过渡

    旧实现对整个新页面套 QGraphicsOpacityEffect，Qt 必须把整棵控件树
    （英雄页 200+ 卡片）离屏渲染再合成，动画期间每帧都全页重绘 -> 卡顿。
    新方案：切换瞬间抓一张旧页截图，新页直接显示，只对截图做淡出动画，
    动画开销从"整页渲染"降为"一张 pixmap 合成"。

    v4.4.0(P2): 两处收尾 ——
    1) `setGeometry(self.rect())` 原本直接铺逻辑矩形, 若 grab() 回来的
       pixmap 自带 DPR(150% 屏上是 1.5), QLabel 会按"物理像素"再算一遍
       逻辑尺寸 → 快照被放大 1.5 倍, 淡出那一瞬间能看到旧页"跳大"。
       现在统一把 pixmap 的 DPR 对齐控件自身 DPR, 并按逻辑尺寸设几何。
    2) `grab()` 是整页离屏渲染, 频率靠 _animating 标志天然限流
       (180ms 内最多一次); 这里再加一道时间戳兜底, 防止外部反复直接
       调 setCurrentIndex 绕过动画状态时连续抓图。
    """
    _ANIM_MS = 180
    # v4.4.0: grab() 最小间隔 —— 低于该间隔的重复抓图直接复用上一张快照
    _GRAB_MIN_MS = 120

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animating = False
        self._fade_anim = None
        self._snap_label = None
        # v4.1.66: 动画期间用户又点了别的页 → 记下最新目标, 淡出结束后补切。
        # 否则连点被静默丢弃, 侧边栏高亮(调用方已更新)与页面内容脱节。
        self._pending_index = None
        # v4.4.0: grab() 限流用
        self._last_grab_ms = 0
        self._last_snap = None

    def _grab_snapshot(self):
        """按 DPR 对齐 + 时间限流地抓当前页快照。"""
        now_ms = int(_time.monotonic() * 1000)
        if (self._last_snap is not None and not self._last_snap.isNull()
                and now_ms - self._last_grab_ms < self._GRAB_MIN_MS):
            return self._last_snap
        snap = self.grab()
        if snap is not None and not snap.isNull():
            try:
                dpr = float(self.devicePixelRatioF())
                if dpr > 0 and abs(float(snap.devicePixelRatio()) - dpr) > 1e-6:
                    snap.setDevicePixelRatio(dpr)
            except Exception:
                pass
        self._last_grab_ms = now_ms
        self._last_snap = snap
        return snap

    def setCurrentIndex(self, index):
        if index == self.currentIndex():
            self._pending_index = None
            return
        if self._animating:
            self._pending_index = index
            return
        self._animating = True
        # 1) 抓当前页快照（轻量，一张 pixmap；DPR 对齐见 _grab_snapshot）
        snap = self._grab_snapshot()
        # 2) 立即切换页面：新页直接完整显示，无透明度开销
        super().setCurrentIndex(index)
        # 3) 旧页快照盖在最上层做淡出，形成交叉淡化视觉效果
        self._snap_label = QLabel(self)
        self._snap_label.setPixmap(snap)
        # v4.4.0: 逻辑矩形 —— pixmap 已带 DPR, QLabel 会按 DPR 还原成 1:1 显示
        self._snap_label.setGeometry(self.rect())
        self._snap_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._snap_label.show()
        self._snap_label.raise_()
        eff = QGraphicsOpacityEffect(self._snap_label)
        eff.setOpacity(1.0)
        self._snap_label.setGraphicsEffect(eff)
        anim = QPropertyAnimation(eff, b'opacity', self)
        anim.setDuration(self._ANIM_MS)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_anim = anim
        anim.finished.connect(lambda: self._fade_finished(self._snap_label))
        anim.start()

    @ui_slot
    def _fade_finished(self, label):
        if label is not None:
            label.hide()
            label.deleteLater()
        self._snap_label = None
        self._fade_anim = None
        self._animating = False
        # v4.1.66: 补切动画期间被连点跳过的那一页
        pending = self._pending_index
        self._pending_index = None
        if pending is not None and pending != self.currentIndex():
            self.setCurrentIndex(pending)


# v4.4.0(P2 死代码清理): 此处原有 `AnimatedStackedWidget = SpringStackedWidget`
# 别名。全项目 grep(`AnimatedStackedWidget`, *.py) 只有这一行定义、零引用
# (main.py / core / lcu / tools 全无), 属于早期重命名留下的兼容壳, 删除。


class HoverRow(QWidget):
    """容器行 - 透明无 hover 绘制（白色 hover 背景已按用户要求移除）"""


def _draw_glow_border(p, w, h, r, base_alpha=28, glow_expand=4, color=None):
    """绘制精致多层柔光边框
    
    5层递进光晕：外扩散→中光→内光→核心→折射高光
    base_alpha: 核心描边透明度 (0-255)
    glow_expand: 外层光晕向外扩展的像素数
    color: 光晕主色(RGB 元组)。缺省用 Glass.NEON_QCOLOR —— v4.4.2(P2-17) 之前
           该参数不存在, PulseGlow(color=...) 传的色被丢弃, 恒用 NEON 色。
    """
    if color is None:
        cr, cg, cb = Glass.NEON_QCOLOR
    else:
        cr, cg, cb = color[0], color[1], color[2]

    # 第1层：最外层柔光扩散（大面积低透明度）
    outer_a = max(6, int(base_alpha * 0.18))
    p.setPen(QColor(cr, cg, cb, outer_a))
    p.setBrush(Qt.NoBrush)
    ext = glow_expand + 3
    path1 = QPainterPath()
    path1.addRoundedRect(-ext * 0.5, -ext * 0.5, w + ext, h + ext, r + ext, r + ext)
    p.drawPath(path1)

    # 第2层：中层光晕
    mid_a = max(10, int(base_alpha * 0.35))
    p.setPen(QColor(cr, cg, cb, mid_a))
    path2 = QPainterPath()
    path2.addRoundedRect(-1, -1, w + 2, h + 2, r + 1, r + 1)
    p.drawPath(path2)

    # 第3层：内光晕
    inner_a = max(15, int(base_alpha * 0.6))
    p.setPen(QColor(cr, cg, cb, inner_a))
    path3 = QPainterPath()
    path3.addRoundedRect(0, 0, w, h, r, r)
    p.drawPath(path3)

    # 第4层：核心描边（最亮）
    p.setPen(QColor(cr, cg, cb, base_alpha))
    path4 = QPainterPath()
    path4.addRoundedRect(0.5, 0.5, w - 1, h - 1, r, r)
    p.drawPath(path4)

    # 第5层：白色折射边（玻璃质感关键）
    p.setPen(QColor(255, 255, 255, max(3, int(base_alpha * 0.12))))
    path5 = QPainterPath()
    path5.addRoundedRect(0.5, 0.5, w - 1, h - 1, r, r)
    p.drawPath(path5)


def _draw_top_highlight(p, w, h, r, intensity=6):
    """绘制顶部折射高光 - 多层细腻渐变"""
    # 主高光带（更窄更集中）
    hl = QLinearGradient(0, 0, 0, int(h * 0.28))
    hl.setColorAt(0, QColor(255, 255, 255, intensity))
    hl.setColorAt(0.15, QColor(255, 255, 255, max(3, int(intensity * 0.7))))
    hl.setColorAt(0.4, QColor(255, 255, 255, max(2, int(intensity * 0.35))))
    hl.setColorAt(0.7, QColor(255, 255, 255, max(1, int(intensity * 0.15))))
    hl.setColorAt(1, QColor(255, 255, 255, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(hl)
    p.drawRoundedRect(2, 1, w - 4, int(h * 0.28), r, r)
    # 次高光带（更宽更淡，增加层次感）
    hl2 = QLinearGradient(0, 0, 0, int(h * 0.45))
    hl2.setColorAt(0, QColor(255, 255, 255, max(1, intensity // 3)))
    hl2.setColorAt(0.5, QColor(255, 255, 255, max(1, intensity // 6)))
    hl2.setColorAt(1, QColor(255, 255, 255, 0))
    p.setBrush(hl2)
    p.drawRoundedRect(4, 2, w - 8, int(h * 0.45), r, r)


class GlassTitleBar(QWidget):
    """标题栏 - 柔光描边 + 玻璃高光"""

    def __init__(self, height=44, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.setStyleSheet('background:transparent;')

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 半透明背景
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(14, 16, 24, 160))
        p.drawRect(0, 0, w, h)

        # 顶部折射高光
        _draw_top_highlight(p, w, h, 0, intensity=7)

        # 底部柔光描边
        cr, cg, cb = Glass.NEON_QCOLOR
        p.setPen(QColor(cr, cg, cb, 25))
        p.drawLine(0, h - 1, w, h - 1)

        # 底部外溢光晕
        glow = QLinearGradient(0, h - 6, 0, h)
        glow.setColorAt(0, QColor(cr, cg, cb, 0))
        glow.setColorAt(1, QColor(cr, cg, cb, 12))
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawRect(0, h - 6, w, 6)

        p.end()


# v4.4.0(P2 死代码清理): 此处原有 `GradientTitleBar = GlassTitleBar` 别名。
# 全项目 grep(`GradientTitleBar`, *.py) 只有这一行定义、零引用, 删除。
# 注意 `GlassTitleBar` 本身**在用**(main_window.py:522 唯一一次), 保留。


class GlassCard(QWidget):
    """玻璃卡片 - hover上浮 + 柔光增强"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet('background:transparent;')
        self._hovered = False
        self._lift = 0.0  # 上浮偏移量
        self.setMouseTracking(True)
        # hover 动画
        self._lift_anim = QPropertyAnimation(self, b'lift_offset')
        self._lift_anim.setDuration(200)
        self._lift_anim.setEasingCurve(QEasingCurve.OutCubic)

    def _get_lift(self):
        return self._lift

    def _set_lift(self, val):
        self._lift = val
        self.update()

    lift_offset = pyqtProperty(float, _get_lift, _set_lift)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = Glass.RADIUS_CARD
        offset = int(self._lift)

        # 投影（hover时加深）
        if offset > 0:
            shadow_alpha = min(40, 15 + offset * 5)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, shadow_alpha))
            p.drawRoundedRect(0, offset, w, h, r, r)

        # 卡片背景
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(12, 14, 24, 180))
        p.drawRoundedRect(0, 0, w, h, r, r)

        # 顶部折射高光
        _draw_top_highlight(p, w, h, r, intensity=8 if self._hovered else 5)

        # 多层柔光边框
        alpha = 45 if self._hovered else 32
        _draw_glow_border(p, w, h, r, base_alpha=alpha, glow_expand=4 if self._hovered else 3)

        p.end()

    def enterEvent(self, e):
        self._hovered = True
        self._lift_anim.stop()
        self._lift_anim.setStartValue(self._lift)
        self._lift_anim.setEndValue(2.0)
        self._lift_anim.start()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._lift_anim.stop()
        self._lift_anim.setStartValue(self._lift)
        self._lift_anim.setEndValue(0.0)
        self._lift_anim.start()
        super().leaveEvent(e)


class PulseGlow(QWidget):
    """脉冲发光边框动画组件"""

    def __init__(self, color='#6366f1', parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self._pulse_value = 0.0
        self._pulse_dir = 1
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._pulse_tick)
        self._timer.setInterval(30)
        self._active = False

    def start_pulse(self):
        if not self._active:
            self._active = True
            self._pulse_value = 0.0
            self._pulse_dir = 1
            self._timer.start()
            self.update()

    def stop_pulse(self):
        self._active = False
        self._timer.stop()
        self._pulse_value = 0.0
        self.update()

    def hideEvent(self, event):
        """v4.4.2(P1-12): 隐藏时停脉冲定时器 —— 原先无此方法, 小窗一隐藏这个
        30ms 定时器就永久空转(每秒 33 次 update()), 与 v4.4.0(P1-14) 承诺的
        "隐藏后停表"相悖(实现漏了这个对象)。"""
        self._timer.stop()
        super().hideEvent(event)

    def showEvent(self, event):
        """v4.4.2(P1-12): 显示时按 _active 恢复脉冲(隐藏前在跑才重启)。"""
        super().showEvent(event)
        if self._active:
            self._timer.start()

    @ui_slot
    def _pulse_tick(self):
        self._pulse_value += 0.05 * self._pulse_dir
        if self._pulse_value >= 1.0:
            self._pulse_value = 1.0
            self._pulse_dir = -1
        elif self._pulse_value <= 0.0:
            self._pulse_value = 0.0
            self._pulse_dir = 1
        self.update()

    def paintEvent(self, event):
        if not self._active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        base_alpha = int(20 + 50 * self._pulse_value)
        # v4.4.2(P2-17): 把构造时传入的 _color 透传给 _draw_glow_border,
        # 否则 color 形参是死的(原恒用 NEON 色)。
        rgb = (self._color.red(), self._color.green(), self._color.blue())
        _draw_glow_border(p, self.width(), self.height(), 10,
                          base_alpha=base_alpha, glow_expand=4, color=rgb)
        p.end()


def _to_qcolor(value):
    """把 元组/列表/`'#rrggbb'` 字符串/`QColor` 统一成 `QColor`。

    历史事故(v4.4.0 收尾实测): `StatusDot.set_state` 原写 `QColor(*color)` ——
    只认元组。而小窗传的是 `Glass.ACCENT = '#34d399'`(字符串),
    `QColor(*'#34d399')` 会抛 `TypeError: arguments did not match any overloaded call`,
    异常被 `@ui_slot` 就地吞掉 → **小窗连接点连接后颜色永远不翻绿**, 而且同一函数里
    它后面的 `setText('已连接')`、呼吸定时器 `start()` 全都跟着不再执行。
    `set_ring` 一直是 `QColor(color)` 所以没事 —— 同一个类里两条路径写法不一致,
    才埋出这个坑。这里统一走一个入口, 三种入参都支持。
    """
    if isinstance(value, QColor):
        return value
    if isinstance(value, str):
        return QColor(value)
    if isinstance(value, (tuple, list)):
        return QColor(*value)
    return QColor(value)


class StatusDot(QWidget):
    """状态圆点 - paintEvent 直接画, 不再每次改样式表 (v4.4.0 新增)

    历史事故(P1-14 / P1-18):
        主窗 10×10 的呼吸点、小窗 9×9 的连接点, 原本都是 QLabel +
        每 tick 一次 `setStyleSheet(f"background-color:rgba(...);border-radius:...")`。
        主窗 40ms 一次(25 次/秒), 小窗 40ms 一次 —— 而 setStyleSheet 会触发
        Qt 的 CSS 解析 + 重新生成整棵控件树的 style 缓存, 一个 10 像素的小点
        吃掉的钱比整页布局还多。setStyleSheet 在动画里出现就是错用法。

    现在: 颜色是 QColor 属性, 每 tick 只改 alpha 后 `update()` —— 走的是
    已经高度优化的绘制路径, 每秒 25 帧的圆点几乎零成本。
    """

    def __init__(self, size=10, color=(52, 211, 153), alpha=170,
                 parent=None):
        super().__init__(parent)
        self._size = int(size)
        self._color = _to_qcolor(color)
        self._alpha = float(alpha)
        # v4.4.0: 可选外描边圆环(H3 起连接态绿点带一圈半透明绿 ring,
        # 原实现是 QSS 的 `border:1px solid rgba(52,211,153,0.60)`)。
        self._ring_color = None
        self.setFixedSize(self._size, self._size)
        # v4.4.0: 圆点直径取 size-1, 与旧 QSS `border-radius: 5px` + 10px 边
        # 的视觉一致(半像素对齐, 边缘不糊)
        self._radius = max(1.0, (self._size - 1) / 2.0)

    def set_ring(self, color=None):
        """设置/清除外描边圆环(None 清除)。"""
        new = None
        if color is not None:
            new = _to_qcolor(color)
        old_rgb = self._ring_color.rgb() if self._ring_color is not None else None
        new_rgb = new.rgb() if new is not None else None
        if old_rgb != new_rgb:
            self._ring_color = new
            self.update()

    def set_state(self, color=None, alpha=None):
        """只改需要改的字段; 值没变则完全不重绘。"""
        changed = False
        if color is not None:
            c = _to_qcolor(color)
            if c.rgb() != self._color.rgb():
                self._color = c
                changed = True
        if alpha is not None and abs(float(alpha) - self._alpha) > 0.01:
            self._alpha = float(alpha)
            changed = True
        if changed:
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        c = QColor(self._color)
        c.setAlpha(max(0, min(255, int(self._alpha))))
        p.setBrush(c)
        cx = self._size / 2.0
        cy = self._size / 2.0
        r = self._radius
        # v4.4.0: 有 ring 时实心圆缩进半个描边宽, 让外圈完整落在控件内
        if self._ring_color is not None:
            r = max(1.0, self._radius - 0.5)
        p.drawEllipse(QPointF(cx, cy), r, r)
        if self._ring_color is not None:
            ring = QColor(self._ring_color)
            pen = p.pen()
            pen.setColor(ring)
            pen.setWidthF(1.0)
            # v4.4.2(P1-15): 必须显式置回实线 —— 上面 `p.setPen(Qt.NoPen)` 已把
            # painter 的 pen 样式改成 NoPen, `p.pen()` 取回的仍是 NoPen 样式,
            # 只改颜色/宽度不会改样式 → drawEllipse 描边什么都不画(实测绿环像素=0)。
            pen.setStyle(Qt.SolidLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), self._radius - 0.5, self._radius - 0.5)
        p.end()


# v4.4.0(P2 死代码清理): 此处原有 `PulseWidget = PulseGlow` 别名。
# 全项目 grep(`PulseWidget`, *.py) 只有这一行定义、零引用, 删除。
# `PulseGlow` 本身在用(overlay_window.py 的 _bench_glow), 保留。


class LoadingDots(QWidget):
    """三点呼吸指示器(v4.4.1 / UI-B2 新增)。

    空态与加载态原来只有一行灰字, 用户分不清"正在加载"还是"卡住了"。
    三点依次明灭给出明确的"进行中"信号, 且不需要任何图片资源(不引 GIF)。

    严格遵守 P1-14/P1-18 的既定做法: 颜色与透明度是**绘制属性**, 每个 tick 只
    `setPhase` + `update()`; 绝不在 tick 里 setStyleSheet —— 那会触发 Qt 重新解析
    CSS 并重建整棵控件树的样式缓存, 一个几像素的指示器能吃掉整页布局的开销。

    可见性保险: `hideEvent` 停表、`showEvent` 按 `_active` 恢复 —— 页面切走时
    定时器不会空转(与 `PulseGlow` 同一套纪律)。
    """

    def __init__(self, dot=6, gap=5, color=None, parent=None):
        super().__init__(parent)
        self._dot = int(dot)
        self._gap = int(gap)
        self._color = _to_qcolor(color if color is not None else Glass.ACCENT)
        self._phase = 0.0
        self._active = False
        self.setFixedSize(self._dot * 3 + self._gap * 2, self._dot)
        self.setStyleSheet('background:transparent;')
        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._active = True
        self._phase = 0.0
        if not self._timer.isActive() and self.isVisible():
            self._timer.start()
        self.update()

    def stop(self):
        self._active = False
        self._timer.stop()
        self.update()

    @ui_slot
    def _tick(self):
        self._phase = (self._phase + 0.075) % 1.0
        self.update()

    def showEvent(self, e):
        if self._active and not self._timer.isActive():
            self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, event):
        if not self._active:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        for i in range(3):
            # 相邻点相位差 1/3, 形成依次明灭
            a = (self._phase - i / 3.0) % 1.0
            wave = 0.5 - 0.5 * _math.cos(a * 2 * _math.pi)
            c = QColor(self._color)
            c.setAlpha(int(60 + 195 * wave))
            p.setBrush(c)
            x = i * (self._dot + self._gap)
            p.drawEllipse(x, 0, self._dot, self._dot)
        p.end()


def make_empty_state(icon_char, title, hint='', min_height=120,
                     animated=False, dot_color=None):
    """统一空态/加载态工厂: 可选大号符号 + 标题 + 可选副提示 + 可选三点呼吸。

    v4.4.1(UI-B2): 原先主窗两处空态各写各的 QLabel —— 字号、行高、居中方式、
    是否用 `\\n` 把副提示塞进同一个字符串, 全都不同。统一走一个工厂, 返回值是
    `QWidget`(可直接 `addWidget` / `removeWidget` / `setVisible` / `hide`)。

    animated=True 时下方挂三点呼吸指示器(仅"加载中"这类进行态需要),
    开始呼吸必须显式调 `.start()` —— 工厂不自动启动, 让调用方决定生命周期。
    返回的 `QWidget` 上挂了 `dots` 属性(未启用时为 None)。
    """
    w = QWidget()
    w.setStyleSheet('background:transparent;')
    w.setMinimumHeight(int(min_height))
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    if icon_char:
        icon = QLabel(icon_char)
        icon.setAlignment(Qt.AlignCenter)
        icon.setStyleSheet(
            f'font-size:26px;color:{Glass.TEXT_MUTED};background:transparent;')
        lay.addWidget(icon)
    title_lbl = QLabel(title)
    title_lbl.setAlignment(Qt.AlignCenter)
    title_lbl.setWordWrap(True)
    title_lbl.setStyleSheet(
        f'font-size:{Glass.FONT_SIZE_SM};color:{Glass.TEXT_SECONDARY};'
        f'background:transparent;')
    lay.addWidget(title_lbl)
    dots = None
    hint_lbl = None
    if animated:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch()
        dots = LoadingDots(color=dot_color)
        row.addWidget(dots)
        row.addStretch()
        lay.addLayout(row)
    if hint:
        hint_lbl = QLabel(hint)
        hint_lbl.setAlignment(Qt.AlignCenter)
        hint_lbl.setWordWrap(True)
        hint_lbl.setStyleSheet(
            f'font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};'
            f'background:transparent;')
        lay.addWidget(hint_lbl)
    w.dots = dots              # 供调用方 start()/stop()
    w.title_label = title_lbl  # 供调用方改文案
    w.hint_label = hint_lbl
    return w
