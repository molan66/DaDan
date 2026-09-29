"""Main Window - 液态玻璃风格主窗口

特性:
- Windows 系统级毛玻璃（Mica/Acrylic）
- 半透明玻璃分层
- 弹簧动画
- 发光卡片
- 光晕高光
"""

import json
import os
import time as _time
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit,
    QApplication, QScrollArea, QProgressBar,
    QGraphicsOpacityEffect
)
from PyQt5.QtCore import Qt, pyqtSignal, QTimer, QPropertyAnimation, QUrl
# v4.4.0 修复(P1-22): QFont 原来在 SidebarButton.paintEvent 内部 import
# (每次重绘都走一遍模块查找), 提到模块顶部, 字体对象在 __init__ 建好复用。
from PyQt5.QtGui import (QColor, QPainter, QLinearGradient, QFont,
                         QPixmap, QPainterPath, QDesktopServices)

from .styles import Glass
# v4.3.0: 对局展示格式化抽到 match_fmt(详情弹窗也要用, 它不能反向 import 本模块);
# 这里用原名别名导入, 模块内既有调用点无需改动。
from .match_fmt import (LANE_CN as _LANE_CN, phase_text as _phase_text,
                        game_mode_text as _game_mode_text,
                        dur_text as _dur_text, time_text as _time_text)
from .icons import (icon_home, icon_settings, icon_hero, icon_chart, icon_log,
                     icon_info, AnimatedToggle, make_title_buttons,
                     rounded_pixmap, icon_to_pixmap)
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
DOT_RGB_ONLINE = (52, 211, 153)        # 已连接: 翠绿
DOT_RGB_OFFLINE = (248, 113, 113)      # 未连接: 红
CONN_TEXT_ONLINE = '客户端已连接'
CONN_TEXT_OFFLINE = '未连接到客户端'
SIDEBAR_PAGES = (('🏠', '首页'), ('⚙️', '设置'), ('⭐', '英雄'),
                 ('📊', '战绩'), ('📋', '日志'), ('ℹ️', '关于'))
SIDEBAR_ICON_FNS = {'🏠': icon_home, '⚙️': icon_settings, '⭐': icon_hero,
                    '📊': icon_chart, '📋': icon_log, 'ℹ️': icon_info}

# v4.5.0: 「关于」页里的项目主页地址（开源仓库）。
# v4.5.1: 仓库已由 Dandan 更名为 DaDan，同步更新。
PROJECT_REPO_URL = 'https://github.com/molan66/DaDan'
PROJECT_REPO_URL_PLACEHOLDER = False   # 已填入真实用户名；若改回占位符请置 True

# v4.4.1(UI-B9 第一步): 账号卡 chip 的几何与 8 组色值收敛为模块常量。
# **数值一个字都没改** —— 这一步只让"改一处即全局生效", 视觉零回归。
# 第二步(统一到 Glass 语义色板、消除 #6ee7b7 与 Glass.ACCENT 的第三种绿)
# 属于会改到观感的动作, 留待单独确认后再做。
CHIP_FONT_PX = 10                      # chip 文字字号(前缀标签与数值同号)
CHIP_RADIUS_PX = 5                     # chip 圆角
CHIP_PAD = '2px 7px'                   # chip 内边距
CHIP_WAIT_BG = 'rgba(255,255,255,0.04)'  # 等待态弱背景
CHIP_GAP = 6                           # chip 之间/与 label 列间距
# (前景色, 背景色) —— 前景即 prefix 文字色, 背景为数值胶囊底色
CHIP_SOLO = ('#a5b4fc', 'rgba(99,102,241,0.18)')       # 单排
CHIP_FLEX = ('#6ee7b7', 'rgba(52,211,153,0.16)')       # 灵活
CHIP_WALLET_IP = ('#93c5fd', 'rgba(59,130,246,0.16)')  # 蓝色精粹
CHIP_WALLET_COSMETIC = ('#fca5a5', 'rgba(248,113,113,0.16)')  # 橙色精粹
CHIP_WALLET_MYTHIC = ('#a78bfa', 'rgba(167,139,250,0.16)')    # 神话精粹
CHIP_HONOR = ('#fcd34d', 'rgba(251,191,36,0.18)')      # 荣誉
CHIP_MASTERY_1 = ('#c4b5fd', 'rgba(139,92,246,0.16)')  # 熟练度 top1
CHIP_MASTERY_2 = ('#c4b5fd', 'rgba(139,92,246,0.12)')  # 熟练度 top2
CHIP_MASTERY_3 = ('#c4b5fd', 'rgba(139,92,246,0.10)')  # 熟练度 top3
CHIP_RECENT = ('#86efac', 'rgba(52,211,153,0.16)')     # 近 10 场


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


