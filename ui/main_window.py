"""Main Window - ULTRA 旗舰浅色主窗口

特性:
- Windows 系统级 Acrylic 背景（DWM 降级自动回退）
- 浅色分层：雾白底 + 暖白卡片 + 低对比边框
- 页面快照淡出过渡（SpringStackedWidget）
- 统一 ULTRA 设计令牌（HIG / Glass 兼容层）
- 可缩放窗口（默认 720x540，内容页自适应滚动）
"""

import json
import os
import threading
import time as _time
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit,
    QApplication, QScrollArea, QProgressBar,
    QGraphicsOpacityEffect, QCheckBox, QMessageBox, QFrame
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation
# v4.4.0 修复(P1-22): QFont 原来在 SidebarButton.paintEvent 内部 import
# (每次重绘都走一遍模块查找), 提到模块顶部, 字体对象在 __init__ 建好复用。
from PyQt5.QtGui import (QColor, QPainter, QLinearGradient, QFont,
                         QPixmap, QPainterPath)

from .styles import Glass, HIG
# v4.3.0: 对局展示格式化抽到 match_fmt(详情弹窗也要用, 它不能反向 import 本模块);
# 这里用原名别名导入, 模块内既有调用点无需改动。
from .match_fmt import (LANE_CN as _LANE_CN, phase_text as _phase_text,
                        game_mode_text as _game_mode_text,
                        dur_text as _dur_text, time_text as _time_text,
                        lane_text as _lane_text, kda_text as _kda_text,
                        kda_ratio as _kda_ratio,
                        wan_compact as _wan_compact, num_text as _num_text)
from .icons import (icon_home, icon_settings, icon_hero, icon_chart, icon_log,
                     AnimatedToggle, make_title_buttons, rounded_pixmap,
                     icon_to_pixmap, icon_pixmap)
from .animated_widgets import (SpringStackedWidget, GlassTitleBar, GlassCard,
                               HoverRow, StatusDot, paint_window_backdrop,
                               make_empty_state)
# v4.4.0 修复(P1-1/P1-21/P2 拖拽): 三窗共用的拖拽/玻璃去重/槽异常保护
from .window_base import DragMixin, GlassEffectMixin, ui_slot
from . import screen_utils
from core.updater import APP_VERSION


# ============================================================
# v4.4.0 修复(P2 硬编码): 原来散落在各处的中文/尺寸字面量抽成具名常量。
# 只做抽取, **文案与数值本身一个字都没改**。
# ============================================================
AVATAR_SIDE = 48                       # 账号卡头像边长(逻辑像素)
SIDEBAR_BUTTON_ICON_PX = 22            # 侧边栏图标边长
DOT_TICK_MS = 40                       # 状态点呼吸灯周期
DOT_ALPHA_MIN = 150.0                  # 呼吸灯透明度下限
DOT_ALPHA_MAX = 255.0                  # 呼吸灯透明度上限
DOT_ALPHA_STEP = 4.0                   # 每 tick 透明度步进
DOT_RGB_ONLINE = (25, 132, 95)        # 已连接: 柔和绿（统一语义色 #4ADE80）
DOT_RGB_OFFLINE = (248, 113, 113)      # 未连接: 红
CONN_TEXT_ONLINE = '客户端已连接'
CONN_TEXT_OFFLINE = '未连接到客户端'
SIDEBAR_PAGES = (('🏠', '首页'), ('⚙️', '设置'), ('⭐', '英雄'),
                 ('📊', '战绩'), ('📋', '日志'))
SIDEBAR_ICON_FNS = {'🏠': icon_home, '⚙️': icon_settings, '⭐': icon_hero,
                    '📊': icon_chart, '📋': icon_log}

# v4.4.1(UI-B9 第一步): 账号卡 chip 的几何与 8 组色值收敛为模块常量。
# **数值一个字都没改** —— 这一步只让"改一处即全局生效", 视觉零回归。
# v5.0.2(ULTRA 收口): 颜色值统一从 HIG/Glass 令牌取，不再重复写字面量。
CHIP_FONT_PX = 12                      # chip 文字字号（对齐 Glass.FONT_SIZE_XS，≥12px 可读）
CHIP_RADIUS_PX = 5                     # chip 圆角
CHIP_PAD = '2px 7px'                   # chip 内边距
CHIP_WAIT_BG = Glass.BG_CARD_ALT       # 等待态弱背景（次级内容色）
CHIP_GAP = 6                           # chip 之间/与 label 列间距
# (前景色, 背景色) —— 前景即 prefix 文字色, 背景为数值胶囊底色
CHIP_SOLO = ('#9A742E', 'rgba(176,131,58,0.14)')       # 单排（段位金色点缀）
CHIP_FLEX = ('#9A742E', 'rgba(176,131,58,0.14)')       # 灵活
CHIP_WALLET_IP = (Glass.TEXT_SECONDARY, Glass.BG_CARD_ALT)   # 蓝色精粹
CHIP_WALLET_COSMETIC = (Glass.TEXT_SECONDARY, Glass.BG_CARD_ALT)  # 橙色精粹
CHIP_WALLET_MYTHIC = (Glass.TEXT_SECONDARY, Glass.BG_CARD_ALT)    # 神话精粹
CHIP_HONOR = ('#9A742E', 'rgba(176,131,58,0.16)')      # 荣誉（金色点缀，唯一游戏成就色）


# v36:段位英文缩写 → 中文(LCU tier 字段恒为英文)
_TIER_CN = {
    'IRON': '黑铁', 'BRONZE': '青铜', 'SILVER': '白银', 'GOLD': '黄金',
    'PLATINUM': '铂金', 'DIAMOND': '钻石', 'MASTER': '大师',
    'GRANDMASTER': '宗师', 'CHALLENGER': '王者',
}


def _rank_text(entry) -> str:
    """把 ranked queueMap 单队列条目格式化成 '黄金 II · 53胜点'。

    entry 为 None / 无 tier / UNRANKED → 返回 ''(未定级)。"""
    if not entry:
        return ''
    tier = entry.get('tier') or ''
    if not tier or tier == 'UNRANKED':
        return ''
    div = entry.get('division') or ''
    try:
        lp = int(entry.get('leaguePoints') or 0)
    except (TypeError, ValueError):
        lp = 0
    cn = _TIER_CN.get(tier, tier)
    base = f'{cn} {div}'.strip()
    return f'{base} · {lp}胜点'


# v4.4.18: 首页右侧竖排的三枚主行动按钮(启动 LOL / 启动脚本 / 全部停止)
# 统一到**同一个样式工厂** —— 用户看截图指出「全部停止」与上面两枚不像一套:
#   原来 全部停止 = 透明底 + 红描边 + 高40 + 字号13 + 字重600
#   而   另两枚   = 实心渐变 + 无边框 + 高44 + 字号16 + 字重700
# 现在几何/字号/字重/交互位移全部共用, 只有配色按语义取(绿=前进, 蓝=品牌主色, 红=终止)。
_BTN_W = 170          # 三枚按钮统一宽度
_BTN_H = 44           # 三枚按钮统一高度


def _mini_btn_qss(primary: bool = False) -> str:
    """设置页授权卡里的小号按钮样式（30px 高）。

    单独一档是因为：卡片内的次要动作不该用 _action_btn_qss 的 170x44 大按钮
    （会把卡片撑高、喧宾夺主）。配色仍取 Glass 令牌，与全局观感一致。
    """
    if primary:
        bg, hover, pressed = Glass.PRIMARY, Glass.PRIMARY_HOVER, Glass.PRIMARY_PRESSED
        fg, bd = '#ffffff', 'transparent'
    else:
        bg, hover, pressed = ('rgba(0,0,0,0.04)', 'rgba(0,0,0,0.08)',
                              'rgba(0,0,0,0.12)')
        fg, bd = Glass.TEXT_SECONDARY, Glass.BORDER_GLASS
    return f"""
            QPushButton {{
                background: {bg}; color: {fg};
                border: 1px solid {bd}; border-radius: {Glass.RADIUS_SM};
                font-size: {Glass.FONT_SIZE_SM}; font-weight: 700;
            }}
            QPushButton:hover {{ background: {hover}; color: {Glass.TEXT_PRIMARY}; }}
            QPushButton:pressed {{ background: {pressed}; padding-top: 1px; }}
        """


def _group_code(code: str, size: int = 5) -> str:
    """把长串按 size 分组加连字符，仅用于**显示**（复制时给纯码）。

    与 license-server/src/shared.js 的 groupCode 行为一致 —— 两边都只是"给人看"，
    解析侧一律先剔除连字符，所以这里格式不占契约。
    """
    s = (code or '').replace('-', '')
    return '-'.join(s[i:i + size] for i in range(0, len(s), size))


def _as_int(v, default=0) -> int:
    """容错 int 转换：None/空串/非数字一律回落 default。

    update_account_info 原形是 `int(info.get(k) or 0)` —— `or 0` 已并入本助手
    (空值走 except 分支返回 default)，行为一致且不再到处裸 int()。
    """
    try:
        return int(v or default)
    except (TypeError, ValueError):
        return default


def _fmt_size(n) -> str:
    """字节 → 人类可读。v4.6.3: 清理缓存卡片显示占用用。

    用 1024 进制并保留一位小数, 与 Windows 资源管理器口径一致(避免用户
    拿这里的数字和右键属性对不上而怀疑程序在乱报)。
    """
    try:
        v = float(n)
    except (TypeError, ValueError):
        return '0 B'
    for unit in ('B', 'KB', 'MB', 'GB'):
        if v < 1024 or unit == 'GB':
            return f'{v:.0f} {unit}' if unit == 'B' else f'{v:.1f} {unit}'
        v /= 1024
    return f'{v:.1f} GB'


def _action_btn_qss(variant: str = 'primary') -> str:
    """首页主行动按钮样式(单一来源)。

    variant:
        'primary'     —— 启动脚本(空闲)：海克斯青实心渐变(唯一主按钮)
        'accent'      —— 保留兼容：同 primary 系的翠绿实心
        'danger'      —— 启动脚本(运行中→"停止脚本")：红实心
        'danger_deep' —— 全部停止实心红(兼容保留)
        'ghost_green' —— 启动 LOL：玻璃底+青描边(次要动作)
        'ghost_red'   —— 全部停止：玻璃底+红描边(危险确认感更强)

    四档实心变体的几何(宽高/圆角/字号/字重/按下位移)完全一致。
    """
    if variant == 'ghost_green':
        return f"""
            QPushButton {{
                background: {Glass.PRIMARY_SUBTLE}; color: {Glass.ACCENT};
                border: 1px solid {Glass.PRIMARY_40};
                border-radius: {Glass.RADIUS_BTN};
                font-size: {Glass.FONT_SIZE_LG}; font-weight: 600;
            }}
            QPushButton:hover {{
                background: {Glass.PRIMARY_20}; color: {Glass.ACCENT_HOVER};
                border-color: {Glass.ACCENT_BORDER};
            }}
            QPushButton:pressed {{ background: {Glass.PRIMARY_30}; padding-top: 2px; }}
            QPushButton:disabled {{
                background: {Glass.BG_CARD_ALT}; color: {Glass.TEXT_DISABLED};
                border: 1px solid {Glass.BORDER};
            }}
        """
    if variant == 'ghost_red':
        return f"""
            QPushButton {{
                background: rgba(248,113,113,0.10); color: {Glass.DANGER};
                border: 1px solid rgba(248,113,113,0.35);
                border-radius: {Glass.RADIUS_BTN};
                font-size: {Glass.FONT_SIZE_LG}; font-weight: 600;
            }}
            QPushButton:hover {{
                background: rgba(248,113,113,0.16); color: {Glass.DANGER_HOVER};
                border-color: rgba(248,113,113,0.55);
            }}
            QPushButton:pressed {{ background: rgba(248,113,113,0.24); padding-top: 2px; }}
            QPushButton:disabled {{
                background: {Glass.BG_CARD_ALT}; color: {Glass.TEXT_DISABLED};
                border: 1px solid {Glass.BORDER};
            }}
        """
    if variant == 'accent':
        c1, c2 = Glass.ACCENT_HOVER, Glass.ACCENT_DARK
        h1, h2 = Glass.ACCENT, Glass.ACCENT_PRESSED
        pressed = Glass.ACCENT_PRESSED
    elif variant == 'danger':
        # 与改版前的 _fab_qss(True) 逐字一致, 保证"停止脚本"观感零回归
        c1, c2 = Glass.DANGER, Glass.STOP_RED
        h1, h2 = Glass.STOP_RED, Glass.STOP_RED_HOVER
        pressed = Glass.STOP_RED_PRESSED
    elif variant == 'danger_deep':
        c1, c2 = Glass.DANGER_DARK, Glass.STOP_RED_HOVER
        h1, h2 = Glass.STOP_RED_HOVER, Glass.STOP_RED_PRESSED
        pressed = Glass.STOP_RED_PRESSED
    else:   # 'primary'
        c1, c2 = Glass.PRIMARY_HOVER, Glass.PRIMARY
        h1, h2 = Glass.PRIMARY, Glass.PRIMARY_PRESSED
        pressed = Glass.PRIMARY_PRESSED
    return f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c1}, stop:1 {c2});
                color: white; border: none;
                border-radius: {Glass.RADIUS_BTN};
                font-size: {Glass.FONT_SIZE_XL}; font-weight: 700;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {h1}, stop:1 {h2});
            }}
            QPushButton:pressed {{
                background: {pressed}; padding-top: 2px;
            }}
            QPushButton:disabled {{
                background: {Glass.BG_CARD_ALT}; color: {Glass.TEXT_DISABLED};
            }}
        """


def _fab_qss(running: bool) -> str:
    """启动/停止按钮样式：空闲=青描边，运行=红描边线框（dsh 意见：停止降干扰）。"""
    return _action_btn_qss('ghost_red' if running else 'ghost_green')


# v4.4.1(UI-B1): Toast 语义类型 → 左侧 3px 色条。键就是 kind 取值。
TOAST_KIND_COLORS = {
    'info': Glass.PRIMARY,
    'success': Glass.ACCENT,
    'error': Glass.DANGER,
    'warning': Glass.WARNING,
}


def _toast_qss(kind='info') -> str:
    """Toast 样式(单一来源)。未知 kind 退化为 info, 不抛异常。"""
    bar = TOAST_KIND_COLORS.get(kind, Glass.PRIMARY)
    return f"""
        QLabel {{
            background: rgba(253, 254, 252, 245);
            color: {Glass.TEXT_PRIMARY};
            border: none;
            border-left: 3px solid {bar};
            border-radius: {Glass.RADIUS_MD};
            padding: 8px 16px;
            font-size: {Glass.FONT_SIZE_SM};
        }}
    """


class Toast(QLabel):
    """液态玻璃 Toast 提示

    v4.4.1(UI-B1): 加语义类型。原来所有提示共用一套深色底 —— 「详情打开失败」
    与「已保存 8 个偏好英雄」长得一模一样, 用户扫一眼分不出成败。现在按 kind
    在左侧加一条 3px 语义色条, 底色/圆角/内边距**一个像素都没动**。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(_toast_qss('info'))
        self.setAlignment(Qt.AlignCenter)
        self.hide()
        # v4.4.0 修复(P2 动画复用): 原 _fade_out 每次新建 QPropertyAnimation
        # (用户连点提示时旧动画对象还没被 GC, 白白累积)。改为惰性建一次,
        # stop() 后重设起止值再 start(); finished 只连一次。
        self._anim = None
        # v4.4.2(P2-46): Toast 是 QLabel **子控件**(带 parent), 而 windowOpacity
        # 只对顶层窗口有效 —— 原动画目标 `b'windowOpacity'` 对子控件是空操作
        # (实测采样恒 [1.0]), 那 300ms 是白等后硬消失。改用 QGraphicsOpacityEffect
        # 的 `b'opacity'`(与 animated_widgets.py:109 同路线), 子控件真渐隐。
        self._opacity_eff = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_eff)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)

    def show_toast(self, text, duration=2000, kind='info'):
        """kind: 'info' | 'success' | 'error' | 'warning'(未知值退化为 info)。

        默认参数, 老调用点 `show_toast(text)` / `show_toast(text, 3000)` 行为不变。
        """
        self.setStyleSheet(_toast_qss(kind))
        self.setText(text)
        self.adjustSize()
        if self.parent():
            pw = self.parent().width()
            self.move((pw - self.width()) // 2, 60)
        self._opacity_eff.setOpacity(1.0)
        self.show()
        self.raise_()
        self._timer.start(duration)

    @ui_slot
    def _fade_out(self):
        # v4.4.0 修复(P2 动画复用): 复用同一个动画对象
        if self._anim is None:
            # v4.4.2(P2-46): 动画目标改为 QGraphicsOpacityEffect 的 b'opacity'
            self._anim = QPropertyAnimation(self._opacity_eff, b'opacity', self)
            self._anim.setDuration(300)
            self._anim.finished.connect(self.hide)
        self._anim.stop()
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()


class SidebarButton(QPushButton):
    """液态玻璃侧边栏导航按钮"""

    def __init__(self, icon_char, label, parent=None):
        super().__init__(parent)
        self._icon_char = icon_char
        self._label = label
        self._active = False
        self._hovered = False
        self.setFixedWidth(60)   # 跟随侧边栏宽（原 80 在 60 宽栏里溢出导致内容偏右）
        self.setMinimumHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("QPushButton{background:transparent;border:none;outline:none;}")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_Hover, True)
        self.setWindowFlag(Qt.ToolTip, False)
        # v4.4.0 修复(P1-22): 字体只建一次(原来每次 paintEvent 都新建 QFont)
        self._font = QFont('Microsoft YaHei UI', 8)
        # v4.4.0 修复(P1-22): 三态图标 pixmap 缓存。
        # 旧 paintEvent 每次重绘都要 `fn(icon_color)` 重画一遍矢量图再
        # `icon.pixmap(22,22)` 栅格化 —— 悬停/选中切换时每帧一次。现在按
        # 状态各缓存一张 DPR 感知位图, 重绘只做 drawPixmap。
        self._icon_cache = {}
        self._icon_char2fn = SIDEBAR_ICON_FNS
        self._build_icon_cache()

    def _icon_color_for(self, active, hovered):
        if active:
            return Glass.PRIMARY
        return Glass.TEXT_SECONDARY if hovered else Glass.TEXT_MUTED

    def _build_icon_cache(self):
        """为 (active, hovered) 四种组合各预栅格化一张 DPR 感知 pixmap。"""
        fn = self._icon_char2fn.get(self._icon_char)
        if fn is None:
            return
        for active in (False, True):
            for hovered in (False, True):
                try:
                    ic = fn(self._icon_color_for(active, hovered))
                    pm = icon_to_pixmap(ic, SIDEBAR_BUTTON_ICON_PX, widget=self)
                    if pm is not None:
                        self._icon_cache[(active, hovered)] = pm
                except Exception as e:
                    print(f'[Sidebar] 图标缓存失败: {type(e).__name__}: {e}')

    def _state_icon(self):
        """取当前状态对应的 pixmap; 缓存缺失时回退现画(保证功能不变)。"""
        pm = self._icon_cache.get((bool(self._active), bool(self._hovered)))
        if pm is not None:
            return pm
        fn = self._icon_char2fn.get(self._icon_char)
        if fn is None:
            return None
        try:
            return fn(self._icon_color_for(self._active,
                                           self._hovered)).pixmap(
                SIDEBAR_BUTTON_ICON_PX, SIDEBAR_BUTTON_ICON_PX)
        except Exception:
            return None

    def set_active(self, active):
        self._active = active
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 不填充背景 - 完全透明，让父容器的背景透出来
        # 只在激活时绘制指示条

        # 激活态：轻量品牌色背景 + 左侧竖条（颜色 + 形状共同体现选中）
        if self._active:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(8, 127, 115, 26))
            p.drawRoundedRect(0, int(h * 0.08), w, int(h * 0.84), 8, 8)
            # 左侧指示条（更宽更亮）
            p.setBrush(QColor(8, 127, 115, 220))
            p.drawRoundedRect(0, int(h * 0.16), 3, int(h * 0.68), 2, 2)

        # hover 背景（柔和深灰透明，浅色主题）
        if self._hovered and not self._active:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 10))
            p.drawRoundedRect(0, int(h * 0.08), w, int(h * 0.84), 8, 8)

        # 图标（图标+文字作为整体在按钮内垂直居中，随实际高度自适应）
        icons = self._icon_char2fn
        fn = icons.get(self._icon_char)
        if fn:
            # v4.4.0 修复(P1-22): 走三态 pixmap 缓存, 不再每次重绘重建
            # dict + 重新栅格化矢量图标(见 __init__ / _state_icon)
            icon_pixmap = self._state_icon()
            if icon_pixmap is not None:
                # 内容块高 = 图标 + 间距(2) + 文字(≈15)；整体居中，避免"偏下"
                text_h = 15
                content_h = SIDEBAR_BUTTON_ICON_PX + 2 + text_h
                top = max(2, (h - content_h) // 2)
                x = (w - SIDEBAR_BUTTON_ICON_PX) // 2
                p.drawPixmap(x, top, icon_pixmap)
                text_rect = self.rect().adjusted(
                    0, top + SIDEBAR_BUTTON_ICON_PX + 2, 0, 0)
                # ⚠️ 文字颜色必须在 drawText 前设置：画背景时 pen 已被置成
                # NoPen，NoPen 下 drawText 不渲染任何文字（激活/hover 项
                # 文字"消失"就是这个原因——非激活项没画背景所以幸存）。
                label_color = Glass.TEXT_PRIMARY if self._active else (
                    Glass.TEXT_SECONDARY if self._hovered else Glass.TEXT_MUTED)
                p.setPen(QColor(label_color))
                p.setFont(self._font)
                p.drawText(text_rect, Qt.AlignHCenter | Qt.AlignTop,
                           self._label)
                p.end()
                return

        # 标签（无图标时的兜底路径）
        label_color = Glass.TEXT_PRIMARY if self._active else (
            Glass.TEXT_SECONDARY if self._hovered else Glass.TEXT_MUTED)
        p.setPen(QColor(label_color))
        # v4.4.0 修复(P1-22): 原 `from PyQt5.QtGui import QFont` 写在这里,
        # 每次重绘都做一次模块查找; 已提到模块顶部 + 字体在 __init__ 建好。
        p.setFont(self._font)
        p.drawText(self.rect().adjusted(0, -10, 0, 0), Qt.AlignHCenter | Qt.AlignBottom, self._label)
        p.end()

    def enterEvent(self, e):
        if not self._active:
            self._hovered = True
            self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.update()
        super().leaveEvent(e)


class _GlassPanel(QWidget):
    """主面板 - 玻璃背景容器(半透明着色, 底层由系统Acrylic模糊)"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('mainpanel')
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def paintEvent(self, event):
        p = QPainter(self)
        paint_window_backdrop(p, self.width(), self.height())
        p.end()


def _make_toggle_row(label, toggle_key, settings, toggles_dict, on_toggle_fn):
    """创建开关行（hover 显示柔和底色）。desc 参数已移除(从未使用)。"""
    row = HoverRow()
    row.setStyleSheet('background:transparent;')
    rl = QHBoxLayout(row)
    rl.setContentsMargins(0, 0, 0, 0)
    rl.setSpacing(10)

    btn = AnimatedToggle()
    checked = settings.get(toggle_key, True)
    btn.setChecked(checked)
    btn.toggled.connect(lambda c, k=toggle_key: on_toggle_fn(k, c, btn))
    toggles_dict[toggle_key] = btn
    rl.addWidget(btn)

    lbl = QLabel(label)
    lbl.setStyleSheet(
        f"font-size:{Glass.FONT_SIZE_MD};color:{Glass.TEXT_PRIMARY};background:transparent;")
    lbl.setCursor(Qt.PointingHandCursor)
    # v5.1(ultra M5): 点标签走 click() 而非 toggle() —— toggle 直接跳变不带动画,
    # 与点按钮的 220ms 滑动动画手感不一致(overlay_window 已修过同一问题)。
    lbl.mousePressEvent = lambda e, b=btn: b.click()
    rl.addWidget(lbl)
    rl.addStretch()

    return row, btn


class _MatchRow(QWidget):
    """战绩页单场行: [胜负竖条+头像] 英雄名/模式·时长·位置·时间 | KDA | 经济/补刀/输出 | 胜/负徽章

    v4.3.0: 整行可点击 → 查看本局详情(clicked 信号由战绩页转发给主窗)。
    v4.10.x: 左侧加 3px 胜负竖条(胜红/负绿), 行内加经济/补刀/输出三列 ——
    字段由 main._parse_recent/_parse_eog_stats 直接带出, 无额外请求。

    注意: **这里不设 setToolTip** —— 用户明确不要"点击查看本局详情"的悬浮小提示。
    可点击性靠 PointingHandCursor + 悬停高亮表达就够了。
    """

    ICON = 38
    clicked = pyqtSignal(dict)
    # 图标轮询的时间窗: 窗口内持续重试(覆盖慢速下载), 超窗即放弃 ——
    # 防某个英雄图标永久不存在时每 900ms 空打网络(LCU + CDN 双请求)
    ICON_RETRY_WINDOW_S = 15.0
    # 胜负竖条宽(画在行最左)
    WIN_BAR_W = 3
    # 指标列宽(经济/补刀/输出, 数值右对齐; 与 KDA 列同源风格)。
    # v4.10.x: 52 → 64 —— "99.9万" 实测 72px(离屏字体), 52 放不下会裁切;
    # 配合 wan 紧凑格式(≥100万 取整)最宽 "99.9万" 必须留足。
    METRIC_W = 64
    # 可压缩文本列的最小宽度: 布局空间不足时允许压到这个宽度(子标题/名字
    # 都有省略号逻辑, 压缩不会丢字, 只是提前截断)。不设的话 QLabel 的
    # minimumSizeHint = 整段文本宽, 滚动区比它窄时整行右侧溢出被裁。
    TEXT_MIN_W = 48

    def __init__(self, game, parent=None):
        super().__init__(parent)
        self._game = game or {}
        # v4.10.x(P0): EOG 即时战绩插入的行打标记。update_recent 全量重建时,
        # 该局若 match-history 尚未入库(1~3 分钟延迟)会被当成"消失"删掉;
        # 标记让重建逻辑把它保留, 等正式数据带回同 game_id 再覆盖。
        self._from_eog = False
        self.icon_loaded = False
        self.icon_deadline = _time.monotonic() + self.ICON_RETRY_WINDOW_S
        # v4.4.x(P2): 只对未请求过的 cid 调一次下载回调, 避免每 900ms 重复触发;
        # set_icon 成功或超窗后复位(想重试时允许再次请求)。
        self._dl_requested = False
        self._hovered = False
        cid = self._game.get('champion_id')
        try:
            self.champion_id = int(cid or 0)
        except (TypeError, ValueError):
            self.champion_id = 0

        self.setFixedHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        # v4.4.x(P3): 键盘可达性 —— 行可聚焦, Enter/Space 触发与鼠标点击相同的
        # clicked 信号(见 keyPressEvent)。
        self.setFocusPolicy(Qt.StrongFocus)
        # QWidget 子类默认不绘制样式表里的 background, 必须开 WA_StyledBackground
        # (v4.4.0: 行底色改 paintEvent 自绘后该属性不再必需, 保留无副作用 ——
        #  避免动到其它继承的样式表语义)
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QHBoxLayout(self)
        # 左边距多留 WIN_BAR_W, 给竖条腾位置
        lay.setContentsMargins(10 + self.WIN_BAR_W, 6, 10, 6)
        lay.setSpacing(10)

        # 英雄头像(占位=英雄名首字, 下载完成后 set_icon 覆盖)
        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(self.ICON, self.ICON)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet(
            f'background: {Glass.BG_INSET};'
            f'border-radius: {Glass.RADIUS_SM}px;'
            f'color: {Glass.TEXT_MUTED}; font-size: {Glass.FONT_SIZE_MD};')

        mid = QVBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(1)
        self._name_lbl = QLabel()
        self._name_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};font-weight:600;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        self._sub_lbl = QLabel()
        self._sub_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};"
            f"color:{Glass.TEXT_MUTED};background:transparent;")
        # v4.10.x: 允许压缩 —— 两个 label 的最小宽度收到 TEXT_MIN_W,
        # 空间不足时省略号截断(截断逻辑 _apply_sub_elide 已有), 行不溢出。
        self._name_lbl.setMinimumWidth(self.TEXT_MIN_W)
        self._sub_lbl.setMinimumWidth(self.TEXT_MIN_W)
        mid.addWidget(self._name_lbl)
        mid.addWidget(self._sub_lbl)

        self._kda_lbl = QLabel()
        self._kda_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._kda_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};font-weight:600;"
            f"color:{Glass.TEXT_SECONDARY};background:transparent;")
        # v4.10.x: KDA 下方叠加 KDA 比小字(国服玩家扫一眼更关心比值)
        self._kda_ratio_lbl = QLabel()
        self._kda_ratio_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._kda_ratio_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};"
            f"color:{Glass.TEXT_MUTED};background:transparent;")
        # v4.10.x: KDA 列同样允许压缩("12 / 10 / 13" 实测 156px 是
        # minimumSizeHint, 滚动区窄时把行撑溢出; 压到 74 也够读)。
        self._kda_lbl.setMinimumWidth(74)
        self._kda_ratio_lbl.setMinimumWidth(40)
        kda_col = QVBoxLayout()
        kda_col.setContentsMargins(0, 0, 0, 0)
        kda_col.setSpacing(0)
        kda_col.addWidget(self._kda_lbl)
        kda_col.addWidget(self._kda_ratio_lbl)

        # v4.10.x: 经济/补刀/输出三列 —— 与 KDA 列同一套"上数值下标签"样式
        self._metric_cols = []
        self._metric_layouts = []
        for label in ('经济', '补刀', '输出'):
            val = QLabel()
            val.setFixedWidth(self.METRIC_W)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;"
                f"color:{Glass.TEXT_SECONDARY};background:transparent;")
            lab = QLabel(label)
            lab.setFixedWidth(self.METRIC_W)
            lab.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lab.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};"
                f"color:{Glass.TEXT_MUTED};background:transparent;")
            col = QVBoxLayout()
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(0)
            col.addWidget(val)
            col.addWidget(lab)
            self._metric_cols.append((val, lab))
            self._metric_layouts.append(col)

        self._badge = QLabel()
        self._badge.setFixedSize(24, 24)
        self._badge.setAlignment(Qt.AlignCenter)

        lay.addWidget(self._icon_lbl)
        lay.addLayout(mid, 1)
        lay.addLayout(kda_col)
        for col in self._metric_layouts:
            lay.addLayout(col)
        lay.addWidget(self._badge)

        self._fill()

    def _fill(self):
        g = self._game
        cid = self.champion_id
        name = (g.get('champion_name') or '').strip() or (
            f'英雄#{cid}' if cid else '未知英雄')
        self._name_lbl.setText(name)
        self._icon_lbl.setText(name[:1])

        parts = [_game_mode_text(g)]
        d = _dur_text(g.get('duration_s'))
        if d:
            parts.append(d)
        lane = _lane_text(g.get('lane'))
        if lane:
            parts.append(lane)
        t = _time_text(g.get('created_ms'))
        if t:
            parts.append(t)
        # v4.10.x: 子标题省略号截断, 过长文本不再被 Qt 硬裁成半截字
        sub = ' · '.join([p for p in parts if p])
        self._sub_full = sub
        self._apply_sub_elide()

        k, dd, a = g.get('kills'), g.get('deaths'), g.get('assists')
        self._kda_lbl.setText(_kda_text(k, dd, a))
        # KDA 比: (击杀+助攻)/max(1,死亡); 任一缺字段显示占位
        if k is None or dd is None or a is None:
            self._kda_ratio_lbl.setText('')
        else:
            self._kda_ratio_lbl.setText(f'KDA {_kda_ratio(k, dd, a)}')

        # v4.10.x: 经济/补刀/输出 —— 缺字段显示 '—', 不瞎猜 0。
        # 输出用 wan_compact(≥100万取整), 保证最宽值也放得进 64px 定宽列。
        gold = g.get('gold')
        cs = g.get('cs')
        dmg = g.get('dmg')
        self._metric_cols[0][0].setText(_wan_compact(gold))
        self._metric_cols[1][0].setText(_num_text(cs))
        self._metric_cols[2][0].setText(_wan_compact(dmg))

        # 胜=红 / 负=绿(国服习惯: 红为正向)
        if g.get('win') is True:
            self._badge.setText('胜')
            self._badge.setStyleSheet(
                f'background: {Glass.DANGER_BG}; color: {Glass.DANGER};'
                f'border-radius: {Glass.RADIUS_SM}px;'
                f'font-size: {Glass.FONT_SIZE_XS}; font-weight: 700;')
        else:
            self._badge.setText('负')
            self._badge.setStyleSheet(
                f'background: {Glass.SUCCESS_SUBTLE}; color: {Glass.SUCCESS};'
                f'border-radius: {Glass.RADIUS_SM}px;'
                f'font-size: {Glass.FONT_SIZE_XS}; font-weight: 700;')
        # 胜负色变了, 竖条也要跟着重画
        self.update()

    def _apply_sub_elide(self):
        """按当前 label 宽度做单行省略; 完整文本在 _sub_full, tooltip 可见。"""
        try:
            sub = getattr(self, '_sub_full', '') or ''
            fm = self._sub_lbl.fontMetrics()
            w = max(20, self._sub_lbl.width())
            self._sub_lbl.setText(fm.elidedText(sub, Qt.ElideRight, w))
            self._sub_lbl.setToolTip(sub)
        except Exception:
            pass

    def resizeEvent(self, event):
        """宽度变化时重算子标题省略号 —— 构造期布局未激活, width 可能不准。"""
        super().resizeEvent(event)
        self._apply_sub_elide()

    def paintEvent(self, event):
        """行底色(悬停/常态) + 左侧胜负竖条, 全在 paintEvent 里画。

        v4.4.0 修复(P1-18): 原来 enterEvent/leaveEvent 各自 `setStyleSheet`
        重写整行 `background/border-radius` —— 每次鼠标划过一行都触发一次
        QSS 解析 + 该行及其子控件重新 polish, 滚动列表里鼠标扫过几十行时
        是可见的抖动源。底色本就只是一个圆角矩形, 直接画即可。

        v4.10.x: 胜红/负绿竖条画在最左, 与底色圆角同框, 不重写 QSS。
        """
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # alpha 对应旧样式表逐位等价（浅色主题：悬停深灰微底）
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, 20 if self._hovered else 6))
        r = Glass.RADIUS_BTN
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), r, r)
        # 胜负竖条: 红=胜 / 绿=负(与徽章同色系), 未定(脏数据)不画
        win = self._game.get('win')
        if isinstance(win, bool):
            bar = QColor(Glass.DANGER) if win else QColor(Glass.SUCCESS)
            p.setBrush(bar)
            p.drawRoundedRect(
                0, 8, self.WIN_BAR_W, self.height() - 16, 2, 2)
        p.end()

    def _apply_bg(self):
        """v4.4.0 修复(P1-18): 不再重写样式表, 只标脏让 paintEvent 重绘。"""
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self._apply_bg()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self._apply_bg()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        # 只认左键, 且要求按下/松开都在本行内(拖到行外松手不算点击)
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            try:
                self.clicked.emit(dict(self._game))
            except Exception as e:
                print(f'[战绩] 行点击处理失败: {type(e).__name__}: {e}')
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        """v4.4.x(P3): 键盘可达性 —— Enter/Space 等价于鼠标点击本行。"""
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            try:
                self.clicked.emit(dict(self._game))
            except Exception as e:
                print(f'[战绩] 行键盘触发失败: {type(e).__name__}: {e}')
            event.accept()
            return
        super().keyPressEvent(event)

    def set_icon(self, data) -> bool:
        """用图标字节填充头像(圆角裁剪)。失败返回 False 让上层继续轮询。"""
        try:
            rounded = rounded_pixmap(data, self.ICON, 6, widget=self)
            if rounded is None:
                return False
            self._icon_lbl.setText('')
            self._icon_lbl.setPixmap(rounded)
            self.icon_loaded = True
            self._dl_requested = False
            return True
        except Exception:
            return False


class MainWindow(DragMixin, GlassEffectMixin, QMainWindow):
    start_script_signal = pyqtSignal()
    stop_script_signal = pyqtSignal()
    minimize_signal = pyqtSignal()
    close_signal = pyqtSignal()
    # v4.4.5: 首页「启动 LOL」按钮
    launch_lol_signal = pyqtSignal()
    # v4.8.0: record_lol_signal(设置页「开始录制/停止录制」)已随录制功能删除。
    # v4.4.16: 首页「全部停止」→ main 关掉 LOL 并 3 秒后停脚本
    stop_all_signal = pyqtSignal()
    settings_changed_signal = pyqtSignal(dict)
    # v34: 用户在设置页打开"自动选英雄" → 由 main 决定是直接开还是提权重启
    auto_select_champion_request_signal = pyqtSignal()
    # v4.1.60: 首次启动须知确认后发出 → main 补查"自动选英雄"提权(覆盖
    # "老版本已开 auto_select 但 agreement_accepted 缺失"升级场景, 800ms
    # 启动检查会因 agreement=False 跳过, 须在确认须知后补一次)
    agreement_confirmed_signal = pyqtSignal()
    # v4.6.3: 设置页「清理缓存」→ main 执行(需要访问缓存实例与日志句柄)
    clean_cache_signal = pyqtSignal(list)
    # v4.7.1: 首页授权卡「输入授权码」→ 由 main 打开激活窗。
    # 用信号而不是直接弹窗：弹窗要访问 licensing（可能触发指纹采集），
    # 统一由 main 处理更符合本项目的分层（UI 不碰耗时逻辑）。
    open_activation_signal = pyqtSignal()


    sig_update_connection = pyqtSignal(bool, str)
    sig_update_phase = pyqtSignal(str)
    sig_set_script_running = pyqtSignal(bool)
    sig_append_log = pyqtSignal(str)
    # v4.9.3(P1): 复制机器码后台采集完成 → 主线程写剪贴板/恢复按钮
    sig_machine_id_ready = pyqtSignal(str)
    sig_machine_id_failed = pyqtSignal(str)
    # 熟练度 top3 英雄头像：后台线程下载的图标 bytes → 主线程渲染
    sig_mastery_icon = pyqtSignal(int, object)

    def __init__(self):
        super().__init__()
        self._running = False
        self._settings = self._load_settings()
        self._toggles = {}
        self._champion_picker = None
        self._toast = None
        # v4.1.66: 战绩页状态
        self._recent_rows = []          # 当前渲染的 _MatchRow 列表
        self._recent_sig = None         # 对局签名(gameId 元组), 变化才重建
        self._recent_icon_cb = None     # main 注入: cid -> bytes|None(仅内存缓存)
        self._recent_dl_cb = None       # main 注入: cid -> 触发后台下载
        self._recent_refresh_cb = None  # main 注入: 手动刷新(置 _force_profile)
        self._refresh_pending = False   # v4.10.x: 手动刷新进行中标记(按钮禁用态)
        self._recent_icon_timer = None
        self._acct_id_state = None      # v4.4.x(P3): 账号 ID 标签脏值比较缓存
        self._lic_copying = False       # v4.9.3(P1): 机器码后台采集进行中标记
        # v4.3.0: 对局详情弹窗(main 注入的装备图标回调 + 数据回调 + 当前弹窗引用)
        self._item_icon_cb = None
        self._item_dl_cb = None
        self._recent_detail_cb = None
        # v4.4.0 修复: v4.3.2 新增的召唤师技能/海克斯强化回调只在
        # set_recent_icon_cb() 里赋值, __init__ 漏了。main 若还没注入就点开
        # 战绩详情, _open_recent_detail 会 AttributeError 直接弹"详情打开失败"。
        self._spell_icon_cb = None
        self._spell_dl_cb = None
        self._aug_icon_cb = None
        self._aug_dl_cb = None
        self._detail_dlg = None
        # v4.4.0 修复(P1-23): 生成式占位头像缓存 (icon_id, 首字, dpr, side) -> QPixmap
        self._avatar_gen_cache = {}
        self._init_ui()
        self._connect_safe()


    def _init_ui(self):
        # v4.4.0 修复(P1-21): 拖拽/玻璃去重状态先初始化(见 window_base)
        self._init_drag()
        self._init_glass()
        self.setWindowTitle('大蛋小助手')
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # 可缩放窗口（ULTRA 验收 §C 要求最大化/拉伸）：
        # 默认 720x540，允许放大；内容页均为 ScrollArea，放大后自适应。
        # 下限略低于默认值，给 150% 缩放的小屏（如 1366x768）留出余量。
        self.setMinimumSize(680, 500)
        self.resize(720, 540)

        # v4.4.0 修复(P1-15): 原 `QApplication.primaryScreen().geometry()` 拿的是
        # **设备像素**, 再喂给 **逻辑像素** 的 move(), 125%/150% 缩放下窗口会偏出
        # 屏幕; 且硬绑主屏, 副屏(尤其在主屏左侧、坐标为负)上会甩到屏外。
        # 改走 screen_utils: 逻辑坐标 availableGeometry + 屏幕原点代入。
        screen_utils.center_on_screen(self)

        container = QWidget()
        container.setStyleSheet('background:transparent;')
        self.setCentralWidget(container)

        main = QVBoxLayout(container)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        panel = _GlassPanel()

        pl = QVBoxLayout(panel)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)

        pl.addWidget(self._make_title_bar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        body.addWidget(self._make_sidebar())

        self._pages = SpringStackedWidget()
        self._pages.setStyleSheet("background:transparent;")
        self._pages.addWidget(self._make_home_page())
        self._pages.addWidget(self._make_settings_page())
        self._pages.addWidget(self._make_hero_page())
        self._pages.addWidget(self._make_recent_page())
        self._pages.addWidget(self._make_log_page())
        body.addWidget(self._pages, 1)

        body_widget = QWidget()
        body_widget.setLayout(body)
        pl.addWidget(body_widget)

        main.addWidget(panel)

        # 延迟应用 Windows 液态玻璃效果（等待窗口完全创建）
        QTimer.singleShot(200, self._apply_glass_effect)

    def _glass_apply_core(self):
        """v4.4.0 修复(P1-21): 真正应用的那一次(由 GlassEffectMixin 去重调度)。"""
        from .glass_effect import apply_liquid_glass
        apply_liquid_glass(self, enable_shadow=False)

    def showEvent(self, event):
        super().showEvent(event)
        # showEvent后确保glass effect已应用（hide后DWM会重置backdrop，延迟重新应用）
        # v4.4.0 修复(P1-21): 原 120ms/500ms 两次 singleShot 在这里 + :576 的
        # 200ms 一次, **每次显示触发最多 3 次** apply_liquid_glass(每次
        # SetWindowLongW + SetWindowPos(SWP_FRAMECHANGED) + 4×DwmSetWindowAttribute)。
        # 改由 GlassEffectMixin 的脏标志去重: 同一 geometry 只真打一次,
        # 观感不变(见 window_base.GlassEffectMixin)。
        self._mark_glass_dirty()
        self._schedule_glass()
        # v4.3.10: 重新可见时恢复呼吸灯动画(隐藏时已停, 见 hideEvent / _update_dot)
        t = getattr(self, '_dot_timer', None)
        if t is not None and not t.isActive():
            t.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        # v4.3.10: 隐藏即停呼吸动画 —— 原来该定时器一旦启动就永不停止,
        # 窗口最小化到托盘后仍以 25fps 重算样式表, 白白吃 CPU。
        t = getattr(self, '_dot_timer', None)
        if t is not None and t.isActive():
            t.stop()
        # v4.4.0(P1-21): hide 时 DWM 会清掉 backdrop, 下次显示必须重新应用
        self._mark_glass_dirty()

    def resizeEvent(self, event):
        # v4.4.0 修复(P2 死代码): 原来是空的 `super().resizeEvent(event)` 覆写,
        # 现在承担真实职责 —— 尺寸变了要让毛玻璃按新 geometry 重算一次。
        super().resizeEvent(event)
        self._mark_glass_dirty()
        self._schedule_glass()

    def _connect_safe(self):
        self.sig_update_connection.connect(self._do_conn)
        self.sig_update_phase.connect(self._do_phase)
        self.sig_set_script_running.connect(self._do_run)
        self.sig_append_log.connect(self._do_log)
        self.sig_machine_id_ready.connect(self._on_machine_id_ready)
        self.sig_machine_id_failed.connect(self._on_machine_id_failed)
        self.sig_mastery_icon.connect(self._do_mastery_icon)

    # ==================== 标题栏 ====================
    def _make_title_bar(self):
        bar = GlassTitleBar(44)
        bar.setStyleSheet('background:transparent;border:none;')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 0, 8, 0)

        t = QLabel('大蛋小助手')
        t.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:bold;color:{Glass.TEXT_PRIMARY};background:transparent;")
        lay.addWidget(t)
        # 版本号移到标题栏（豆包意见：移出首页侧栏）
        ver = QLabel(f'v{APP_VERSION}')
        ver.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
            f"background:transparent;padding-top:2px;")
        lay.addWidget(ver)
        lay.addStretch()

        # 窗口控制按钮（统一工厂：主窗口/小窗共用同一样式）
        for b in make_title_buttons(minimize_cb=self.minimize_signal,
                                    close_cb=self.close_signal):
            lay.addWidget(b)

        # v4.4.0 修复(P2 拖拽): 原来内联三个 lambda, 且 `if self._drag_pos:`
        # —— QPoint(0,0) 为假, 从窗口左上角(偏移恰为 0,0)无法起拖。
        # 改走 window_base.DragMixin 的 `is not None` 判定。
        self._install_drag(bar)
        return bar

    # ==================== 侧边栏 ====================
    def _make_sidebar(self):
        sidebar = QWidget()
        sidebar.setFixedWidth(60)
        # v4.4.0 修复(P2 设计令牌): 内联 rgba 改走 Glass.BG_SIDEBAR
        sidebar.setStyleSheet(f'background:{Glass.BG_SIDEBAR};')
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 8, 0, 8)
        lay.setSpacing(4)   # ds 意见：导航项垂直间距加大

        self._nav_buttons = []
        # v4.4.0(P2 硬编码): 页表抽成模块级常量 SIDEBAR_PAGES
        for i, (ic, label) in enumerate(SIDEBAR_PAGES):
            btn = SidebarButton(ic, label)
            btn.clicked.connect(lambda _, idx=i: self._switch_page(idx))
            lay.addWidget(btn)
            self._nav_buttons.append(btn)

        lay.addStretch()

        self._nav_buttons[0].set_active(True)
        return sidebar

    @ui_slot
    def _switch_page(self, idx):
        if self._pages.currentIndex() == idx:
            return
        self._pages.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.set_active(i == idx)
        # 切到战绩页时补一轮图标并重开时间窗; 离开战绩页则停掉轮询(下载是后台
        # 异步的, 页面不可见时不该每 900ms 空转), 切回时 _tick_recent_icons(rearm=True)
        # 会按需重新 start。
        if idx == 3:
            self._tick_recent_icons(rearm=True)
        else:
            t = getattr(self, '_recent_icon_timer', None)
            if t is not None and t.isActive():
                t.stop()
        # v4.6.3: 切到设置页时清点一次缓存占用 —— 否则用户看到的是上次的旧数字
        if idx == 1:
            self.refresh_clean_sizes()

    # ==================== 首页 ====================
    def _make_home_page(self):
        """首页（液态玻璃版三段式布局）：

        ① 状态条卡（合并「连接状态」+「当前状态」为一条，当前阶段放大到右端）
        ② 中部横排 = 账号卡(左, 弹性) + 操作卡(右, 固定宽)
        ③ 底部近期战绩速览条
        消除旧版「两张一行卡 + 中部大空洞 + 按钮贴底」的断裂节奏。
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + Glass.SCROLL_QSS)

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(24, 20, 24, 18)   # 呼吸感：内边距加大
        lay.setSpacing(12)

        # ===== ① 状态条：左=连接状态 pill，右=当前阶段 pill（胶囊化） =====
        status_card = GlassCard()
        sc = QHBoxLayout(status_card)
        sc.setContentsMargins(16, 12, 16, 12)
        sc.setSpacing(10)
        # 连接状态 pill（自绘 StatusDot 呼吸灯）
        conn_pill = QWidget()
        conn_pill.setStyleSheet(
            f"background:{Glass.BG_CARD_ALT};border-radius:10px;")
        cp = QHBoxLayout(conn_pill)
        cp.setContentsMargins(10, 4, 12, 4)
        cp.setSpacing(6)
        self._dot = StatusDot(10, color=DOT_RGB_OFFLINE, alpha=170)
        self._update_dot(False)
        cp.addWidget(self._dot)
        self._conn_label = QLabel('未连接到客户端')
        self._conn_label.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};font-weight:600;"
            f"color:{Glass.TEXT_SECONDARY};background:transparent;")
        cp.addWidget(self._conn_label)
        sc.addWidget(conn_pill)
        sc.addStretch()
        # 当前状态 pill（当前阶段放大）
        phase_pill = QWidget()
        phase_pill.setStyleSheet(
            f"background:{Glass.PRIMARY_SUBTLE};border-radius:10px;")
        pp = QHBoxLayout(phase_pill)
        pp.setContentsMargins(10, 4, 12, 4)
        pp.setSpacing(6)
        phase_tag = QLabel('当前状态')
        phase_tag.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        pp.addWidget(phase_tag)
        self._phase_label = QLabel('等待启动')
        self._phase_label.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:600;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        pp.addWidget(self._phase_label)
        sc.addWidget(phase_pill)
        lay.addWidget(status_card)

        # ===== ② 中部：账号卡(左, 弹性) + 操作卡(右, 固定宽) =====
        mid = QHBoxLayout()
        mid.setContentsMargins(0, 0, 0, 0)
        mid.setSpacing(12)

        acct_card = self._build_account_card()
        self._acct_card = acct_card
        mid.addWidget(acct_card, 1)

        ops_card = GlassCard()
        ol = QVBoxLayout(ops_card)
        ol.setContentsMargins(14, 14, 14, 14)
        ol.setSpacing(8)   # ds 意见：按钮间距收紧，避免留白过多
        ops_title = QLabel('操作')
        ops_title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};font-weight:600;"
            f"color:{Glass.TEXT_MUTED};background:transparent;")
        ol.addWidget(ops_title)

        # 启动 LOL：绝对主操作（青色实心），压过其它按钮
        self._launch_lol_btn = QPushButton('启动 LOL')
        self._launch_lol_btn.setFixedSize(_BTN_W, _BTN_H)
        self._launch_lol_btn.setCursor(Qt.PointingHandCursor)
        self._launch_lol_btn.setStyleSheet(_action_btn_qss('accent'))
        self._launch_lol_btn.clicked.connect(self._on_launch_lol)
        ol.addWidget(self._launch_lol_btn, 0, Qt.AlignHCenter)

        # 说明小字：正下方居中，12px 灰
        self._launch_lol_hint = QLabel('')
        self._launch_lol_hint.setWordWrap(True)
        self._launch_lol_hint.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self._launch_lol_hint.setFixedWidth(_BTN_W)
        self._launch_lol_hint.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};color:#8A8F99;background:transparent;")
        self._refresh_launch_hint()
        ol.addWidget(self._launch_lol_hint)

        # 启动脚本：次级操作（青描边），运行中变「停止脚本」红描边（降低视觉干扰）
        self._fab = QPushButton('启动脚本')
        self._fab.setFixedSize(_BTN_W, _BTN_H)
        self._fab.setCursor(Qt.PointingHandCursor)
        self._fab.setStyleSheet(_fab_qss(False))
        self._fab.clicked.connect(self._on_start)
        ol.addWidget(self._fab, 0, Qt.AlignHCenter)

        # 全部停止：红描边（危险确认感更强，且运行中不与「停止脚本」实心红撞型）
        self._stop_all_btn = QPushButton('全部停止')
        self._stop_all_btn.setFixedSize(_BTN_W, _BTN_H)
        self._stop_all_btn.setCursor(Qt.PointingHandCursor)
        self._stop_all_btn.setStyleSheet(_action_btn_qss('ghost_red'))
        self._stop_all_btn.clicked.connect(self._on_stop_all)
        ol.addWidget(self._stop_all_btn, 0, Qt.AlignHCenter)
        ol.addStretch()

        mid.addWidget(ops_card)
        lay.addLayout(mid, 1)

        # ===== ③ 底部：近期战绩速览条（背景与主面板融合，顶部分隔线，贴底） =====
        recent_bar = QWidget()
        recent_bar.setStyleSheet("background:transparent;")
        rc = QHBoxLayout(recent_bar)
        rc.setContentsMargins(4, 10, 4, 2)
        rc.setSpacing(8)
        rtitle = QLabel('近期战绩')
        rtitle.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};font-weight:600;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        rc.addWidget(rtitle)
        # 提示文案随连接态切换（已连接不再显示"自动加载"）
        self._recent_hint = QLabel('连接客户端后自动加载')
        self._recent_hint.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        rc.addWidget(self._recent_hint)
        rc.addStretch()
        rc.addWidget(self._chip_recent)
        # 整条可点击 → 跳转战绩页（第 4 页，索引 3）
        recent_bar.setCursor(Qt.PointingHandCursor)
        recent_bar.mousePressEvent = lambda e: self._switch_page(3)
        lay.addWidget(recent_bar)

        scroll.setWidget(content)
        return scroll


    def _build_account_card(self) -> GlassCard:
        """构建首页「账号信息卡」(v4.6.11 从 _make_home_page 抽出)。

        为什么要抽: _make_home_page 原本 273 行, 一个函数里串了连接状态卡/
        按钮组/账号卡/当前状态卡/底部横排五块。账号卡这一块独占 150 行、
        内部自成体系(7 行: 头像+ID / 等级 / 段位 / 资产 / 神话+荣誉 /
        熟练度 top3 / 近期战绩), 是天然的分节边界。
        抽出的正文**一字未改**, 只把末尾的 `self._acct_card = acct_card`
        换成 `return acct_card` —— 赋值仍由调用方完成, 保持原语义。
        """
        acct_card = GlassCard()
        # v4.1.48: 按用户截图红框宽 = 380, 给账号卡固定宽让右侧 fab 区域稳定;
        # 头像缩 48 / label 列缩 44 让 7 行内容可塞下不溢出.
        acct_card.setMinimumWidth(340)   # 液态玻璃版：放开固定宽，随中部横排弹性伸缩
        acl = QVBoxLayout(acct_card)
        acl.setContentsMargins(14, 10, 14, 12)
        acl.setSpacing(6)

        # v4.1.48 重构: label 列统一 44px(原 56), chip 紧凑单段(去 prefix), 头像 48x48,
        # 让 row1/row6 等溢出行在 380-400 宽账号卡内可放下.
        LBL_W = 44

        # 行1:头像 + 游戏ID(标签值两列布局)
        row1 = QHBoxLayout()
        row1.setSpacing(10)
        self._avatar_lbl = QLabel('🎮')
        self._avatar_lbl.setFixedSize(48, 48)
        self._avatar_lbl.setAlignment(Qt.AlignCenter)
        # 头像：纯圆形，无底色无描边（用户反馈：描边+底色看着像"绿色底子"）
        self._avatar_lbl.setStyleSheet(
            f"background:transparent;border:none;border-radius:24px;"
            f"font-size:20px;color:{Glass.TEXT_MUTED};font-weight:600;")
        # v4.1.39:真头像已成功上屏的 icon_id(0=无)。探活轮询(3s)刷新账号卡
        # 时,若 icon_id 与已渲染真头像一致则不再覆盖成"首字生成式占位"。
        self._avatar_real_id = 0
        row1.addWidget(self._avatar_lbl)
        name_col = QVBoxLayout()
        name_col.setSpacing(2)
        # 标签: 游戏ID
        id_kv = QHBoxLayout(); id_kv.setSpacing(6)
        k = QLabel('游戏ID'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        id_kv.addWidget(k)
        self._acct_id_lbl = QLabel('--')
        self._acct_id_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_LG};font-weight:600;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        id_kv.addWidget(self._acct_id_lbl, 1)
        name_col.addLayout(id_kv)
        row1.addLayout(name_col, 1)
        acl.addLayout(row1)

        # 行2:等级 + 经验进度（进度条与数字间距拉开）
        row2 = QHBoxLayout(); row2.setSpacing(8)
        k = QLabel('等级'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row2.addWidget(k)
        self._acct_lv_lbl = QLabel('--')
        self._acct_lv_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};color:{Glass.TEXT_PRIMARY};"
            f"background:transparent;font-weight:600;")
        row2.addWidget(self._acct_lv_lbl)
        # 经验进度条(距下一级百分比)
        self._acct_xp = QProgressBar()
        self._acct_xp.setRange(0, 100)
        self._acct_xp.setValue(0)
        self._acct_xp.setTextVisible(False)
        self._acct_xp.setFixedHeight(4)
        self._acct_xp.setStyleSheet(f"""
            QProgressBar {{ background: {Glass.BORDER};
                border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {Glass.PRIMARY};
                border-radius: 2px; }}
        """)
        row2.addWidget(self._acct_xp, 1)
        self._acct_xp_txt = QLabel('升级 --%')
        self._acct_xp_txt.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        self._acct_xp_txt.setMinimumWidth(60)
        self._acct_xp_txt.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row2.addWidget(self._acct_xp_txt)
        acl.addLayout(row2)

        # 行3:段位(单排/灵活) —— ultra 版：两个等分统计块（label 上 / 值下）
        row3 = QHBoxLayout(); row3.setSpacing(CHIP_GAP)
        k = QLabel('段位'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row3.addWidget(k)
        self._chip_solo = self._mk_stat_chip(CHIP_SOLO[0], '单排', '--')
        self._chip_flex = self._mk_stat_chip(CHIP_FLEX[0], '灵活', '--')
        row3.addWidget(self._chip_solo, 1)
        row3.addWidget(self._chip_flex, 1)
        acl.addLayout(row3)

        # 行4:资产 + 荣誉 —— ultra 版：四个等分统计块一行
        row4 = QHBoxLayout(); row4.setSpacing(CHIP_GAP)
        k = QLabel('资产'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row4.addWidget(k)
        self._chip_wallet_ip = self._mk_stat_chip(CHIP_WALLET_IP[0], '蓝色精粹', '--', mono=True)
        self._chip_wallet_cosmetic = self._mk_stat_chip(
            CHIP_WALLET_COSMETIC[0], '橙色精粹', '--', mono=True)
        self._chip_wallet_mythic = self._mk_stat_chip(
            CHIP_WALLET_MYTHIC[0], '神话精粹', '--', mono=True)
        self._chip_honor = self._mk_stat_chip(CHIP_HONOR[0], '荣誉', '--', mono=True)
        for c in (self._chip_wallet_ip, self._chip_wallet_cosmetic,
                  self._chip_wallet_mythic, self._chip_honor):
            row4.addWidget(c, 1)
        acl.addLayout(row4)

        # 行5:熟练度 top3 —— 液态玻璃 ultra 版：改为「英雄头像 + 等级」统计块，
        # 三个等分撑满一行（不再显示英雄名，用户指定）。
        row5 = QHBoxLayout(); row5.setSpacing(CHIP_GAP)
        k = QLabel('熟练度'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row5.addWidget(k)
        self._chip_m1 = self._mk_mast_chip()
        self._chip_m2 = self._mk_mast_chip()
        self._chip_m3 = self._mk_mast_chip()
        row5.addWidget(self._chip_m1, 1)
        row5.addWidget(self._chip_m2, 1)
        row5.addWidget(self._chip_m3, 1)
        acl.addLayout(row5)

        # 行6:最近战绩 —— chip 挪到首页底部速览条（双色胜负 tag）
        self._chip_recent = self._mk_recent_chip()
        # 注:v37b 移到启动按钮下方, 此处不再 addWidget(延迟到 fab 后)
        return acct_card
    # ==================== 设置页 ====================
    def _make_settings_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + Glass.SCROLL_QSS)

        content = QWidget()
        content.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 16, 16, 16)
        lay.setSpacing(10)

        title = QLabel('功能设置')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:700;color:{Glass.TEXT_PRIMARY};background:transparent;")
        lay.addWidget(title)

        def _group(text, first=False):
            g = QLabel(text)
            g.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;"
                f"color:{Glass.TEXT_SECONDARY};background:transparent;"
                f"padding-left:2px;padding-top:{'0px' if first else '6px'};")
            lay.addWidget(g)

        _group('自动化操作', first=True)
        lay.addWidget(self._make_delay_card('自动匹配', 'auto_queue', 'auto_queue_delay', 2, 0, 30, '进入房间后自动开始匹配'))
        lay.addWidget(self._make_delay_card('自动接受', 'auto_accept', 'auto_accept_delay', 1, 0, 15, '匹配成功后自动接受对局'))
        lay.addWidget(self._make_delay_card('自动返回', 'auto_play_again', 'auto_play_again_delay', 2, 0, 30, '游戏结束后自动返回房间'))

        # v4.4.16(用户要求): 连上客户端后自动启动脚本(可设延时)。
        # 场景: 开机 → 点「启动 LOL」→ 进大厅后不用再点一次「启动脚本」。
        lay.addWidget(self._make_delay_card(
            '连上客户端自动启动脚本', 'auto_start_on_connect',
            'auto_start_on_connect_delay', 5, 0, 120,
            '检测到连上 LOL 客户端后，等设定秒数自动启动脚本'))

        _group('界面行为')
        # v4.8.1(用户要求): 点「启动 LOL」成功后隐藏主界面并打开小窗。
        # 做成"开关 + 说明"的独立卡片(不带延时控件 —— 它与时间无关)。
        card_lo = GlassCard()
        clo = QVBoxLayout(card_lo)
        clo.setContentsMargins(14, 12, 14, 12)
        clo.setSpacing(8)
        row_lo, _ = _make_toggle_row(
            '启动 LOL 后收起主界面', 'lol_launch_to_overlay',
            self._settings, self._toggles, self._toggle)
        clo.addWidget(row_lo)
        desc_lo = QLabel(
            '登录器启动成功后，自动把主界面收起、只留小窗；'
            '想回到主界面点小窗上的「主窗」按钮即可')
        desc_lo.setWordWrap(True)
        desc_lo.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
            f"background:transparent;padding-left:52px;padding-right:8px;")
        clo.addWidget(desc_lo)
        lay.addWidget(card_lo)

        _group('英雄选择')
        # v34: 自动选英雄(进选人界面2-3 张卡时主动点选; 默认关; 打开时若非管理员则请求提权)
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        row, _ = _make_toggle_row('自动选英雄', 'auto_select_champion', self._settings, self._toggles, self._toggle)
        cl.addWidget(row)
        desc = QLabel('进选人界面按偏好点击卡片；无偏好时随机点一张兜底（需要管理员权限以绕开 UIPI）')
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;padding-left:52px;padding-right:8px;")
        cl.addWidget(desc)
        lay.addWidget(card)

        # 自动换英雄(锁定后按偏好从 bench 秒换; 默认开)
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        row, _ = _make_toggle_row('自动换英雄', 'auto_pick_champion', self._settings, self._toggles, self._toggle)
        cl.addWidget(row)
        desc = QLabel('按偏好英雄优先级自动切换，无偏好时不换')
        desc.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;padding-left:52px;")
        cl.addWidget(desc)
        lay.addWidget(card)

        # v4.9.13(用户要求): 小窗备选栏单击 = 预输入本局想玩的英雄(蓝光)。
        # 优先级高于偏好; 单击选中、再单击取消; 脚本在备选池里一出现就换过去。
        card_pi = GlassCard()
        cpi = QVBoxLayout(card_pi)
        cpi.setContentsMargins(14, 12, 14, 12)
        cpi.setSpacing(8)
        row_pi, _ = _make_toggle_row('预输入选英雄', 'auto_preinput_champion',
                                     self._settings, self._toggles, self._toggle)
        cpi.addWidget(row_pi)
        desc_pi = QLabel(
            '在小窗备选栏单击头像即可预先指定本局想玩的英雄（蓝光标记，再单击取消）；'
            '脚本会优先切到它，优先级高于偏好英雄。关闭后单击头像恢复为“立即换英雄”')
        desc_pi.setWordWrap(True)
        desc_pi.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
            f"background:transparent;padding-left:52px;padding-right:8px;")
        cpi.addWidget(desc_pi)
        lay.addWidget(card_pi)

        # 自动点赞
        card_h = GlassCard()
        ch = QVBoxLayout(card_h)
        ch.setContentsMargins(14, 12, 14, 12)
        ch.setSpacing(8)
        row_h, _ = _make_toggle_row('自动点赞', 'auto_honor', self._settings, self._toggles, self._toggle)
        ch.addWidget(row_h)
        desc_h = QLabel('对局结束后自动给队友点赞')
        desc_h.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;padding-left:52px;")
        ch.addWidget(desc_h)
        lay.addWidget(card_h)

        # G5:移除"允许接受后再拒绝"伪开关——core 无对应实现,开了无任何效果,
        # 保留只会误导用户。对应 settings 遗留键无害,load 时被忽略。

        # v4.3.8: 「检查更新」卡片已整体移除(用户明确表示不需要该功能)。

        # v4.4.5: 启动 LOL 按钮坐标标定
        # v4.8.0: 设置页「启动 LOL 录制/回放」卡片已整体删除
        # (录制回放功能移除, 启动 LOL 只打开登录器, 不再需要录制任何点击流程)。

        # v4.6.3: 清理缓存(用户要求)
        self._add_clean_cache_card(lay)

        # v4.7.3: 授权/激活（用户要求放设置页，不放首页）
        self._add_license_card(lay)

        lay.addStretch()
        scroll.setWidget(content)
        return scroll

    # ==================== v4.7.3: 授权 / 激活 ====================
    def _add_license_card(self, lay):
        """设置页的「授权 / 激活」卡片。

        ## 为什么放设置页（用户明确要求）

        原来是首页大卡片，两个问题：
          1. **把首页右下角的功能按钮挤走了** —— 首页底部是「启动 LOL /
             启动脚本 / 全部停止」这条主操作链，多一张卡就顶出去了，显示不全；
          2. **位置不合常识** —— 激活码/授权这类东西用户会去"设置"或"关于"里找，
             没有放首页的道理（用户原话："激活码这东西哪有放首页的"）。

        ## 为什么必须常驻可见（这是 v4.7.0 的真缺陷）

        v4.7.0 把试用状态与机器码只放进了"被拦下时弹的激活窗"，而试用期内
        不弹窗 ⇒ **用户拿不到自己的机器码**，没法发给卖家换授权码，
        购买流程直接断掉（实测用户第一句话就是"没看到7天试用和获取机器码按钮"）。
        所以这个入口的可见性是**功能必需**，不是装饰。
        """
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        # ---- 标题行：🔑 授权 + 右侧状态 ----
        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title = QLabel('授权 / 激活')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;"
            f"color:{Glass.TEXT_SECONDARY};background:transparent;")
        title_row.addWidget(title)
        title_row.addStretch()

        self._lic_state_lbl = QLabel('读取中…')
        self._lic_state_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};font-weight:700;"
            f"color:{Glass.TEXT_MUTED};background:transparent;")
        title_row.addWidget(self._lic_state_lbl)
        cl.addLayout(title_row)

        # ---- 说明/剩余天数 ----
        self._lic_hint_lbl = QLabel('')
        self._lic_hint_lbl.setWordWrap(True)
        self._lic_hint_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        cl.addWidget(self._lic_hint_lbl)

        # ---- 机器码：值 + 复制 ----
        mid_row = QHBoxLayout()
        mid_row.setSpacing(8)
        self._lic_mid_lbl = QLabel('机器码读取中…')
        self._lic_mid_lbl.setWordWrap(True)
        self._lic_mid_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._lic_mid_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_SECONDARY};"
            f"background:transparent;font-family:'Cascadia Code','Consolas',monospace;")
        mid_row.addWidget(self._lic_mid_lbl, 1)

        self._lic_copy_btn = QPushButton('复制机器码')
        self._lic_copy_btn.setFixedSize(96, 30)
        self._lic_copy_btn.setCursor(Qt.PointingHandCursor)
        self._lic_copy_btn.setStyleSheet(_mini_btn_qss())
        self._lic_copy_btn.clicked.connect(self._on_copy_machine_id)
        mid_row.addWidget(self._lic_copy_btn, 0, Qt.AlignTop)
        cl.addLayout(mid_row)

        # ---- 动作行 ----
        act_row = QHBoxLayout()
        act_row.setSpacing(8)
        self._lic_hint2_lbl = QLabel('')
        self._lic_hint2_lbl.setWordWrap(True)
        self._lic_hint2_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        act_row.addWidget(self._lic_hint2_lbl, 1)

        self._lic_act_btn = QPushButton('输入授权码')
        self._lic_act_btn.setFixedSize(110, 30)
        self._lic_act_btn.setCursor(Qt.PointingHandCursor)
        self._lic_act_btn.setStyleSheet(_mini_btn_qss(primary=True))
        self._lic_act_btn.clicked.connect(self.open_activation_signal.emit)
        act_row.addWidget(self._lic_act_btn, 0, Qt.AlignTop)
        cl.addLayout(act_row)

        self._lic_machine_id = ''
        self.update_license_card(None)
        lay.addWidget(card)

    def update_license_card(self, status):
        """由 main.py 在授权判定完成后调用，刷新设置页的授权卡。

        `status` 是 core.licensing.LicenseStatus（或 None = 还没查出来）。
        **只读它、不自己判定** —— 放行口径统一在 licensing.usable，
        别在 UI 里重写一遍（那是两处逻辑迟早分家的经典来源）。
        """
        try:
            if getattr(self, '_lic_state_lbl', None) is None:
                return

            if status is None:
                self._lic_state_lbl.setText('读取中…')
                self._lic_state_lbl.setStyleSheet(
                    f"font-size:{Glass.FONT_SIZE_SM};font-weight:700;"
                    f"color:{Glass.TEXT_MUTED};background:transparent;")
                self._lic_mid_lbl.setText('机器码读取中…')
                self._lic_hint_lbl.setText('')
                self._lic_hint2_lbl.setText('')
                return

            mid = getattr(status, 'machine_id', '') or ''
            if mid:
                self._lic_machine_id = mid
                # 分组显示更易读；复制时给纯码（不带连字符）
                self._lic_mid_lbl.setText(f'机器码  {_group_code(mid)}')

            if getattr(status, 'activated', False):
                d = status.days_left
                if d is not None and d < 0:
                    self._lic_state_lbl.setText('已过期')
                    color = Glass.DANGER
                    self._lic_hint_lbl.setText(
                        f'授权已于 {status.expires_text} 到期。')
                    self._lic_hint2_lbl.setText('把上面的机器码发给卖家换取新授权码，'
                                                '然后点右侧按钮输入。')
                else:
                    self._lic_state_lbl.setText('已激活')
                    color = Glass.SUCCESS
                    tail = f'（剩余 {d} 天）' if d is not None else ''
                    self._lic_hint_lbl.setText(f'有效期至 {status.expires_text}{tail}')
                    self._lic_hint2_lbl.setText('换电脑后需要重新激活：把新电脑的机器码发给卖家。')
            elif getattr(status, 'trial', False):
                self._lic_state_lbl.setText('试用中')
                color = Glass.WARNING
                left = status.trial_days_left
                left_txt = f'剩余约 {left:.1f} 天' if left is not None else ''
                self._lic_hint_lbl.setText(f'{left_txt}，功能不受限。')
                # 这句是**转化关键**：明确告诉他下一步做什么
                self._lic_hint2_lbl.setText('想长期使用：把上面的机器码发给卖家，'
                                            '拿到授权码后点右侧按钮输入即可。')
            else:
                self._lic_state_lbl.setText('未激活')
                color = Glass.DANGER
                self._lic_hint_lbl.setText('试用已结束。')
                self._lic_hint2_lbl.setText('把上面的机器码发给卖家换取授权码，'
                                            '然后点右侧按钮输入。')

            self._lic_state_lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_SM};font-weight:700;"
                f"color:{color};background:transparent;")
        except Exception as e:
            print(f'[授权卡] 刷新失败: {type(e).__name__}: {e}')

    @ui_slot
    def _on_copy_machine_id(self):
        """复制机器码。没有就现场采集一次。

        v4.9.3(P1): 采集放到后台线程(L.machine_id() 实测冷启动 1.5s、最坏 30s),
        完成后经信号回主线程写剪贴板。期间按钮禁用 + 文案「读取中…」, 避免
        点下去界面卡死。
        """
        if getattr(self, '_lic_copying', False):
            return
        mid = self._lic_machine_id
        if mid:
            self._write_clipboard(mid)
            return
        self._lic_copying = True
        try:
            self._lic_copy_btn.setEnabled(False)
            self._lic_copy_btn.setText('读取中…')
        except Exception:
            pass
        try:
            threading.Thread(target=self._collect_machine_id_bg,
                             daemon=True).start()
        except Exception as e:
            # 线程都起不来(极端环境)就原地兜底, 不卡死按钮
            self._lic_copying = False
            try:
                self._lic_copy_btn.setEnabled(True)
                self._lic_copy_btn.setText('复制机器码')
            except Exception:
                pass
            self._lic_hint2_lbl.setText(f'读取机器码失败：{type(e).__name__}: {e}')

    def _collect_machine_id_bg(self):
        """后台线程: 采集机器码, 结果经信号送回主线程(不在此碰任何 UI)。"""
        try:
            from core import licensing as L
            mid = L.machine_id()
            if not mid:
                raise ValueError('machine_id 返回空')
            self.sig_machine_id_ready.emit(mid)
        except Exception as e:
            self.sig_machine_id_failed.emit(f'{type(e).__name__}: {e}')

    @ui_slot
    def _on_machine_id_ready(self, mid):
        """后台采集成功 → 主线程缓存 + 写剪贴板 + 恢复按钮。"""
        self._lic_machine_id = mid
        try:
            self._lic_mid_lbl.setText(f'机器码  {_group_code(mid)}')
        except Exception:
            pass
        self._write_clipboard(mid)

    @ui_slot
    def _on_machine_id_failed(self, err):
        """后台采集失败 → 主线程提示 + 恢复按钮。"""
        try:
            self._lic_hint2_lbl.setText(f'读取机器码失败：{err}')
        except Exception:
            pass
        self._restore_copy_btn()

    def _write_clipboard(self, mid):
        """主线程写剪贴板(纯码, 无连字符), 并恢复按钮状态。"""
        try:
            cb = QApplication.clipboard()
            if cb is None:
                self._lic_hint2_lbl.setText('无法访问剪贴板，请手动选中机器码后按 Ctrl+C。')
                return
            cb.setText(mid)
            self._lic_hint2_lbl.setText('机器码已复制 ✓ 发给卖家，他会回你一串授权码。')
        finally:
            self._restore_copy_btn()

    def _restore_copy_btn(self):
        self._lic_copying = False
        try:
            self._lic_copy_btn.setEnabled(True)
            self._lic_copy_btn.setText('复制机器码')
        except Exception:
            pass


    # ==================== v4.6.3: 清理缓存 ====================
    def _add_clean_cache_card(self, lay):
        """设置页的「清理缓存」卡片。

        用户要求能清理: 所有日志 / 对局记录 / 旧英雄、海克斯、装备图标。

        设计取舍:
        - **显示实际占用**再让用户勾 —— 否则"清理"是个盲操作, 用户不知道
          清了能腾出多少、也不知道有没有东西可清。
        - 默认**不勾任何项**: 清理是破坏性操作, 默认全选会导致手快点一下就
          把日志清空, 而日志正是排查问题的依据。让用户显式选择。
        - 英雄头像与英雄图标在同一个目录(icon_cache), 无法只清其一, 故合并
          为一项并在文案里说明。
        """
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)

        title = QLabel('清理缓存')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;"
            f"color:{Glass.TEXT_SECONDARY};background:transparent;")
        cl.addWidget(title)

        desc = QLabel(
            '游戏版本更新后，旧图标可能已失效（官方改过立绘/强化图标）。'
            '这里可以手动清理，清掉的内容下次连上客户端会自动重新下载。\n'
            '日志请谨慎清理：它是排查问题时唯一的依据。')
        desc.setWordWrap(True)
        desc.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        cl.addWidget(desc)

        # 勾选项: (key, 文案)
        self._clean_checks = {}
        _ITEMS = (
            ('icons', '英雄 / 装备 / 技能 / 海克斯图标'),
            ('avatars', '账号头像（与英雄图标同目录，一并清理）'),
            ('logs', '所有日志（含历史日志与崩溃记录）'),
            ('data', '英雄数据缓存（下次自动重拉）'),
            # v4.6.3: 对局记录本地不落盘(每次从 LCU 实时拉), 这里点一下只是
            # 清空界面显示。仍然列出来 —— 用户明确要求能清"对局记录",
            # 界面上找不到会以为漏做了; 点了会明确告知"无本地缓存文件"。
            ('recent', '对局记录（本地无缓存，仅清空界面显示）'),
        )
        for key, text in _ITEMS:
            row = HoverRow()
            row.setStyleSheet('background:transparent;')
            rl = QHBoxLayout(row)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(8)
            cb = QCheckBox()
            cb.setStyleSheet(
                "QCheckBox{background:transparent;}"
                "QCheckBox::indicator{width:14px;height:14px;border-radius:3px;"
                f"border:1px solid {Glass.BORDER_HOVER};background:rgba(0,0,0,0.04);}}"
                "QCheckBox::indicator:checked{"
                f"background:{Glass.PRIMARY};border-color:{Glass.PRIMARY};}}")
            cb.setCursor(Qt.PointingHandCursor)
            rl.addWidget(cb)
            lbl = QLabel(text)
            lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_SECONDARY};"
                f"background:transparent;")
            lbl.setCursor(Qt.PointingHandCursor)
            # v5.1(ultra): 与 _make_toggle_row 同款 —— QCheckBox 用 click() 也走
            # 原生动画/点击语义, 统一手感(此处是 QCheckBox 非 AnimatedToggle)。
            lbl.mousePressEvent = lambda e, b=cb: b.click()
            rl.addWidget(lbl)
            rl.addStretch()
            size_lbl = QLabel('')
            size_lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
                f"background:transparent;")
            rl.addWidget(size_lbl)
            cl.addWidget(row)
            self._clean_checks[key] = (cb, size_lbl)

        # 按钮行
        brow = QHBoxLayout()
        brow.setContentsMargins(0, 0, 0, 0)
        brow.setSpacing(10)
        self._clean_btn = QPushButton('清理所选')
        self._clean_btn.setCursor(Qt.PointingHandCursor)
        self._clean_btn.setFixedHeight(30)
        self._clean_btn.setStyleSheet(f"""
            QPushButton {{
                background: {Glass.DANGER_SUBTLE}; color: {Glass.DANGER};
                border: 1px solid {Glass.DANGER}; border-radius: {Glass.RADIUS_XS}px;
                font-size: {Glass.FONT_SIZE_SM}; font-weight: 600; padding: 0 14px;
            }}
            QPushButton:hover {{ background: rgba(248,113,113,0.20); color: white; }}
            QPushButton:disabled {{ color: {Glass.TEXT_MUTED};
                border-color: {Glass.BORDER_GLASS}; background: transparent; }}
        """)
        self._clean_btn.clicked.connect(self._on_clean_clicked)
        brow.addWidget(self._clean_btn)

        self._clean_refresh_btn = QPushButton('刷新占用')
        self._clean_refresh_btn.setCursor(Qt.PointingHandCursor)
        self._clean_refresh_btn.setFixedHeight(30)
        self._clean_refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {Glass.TEXT_MUTED};
                border: 1px solid {Glass.BORDER_GLASS}; border-radius: {Glass.RADIUS_XS}px;
                font-size: {Glass.FONT_SIZE_SM}; padding: 0 12px;
            }}
            QPushButton:hover {{ color: {Glass.TEXT_PRIMARY};
                border-color: {Glass.BORDER_HOVER}; }}
        """)
        self._clean_refresh_btn.clicked.connect(self.refresh_clean_sizes)
        brow.addWidget(self._clean_refresh_btn)
        brow.addStretch()
        cl.addLayout(brow)

        self._clean_state = QLabel('')
        self._clean_state.setWordWrap(True)
        self._clean_state.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        cl.addWidget(self._clean_state)

        lay.addWidget(card)

    def update_clean_sizes(self, items, keep_state=False):
        """由 main 回填各项占用。items: {key: (文件数, 字节数)}。

        v4.6.8 修 🔴: `keep_state=True` 时**不覆写**下方那行状态文案。
        原来的缺陷(实测复现): 清理完成后 main 先调 `on_clean_done()` 写上
        "✅ 已清理 N 个文件"，紧接着在**同一帧、同一调用栈**里调本方法回填占用，
        而本方法无条件 `setText('当前共 N 个文件、X')` —— 于是成功提示被顶掉,
        用户一个字都看不到; 失败提示同样被吞。清理功能的唯一反馈通道等于失效。
        修法: 清理路径传 keep_state=True(保留刚写的结果), 纯清点路径照旧覆盖。
        """
        try:
            total_n = total_sz = 0
            for key, (cb, lbl) in self._clean_checks.items():
                n, sz = items.get(key, (0, 0))
                total_n += n
                total_sz += sz
                if n:
                    lbl.setText(f'{n} 个 · {_fmt_size(sz)}')
                    lbl.setStyleSheet(
                        f"font-size:{Glass.FONT_SIZE_XS};"
                        f"color:{Glass.TEXT_SECONDARY};background:transparent;")
                else:
                    lbl.setText('空')
                    lbl.setStyleSheet(
                        f"font-size:{Glass.FONT_SIZE_XS};"
                        f"color:{Glass.TEXT_MUTED};background:transparent;")
            if keep_state:
                # 刚显示过"已清理…"的结果 → 保留它, 不拿占用数字盖掉
                return
            if total_n:
                self._clean_state.setText(
                    f'当前共 {total_n} 个文件、{_fmt_size(total_sz)}')
            else:
                self._clean_state.setText('当前没有可清理的内容')
        except Exception:
            pass

    def refresh_clean_sizes(self):
        """请求 main 重新清点占用(卡片每次显示时也会调)。"""
        try:
            self.clean_cache_signal.emit([])      # 空列表 = 只清点不清理
        except Exception:
            pass

    def _on_clean_clicked(self):
        try:
            picked = [k for k, (cb, _l) in self._clean_checks.items()
                      if cb.isChecked()]
        except Exception:
            picked = []
        if not picked:
            self._clean_state.setText('⚠️ 请先勾选要清理的项目')
            self._clean_state.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.WARNING};"
                f"background:transparent;")
            return
        # v4.4.x(P3): 日志是排查问题的唯一依据, 破坏性操作前弹一次确认
        if 'logs' in picked:
            ret = QMessageBox.question(
                self, '确认清理', '清理将删除所有日志（含历史日志与崩溃记录），且不可恢复。\n确定继续吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if ret != QMessageBox.Yes:
                return
        self._clean_state.setText('正在清理…')
        self._clean_state.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
            f"background:transparent;")
        # 交给 main 执行(它持有缓存实例与日志句柄), 清理完回填占用
        self.clean_cache_signal.emit(picked)

    def on_clean_done(self, removed, freed, notes):
        """main 清理完成后回调(主线程)。"""
        try:
            txt = f'✅ 已清理 {removed} 个文件，释放 {_fmt_size(freed)}'
            if notes:
                txt += f'（{"; ".join(notes[:3])}）'
            self._clean_state.setText(txt)
            self._clean_state.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.ACCENT};"
                f"background:transparent;")
            for cb, _l in self._clean_checks.values():
                cb.setChecked(False)
        except Exception:
            pass

    # v4.8.0: 录制/回放卡片(_add_lol_calibrate_card)、_rec_refresh、
    # set_recording_ui、_on_record_clicked、_on_record_clear 全部随录制功能删除。

    def _make_delay_card(self, label, toggle_key, delay_key, delay_def, dmin, dmax, desc):
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        # v4.4.x(P2): 开关行与 _make_toggle_row 完全同构(仅回调不同), 复用避免双份拷贝。
        # _toggle(key, checked, btn) 内部就是 _on_setting(key, checked), 签名正好匹配。
        row, _ = _make_toggle_row(label, toggle_key, self._settings,
                                  self._toggles, self._toggle)
        rl = row.layout()
        self._make_delay_ctrl(rl, delay_key, delay_def, dmin, dmax)
        rl.addSpacing(4)
        cl.addWidget(row)

        d = QLabel(f'  {desc}')
        d.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        cl.addWidget(d)
        return card

    def _make_delay_ctrl(self, parent, key, default, mn, mx):
        btn_style = f"""
            QPushButton{{
                background: {Glass.PRIMARY_SUBTLE};
                color: {Glass.TEXT_PRIMARY};
                border: none;
                border-radius: {Glass.RADIUS_XS};
                font-size: {Glass.FONT_SIZE_SM};
                font-weight: bold;
                padding: 0;
                min-width: 28px;
                min-height: 22px;
            }}
            QPushButton:hover{{
                background: {Glass.PRIMARY_20};
            }}
            QPushButton:pressed{{
                background: {Glass.PRIMARY_30};
                padding-top: 1px;
            }}
        """

        mb = QPushButton('−')
        mb.setFixedSize(28, 22)
        mb.setCursor(Qt.PointingHandCursor)
        mb.setStyleSheet(btn_style)

        val = QLabel(str(self._settings.get(key, default)))
        val.setFixedWidth(28)
        val.setAlignment(Qt.AlignCenter)
        val.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};font-weight:bold;color:{Glass.PRIMARY};background:transparent;")

        pb = QPushButton('+')
        pb.setFixedSize(28, 22)
        pb.setCursor(Qt.PointingHandCursor)
        pb.setStyleSheet(btn_style)

        unit = QLabel('秒')
        unit.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")

        cur = [self._settings.get(key, default)]

        def update(d):
            v = max(mn, min(mx, cur[0] + d))
            cur[0] = v
            val.setText(str(v))
            self._on_setting(key, v)

        mb.clicked.connect(lambda: update(-1))
        pb.clicked.connect(lambda: update(1))
        parent.addWidget(mb)
        parent.addWidget(val)
        parent.addWidget(pb)
        parent.addWidget(unit)

    # ==================== 英雄页 ====================
    def _make_hero_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        self._picker_container = QWidget()
        self._picker_container.setStyleSheet("background:transparent;")
        self._picker_lay = QVBoxLayout(self._picker_container)
        self._picker_lay.setContentsMargins(0, 0, 0, 0)

        # v4.4.1(UI-B2): 统一空态工厂 + 三点呼吸(加载态要给"还在干活"的信号)
        self._picker_placeholder = make_empty_state(
            '⚔', '正在加载英雄数据', '首次启动需要从客户端读取英雄列表',
            min_height=120, animated=True)
        self._picker_placeholder.dots.start()
        self._picker_lay.addWidget(self._picker_placeholder)

        lay.addWidget(self._picker_container, 1)
        return page

    # ==================== 战绩页 ====================
    def _make_recent_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(16, 14, 12, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        head.setContentsMargins(2, 0, 6, 0)
        title = QLabel('近期战绩')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:700;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        self._recent_sum_lbl = QLabel('')
        self._recent_sum_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};color:{Glass.TEXT_MUTED};"
            f"background:transparent;")
        # v4.10.x: 胜率 pill —— 与刷新按钮同排, 数据由 _set_recent_sum 统一写
        self._recent_rate_lbl = QLabel('')
        self._recent_rate_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};font-weight:700;"
            f"color:{Glass.PRIMARY};background:{Glass.PRIMARY_SUBTLE};"
            f"border-radius:6px;padding:3px 8px;")
        self._recent_rate_lbl.setAlignment(Qt.AlignCenter)
        self._recent_rate_lbl.setVisible(False)
        # v4.9.x: 手动刷新按钮 —— 复用 main 的 _force_profile_refresh 通道,
        # 置位后下一轮探活(≤3s)立即重拉战绩, 不等 30s 周期。
        self._recent_refresh_btn = QPushButton('刷新')
        self._recent_refresh_btn.setFixedHeight(26)
        self._recent_refresh_btn.setCursor(Qt.PointingHandCursor)
        self._recent_refresh_btn.setStyleSheet(f"""
            QPushButton {{ background: {Glass.PRIMARY_06}; color: {Glass.TEXT_SECONDARY};
                border: none; border-radius: 6px; font-size: {Glass.FONT_SIZE_XS};
                padding: 0 10px; }}
            QPushButton:hover {{ background: {Glass.PRIMARY_18}; color: {Glass.TEXT_PRIMARY}; }}
            QPushButton:pressed {{ background: {Glass.PRIMARY_30}; }}
            QPushButton:disabled {{ background: rgba(0,0,0,0.05); color: {Glass.TEXT_MUTED}; }}
        """)
        self._recent_refresh_btn.clicked.connect(self._on_recent_refresh)
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self._recent_sum_lbl)
        head.addWidget(self._recent_rate_lbl)
        head.addSpacing(8)
        head.addWidget(self._recent_refresh_btn)
        lay.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + Glass.SCROLL_QSS)
        holder = QWidget()
        holder.setStyleSheet('background:transparent;')
        self._recent_lay = QVBoxLayout(holder)
        self._recent_lay.setContentsMargins(0, 0, 6, 0)
        self._recent_lay.setSpacing(6)

        # v4.4.1(UI-B2): 统一空态工厂(原来把副提示用 \n 塞进同一个标签)
        # v4.10.x: 改为 animated=True —— 空态要表达三态(未连接/拉取中/无数据),
        # 拉取中时启动呼吸点, 消除"刚连上正在拉, 却显示暂无记录"的误报。
        self._recent_empty = make_empty_state(
            '🗂', '暂无对局记录', '连接客户端后自动加载最近 10 场',
            min_height=140, animated=True)
        self._recent_empty._state = 'empty'
        self._recent_lay.addWidget(self._recent_empty)
        self._recent_lay.addStretch()
        scroll.setWidget(holder)
        lay.addWidget(scroll, 1)

        # 图标异步轮询(仅战绩页可见且有未加载图标时运行)
        self._recent_icon_timer = QTimer(self)
        self._recent_icon_timer.setInterval(900)
        self._recent_icon_timer.timeout.connect(self._tick_recent_icons)
        return page

    def _set_recent_empty(self, state, title=None, hint=None):
        """战绩页空态三态切换(v4.10.x)。

        state: 'loading' 正在拉取(呼吸点) / 'empty' 真无数据 / 'offline' 未连接。
        未连接态不复用"正在加载"的呼吸点, 避免"客户端没开"看起来像"软件在忙"。
        """
        try:
            w = self._recent_empty
            if w is None:
                return
            prev = getattr(w, '_state', None)
            if prev == state and title is None and hint is None:
                return
            w._state = state
            if title is None:
                title = {'loading': '正在读取对局记录…',
                         'offline': '未连接到客户端',
                         'empty': '暂无对局记录'}.get(state, '暂无对局记录')
            if hint is None:
                hint = {'loading': '连接后自动加载最近 10 场，请稍候',
                        'offline': '请先打开英雄联盟客户端并登录',
                        'empty': '连接客户端后自动加载最近 10 场'}.get(state, '')
            if w.title_label is not None:
                w.title_label.setText(title)
            if w.hint_label is not None:
                w.hint_label.setText(hint)
            if state == 'loading' and w.dots is not None:
                w.dots.start()
            elif w.dots is not None:
                w.dots.stop()
        except Exception as e:
            print(f'[战绩] 空态切换失败: {type(e).__name__}: {e}')

    def set_recent_icon_cb(self, icon_cb, download_cb=None,
                           item_icon_cb=None, item_dl_cb=None,
                           spell_icon_cb=None, spell_dl_cb=None,
                           aug_icon_cb=None, aug_dl_cb=None):
        """main 注入图标回调(与英雄选择器同源):
        icon_cb(cid)->bytes|None(纯内存缓存, 不阻塞 UI); download_cb(cid)->触发后台下载。
        item_* 是装备图标(详情弹窗装备栏用; main 侧独立缓存 + 独立线程池)。
        spell_* / aug_* 是 v4.3.2 新增的召唤师技能与海克斯强化图标(同样独立缓存,
        且都从 LCU 本地资源取, 不走外网)。
        """
        self._recent_icon_cb = icon_cb
        self._recent_dl_cb = download_cb
        self._item_icon_cb = item_icon_cb
        self._item_dl_cb = item_dl_cb
        self._spell_icon_cb = spell_icon_cb
        self._spell_dl_cb = spell_dl_cb
        self._aug_icon_cb = aug_icon_cb
        self._aug_dl_cb = aug_dl_cb

    def set_recent_detail_cb(self, fetch_cb):
        """main 注入"拉单局详情"回调: fetch_cb(game_id, callback(detail, err))。"""
        self._recent_detail_cb = fetch_cb

    def set_recent_refresh_cb(self, refresh_cb):
        """main 注入"手动刷新战绩"回调: refresh_cb() → 置 _force_profile。"""
        self._recent_refresh_cb = refresh_cb

    @ui_slot
    def _on_recent_refresh(self):
        """点击「刷新」→ 调 main 的强制刷新通道, 下一轮探活立即重拉战绩。

        v4.10.x: 增加进行中反馈 —— 点击后按钮短暂禁用并显示"刷新中", 下一轮
        探活结果到达(update_recent 或 update_account_info)时恢复。用户之前看到
        的只有一句 toast, 无法确认刷新真的在执行。
        """
        cb = self._recent_refresh_cb
        if cb is None:
            self._show_toast('刷新功能未就绪', kind='error')
            return
        try:
            cb()
            self._refresh_pending = True
            self._apply_refresh_state()
            self._show_toast('已触发刷新，即将更新')
            # 兜底: 若回调通道失效(探活停摆), 3.5s 后恢复按钮, 避免永久"刷新中"
            QTimer.singleShot(3500, self._clear_refresh_pending)
        except Exception as e:
            print(f'[战绩] 手动刷新失败: {type(e).__name__}: {e}')
            self._show_toast('刷新失败', kind='error')

    def _apply_refresh_state(self):
        """按 _refresh_pending 渲染刷新按钮的禁用态(有 pending 时禁用)。"""
        btn = self._recent_refresh_btn
        if btn is None:
            return
        pending = bool(getattr(self, '_refresh_pending', False))
        btn.setEnabled(not pending)
        btn.setText('刷新中…' if pending else '刷新')

    def _clear_refresh_pending(self):
        """刷新兜底复位: 探活结果正常到达时也会调, 幂等。"""
        try:
            if getattr(self, '_refresh_pending', False):
                self._refresh_pending = False
                self._apply_refresh_state()
        except Exception as e:
            print(f'[战绩] 刷新状态复位失败: {type(e).__name__}: {e}')

    @ui_slot
    def _open_recent_detail(self, game):
        """点击战绩行 → 弹出对局详情。摘要立即渲染, 10 人明细由 main 异步回填。"""
        if not isinstance(game, dict):
            return
        try:
            from .recent_detail import RecentDetailDialog
        except Exception as e:
            print(f'[战绩] 详情模块加载失败: {type(e).__name__}: {e}')
            self._show_toast('详情功能不可用', kind='error')
            return
        try:
            # 同时只允许一个详情窗: 重开先拆掉旧的
            # v4.4.0 修复(P1-19): 原来只 old.close() —— 而详情窗是以
            # `parent=self` 构造的(见下方构造函数), close() 只是隐藏, 对象
            # 仍然挂在主窗口的 Qt 父子树上永不释放: 每次开一次详情就泄漏
            # 3×10 个 _PlayerRow 与数百个 _IconBox。改为 close() 后就地
            # 脱离父子树并 deleteLater, 真正回收。
            # 注意必须先 close() 再 setParent(None): setParent/deleteLater
            # 不会派发 QCloseEvent, 少了 close() 的话详情窗的 `_closed` 活
            # 体标志仍是 False、它自己的 _icon_timer 也不会停, main 侧异步
            # 回填的 set_detail 可能打到半死的窗口上。
            old = self._detail_dlg
            if old is not None:
                try:
                    old.close()
                except Exception:
                    pass
                try:
                    old.setParent(None)
                    old.deleteLater()
                except Exception as e:
                    print(f'[战绩] 旧详情窗释放失败: {type(e).__name__}: {e}')
            self._detail_dlg = None
            # v4.10.x: 把当前列表快照与下标传给详情窗, 支持 ◀/▶ 上下局导航。
            # 弹窗切局时经 on_nav 回调调本方法(传同一列表的相邻项)。
            nav_games = [dict(r._game) for r in self._recent_rows]
            nav_index = 0
            gid = game.get('game_id')
            for i, g in enumerate(nav_games):
                if g.get('game_id') == gid:
                    nav_index = i
                    break

            def _nav_to(idx):
                if 0 <= idx < len(nav_games):
                    self._open_recent_detail(nav_games[idx])

            dlg = RecentDetailDialog(
                game,
                champ_icon_cb=self._recent_icon_cb,
                champ_dl_cb=self._recent_dl_cb,
                item_icon_cb=self._item_icon_cb,
                item_dl_cb=self._item_dl_cb,
                spell_icon_cb=self._spell_icon_cb,
                spell_dl_cb=self._spell_dl_cb,
                aug_icon_cb=self._aug_icon_cb,
                aug_dl_cb=self._aug_dl_cb,
                fetch_cb=self._recent_detail_cb,
                parent=self,
                games=nav_games, index=nav_index, on_nav=_nav_to)
            self._detail_dlg = dlg
            # 先 show 再发起请求: 回调可能比首帧还快, 先显示才不会丢回填
            dlg.show()
            try:
                dlg.move(self.frameGeometry().center() - dlg.rect().center())
            except Exception:
                pass
            dlg.raise_()
            dlg.activateWindow()
            fetch = self._recent_detail_cb
            if fetch is not None:
                fetch(game.get('game_id'), dlg.set_detail)
            else:
                dlg.set_detail(None, '详情功能未就绪')
        except Exception as e:
            print(f'[战绩] 打开详情失败: {type(e).__name__}: {e}')
            self._show_toast('详情打开失败', kind='error')

    def _show_toast(self, text, duration=2000, kind='info'):
        if self._toast is None:
            self._toast = Toast(self)
        self._toast.show_toast(text, duration, kind)

    def update_recent(self, games):
        """由账号卡刷新(每 3s)驱动。games 来自 main._parse_recent()['games']。

        以 gameId 元组做签名: 内容没变直接返回, 避免每 3s 重建 10 行控件导致闪烁。
        """
        try:
            _games_none = games is None
            games = list(games or [])
        except Exception:
            _games_none = True
            games = []
        # v4.3.10: 签名 = (game_id, 英雄名)。原来只比 game_id, 但英雄名是 main 侧
        # 探活时从 _champ_data 补上去的 —— 若第一轮探活在英雄数据就绪前完成, 名字
        # 会是占位符 "英雄#NNN", 之后名字补上了签名却没变 → 战绩页长期显示占位符。
        # main 侧换号时会把 _recent_sig 置 None, None != 任何 tuple, 仍然生效。
        sig = tuple((g.get('game_id'), (g.get('champion_name') or '').strip())
                    for g in games)
        if sig == self._recent_sig:
            return
        # v4.10.x(P0): EOG 即时战绩的行保留合并。match-history 服务端入库有
        # 1~3 分钟延迟 —— 刚打完的局由 EOG 秒出并 prepend 到列表顶部, 但随后
        # 每 3s 一轮的探活带回来的 games 里还没有它, 全量重建会把它删掉,
        # 表现成"秒出 → 3 秒后消失 → 1~3 分钟后才回来"。这里把"本地有 EOG 行、
        # 新数据没有、且未被换号/手动刷新清场"的局合并进新数据顶部, 保留到正式
        # 数据带回同 game_id 为止(届时以 match-history 的正式数据为准覆盖)。
        _new_ids = {g.get('game_id') for g in games}
        _eog_keep = [r._game for r in self._recent_rows
                     if r._from_eog and r._game.get('game_id') not in _new_ids]
        # 合并后统一截到 10 场: EOG 局是最新一局, 优先保留; 挤掉的应是最老的
        # match-history 行, 而不是刚打完的 EOG 局(满 10 场时尤其如此)。
        merged = (_eog_keep + games)[:10]

        self._recent_sig = sig
        _render = merged

        # v4.10.x: 空态三态 —— games 为 None(尚未拉到)显示"拉取中", 空列表显示"暂无",
        # 未连接由 _do_conn 置 offline; 有数据/合并后非空则收起空态。
        if _render:
            if self._recent_empty is not None:
                self._recent_empty.setVisible(False)
        else:
            if self._recent_empty is not None:
                self._recent_empty.setVisible(True)
                if _games_none:
                    self._set_recent_empty('loading')
                else:
                    self._set_recent_empty('empty')
            self._set_recent_sum(0, 0, 0)

        for w in self._recent_rows:
            try:
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        self._recent_rows = []

        if not _render:
            return

        if self._recent_empty is not None:
            self._recent_empty.setVisible(False)

        wins = losses = 0
        for g in _render:
            try:
                row = _MatchRow(g)
                row._from_eog = bool(g.get('_from_eog'))
            except Exception as e:
                print(f'[战绩] 行构建失败: {type(e).__name__}: {e}')
                continue
            # v4.3.0: 整行可点击 → 对局详情
            row.clicked.connect(self._open_recent_detail)
            # 插到末尾 stretch 之前
            self._recent_lay.insertWidget(self._recent_lay.count() - 1, row)
            self._recent_rows.append(row)
            if g.get('win') is True:
                wins += 1
            elif g.get('win') is False:
                losses += 1
        self._set_recent_sum(len(self._recent_rows), wins, losses)

        self._tick_recent_icons()

    def _set_recent_sum(self, total, wins, losses):
        """统一写战绩页标题统计(近 N 场 · X胜Y负 + 胜率 pill)。

        v4.10.x: 原先 update_recent / prepend_recent_game 各写一遍文案, 且没
        胜率。收口到单一方法, 无数据时清空, 有数据时胜率 = wins/total。
        """
        try:
            if self._recent_sum_lbl is None:
                return
            if total <= 0:
                self._recent_sum_lbl.setText('')
                if self._recent_rate_lbl is not None:
                    self._recent_rate_lbl.setVisible(False)
                    self._recent_rate_lbl.setText('')
                return
            self._recent_sum_lbl.setText(f'近 {total} 场 · {wins}胜{losses}负')
            if self._recent_rate_lbl is not None:
                rate = round(wins * 100 / max(1, total))
                self._recent_rate_lbl.setText(f'胜率 {rate}%')
                self._recent_rate_lbl.setVisible(True)
        except Exception as e:
            print(f'[战绩] 标题统计写入失败: {type(e).__name__}: {e}')

    def prepend_recent_game(self, game):
        """v4.9.x: EOG 即时战绩回填 —— 把刚结束的一局插到列表最前。

        与 update_recent(全量重建)不同: 这里只插一行, 不重建整页, 避免闪烁;
        随后 3s 一轮的探活会把 match-history 的正式数据带过来并全量刷新,
        二者签名去重, 不会重复。game 结构与 _MatchRow 消费的 dict 一致。
        """
        try:
            if not isinstance(game, dict) or not game.get('game_id'):
                return
            gid = game.get('game_id')
            # 已有同局(探活已带来)就不重复插
            if any(r._game.get('game_id') == gid for r in self._recent_rows):
                return
            if self._recent_empty is not None:
                self._recent_empty.setVisible(False)
            try:
                row = _MatchRow(game)
            except Exception as e:
                print(f'[战绩] EOG 行构建失败: {type(e).__name__}: {e}')
                return
            # v4.10.x(P0): 标记 EOG 来源, 供 update_recent 全量重建时保留合并
            game['_from_eog'] = True
            row._from_eog = True
            row.clicked.connect(self._open_recent_detail)
            # 插到最前(布局 index 0), 使"最新一局"置顶
            self._recent_lay.insertWidget(0, row)
            self._recent_rows.insert(0, row)
            # 更新签名: 与 update_recent 同一签名规则, 防后续全量刷新误判。
            # v4.10.x(P0): 原代码这里 `for g in self._recent_rows` 而 _recent_rows
            # 存的是 _MatchRow 对象(不是 dict), g.get() 必抛 AttributeError 被外层
            # 吞掉 → 签名停留旧值, EOG 插入后签名从未反映真实列表。改为 r._game。
            self._recent_sig = tuple(
                (r._game.get('game_id'),
                 (r._game.get('champion_name') or '').strip())
                for r in self._recent_rows)
            wins = sum(1 for r in self._recent_rows if r._game.get('win') is True)
            losses = sum(1 for r in self._recent_rows if r._game.get('win') is False)
            self._set_recent_sum(len(self._recent_rows), wins, losses)
            self._tick_recent_icons()
        except Exception as e:
            print(f'[战绩] EOG 回填异常: {type(e).__name__}: {e}')

    def _tick_recent_icons(self, rearm=False):
        """补图标: 有缓存直接贴, 没有则请求下载并保持轮询直到全部就位或超出时间窗。

        rearm=True(用户切回战绩页时)给未加载的行重新开一个时间窗, 让慢速/失败过的
        图标再有一次机会。
        """
        if not self._recent_rows:
            return
        now = _time.monotonic()
        pending = 0
        for row in self._recent_rows:
            if row.icon_loaded:
                continue
            if rearm and row.icon_deadline < now:
                row.icon_deadline = now + row.ICON_RETRY_WINDOW_S
            if now > row.icon_deadline:
                # 超窗放弃, 复位下载标记 —— 下次 rearm(切回战绩页)允许再请求一次
                row._dl_requested = False
                continue
            cid = row.champion_id
            if cid <= 0:
                continue
            data = None
            if self._recent_icon_cb is not None:
                try:
                    data = self._recent_icon_cb(cid)
                except Exception:
                    data = None
            if data and row.set_icon(data):
                continue
            pending += 1
            # v4.4.x(P2): 每个 cid 只触发一次下载, 避免每 900ms 重复打网络
            if self._recent_dl_cb is not None and not row._dl_requested:
                row._dl_requested = True
                try:
                    self._recent_dl_cb(cid)
                except Exception:
                    row._dl_requested = False
        timer = self._recent_icon_timer
        if timer is None:
            return
        if pending and not timer.isActive():
            timer.start()
        elif not pending and timer.isActive():
            timer.stop()

    # ==================== 日志页 ====================
    def _make_log_page(self):
        page = QWidget()
        page.setStyleSheet("background:transparent;")
        lay = QVBoxLayout(page)
        lay.setContentsMargins(20, 16, 16, 16)
        lay.setSpacing(10)

        title = QLabel('运行日志')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:700;color:{Glass.TEXT_PRIMARY};background:transparent;")
        lay.addWidget(title)

        self._log_text = QTextEdit()
        self._log_text.setReadOnly(True)
        self._log_text.setStyleSheet(Glass.LOG_STYLE)
        lay.addWidget(self._log_text, 1)

        # v4.4.x(P3): 该按钮只清空 QTextEdit 的界面显示, 不删磁盘日志文件, 文案如实说明
        clear_btn = QPushButton('清空界面显示')
        clear_btn.setFixedHeight(32)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(f"""
            QPushButton {{ background: {Glass.PRIMARY_06}; color: {Glass.TEXT_SECONDARY}; border: none; border-radius: 6px; font-size: {Glass.FONT_SIZE_SM}; }}
            QPushButton:hover {{ background: {Glass.PRIMARY_18}; color: {Glass.TEXT_PRIMARY}; }}
        """)
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        lay.addWidget(clear_btn)

        return page

    @ui_slot
    def _on_start(self):
        if self._running:
            self.stop_script_signal.emit()
        else:
            self.start_script_signal.emit()

    @ui_slot
    def _on_launch_lol(self):
        """v4.4.5: 首页「启动 LOL」→ 通知 main 执行直启流程。"""
        self.launch_lol_signal.emit()

    @ui_slot
    def _on_stop_all(self):
        """v4.4.16: 首页「全部停止」→ 通知 main 关 LOL + 停脚本。"""
        self.stop_all_signal.emit()

    def _refresh_launch_hint(self):
        """刷新首页按钮上方的小字: 这个按钮做什么。

        v4.8.0: 固定文案。原先按"有没有录制步骤"在两种说法间切换
        (已录制N步: 启动并自动点击 / 未录制: 仅启动不自动点击), 录制回放删除后
        只有一种行为了, 再保留分支就是给后来人留个不存在的状态。
        """
        try:
            self._launch_lol_hint.setText('仅打开登录器，不自动登录')
            self._launch_lol_hint.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        except Exception:
            pass

    def _toggle(self, key, checked, btn):
        self._on_setting(key, checked)

    @ui_slot
    def _on_setting(self, key, value):
        # v34: 打开"自动选英雄" → 通知 main.py 去做提权流程(main 才有 _core / _start / _stop)。
        # 仅 UI 存盘 + emit, 避免 UI 直接 os._exit 错过 main 的优雅停脚本。
        if key == 'auto_select_champion' and value:
            self._settings[key] = True
            self._save_settings()
            self.settings_changed_signal.emit(self._settings)
            self.auto_select_champion_request_signal.emit()
            return

        self._settings[key] = value
        for k, b in self._toggles.items():
            if k == key and b.isChecked() != value:
                blocked = b.blockSignals(True)
                try:
                    b.setChecked(value)
                finally:
                    b.blockSignals(blocked)
        self._save_settings()
        self.settings_changed_signal.emit(self._settings)

    # ==================== 外部接口 ====================
    # v4.6.11 删除 update_connection_status(): 全项目零调用(仅注释里提到"不再
    # 调用它"), 而它做的事与下面的 apply_connection_ui 完全一样(同一个信号)。
    # 留着会让维护者以为有两个入口、且不知道该改哪个。

    def apply_connection_ui(self, connected: bool, text: str = '') -> None:
        """v4.4.0 修复(P1-24): 一次性更新连接状态文本 + 状态点颜色。

        原 main.py 是绕过公开接口、直接伸进 UI 私有成员写三处
        (`self._main._conn_label.setText` / `_update_dot` / `_summoner_label.setText`,
        见 main.py:1692-1694 与 :1714-1716), 接口一改就崩。这里提供等价公开入口,
        内部复用 _do_conn 的既有逻辑(仍然通过信号排队, 保持线程纪律不变)。
        注: 其中的 `_summoner_label` 已于 v4.4.0(P2) 随空 label 一并删除。
        """
        try:
            self.sig_update_connection.emit(bool(connected), text or '')
        except Exception as e:
            print(f'[ui_slot] MainWindow.apply_connection_ui 异常已被拦下: '
                  f'{type(e).__name__}: {e}')

    def mark_recent_dirty(self) -> None:
        """v4.4.0 修复(P1-24): 让最近对局列表下次刷新时强制重建。

        等价于 `self._recent_sig = None`(main.py:1617 原来就是这么干的)。
        None != 任何 tuple, 因此下一次 update_recent() 必然重绘 —— 换号/换区后
        英雄名与 gameId 一起变, 但旧签名可能恰好相同(见 update_recent 注释),
        这个显式入口比让外部直接改私有属性安全。

        v4.10.x(P0): 同时清掉所有 EOG 来源标记 —— 换号/手动清空战绩后, 旧账号
        的 EOG 行绝不能并入新账号列表(update_recent 的合并逻辑依赖 _from_eog)。
        """
        try:
            self._recent_sig = None
            for r in self._recent_rows:
                try:
                    r._from_eog = False
                except Exception:
                    pass
        except Exception as e:
            print(f'[ui_slot] MainWindow.mark_recent_dirty 异常已被拦下: '
                  f'{type(e).__name__}: {e}')

    def persist_setting(self, key: str, value) -> None:
        """v4.4.0 修复(P1-24): 写值 → 落盘 → 同步界面开关状态。

        main.py:2148-2149 原来是 `_main._settings['auto_select_champion'] = False`
        紧跟 `_main._save_settings()`, 两次伸进私有成员。这里合并成一个公开方法:
        先写 self._settings[key], 再复用 _save_settings()(已改原子写),
        并同步 sync_setting 的界面状态(toggle 按钮)。

        v5.0.0(#3 P3): 补发 settings_changed_signal —— 与 _on_setting 同口径。
        否则调用方(如 main._revert_auto_select_champion 的 UAC 取消回退)只改了
        UI/磁盘, core 侧 _settings 仍是旧值, 脚本运行中会出现 UI 与行为不一致。
        """
        try:
            self._settings[key] = value
            self._save_settings()
            self.sync_setting(key, value)
            self.settings_changed_signal.emit(self._settings)
        except Exception as e:
            print(f'[ui_slot] MainWindow.persist_setting 异常已被拦下: '
                  f'{type(e).__name__}: {e}')

    def append_log(self, text):
        self.sig_append_log.emit(text)

    @ui_slot
    def update_phase(self, phase):
        self.sig_update_phase.emit(phase)

    @ui_slot
    def set_script_running(self, running):
        self.sig_set_script_running.emit(running)

    def get_settings(self):
        return self._settings.copy()

    def show_first_launch_agreement(self):
        """首次启动强制阅读须知：未确认前弹液态玻璃弹窗，30s 倒计时后点「我已知晓」。

        v4.1.55 增。主窗 show 后由 main.py 调用；已确认过(agreement_accepted=True)
        则静默跳过。弹窗确认后写回 settings 持久化，重启不再弹。
        """
        if self._settings.get('agreement_accepted'):
            return
        from .agreement_dialog import AgreementDialog
        # 保持引用，避免局部变量被 GC 回收导致弹窗提前销毁
        self._agreement_dlg = AgreementDialog(self)
        self._agreement_dlg.accepted.connect(self._on_agreement_accepted)
        self._agreement_dlg.show()

    @ui_slot
    def _on_agreement_accepted(self):
        self._settings['agreement_accepted'] = True
        self._save_settings()
        # v4.1.60: 通知 main 补查提权(覆盖"老版本已开 auto_select 但
        # agreement_accepted 缺失"的升级场景)
        self.agreement_confirmed_signal.emit()

    @ui_slot
    def sync_setting(self, key, value):
        # v5.1(ultra H6): 用 blockSignals 包住 setChecked —— 否则按钮 toggled 信号
        # 会再走一遍 _on_setting → _save_settings + settings_changed emit, 与调用方
        # (persist_setting) 自己那次落盘/广播叠加成双写。overlay_window 已是此写法。
        if key in self._toggles:
            btn = self._toggles[key]
            if btn.isChecked() != value:
                blocked = btn.blockSignals(True)
                try:
                    btn.setChecked(value)
                finally:
                    btn.blockSignals(blocked)
        self._settings[key] = value

    def init_champion_picker(self, champ_data, preferred, icon_cb, download_cb=None, **kw):
        if self._champion_picker is None:
            from .champion_picker import ChampionPickerWidget
            self._champion_picker = ChampionPickerWidget(
                champ_data, preferred, icon_cb, self, download_cb=download_cb, **kw
            )
            self._champion_picker.champions_selected.connect(self._on_picker_saved)
            if self._picker_placeholder:
                # v4.4.x(P3): 占位三点动画在 hide 前显式停掉, 避免残留定时器空转
                dots = getattr(self._picker_placeholder, 'dots', None)
                if dots is not None:
                    try:
                        dots.stop()
                    except Exception:
                        pass
                self._picker_lay.removeWidget(self._picker_placeholder)
                self._picker_placeholder.hide()
                self._picker_placeholder = None
            self._picker_lay.addWidget(self._champion_picker, 1)
        else:
            self._champion_picker._data = champ_data
            self._champion_picker._preferred = list(preferred)
            self._champion_picker._refresh_sel_list()

    @ui_slot
    def _on_picker_saved(self, ids):
        self._settings['preferred_champions'] = ids
        self._save_settings()
        self.settings_changed_signal.emit(self._settings)
        if self._toast is None:
            self._toast = Toast(self)
        self._toast.show_toast(f'已保存 {len(ids)} 个偏好英雄', kind='success')

    # ==================== v37 账号卡更新 ====================
    def _mk_mast_chip(self):
        """熟练度统计块（液态玻璃 ultra 版）：英雄头像 + 成就等级。

        接口与 rank chip 一致（set_value / set_default），_prefix 为 None
        （update_account_info 已不再触碰 _prefix）。
        """
        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame{{background:{Glass.BG_CARD_ALT};"
            f"border:1px solid {Glass.BORDER};border-radius:8px;}}")
        h = QHBoxLayout(wrap)
        h.setContentsMargins(8, 6, 10, 6)
        h.setSpacing(8)
        avatar = QLabel('')
        avatar.setFixedSize(32, 32)
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setStyleSheet(
            f"background:{Glass.BG_INSET};border-radius:8px;border:none;")
        h.addWidget(avatar)
        val = QLabel('--')
        val.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{Glass.TEXT_MUTED};"
            f"font-family:'Cascadia Code','Consolas','Microsoft YaHei UI';"
            f"background:transparent;border:none;")
        h.addWidget(val, 1)
        wrap._avatar = avatar
        wrap._value = val
        wrap._prefix = None
        _last = {'key': None}

        def _apply_value(text, data_mode):
            key = (text, bool(data_mode))
            if key == _last['key']:
                return
            _last['key'] = key
            color = Glass.TEXT_PRIMARY if data_mode else Glass.TEXT_MUTED
            val.setText(text)
            val.setStyleSheet(
                f"font-size:13px;font-weight:700;color:{color};"
                f"font-family:'Cascadia Code','Consolas','Microsoft YaHei UI';"
                f"background:transparent;border:none;")

        wrap.set_value = lambda v: _apply_value(v, True)
        wrap.set_default = lambda: _apply_value('--', False)
        return wrap

    def _mk_stat_chip(self, color, label, default_value, mono=False):
        """统计块（液态玻璃 ultra 版）：垂直排布，label 上 / value 下，统一底。

        接口与 rank chip 一致（set_value / set_default）。段位/资产/荣誉共用，
        等分撑满一行 —— 消除旧 chip「前缀+值水平拼接」导致的尺寸参差。
        mono=True 时 value 用等宽字体（资产/荣誉等数字）。
        """
        wrap = QFrame()
        wrap.setStyleSheet(
            f"QFrame{{background:{Glass.BG_CARD_ALT};"
            f"border:1px solid {Glass.BORDER};border-radius:8px;}}")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(10, 8, 10, 8)
        v.setSpacing(3)
        lab = QLabel(label)
        lab.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;border:none;")
        mono_fam = ("font-family:'Cascadia Code','Consolas','Microsoft YaHei UI';"
                    if mono else '')
        val = QLabel(default_value)
        val.setStyleSheet(
            f"font-size:13px;font-weight:700;color:{Glass.TEXT_MUTED};"
            f"{mono_fam}background:transparent;border:none;")
        v.addWidget(lab)
        v.addWidget(val)
        wrap._value = val
        wrap._lab = lab
        wrap._color = color
        _last = {'key': None}

        def _apply_value(text, data_mode):
            key = (text, bool(data_mode))
            if key == _last['key']:
                return
            _last['key'] = key
            vc = Glass.TEXT_PRIMARY if data_mode else Glass.TEXT_MUTED
            lc = color if data_mode else Glass.TEXT_MUTED
            val.setText(text)
            val.setStyleSheet(
                f"font-size:13px;font-weight:700;color:{vc};"
                f"{mono_fam}background:transparent;border:none;")
            lab.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{lc};background:transparent;border:none;")

        wrap.set_value = lambda x: _apply_value(x, True)
        wrap.set_default = lambda: _apply_value(default_value, False)
        return wrap

    def _mk_recent_chip(self):
        """近期战绩 chip：'近10场' 前缀 + N胜(红 tag) + N负(绿 tag)。

        胜负语义全局统一：胜=红 / 负=绿（国服习惯，与战绩页徽章、
        对局详情 WIN_COLOR/LOSE_COLOR 一致）。
        """
        wrap = QWidget()
        wrap.setStyleSheet('background:transparent;')
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        prefix = QLabel('近10场')
        prefix.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        win = QLabel('--')
        win.setStyleSheet(
            f"background:rgba(195,79,85,0.13);color:{Glass.DANGER};"
            f"border-radius:6px;padding:2px 8px;font-size:{Glass.FONT_SIZE_XS};font-weight:600;")
        loss = QLabel('--')
        loss.setStyleSheet(
            f"background:rgba(25,132,95,0.13);color:{Glass.SUCCESS};"
            f"border-radius:6px;padding:2px 8px;font-size:{Glass.FONT_SIZE_XS};font-weight:600;")
        h.addWidget(prefix)
        h.addWidget(win)
        h.addWidget(loss)
        wrap._win = win
        wrap._loss = loss
        _last = {'key': None}

        def _set_result(w, l):
            key = (w, l)
            if key == _last['key']:
                return
            _last['key'] = key
            win.setText(f'{w}胜')
            loss.setText(f'{l}负')

        def _set_default():
            key = ('--', '--')
            if key == _last['key']:
                return
            _last['key'] = key
            win.setText('--')
            loss.setText('--')

        wrap.set_result = _set_result
        wrap.set_default = _set_default
        # set_value 兼容占位（不用于本 chip）
        wrap.set_value = lambda v: _set_default()
        return wrap

    def set_mastery_icon(self, idx, data):
        """线程安全入口：熟练度 top3 英雄图标 bytes → 信号回主线程渲染头像。"""
        self.sig_mastery_icon.emit(idx, data)

    @ui_slot
    def _do_mastery_icon(self, idx, data):
        """主线程渲染熟练度头像（QPixmap 只能在主线程创建）。"""
        try:
            idx = int(idx)
            chip = (self._chip_m1, self._chip_m2, self._chip_m3)[idx]
            pm = icon_pixmap(data, 32, widget=self)
            if pm is not None and not pm.isNull():
                chip._avatar.setPixmap(pm)
        except Exception as e:
            print(f'[ui_slot] 熟练度头像渲染失败: {type(e).__name__}: {e}')

    def _set_acct_id(self, text, connected):
        """账号 ID 标签：昵称 + #tag 胶囊（tag 降低对比度，与主昵称拉开层次）。

        v4.4.x(P3): 脏值比较 —— 探活每 3s 刷新一次，(text, connected)
        没变就跳过 setStyleSheet。
        """
        key = (text, bool(connected))
        if key == getattr(self, '_acct_id_state', None):
            return
        self._acct_id_state = key
        color = Glass.TEXT_PRIMARY if connected else Glass.TEXT_MUTED
        if text and '#' in text:
            base, tag = text.rsplit('#', 1)
            base = (base or '--').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            tag = tag.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            # 用户反馈：#数字 的胶囊底看着像阴影 → 改普通文字，仅用次级色弱化
            self._acct_id_lbl.setText(
                f'<span style="color:{color};">{base}</span>'
                f'&nbsp;<span style="color:{Glass.TEXT_MUTED};">#{tag}</span>')
            self._acct_id_lbl.setTextFormat(Qt.RichText)
            self._acct_id_lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_LG};font-weight:600;background:transparent;")
        else:
            self._acct_id_lbl.setTextFormat(Qt.PlainText)
            self._acct_id_lbl.setText(text)
            self._acct_id_lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_LG};font-weight:600;"
                f"color:{color};background:transparent;")

    def update_account_info(self, info):
        """主线程调用(连接探活数据/断线重置)。info=None → 等待态。

        全部字段容错:任一缺失只让对应控件保持默认值,不抛异常。
        v37 用 _label/_value 双控件:label 显示列名(单双/灵活),value 显示段位。
        """
        if not info:
            self._set_acct_id('--', False)
            self._acct_lv_lbl.setText('--')
            self._acct_xp.setValue(0)
            self._acct_xp_txt.setText('升级 --%')
            for chip in (self._chip_solo, self._chip_flex,
                         self._chip_wallet_ip, self._chip_wallet_cosmetic,
                         self._chip_wallet_mythic,
                         self._chip_honor,
                         self._chip_m1, self._chip_m2, self._chip_m3,
                         self._chip_recent):
                chip.set_default()
            self._avatar_lbl.setText('🎮')
            # v4.1.39:断线清真头像标记,重连后重新走缓存/下载
            self._avatar_real_id = 0
            return
        # 行1:游戏ID
        name = (info.get('name') or '').strip()
        if name:
            self._set_acct_id(name, True)
        # 行2:等级 + 经验
        level = _as_int(info.get('level'))
        self._acct_lv_lbl.setText(str(level) if level else '--')
        try:
            pct = float(info.get('xp_pct') or 0)
        except (TypeError, ValueError):
            pct = 0.0
        pct = max(0.0, min(100.0, pct))
        self._acct_xp.setValue(int(round(pct)))
        self._acct_xp_txt.setText(f'升级 {int(round(pct))}%' if pct else '升级 --%')
        # 行3:段位(单排/灵活) —— prefix='单排'/'灵活' 永远显示, value=段位文本
        # v4.1.53: 已连接时无论有无段位都用 set_value(空→'未定级'); 未连接走 set_default
        # → '--'(default_value 已改为 '--'), 与用户期望"未连接显示 --"一致。
        # v4.1.62: 后缀拼接"X胜Y负"(本季总场), 玩家直观看本季战绩。
        solo_txt = _rank_text(info.get('solo'))
        flex_txt = _rank_text(info.get('flex'))
        solo_w = _as_int(info.get('solo_wins'))
        solo_l = _as_int(info.get('solo_losses'))
        flex_w = _as_int(info.get('flex_wins'))
        flex_l = _as_int(info.get('flex_losses'))
        if solo_txt and (solo_w or solo_l):
            solo_txt = f'{solo_txt} {solo_w}胜{solo_l}负'
        if flex_txt and (flex_w or flex_l):
            flex_txt = f'{flex_txt} {flex_w}胜{flex_l}负'
        self._chip_solo.set_value(solo_txt or '未定级')
        self._chip_flex.set_value(flex_txt or '未定级')
        # 行4:资产 = 蓝精粹 / 橙精粹
        wallet_ip = _as_int(info.get('wallet_ip'))
        wallet_cos = _as_int(info.get('wallet_cosmetic'))
        if wallet_ip > 0:
            self._chip_wallet_ip.set_value(f'{wallet_ip:,}')
        else:
            self._chip_wallet_ip.set_default()
        if wallet_cos > 0:
            self._chip_wallet_cosmetic.set_value(f'{wallet_cos:,}')
        else:
            self._chip_wallet_cosmetic.set_default()
        # 行5:资产(续) 神话精粹 / 荣誉
        wallet_my = _as_int(info.get('wallet_mythic'))
        honor = _as_int(info.get('honor'))
        if wallet_my > 0:
            self._chip_wallet_mythic.set_value(f'{wallet_my:,}')
        else:
            self._chip_wallet_mythic.set_default()
        if honor > 0:
            self._chip_honor.set_value(str(honor))
        else:
            self._chip_honor.set_default()
        # 行6:熟练度 top3 —— 只显示等级（头像由 set_mastery_icon 异步回填）
        mt = info.get('mastery_top') or []
        chips = (self._chip_m1, self._chip_m2, self._chip_m3)
        for i, chip in enumerate(chips):
            if i < len(mt):
                m = mt[i]
                lv = _as_int(m.get('level'))
                chip.set_value(f'{lv}级' if lv > 0 else '--')
            else:
                chip.set_default()
        # 行6:最近战绩 —— 双色胜负 tag
        recent = info.get('recent')
        if recent and recent.get('total', 0) > 0:
            self._chip_recent.set_result(
                recent.get('wins', 0), recent.get('losses', 0))
        else:
            self._chip_recent.set_default()
        # v4.1.66: 同批数据驱动战绩页(签名去重, 内容没变不重建)
        try:
            self.update_recent((recent or {}).get('games'))
        except Exception as e:
            print(f'[战绩] 页面刷新失败: {type(e).__name__}: {e}')
        # v4.10.x: 手动刷新按钮的"刷新中"复位 —— 探活结果已带回, 说明刷新完成
        self._clear_refresh_pending()
        # 头像:真头像已上屏(icon 匹配)时探活刷新不覆盖;未就绪/换头像才显示
        # v4.1.39 生成式占位等下载完成替换 —— 修复"真头像渲染后 3s 被占位盖掉"
        cur_icon = _as_int(info.get('icon_id'))
        if name and not (cur_icon and cur_icon == self._avatar_real_id):
            self._avatar_real_id = 0
            self._set_generated_avatar(name, cur_icon)

    def _set_generated_avatar(self, display_name: str, icon_id: int):
        """v37:CDN 头像拉不到时,基于 ID + display_name 哈希生成单字母彩色头像。

        永远可用,作为「真头像加载失败」的兜底,避免界面上永远显示 🎮 占位。

        v4.4.0 修复(P1-23): 原实现每次调用都 md5 + 新建 QPixmap + QPainter 重画。
        调用方 update_account_info 每 3 秒刷新一次, 而守卫比较的是
        `_avatar_real_id`(只在真头像上屏时被写)—— 连不上 CDN 时它恒为 0,
        于是 **每 3 秒重建一次同样的头像**。现在按 (icon_id, 首字符, dpr) 缓存,
        命中直接复用同一个 QPixmap。
        """
        try:
            # 中央字 = 第一个非空白字符(中英文都行)
            ch = (display_name.strip()[:1] or '?')
            side = AVATAR_SIDE
            dpr = 1.0
            try:
                dpr = float(self._avatar_lbl.devicePixelRatioF() or 1.0)
            except Exception:
                dpr = 1.0
            cache_key = (int(icon_id or 0), ch, round(dpr, 3), side)
            cached = self._avatar_gen_cache.get(cache_key)
            if cached is not None and not cached.isNull():
                self._avatar_lbl.setPixmap(cached)
                self._avatar_lbl.setText('')
                return
            # 背景色:HLS 空间拉一个固定范围,base 哈希定型
            import hashlib
            base_h = hashlib.md5(f'{icon_id}'.encode('utf-8')).digest()[0]
            hue = base_h * 137 % 360  # 黄金角分布,均匀
            sat = 60
            light = 45
            bg = f'hsl({hue},{sat}%,{light}%)'
            # v4.4.0(P1-16): DPR 感知 —— 1.5x/2x 缩放下按物理像素出图再标回逻辑
            px = max(1, int(round(side * dpr)))
            out = QPixmap(px, px)
            out.setDevicePixelRatio(dpr)
            out.fill(Qt.transparent)
            p = QPainter(out)
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(QColor(bg))
            p.setPen(Qt.NoPen)
            # v4.4.2-r2 同类回归修复: 画布已 setDevicePixelRatio(dpr), QPainter 的
            # 坐标系是**逻辑**坐标(0..side), 这里却传了物理尺寸 px 与 out.rect()
            # (物理 rect) —— 圆和文字被 Qt 再乘一次 dpr, 偏向右下、右下越界被裁。
            # _probe_avatar_dpr.py 实测 dpr=2.0 四象限墨迹 10.2/29.1/29.1/31.6
            # (左上÷右下=0.32, 严重不对称); 改全逻辑坐标后恒为 25/25/25/25。
            # dpr=1.0 时 px==side, 新旧等价 —— 100% 缩放机器上看不出差异(故长期潜伏)。
            p.drawEllipse(0, 0, side, side)
            p.setPen(QColor('#ffffff'))
            font = p.font()
            font.setBold(True)
            font.setPointSize(24)
            p.setFont(font)
            p.drawText(0, 0, side, side, Qt.AlignCenter, ch)
            p.end()
            self._avatar_gen_cache[cache_key] = out
            # 上限 32 项, 超限丢最旧(头像组合极少, 只是防止换号刷屏时无界增长)
            if len(self._avatar_gen_cache) > 32:
                for k in list(self._avatar_gen_cache.keys())[:-32]:
                    self._avatar_gen_cache.pop(k, None)
            self._avatar_lbl.setPixmap(out)
            self._avatar_lbl.setText('')
        except Exception:
            pass

    def set_account_avatar(self, data, icon_id=0):
        """真头像 bytes → 圆形头像(主线程)。失败/坏数据直接忽略,保留生成式占位。
        v4.1.39:成功后记录 icon_id,探活轮询据此不再覆盖真头像。"""
        try:
            pm = QPixmap()
            if not pm.loadFromData(data):
                return
            side = AVATAR_SIDE
            pm = pm.scaled(side, side, Qt.KeepAspectRatioByExpanding,
                           Qt.SmoothTransformation)
            x = (pm.width() - side) // 2
            y = (pm.height() - side) // 2
            pm = pm.copy(x, y, side, side)
            out = QPixmap(side, side)
            out.fill(Qt.transparent)
            p = QPainter(out)
            p.setRenderHint(QPainter.Antialiasing)
            path = QPainterPath()
            path.addEllipse(0, 0, side, side)
            p.setClipPath(path)
            p.drawPixmap(0, 0, pm)
            p.end()
            self._avatar_lbl.setPixmap(out)
            self._avatar_lbl.setText('')
            if icon_id:
                self._avatar_real_id = int(icon_id)
        except Exception:
            pass

    # ==================== 实际更新 ====================
    def _do_conn(self, connected, name=''):
        # v4.4.0 修复(P1-1): 槽异常保护 —— PyQt5 默认处理器对未捕获异常直接
        # qFatal(abort), 一个脏数据就能杀进程。这里只打印并继续。
        try:
            self._update_dot(connected)
            if connected:
                # v4.4.x(P2): name 参数此前被静默丢弃, 现在拼接显示(如"客户端已连接 · 召唤师名")
                self._conn_label.setText(
                    CONN_TEXT_ONLINE + (f' · {name}' if name else ''))
            else:
                self._conn_label.setText(CONN_TEXT_OFFLINE)
            # v4.10.x: 战绩页空态三态 —— 断线且当前没有渲染任何行时, 空态显示
            # "未连接到客户端"; 有历史行则保留不动(旧数据仍可浏览, 不强行清空)。
            if not connected and not self._recent_rows:
                self._set_recent_empty('offline')
            elif connected and getattr(self._recent_empty, '_state', '') == 'offline':
                self._set_recent_empty('loading')
            # 速览条提示随连接态切换（用户反馈：已连接还显示"自动加载"不合适）
            hint = getattr(self, '_recent_hint', None)
            if hint is not None:
                hint.setText('点击查看对局详情' if connected
                             else '连接客户端后自动加载')
        except Exception as e:
            print(f'[ui_slot] MainWindow._do_conn 异常已被拦下: '
                  f'{type(e).__name__}: {e}')

    def _update_dot(self, connected):
        """状态点 - 呼吸灯动画（与小窗连接点风格一致）

        v4.4.0 修复(P1-18): 改为自绘 StatusDot, 不再 setStyleSheet。
        """
        self._dot_connected = connected
        # 基色：已连接翠绿 / 未连接红
        self._dot_rgb = DOT_RGB_ONLINE if connected else DOT_RGB_OFFLINE
        self._dot_alpha = 170.0
        self._dot_dir = 1
        if not hasattr(self, '_dot_timer') or self._dot_timer is None:
            # v5.1(ultra M4): QTimer 带 parent —— 窗口销毁时随父对象自动回收,
            # 与项目内其余 QTimer(_recent_icon_timer / PulseGlow._timer)一致。
            self._dot_timer = QTimer(self)
            self._dot_timer.timeout.connect(self._dot_tick)
            self._dot_timer.setInterval(DOT_TICK_MS)
        # 立即把颜色同步到圆点(不等下一 tick, 避免状态切换后 40ms 内还是旧色)
        dot = getattr(self, '_dot', None)
        if dot is not None and hasattr(dot, 'set_state'):
            dot.set_state(color=self._dot_rgb, alpha=int(self._dot_alpha))
        # v4.3.10: 只在窗口可见时跑 40ms 呼吸动画。原来一旦启动就永不停止 ——
        # 窗口隐藏/最小化到托盘后仍在以 25fps 重算样式表, 白白吃 CPU。
        # (恢复/停止分别在 showEvent / hideEvent 里, 见本文件上方。)
        if self.isVisible():
            self._dot_timer.start()

    @ui_slot
    def _dot_tick(self):
        if not hasattr(self, '_dot_rgb'):
            return
        self._dot_alpha += DOT_ALPHA_STEP * self._dot_dir
        if self._dot_alpha >= DOT_ALPHA_MAX:
            self._dot_alpha = DOT_ALPHA_MAX
            self._dot_dir = -1
        elif self._dot_alpha <= DOT_ALPHA_MIN:
            self._dot_alpha = DOT_ALPHA_MIN
            self._dot_dir = 1
        dot = getattr(self, '_dot', None)
        if dot is None:
            return
        a = int(self._dot_alpha)
        # v4.4.0 修复(P1-18): 原来是 setStyleSheet 重写 background-color
        # (每秒 25 次 QSS 解析)。改自绘圆点, 只 update() 一次小控件。
        if hasattr(dot, 'set_state'):
            dot.set_state(alpha=a)
        else:
            r, g, b = self._dot_rgb
            dot.setStyleSheet(
                f"background-color:rgba({r},{g},{b},{a});border-radius:5px;min-width:10px;min-height:10px;")

    def _do_phase(self, phase):
        # v4.4.0 修复(P1-1): 槽异常保护(见 window_base.ui_slot 说明)
        try:
            # v4.4.0(P2 硬编码): 阶段名映射表移到 match_fmt.PHASE_CN
            self._phase_label.setText(_phase_text(phase))
        except Exception as e:
            print(f'[ui_slot] MainWindow._do_phase 异常已被拦下: '
                  f'{type(e).__name__}: {e}')

    def _do_run(self, running):
        # v4.4.0 修复(P1-1): 槽异常保护
        try:
            self._running = running
            if running:
                self._fab.setText('停止脚本')
                # v4.4.1(UI-A1): 与构造时共用 _fab_qss(), 消除双份拷贝
                # (顺便把硬编的 #dc2626/#b91c1c/#991b1b 收进 Glass.STOP_RED*)
                self._fab.setStyleSheet(_fab_qss(True))
            else:
                self._fab.setText('启动脚本')
                self._fab.setStyleSheet(_fab_qss(False))
        except Exception as e:
            print(f'[ui_slot] MainWindow._do_run 异常已被拦下: '
                  f'{type(e).__name__}: {e}')

    @ui_slot
    def _do_log(self, text):
        sb = self._log_text.verticalScrollBar()
        was_at_bottom = sb and sb.value() >= sb.maximum() - 20
        self._log_text.append(text)
        doc = self._log_text.document()
        if doc.blockCount() > 400:
            cursor = self._log_text.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.Down, cursor.KeepAnchor, doc.blockCount() - 400)
            cursor.removeSelectedText()
        if was_at_bottom and sb:
            sb.setValue(sb.maximum())

    # ==================== 持久化 ====================
    def _load_settings(self):
        path = self._settings_path()
        defaults = {
            'auto_queue': True, 'auto_queue_delay': 2,
            'auto_accept': True, 'auto_accept_delay': 1,
            'auto_play_again': True, 'auto_play_again_delay': 2,
            # v4.4.16: 连上客户端后自动启动脚本(默认关 —— 用户要显式开启才生效),
            # 延时默认 5 秒(给客户端进大厅后的首帧/账号信息加载留点时间)。
            'auto_start_on_connect': False, 'auto_start_on_connect_delay': 5,
            'auto_select_champion': False,   # v34: 进选人自动选英雄(默认关)
            'auto_pick_champion': True,      # 锁后自动换 bench(默认开, 保留旧行为)
            # v4.9.13(用户要求): 小窗备选栏单击 = 预输入本局想玩的英雄(蓝光),
            # 由 core 的 1s 轮询在备选池里一出现就换过去, 优先级高于偏好。
            # 默认开 —— 这是用户点名要的用法; 关掉后单击退回"立即换英雄"。
            'auto_preinput_champion': True,
            'preferred_champions': [],       # v41: 移除死配置 'queue_id', 队列从 session 取
            'agreement_accepted': False,     # v4.1.55: 首次启动强制阅读须知(30s)后置 True
            # v4.8.1(用户要求): 点「启动 LOL」成功后自动"隐藏主界面 + 打开小窗"。
            # 默认**关** —— 保持 v4.8.0 的既有行为不变, 让用户自己在设置里打开。
            # 为什么默认关: 它改变了点一个按钮的可见后果(主窗消失), 属于"用户没要求
            # 就不该发生"的界面变化。显式开启才生效。
            'lol_launch_to_overlay': False,
            # v4.8.0: 'lol_steps'(录制的点击步骤)已删除 —— 录制回放功能移除。
            # 老用户设置文件里若还留着这个键, load 时按"未知键"忽略即可, 不做迁移。
            # v4.3.7: 移除 'update_url' —— 自定义更新源设置项已删(不再有对外
            # 发布/更新检查的打算, 不需要在软件里手填地址)。旧的 settings.json
            # 里若还留着这个键, defaults.update() 会把它带进来, 但已无人读取, 无害。
        }
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    defaults.update(loaded)
                    # v5.0.0 审计修复(P2): 按 defaults 的类型对 bool/int 键做规约。
                    # 手改 settings.json 写成 "auto_queue": "false"(字符串 truthy)
                    # 会让 PyQt 的 setChecked 把开关显示成"开", 与用户意图相反。
                    # preferred_champions 的脏值已有 overlay 侧防护, 这里只规约
                    # 标量开关/延迟项。
                    for k, dflt in defaults.items():
                        if isinstance(dflt, bool) and not isinstance(defaults[k], bool):
                            # 非 bool(字符串/数字)一律回退默认值 —— 无法可靠推断
                            # 用户意图, 与其赌 truthiness 不如用默认, 用户下次自己开。
                            defaults[k] = dflt
                        elif isinstance(dflt, int) and not isinstance(defaults[k], int):
                            try:
                                defaults[k] = int(defaults[k])
                            except (TypeError, ValueError):
                                defaults[k] = dflt
                else:
                    # v4.4.0 修复(P1-24): 顶层不是对象(手改坏/被别的工具写坏)
                    # 也按损坏处理, 否则 defaults.update(list) 会抛。
                    raise ValueError(f'settings.json 顶层不是对象: {type(loaded).__name__}')
        except Exception as e:
            # v4.4.0 修复(P1-24): 原来 except 里 pass —— 损坏的 settings.json
            # 会被静默忽略, 用户设置全丢且无从察觉; 更糟的是下一次 _save_settings
            # 直接把它覆盖掉, 坏档连取证的机会都没有。现在: 备份坏文件 + 打印,
            # 然后用默认值继续(功能不崩)。
            print(f'[Settings] 读取失败, 已回退默认值: {type(e).__name__}: {e}')
            self._backup_broken_settings(path)
        # v34 一次性迁移: 把 v33 中段调试残留的 auto_select_champion=true 清掉。
        # 此前该 key 没有 UI 卡片, 用户没显式打开过, 默认值应为 False。
        # 用 _v34_migrated 标记防止重复执行; 若用户后续在 UI 真打开了, 标记已存在不会清。
        if not defaults.get('_v34_migrated'):
            defaults['auto_select_champion'] = False
            defaults['_v34_migrated'] = True
            try:
                self._write_settings_file(path, defaults)
                print('[启动] v34 迁移: 已重置 auto_select_champion=False (清掉 v33 残留)')
            except Exception:
                pass
        return defaults

    def _backup_broken_settings(self, path):
        """v4.4.0 修复(P1-24): 把解析失败的 settings.json 改名留证(不删)。"""
        try:
            if not path or not os.path.exists(path):
                return
            bak = path + '.broken'
            try:
                if os.path.exists(bak):
                    os.remove(bak)
            except Exception:
                pass
            os.replace(path, bak)
            print(f'[Settings] 坏档已备份到: {bak}')
        except Exception as e:
            print(f'[Settings] 坏档备份失败: {type(e).__name__}: {e}')

    def _write_settings_file(self, path, data):
        """v4.4.0 修复(P1-24): 原子写 —— 先写同目录 .tmp, 再 os.replace 换名。

        裸 open(path,'w') 有两个真实隐患(main.py 会在退出流程里连续调用):
        1. 写入过程中进程被 kill(如 P0-3 的 os._exit 交接路径 / 拔电) → 留下
           半个 JSON, 下次启动解析失败, 设置全部回默认;
        2. json.dump 抛异常(如 _settings 里混进不可序列化对象)时文件已被前面
           的 write 截断, 同样是半个 JSON。
        os.replace 在同一卷上是原子的: 要么是完整旧档, 要么是完整新档。
        v4.4.x(P3): os.makedirs 移进本方法 —— v34 迁移路径直接调本方法写盘,
        此前它不建目录, 首启时 %APPDATA%\\LOLScript 可能还不存在导致迁移静默失败。
        """
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, path)

    def _save_settings(self):
        try:
            path = self._settings_path()
            self._write_settings_file(path, self._settings)
        except Exception as e:
            print(f'[Settings] 保存失败: {e}')
            # 失败时清掉可能残留的 .tmp(避免用户看到项目外目录里多出的碎文件)
            try:
                tmp = self._settings_path() + '.tmp'
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass

    def _settings_path(self):
        """设置文件路径: %APPDATA%\\LOLScript\\settings.json。

        v4.6.8 修兜底: 原写 `os.environ.get('APPDATA', '~')` —— 而 `'~'` 在这里
        **不会被展开**(os.path.join 不做 shell 展开), 会在当前工作目录下造出一个
        名字就叫 `~` 的目录。实测确认 APPDATA 正常时不受影响, 但环境变量缺失
        (服务账户/精简环境)就会写出非法路径。
        改为 `os.path.expanduser('~')`, 至少指向真实的家目录。
        ⚠️ 存储位置本身**有意不动**: 改到别处会让老用户的既有设置全部读不到。
        """
        base = os.environ.get('APPDATA') or os.path.expanduser('~')
        return os.path.join(base, 'LOLScript', 'settings.json')