def _fab_qss(running: bool) -> str:
    """启动/停止按钮的完整样式表(单一来源)。

    v4.4.1(UI-A1) 修复两件事:
    ① **字号不一致**: 构造时(:914)写的是 FONT_SIZE_LG(14px), 而首次点启动后
       _do_run 两态都写 FONT_SIZE_XL(16px) → 按钮文字点一下就永久变大一号。
       现在统一走 FONT_SIZE_XL。
    ② **QSS 双份拷贝**: 蓝态原本在 __init__ 与 _do_run(False) 里各写一份、
       逐字相同, 改一处必漏另一处; 红色三档 hex 也硬编在 _do_run 里。
       现在四态样式只此一处, 色值取 Glass.STOP_RED* / PRIMARY*。
    """
    if running:
        return f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {Glass.DANGER}, stop:1 {Glass.STOP_RED});
                color: white; border: none; border-radius: {Glass.RADIUS_BTN};
                font-size: {Glass.FONT_SIZE_XL}; font-weight: 700;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {Glass.STOP_RED}, stop:1 {Glass.STOP_RED_HOVER});
            }}
            QPushButton:pressed {{
                background: {Glass.STOP_RED_PRESSED}; padding-top: 2px;
            }}
        """
    return f"""
            QPushButton {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {Glass.PRIMARY_HOVER}, stop:1 {Glass.PRIMARY});
                color: white; border: none; border-radius: {Glass.RADIUS_BTN};
                font-size: {Glass.FONT_SIZE_XL}; font-weight: 700;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {Glass.PRIMARY}, stop:1 {Glass.PRIMARY_PRESSED});
            }}
            QPushButton:pressed {{
                background: {Glass.PRIMARY_PRESSED}; padding-top: 2px;
            }}
        """


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
            background: rgba(28, 30, 42, 200);
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
        self.setFixedWidth(80)
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

        # 激活态：背景高光 + 左侧指示条
        if self._active:
            # 整行背景微光
            bg = QColor(99, 102, 241, 18)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(0, int(h * 0.08), w, int(h * 0.84), 8, 8)
            # 左侧指示条（更宽更亮）
            bar = QLinearGradient(0, h * 0.12, 0, h * 0.88)
            bar.setColorAt(0, QColor(99, 102, 241, 60))
            bar.setColorAt(0.5, QColor(129, 140, 248, 200))
            bar.setColorAt(1, QColor(99, 102, 241, 60))
            p.setPen(Qt.NoPen)
            p.setBrush(bar)
            p.drawRoundedRect(0, int(h * 0.12), 3, int(h * 0.76), 2, 2)

        # hover 背景（柔和渐变）
        if self._hovered and not self._active:
            hover_bg = QLinearGradient(0, int(h * 0.08), 0, int(h * 0.92))
            hover_bg.setColorAt(0, QColor(255, 255, 255, 3))
            hover_bg.setColorAt(0.5, QColor(255, 255, 255, 6))
            hover_bg.setColorAt(1, QColor(255, 255, 255, 2))
            p.setPen(Qt.NoPen)
            p.setBrush(hover_bg)
            p.drawRoundedRect(0, int(h * 0.08), w, int(h * 0.84), 8, 8)

        # 图标
        icons = self._icon_char2fn
        fn = icons.get(self._icon_char)
        if fn:
            # v4.4.0 修复(P1-22): 走三态 pixmap 缓存, 不再每次重绘重建
            # dict + 重新栅格化矢量图标(见 __init__ / _state_icon)
            icon_pixmap = self._state_icon()
            if icon_pixmap is not None:
                x = (w - SIDEBAR_BUTTON_ICON_PX) // 2
                p.drawPixmap(x, 8, icon_pixmap)

        # 标签
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
    lbl.mousePressEvent = lambda e, b=btn: b.toggle()
    rl.addWidget(lbl)
    rl.addStretch()

    return row, btn


class _MatchRow(QWidget):
    """战绩页单场行: [英雄头像] 英雄名/模式·时长·位置·时间 | KDA | 胜/负徽章

    v4.3.0: 整行可点击 → 查看本局详情(clicked 信号由战绩页转发给主窗)。

    注意: **这里不设 setToolTip** —— 用户明确不要"点击查看本局详情"的悬浮小提示。
    可点击性靠 PointingHandCursor + 悬停高亮表达就够了。
    """

    ICON = 38
    clicked = pyqtSignal(dict)
    # 图标轮询的时间窗: 窗口内持续重试(覆盖慢速下载), 超窗即放弃 ——
    # 防某个英雄图标永久不存在时每 900ms 空打网络(LCU + CDN 双请求)
    ICON_RETRY_WINDOW_S = 15.0

    def __init__(self, game, parent=None):
        super().__init__(parent)
        self._game = game or {}
        self.icon_loaded = False
        self.icon_deadline = _time.monotonic() + self.ICON_RETRY_WINDOW_S
        self._hovered = False
        cid = self._game.get('champion_id')
        try:
            self.champion_id = int(cid or 0)
        except (TypeError, ValueError):
            self.champion_id = 0

        self.setFixedHeight(52)
        self.setCursor(Qt.PointingHandCursor)
        # QWidget 子类默认不绘制样式表里的 background, 必须开 WA_StyledBackground
        # (v4.4.0: 行底色改 paintEvent 自绘后该属性不再必需, 保留无副作用 ——
        #  避免动到其它继承的样式表语义)
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(10)

        # 英雄头像(占位=英雄名首字, 下载完成后 set_icon 覆盖)
        self._icon_lbl = QLabel()
        self._icon_lbl.setFixedSize(self.ICON, self.ICON)
        self._icon_lbl.setAlignment(Qt.AlignCenter)
        self._icon_lbl.setStyleSheet(
            'background: rgba(255,255,255,0.06);'
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
        mid.addWidget(self._name_lbl)
        mid.addWidget(self._sub_lbl)

        self._kda_lbl = QLabel()
        self._kda_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._kda_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};font-weight:600;"
            f"color:{Glass.TEXT_SECONDARY};background:transparent;")

        self._badge = QLabel()
        self._badge.setFixedSize(24, 24)
        self._badge.setAlignment(Qt.AlignCenter)

        lay.addWidget(self._icon_lbl)
        lay.addLayout(mid, 1)
        lay.addWidget(self._kda_lbl)
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
        lane = _LANE_CN.get(str(g.get('lane') or '').upper())
        if lane:
            parts.append(lane)
        t = _time_text(g.get('created_ms'))
        if t:
            parts.append(t)
        self._sub_lbl.setText(' · '.join([p for p in parts if p]))

        k, dd, a = g.get('kills'), g.get('deaths'), g.get('assists')
        if k is None or dd is None or a is None:
            self._kda_lbl.setText('— / — / —')
        else:
            self._kda_lbl.setText(f'{k} / {dd} / {a}')

        # 胜=红 / 负=绿(国服习惯: 红为正向)
        if g.get('win') is True:
            self._badge.setText('胜')
            self._badge.setStyleSheet(
                'background: rgba(248,113,113,0.18); color: #f87171;'
                f'border-radius: {Glass.RADIUS_SM}px;'
                f'font-size: {Glass.FONT_SIZE_XS}; font-weight: 700;')
        else:
            self._badge.setText('负')
            self._badge.setStyleSheet(
                'background: rgba(52,211,153,0.16); color: #34d399;'
                f'border-radius: {Glass.RADIUS_SM}px;'
                f'font-size: {Glass.FONT_SIZE_XS}; font-weight: 700;')

    def paintEvent(self, event):
        """行底色(悬停/常态)在 paintEvent 里画。

        v4.4.0 修复(P1-18): 原来 enterEvent/leaveEvent 各自 `setStyleSheet`
        重写整行 `background/border-radius` —— 每次鼠标划过一行都触发一次
        QSS 解析 + 该行及其子控件重新 polish, 滚动列表里鼠标扫过几十行时
        是可见的抖动源。底色本就只是一个圆角矩形, 直接画即可。
        """
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # alpha = 0.09 / 0.035 → 23 / 9 (与旧样式表逐位等价)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 23 if self._hovered else 9))
        r = Glass.RADIUS_BTN
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), r, r)
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

    def set_icon(self, data) -> bool:
        """用图标字节填充头像(圆角裁剪)。失败返回 False 让上层继续轮询。"""
        try:
            rounded = rounded_pixmap(data, self.ICON, 6, widget=self)
            if rounded is None:
                return False
            self._icon_lbl.setText('')
            self._icon_lbl.setPixmap(rounded)
            self.icon_loaded = True
            return True
        except Exception:
            return False


class MainWindow(DragMixin, GlassEffectMixin, QMainWindow):
    start_script_signal = pyqtSignal()
    stop_script_signal = pyqtSignal()
    minimize_signal = pyqtSignal()
    close_signal = pyqtSignal()
    settings_changed_signal = pyqtSignal(dict)
    # v34: 用户在设置页打开"自动选英雄" → 由 main 决定是直接开还是提权重启
    auto_select_champion_request_signal = pyqtSignal()
    # v4.1.60: 首次启动须知确认后发出 → main 补查"自动选英雄"提权(覆盖
    # "老版本已开 auto_select 但 agreement_accepted 缺失"升级场景, 800ms
    # 启动检查会因 agreement=False 跳过, 须在确认须知后补一次)
    agreement_confirmed_signal = pyqtSignal()


    sig_update_connection = pyqtSignal(bool, str)
    sig_update_phase = pyqtSignal(str)
    sig_set_script_running = pyqtSignal(bool)
    sig_append_log = pyqtSignal(str)

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
        self._recent_icon_timer = None
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
        self.setFixedSize(680, 520)

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
        # v4.5.0: 「关于」页 —— 版权/许可证/项目地址/第三方归属的唯一集中展示处
        # (AGPL-3.0 §0「Appropriate Legal Notices」+ §5 的界面落点)。
        self._pages.addWidget(self._make_about_page())
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
        sidebar.setFixedWidth(80)
        # v4.4.0 修复(P2 设计令牌): 内联 rgba 改走 Glass.BG_SIDEBAR
        sidebar.setStyleSheet(f'background:{Glass.BG_SIDEBAR};')
        lay = QVBoxLayout(sidebar)
        lay.setContentsMargins(0, 6, 0, 6)
        lay.setSpacing(2)

        self._nav_buttons = []
        # v4.4.0(P2 硬编码): 页表抽成模块级常量 SIDEBAR_PAGES
        for i, (ic, label) in enumerate(SIDEBAR_PAGES):
            btn = SidebarButton(ic, label)
            btn.clicked.connect(lambda _, idx=i: self._switch_page(idx))
            lay.addWidget(btn)
            self._nav_buttons.append(btn)

        lay.addStretch()

        ver = QLabel(f'v{APP_VERSION}')
        ver.setAlignment(Qt.AlignCenter)
        ver.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;padding-bottom:4px;")
        lay.addWidget(ver)

        self._nav_buttons[0].set_active(True)
        return sidebar

    @ui_slot
    def _switch_page(self, idx):
        if self._pages.currentIndex() == idx:
            return
        self._pages.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            btn.set_active(i == idx)
        # 切到战绩页时补一轮图标并重开时间窗(下载是后台异步的, 离开页面后轮询会停)
        if idx == 3:
            self._tick_recent_icons(rearm=True)

    # ==================== 首页 ====================
    def _make_home_page(self):
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

        # 连接状态
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        # v4.4.0 修复(P1-18): 原来是 QLabel + 每次 tick 重写 background 样式表
        # (40ms 一次 = 每秒 25 次 QSS 解析 + polish)。改用自绘 StatusDot,
        # 透明度变化只触发一次轻量 update()。
        self._dot = StatusDot(10, color=DOT_RGB_OFFLINE, alpha=170)
        self._update_dot(False)
        status_row.addWidget(self._dot)
        self._conn_label = QLabel('未连接到客户端')
        self._conn_label.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};color:{Glass.TEXT_SECONDARY};background:transparent;")
        status_row.addWidget(self._conn_label)
        status_row.addStretch()
        # v4.4.0(P2) 清理: 原 v36 起这里保留了一个空 QLabel `_summoner_label`
        # (不在布局里、永远 hide()), 只为接住 `_do_conn` 里的旧 setText。
        # 召唤师名自 v36 起已由下方账号卡大号展示, 该 label 从不出现在界面上,
        # 属于纯残留 —— 连同 `_do_conn` 里那一行一起删除。
        cl.addLayout(status_row)
        lay.addWidget(card)

        # ===== 账号信息卡(v37:四行规范结构, 挪到红框位置) =====
        acct_card = GlassCard()
        # v4.1.48: 按用户截图红框宽 = 380, 给账号卡固定宽让右侧 fab 区域稳定;
        # 头像缩 48 / label 列缩 44 让 7 行内容可塞下不溢出.
        acct_card.setFixedWidth(380)
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
        self._avatar_lbl.setStyleSheet(
            f"background:rgba(255,255,255,0.06);border-radius:24px;"
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

        # 行2:等级 + 经验进度
        row2 = QHBoxLayout(); row2.setSpacing(6)
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
            QProgressBar {{ background: rgba(255,255,255,0.08);
                border: none; border-radius: 2px; }}
            QProgressBar::chunk {{ background: {Glass.PRIMARY_HOVER};
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

        # 行3:段位(单排/灵活) —— prefix='单排'/'灵活' 永远显示, value=段位文本
        # v4.1.51 改: 创建时 default_value 必须都是 '未定级', 否则未连接/未拉取到段位
        # 时 chip 会显示残留旧数据「白银 I · 60胜点」造成误导。prefix 加「单排/灵活」
        # 便于区分两条队列(用户反馈两 chip 文字相同时分不清)
        row3 = QHBoxLayout(); row3.setSpacing(CHIP_GAP)
        k = QLabel('段位'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row3.addWidget(k)
        self._chip_solo = self._mk_rank_chip(*CHIP_SOLO, '单排', '--')
        self._chip_flex = self._mk_rank_chip(*CHIP_FLEX, '灵活', '--')
        row3.addWidget(self._chip_solo)
        row3.addWidget(self._chip_flex)
        row3.addStretch()
        acl.addLayout(row3)

        # 行4:资产 蓝精粹 / 橙精粹 —— prefix=中文标签, value=数字
        row4 = QHBoxLayout(); row4.setSpacing(CHIP_GAP)
        k = QLabel('资产'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row4.addWidget(k)
        self._chip_wallet_ip = self._mk_rank_chip(*CHIP_WALLET_IP, '蓝色精粹', '--')
        self._chip_wallet_cosmetic = self._mk_rank_chip(
            *CHIP_WALLET_COSMETIC, '橙色精粹', '--')
        row4.addWidget(self._chip_wallet_ip)
        row4.addWidget(self._chip_wallet_cosmetic)
        row4.addStretch()
        acl.addLayout(row4)

        # 行5:资产(续) 神话精粹 / 荣誉(无 label, 沿用上行"资产"语义)
        row5 = QHBoxLayout(); row5.setSpacing(CHIP_GAP)
        k5 = QLabel(''); k5.setFixedWidth(LBL_W)
        k5.setStyleSheet('background:transparent;')
        row5.addWidget(k5)
        self._chip_wallet_mythic = self._mk_rank_chip(
            *CHIP_WALLET_MYTHIC, '神话精粹', '--')
        self._chip_honor = self._mk_rank_chip(*CHIP_HONOR, '荣誉', '--')
        row5.addWidget(self._chip_wallet_mythic)
        row5.addWidget(self._chip_honor)
        row5.addStretch()
        acl.addLayout(row5)

        # 行6:熟练度 top3 —— 单段紧凑 chip, expanding 模式自动均分一行宽度
        # v4.1.52 修: 创建时 prefix=空(原 prefix='堕落天使 72级' 导致未连接时残留显示
        # 「堕落天使 72级」+ val='--' 双段, 看起来像"有数据但未完整体"误导)
        # v4.1.54 修: 去掉 expanding/stretch, 与其他行 chip 一样靠左对齐(label 列右侧);
        # expanding 让 chip 拉伸到卡右半, 起点偏右与段位/资产/战绩行不对齐
        row6 = QHBoxLayout(); row6.setSpacing(CHIP_GAP)
        k = QLabel('熟练度'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row6.addWidget(k)
        self._chip_m1 = self._mk_rank_chip(*CHIP_MASTERY_1, '', '--')
        self._chip_m2 = self._mk_rank_chip(*CHIP_MASTERY_2, '', '--')
        self._chip_m3 = self._mk_rank_chip(*CHIP_MASTERY_3, '', '--')
        row6.addWidget(self._chip_m1)
        row6.addWidget(self._chip_m2)
        row6.addWidget(self._chip_m3)
        row6.addStretch()
        acl.addLayout(row6)

        # 行7:最近战绩 —— prefix='近10场'(分类标签), value='3胜8负'(数字)
        # v4.1.51 改: prefix='近10场 3胜8负' 双重数据, 与 val 重复; 拆 prefix=分类, val=数字
        row7 = QHBoxLayout(); row7.setSpacing(CHIP_GAP)
        k = QLabel('战绩'); k.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        k.setFixedWidth(LBL_W)
        row7.addWidget(k)
        self._chip_recent = self._mk_rank_chip(*CHIP_RECENT, '近10场', '--')
        row7.addWidget(self._chip_recent)
        row7.addStretch()
        acl.addLayout(row7)
        # 注:v37b 移到启动按钮下方, 此处不再 addWidget(延迟到 fab 后)
        self._acct_card = acct_card

        # 当前状态
        card2 = GlassCard()
        c2l = QVBoxLayout(card2)
        c2l.setContentsMargins(14, 12, 14, 12)
        c2l.setSpacing(8)
        t2 = QLabel('当前状态')
        t2.setStyleSheet(f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;color:{Glass.TEXT_SECONDARY};background:transparent;")
        c2l.addWidget(t2)
        self._phase_label = QLabel('等待启动')
        self._phase_label.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_LG};color:{Glass.TEXT_PRIMARY};background:transparent;font-weight:600;")
        c2l.addWidget(self._phase_label)
        lay.addWidget(card2)

        # 启动按钮（右对齐）
        # v4.1.45: 缩到 170x44, 原 200x50 + acct 实测 404 + spacing 12 = 616
        # > 内容区 564 → fab 起点 516+200=716 > 窗口 680 越界 36px
        self._fab = QPushButton('⚡  启动脚本')
        self._fab.setFixedSize(170, 44)
        self._fab.setCursor(Qt.PointingHandCursor)
        # v4.4.1(UI-A1): 样式表抽到模块级 _fab_qss(), 与 _do_run 共用同一份,
        # 字号统一 FONT_SIZE_XL(原先这里 14px、_do_run 里 16px, 点一下跳一号)。
        self._fab.setStyleSheet(_fab_qss(False))
        self._fab.clicked.connect(self._on_start)
        lay.addStretch()

        # v37c:底部横排 = 账号信息卡(左下) + 启动按钮(右下) 同处一行。
        # 此前账号卡整行塞在按钮下方会把按钮顶离底部, 现在按钮保持右下贴底不动。
        # v4.1.45: maxWidth 380 给 fab 让出空间; fab 缩 170x44 + 直接 addWidget
        # + AlignBottom | AlignRight 贴底(原 QVBoxLayout 包装会被 HBox 拉伸到同高)
        acct_card.setMaximumWidth(380)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(12)
        bottom.addWidget(acct_card)                 # 账号卡占左侧(可随空间伸缩)
        bottom.addStretch(1)                        # 弹性把按钮推到右侧
        # fab 170x44 fixed: HBox 不会拉伸, AlignBottom 让 fab 贴底
        bottom.addWidget(self._fab, 0, Qt.AlignBottom | Qt.AlignRight)
        bottom_w = QWidget()
        bottom_w.setLayout(bottom)
        bottom_w.setStyleSheet('background:transparent;')
        lay.addWidget(bottom_w)

        scroll.setWidget(content)
        return scroll

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

        lay.addWidget(self._make_delay_card('自动匹配', 'auto_queue', 'auto_queue_delay', 2, 0, 30, '进入房间后自动开始匹配'))
        lay.addWidget(self._make_delay_card('自动接受', 'auto_accept', 'auto_accept_delay', 1, 0, 15, '匹配成功后自动接受对局'))
        lay.addWidget(self._make_delay_card('自动返回', 'auto_play_again', 'auto_play_again_delay', 2, 0, 30, '游戏结束后自动返回房间'))

        # v34: 自动选英雄(进选人界面2-3 张卡时主动点选; 默认关; 打开时若非管理员则请求提权)
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        row, btn = _make_toggle_row('自动选英雄', 'auto_select_champion', self._settings, self._toggles, self._toggle)
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
        row, btn = _make_toggle_row('自动换英雄', 'auto_pick_champion', self._settings, self._toggles, self._toggle)
        cl.addWidget(row)
        desc = QLabel('按偏好英雄优先级自动切换，无偏好时不换')
        desc.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;padding-left:52px;")
        cl.addWidget(desc)
        lay.addWidget(card)

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

        lay.addStretch()
        scroll.setWidget(content)
        return scroll

    def _make_delay_card(self, label, toggle_key, delay_key, delay_def, dmin, dmax, desc):
        card = GlassCard()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(14, 12, 14, 12)
        cl.setSpacing(8)
        row = HoverRow()
        row.setStyleSheet('background:transparent;')
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(10)

        btn = AnimatedToggle()
        checked = self._settings.get(toggle_key, True)
        btn.setChecked(checked)
        btn.toggled.connect(lambda c, k=toggle_key: self._on_setting(k, c))
        self._toggles[toggle_key] = btn
        rl.addWidget(btn)

        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};color:{Glass.TEXT_PRIMARY};background:transparent;")
        lbl.setCursor(Qt.PointingHandCursor)
        lbl.mousePressEvent = lambda e, b=btn: b.toggle()
        rl.addWidget(lbl)
        rl.addStretch()

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
                background: rgba(99, 102, 241, 0.12);
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
                background: rgba(99, 102, 241, 0.22);
            }}
            QPushButton:pressed{{
                background: rgba(99, 102, 241, 0.30);
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
        head.addWidget(title)
        head.addStretch()
        head.addWidget(self._recent_sum_lbl)
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
        self._recent_empty = make_empty_state(
            '🗂', '暂无对局记录', '连接客户端后自动加载最近 10 场',
            min_height=140)
        self._recent_lay.addWidget(self._recent_empty)
        self._recent_lay.addStretch()
        scroll.setWidget(holder)
        lay.addWidget(scroll, 1)

        # 图标异步轮询(仅战绩页可见且有未加载图标时运行)
        self._recent_icon_timer = QTimer(self)
        self._recent_icon_timer.setInterval(900)
        self._recent_icon_timer.timeout.connect(self._tick_recent_icons)
        return page

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
                parent=self)
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
            games = list(games or [])
        except Exception:
            games = []
        # v4.3.10: 签名 = (game_id, 英雄名)。原来只比 game_id, 但英雄名是 main 侧
        # 探活时从 _champ_data 补上去的 —— 若第一轮探活在英雄数据就绪前完成, 名字
        # 会是占位符 "英雄#NNN", 之后名字补上了签名却没变 → 战绩页长期显示占位符。
        # main 侧换号时会把 _recent_sig 置 None, None != 任何 tuple, 仍然生效。
        sig = tuple((g.get('game_id'), (g.get('champion_name') or '').strip())
                    for g in games)
        if sig == self._recent_sig:
            return
        self._recent_sig = sig

        for w in self._recent_rows:
            try:
                w.setParent(None)
                w.deleteLater()
            except Exception:
                pass
        self._recent_rows = []

        if not games:
            if self._recent_empty is not None:
                self._recent_empty.setVisible(True)
            try:
                self._recent_sum_lbl.setText('')
            except Exception:
                pass
            return

        if self._recent_empty is not None:
            self._recent_empty.setVisible(False)

        wins = losses = 0
        for g in games:
            try:
                row = _MatchRow(g)
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
        self._recent_sum_lbl.setText(
            f'近 {len(self._recent_rows)} 场 · {wins}胜{losses}负')

        self._tick_recent_icons()

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
            if self._recent_dl_cb is not None:
                try:
                    self._recent_dl_cb(cid)
                except Exception:
                    pass
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

        clear_btn = QPushButton('清空日志')
        clear_btn.setFixedHeight(32)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(f"""
            QPushButton {{ background: rgba(99,102,241,0.08); color: {Glass.TEXT_SECONDARY}; border: none; border-radius: 6px; font-size: {Glass.FONT_SIZE_SM}; }}
            QPushButton:hover {{ background: rgba(99,102,241,0.18); color: {Glass.TEXT_PRIMARY}; }}
        """)
        clear_btn.clicked.connect(lambda: self._log_text.clear())
        lay.addWidget(clear_btn)

        return page

    # ==================== 关于页 ====================
    def _make_about_page(self):
        """v4.5.0: 版权 / 许可证 / 项目地址 / 第三方归属的唯一集中展示处。

        上游 sona 是无界面的注入插件, 本程序有界面 —— 按 AGPL-3.0 §0 对
        "Appropriate Legal Notices" 的定义(菜单式界面里"列表中的一个醒目项"即
        满足), 这里把许可、来源、免责声明一并摆出来, 属于最省事也最没有争议的做法。
        页面本身不含任何交互逻辑, 打开外链一律走 QDesktopServices(系统浏览器)。
        """
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + Glass.SCROLL_QSS)

        content = QWidget()
        content.setStyleSheet('background:transparent;')
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 16, 16, 16)
        lay.setSpacing(10)

        title = QLabel('关于')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:700;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        lay.addWidget(title)

        # ---- 卡片 1: 程序与版本 ----
        card1 = GlassCard()
        c1 = QVBoxLayout(card1)
        c1.setContentsMargins(14, 12, 14, 12)
        c1.setSpacing(6)
        name_lbl = QLabel('大蛋小助手')
        name_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_LG};font-weight:700;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        c1.addWidget(name_lbl)
        ver_lbl = QLabel(f'版本 v{APP_VERSION}')
        ver_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};color:{Glass.TEXT_SECONDARY};"
            f"background:transparent;")
        c1.addWidget(ver_lbl)
        desc_lbl = QLabel(
            '英雄联盟客户端辅助工具 —— 通过客户端本地 LCU 接口'
            '（REST + WebSocket）通信，不注入游戏进程、不修改游戏内存。')
        desc_lbl.setWordWrap(True)
        desc_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
            f"background:transparent;")
        c1.addWidget(desc_lbl)
        lay.addWidget(card1)

        # ---- 卡片 2: 开源许可 ----
        card2 = GlassCard()
        c2 = QVBoxLayout(card2)
        c2.setContentsMargins(14, 12, 14, 12)
        c2.setSpacing(6)
        c2.addWidget(self._about_heading('开源许可'))
        lic_lbl = QLabel(
            '本项目以 GNU Affero General Public License v3.0 or later'
            '（AGPL-3.0-or-later）发布。你可以自由使用、修改、再分发，'
            '但须保留版权与许可声明，并且任何基于本项目的衍生作品也必须以'
            '同一许可开放源代码。完整条款见仓库中的 LICENSE 文件。')
        lic_lbl.setWordWrap(True)
        lic_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_SECONDARY};"
            f"background:transparent;")
        c2.addWidget(lic_lbl)
        lay.addWidget(card2)

        # ---- 卡片 3: 项目主页 ----
        card3 = GlassCard()
        c3 = QVBoxLayout(card3)
        c3.setContentsMargins(14, 12, 14, 12)
        c3.setSpacing(6)
        c3.addWidget(self._about_heading('项目主页'))
        link_btn = QPushButton(PROJECT_REPO_URL)
        link_btn.setCursor(Qt.PointingHandCursor)
        link_btn.setStyleSheet(f"""
            QPushButton {{ background: rgba(99,102,241,0.10); color: {Glass.PRIMARY_HOVER};
                           border: none; border-radius: 6px; padding: 8px 10px;
                           font-size: {Glass.FONT_SIZE_SM}; text-align: left; }}
            QPushButton:hover {{ background: rgba(99,102,241,0.22); color: {Glass.TEXT_PRIMARY}; }}
        """)
        link_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(PROJECT_REPO_URL)))
        c3.addWidget(link_btn)
        if PROJECT_REPO_URL_PLACEHOLDER:
            hint = QLabel('（地址中仍是占位用户名，发布前请替换）')
            hint.setWordWrap(True)
            hint.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.WARNING};"
                f"background:transparent;")
            c3.addWidget(hint)
        src_lbl = QLabel('源码、问题反馈与更新都在该仓库获得。')
        src_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
            f"background:transparent;")
        c3.addWidget(src_lbl)
        lay.addWidget(card3)

        # ---- 卡片 4: 第三方归属 ----
        card4 = GlassCard()
        c4 = QVBoxLayout(card4)
        c4.setContentsMargins(14, 12, 14, 12)
        c4.setSpacing(6)
        c4.addWidget(self._about_heading('第三方组件与致谢'))
        for txt in (
            '功能设计参考并改写了 sona（https://github.com/WJZ-P/sona，AGPL-3.0）'
            '的若干功能实现，详见仓库中的 NOTICE.md 与 docs/PROVENANCE.md。',
            '界面基于 PyQt5（Riverbank Computing，GPL-3.0）构建，'
            '其余依赖清单与许可证见 THIRD_PARTY.md。',
            '英雄中文名、别名与拼音索引基于官方公开数据整理；'
            '英雄与物品图标由程序在运行时按需从官方 CDN 获取，不随本仓库分发。',
        ):
            t = QLabel(txt)
            t.setWordWrap(True)
            t.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_SECONDARY};"
                f"background:transparent;")
            c4.addWidget(t)
        lay.addWidget(card4)

        # ---- 卡片 5: 免责声明（Riot 要求的逐字声明 + 风险提示）----
        card5 = GlassCard()
        c5 = QVBoxLayout(card5)
        c5.setContentsMargins(14, 12, 14, 12)
        c5.setSpacing(6)
        c5.addWidget(self._about_heading('免责声明'))
        disco = QLabel(
            '大蛋小助手 isn\'t endorsed by Riot Games and doesn\'t reflect the '
            'views or opinions of Riot Games or anyone officially involved in '
            'producing or managing Riot Games properties. Riot Games, and all '
            'associated properties are trademarks or registered trademarks of '
            'Riot Games, Inc.')
        disco.setWordWrap(True)
        disco.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};"
            f"background:transparent;")
        c5.addWidget(disco)
        risk = QLabel(
            '本程序为非官方第三方工具，与 Riot Games、腾讯及其关联公司无任何关系。'
            '使用自动化工具可能违反游戏用户协议，并可能导致账号受到处罚，'
            '请自行评估风险，作者不承担由此产生的任何后果。')
        risk.setWordWrap(True)
        risk.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.WARNING};"
            f"background:transparent;")
        c5.addWidget(risk)
        lay.addWidget(card5)

        lay.addStretch()
        scroll.setWidget(content)
        return scroll

    def _about_heading(self, text):
        """「关于」页卡片内的小标题(统一样式, 避免每张卡片各写一份)。"""
        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};font-weight:700;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        return lbl

    @ui_slot
    def _on_start(self):
        if self._running:
            self.stop_script_signal.emit()
        else:
            self.start_script_signal.emit()

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
                b.setChecked(value)
        self._save_settings()
        self.settings_changed_signal.emit(self._settings)

    # ==================== 外部接口 ====================
    def update_connection_status(self, connected, name=''):
        self.sig_update_connection.emit(connected, name)

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
        """
        try:
            self._recent_sig = None
        except Exception as e:
            print(f'[ui_slot] MainWindow.mark_recent_dirty 异常已被拦下: '
                  f'{type(e).__name__}: {e}')

    def persist_setting(self, key: str, value) -> None:
        """v4.4.0 修复(P1-24): 写值 → 落盘 → 同步界面开关状态。

        main.py:2148-2149 原来是 `_main._settings['auto_select_champion'] = False`
        紧跟 `_main._save_settings()`, 两次伸进私有成员。这里合并成一个公开方法:
        先写 self._settings[key], 再复用 _save_settings()(已改原子写),
        并同步 sync_setting 的界面状态(toggle 按钮)。
        """
        try:
            self._settings[key] = value
            self._save_settings()
            self.sync_setting(key, value)
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
        if key in self._toggles:
            btn = self._toggles[key]
            if btn.isChecked() != value:
                btn.setChecked(value)
        self._settings[key] = value

    def init_champion_picker(self, champ_data, preferred, icon_cb, download_cb=None, **kw):
        if self._champion_picker is None:
            from .champion_picker import ChampionPickerWidget
            self._champion_picker = ChampionPickerWidget(
                champ_data, preferred, icon_cb, self, download_cb=download_cb, **kw
            )
            self._champion_picker.champions_selected.connect(self._on_picker_saved)
            if self._picker_placeholder:
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
    def _mk_rank_chip(self, color, bg, label, default_value,
                       size_policy_expanding=False):
        """段位/荣誉标签-值双段胶囊(带前缀 label,如「单双 白银 I·60胜点」)。

        v4.1.46 改: 数据态 vs 等待态分两种 style, **chip 默认 visible 不隐藏**。
        v4.1.48 增: size_policy_expanding=True 让 chip 在 QHBoxLayout 中按 stretch
        自动均分可用宽度(用于熟练度三 chip 等一行放不下的场景)。
        原 set_default 用 setVisible(False) 会让整行塌陷, 未连接时账号卡
        比连接时矮 24px, 顶部连接/未连接状态卡 + fab 高度都不变, 看起来
        不协调。现在让两种状态行高一致, 仅 chip 背景透明度区分:
        数据态  bg=原色(0.16)  text=原色
        等待态  bg=rgba(255,255,255,0.04)  text=TEXT_MUTED
        """
        wrap = QWidget()
        wrap.setStyleSheet('background:transparent;')
        h = QHBoxLayout(wrap)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        # v4.1.48: expanding 模式下让 chip 拉伸填满, 文本居中
        # v4.1.50: prefix 文字跟随 chip 主色(蓝 chip 的「蓝精粹」也染蓝、橙 chip 的
        # 「橙精粹」也染橙), 与 value 视觉统一; 等待态弱化为 TEXT_MUTED。
        prefix = QLabel(label)
        prefix.setStyleSheet(
            f"font-size:{CHIP_FONT_PX}px;color:{color};background:transparent;")
        val = QLabel(default_value)
        val.setAlignment(Qt.AlignCenter)
        # 初始为等待态:弱背景 + 弱文字
        val.setStyleSheet(
            f"background:{CHIP_WAIT_BG};color:{Glass.TEXT_MUTED};"
            f"border-radius:{CHIP_RADIUS_PX}px;padding:{CHIP_PAD};"
            f"font-size:{CHIP_FONT_PX}px;font-weight:600;")
        h.addWidget(prefix)
        h.addWidget(val)
        # v4.1.48: expanding 模式下设 sizePolicy 让 chip 按 stretch 自动填宽
        if size_policy_expanding:
            from PyQt5.QtWidgets import QSizePolicy
            wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # v4.1.46: chip 默认 visible, 行高稳定
        wrap.setVisible(True)
        wrap._prefix = prefix
        wrap._value = val
        wrap._color = color
        wrap._bg = bg

        # v4.4.0 修复(P1-18): 脏值比较 —— 账号卡每 3 秒刷新一次, 10 个 chip 的
        # set_value/set_default 会把「文本没变、样式没变」的标签也重写一遍样式
        # (每次 setStyleSheet 都要 QSS 解析 + style polish + 重排)。记住上次
        # 的 (text, data_mode), 完全相同时直接 return。
        _last = {'key': None}

        def _apply_value(text, data_mode):
            """data_mode=True 用原色背景, False 用等待态弱背景。"""
            key = (text, bool(data_mode))
            if key == _last['key']:
                return
            _last['key'] = key
            if data_mode:
                bg_use = wrap._bg
                color_use = wrap._color
            else:
                bg_use = CHIP_WAIT_BG
                color_use = Glass.TEXT_MUTED
            val.setText(text)
            # v4.1.48: chip 字号缩到 10px, padding 2px 7px, 让熟练度三个 chip 在
            # 380 宽账号卡内可放下不溢出.
            val.setStyleSheet(
                f"background:{bg_use};color:{color_use};"
                f"border-radius:{CHIP_RADIUS_PX}px;"
                f"padding:{CHIP_PAD};font-size:{CHIP_FONT_PX}px;"
                f"font-weight:600;")
            # v4.1.50: prefix 跟随状态切换颜色, 数据态亮、等待态弱
            prefix.setStyleSheet(
                f"font-size:{CHIP_FONT_PX}px;color:{color_use};"
                f"background:transparent;font-weight:600;")
            wrap.setVisible(True)

        wrap.set_value = lambda v: _apply_value(v, True)
        wrap.set_default = lambda: _apply_value(default_value, False)
        return wrap

    def update_account_info(self, info):
        """主线程调用(连接探活数据/断线重置)。info=None → 等待态。

        全部字段容错:任一缺失只让对应控件保持默认值,不抛异常。
        v37 用 _label/_value 双控件:label 显示列名(单双/灵活),value 显示段位。
        """
        if not info:
            self._acct_id_lbl.setText('--')
            self._acct_id_lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_LG};font-weight:600;"
                f"color:{Glass.TEXT_MUTED};background:transparent;")
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
            self._acct_id_lbl.setText(name)
            self._acct_id_lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_LG};font-weight:600;"
                f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        # 行2:等级 + 经验
        level = int(info.get('level') or 0)
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
        solo_w = int(info.get('solo_wins') or 0)
        solo_l = int(info.get('solo_losses') or 0)
        flex_w = int(info.get('flex_wins') or 0)
        flex_l = int(info.get('flex_losses') or 0)
        if solo_txt and (solo_w or solo_l):
            solo_txt = f'{solo_txt} {solo_w}胜{solo_l}负'
        if flex_txt and (flex_w or flex_l):
            flex_txt = f'{flex_txt} {flex_w}胜{flex_l}负'
        self._chip_solo.set_value(solo_txt or '未定级')
        self._chip_flex.set_value(flex_txt or '未定级')
        # 行4:资产 = 蓝精粹 / 橙精粹
        wallet_ip = int(info.get('wallet_ip') or 0)
        wallet_cos = int(info.get('wallet_cosmetic') or 0)
        if wallet_ip > 0:
            self._chip_wallet_ip.set_value(f'{wallet_ip:,}')
        else:
            self._chip_wallet_ip.set_default()
        if wallet_cos > 0:
            self._chip_wallet_cosmetic.set_value(f'{wallet_cos:,}')
        else:
            self._chip_wallet_cosmetic.set_default()
        # 行5:资产(续) 神话精粹 / 荣誉
        wallet_my = int(info.get('wallet_mythic') or 0)
        honor = int(info.get('honor') or 0)
        if wallet_my > 0:
            self._chip_wallet_mythic.set_value(f'{wallet_my:,}')
        else:
            self._chip_wallet_mythic.set_default()
        if honor > 0:
            self._chip_honor.set_value(str(honor))
        else:
            self._chip_honor.set_default()
        # 行6:熟练度 top3 —— v4.1.48 单段紧凑 chip(prefix 留空, 整个文本进 value)
        mt = info.get('mastery_top') or []
        chips = (self._chip_m1, self._chip_m2, self._chip_m3)
        for i, chip in enumerate(chips):
            if i < len(mt):
                m = mt[i]
                nm = m.get('name') or f"#{m.get('champion_id')}"
                lv = int(m.get('level') or 0)
                # v4.1.49: 去掉 grade, 紧凑单段 = "英雄名 等级级", 与游戏"英雄成就"页一致
                if lv > 0:
                    text = f'{nm} {lv}级'
                else:
                    text = nm
                # prefix 永远留空(本行 chip 已是单段), 避免重复显示
                chip._prefix.setText('')
                chip.set_value(text)
            else:
                chip._prefix.setText('')
                chip.set_default()
        # 行6:最近战绩
        recent = info.get('recent')
        if recent and recent.get('total', 0) > 0:
            w = recent.get('wins', 0)
            l = recent.get('losses', 0)
            self._chip_recent.set_value(f'{w}胜{l}负')
        else:
            self._chip_recent.set_default()
        # v4.1.66: 同批数据驱动战绩页(签名去重, 内容没变不重建)
        try:
            self.update_recent((recent or {}).get('games'))
        except Exception as e:
            print(f'[战绩] 页面刷新失败: {type(e).__name__}: {e}')
        # 头像:真头像已上屏(icon 匹配)时探活刷新不覆盖;未就绪/换头像才显示
        # v4.1.39 生成式占位等下载完成替换 —— 修复"真头像渲染后 3s 被占位盖掉"
        cur_icon = int(info.get('icon_id') or 0)
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
            self._conn_label.setText(CONN_TEXT_ONLINE if connected
                                     else CONN_TEXT_OFFLINE)
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
            self._dot_timer = QTimer()
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
                self._fab.setText('⏹  停止脚本')
                # v4.4.1(UI-A1): 与构造时共用 _fab_qss(), 消除双份拷贝
                # (顺便把硬编的 #dc2626/#b91c1c/#991b1b 收进 Glass.STOP_RED*)
                self._fab.setStyleSheet(_fab_qss(True))
            else:
                self._fab.setText('⚡  启动脚本')
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
            'auto_select_champion': False,   # v34: 进选人自动选英雄(默认关)
            'auto_pick_champion': True,      # 锁后自动换 bench(默认开, 保留旧行为)
            'preferred_champions': [],       # v41: 移除死配置 'queue_id', 队列从 session 取
            'agreement_accepted': False,     # v4.1.55: 首次启动强制阅读须知(30s)后置 True
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
        """
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
            os.makedirs(os.path.dirname(path), exist_ok=True)
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
        return os.path.join(os.environ.get('APPDATA', '~'), 'LOLScript', 'settings.json')
