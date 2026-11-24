"""Overlay Window - 液态玻璃风格小窗 v12

v12 改动（用户明确诉求：只动日志区，不要改"上面"区域）：
- 备选英雄头像宫格已回 v10 完整形态（4×N ChampionIcon + PulseGlow + ScrollArea）
- mini_log 4 行 → 5 行（用户明确指定）
- 删 v11 临时加入的"偏好/当前/即将切换"三行 widget 与对应 API
- 窗口尺寸回 v10 的 300×420（容下备选宫格）

特性：
- 霓虹发光描边
- Windows 系统级 Acrylic 模糊
- 统一圆角和配色
- 与主窗口视觉风格一致
"""

from typing import Dict
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QGridLayout
)
from PyQt5.QtCore import (Qt, pyqtSignal, QSize, QTimer, QPropertyAnimation,
                          QEasingCurve, QRectF)
from PyQt5.QtGui import QColor, QIcon, QPainter

from .styles import Glass
from .icons import AnimatedToggle, make_title_buttons, icon_pixmap
from .animated_widgets import (_draw_glow_border, _draw_top_highlight,
                               paint_window_backdrop, PulseGlow, HoverRow,
                               StatusDot)
# v4.4.0 修复(P1-15/P1-21/P2): 多屏安全定位 + 玻璃效果去重 + 拖拽 (0,0) 判定
from . import screen_utils
from .window_base import DragMixin, GlassEffectMixin, ui_slot


# v4.4.0 修复(P2 硬编码): 小窗内几处魔数/文案提到具名常量, 只抽常量不改文案。
CHAMP_ICON_PX = 46          # 备选英雄格子里图标显示尺寸(原写死 .scaled(46,46))
MINI_LOG_ROWS = 5           # v12 起 5 行
MINI_LOG_LINE_H = 14        # 一行 11px 字的行高(含行距)
MINI_LOG_PAD = 6            # 上下 padding 合计 → 5*14+6 = 76
MINI_LOG_HEIGHT = MINI_LOG_ROWS * MINI_LOG_LINE_H + MINI_LOG_PAD
MINI_LOG_MAX_CHARS = 36     # 超出则截断加省略号
MINI_LOG_KEEP_CHARS = 33    # 截断后保留尾部字符数(36 = len('...') + 33)
MINI_LOG_ELLIPSIS = '...'
# v4.4.0 修复(P2): 原来用中文字符串 `'等待' in clean or '启动' in clean` 做分支,
# 文案一改逻辑就悄悄失效(且没人会想到去改这里)。抽成具名状态词表。
DOTS_TRIGGER_WORDS = ('等待', '启动')
STATUS_EMOJI_STRIP = ('🔄', '🎯', '⚔️', '🎮', '🔁', '🎲',
                      '⏳', '✅', '❌', '💤', '🏠', '🏁')

# v4.4.0 修复(P1-15): 小窗初始落点的边距(逻辑像素, 相对所在屏的 availableGeometry)。
# 原值 30/120 硬编码在 primaryScreen().geometry() 上。
OVERLAY_MARGIN_RIGHT = 30
OVERLAY_MARGIN_BOTTOM = 120

# v4.4.1(UI-B5): 连接三态 → 颜色/默认文案。
# 'connecting' = 正在探测/连客户端(琥珀), 与"断开"(红)是不同语义;
# 见 ui/styles.py 的 Glass.CONNECTING 注释。
CONN_STATE_COLORS = {
    'connected': Glass.ACCENT,
    'connecting': Glass.CONNECTING,
    'disconnected': Glass.DANGER,
}
CONN_STATE_TEXTS = {
    'connected': '已连接',
    'connecting': '连接中',
    'disconnected': '未连接',
}


def _run_btn_qss(bg, fg, border, hover_bg, hover_fg, hover_border):
    """启动/停止按钮 QSS 模板(单色底)。"""
    return f"""
        QPushButton {{
            background: {bg}; color: {fg};
            border: 1px solid {border}; border-radius: {Glass.RADIUS_SM};
            font-size: {Glass.FONT_SIZE_SM}; font-weight: 600;
        }}
        QPushButton:hover {{ background: {hover_bg}; color: {hover_fg}; border-color: {hover_border}; }}
        QPushButton:pressed {{ padding-top: 1px; }}
    """


def _pill_btn_qss(bg, bg2, hover_bg, hover_bg2, pressed):
    """渐变 pill 按钮(接受/拒绝)QSS 模板。

    v4.4.1(UI-A2): 原为 `stop:0 {hover_bg}, stop:0 {bg}` —— 两个 stop 都是 0,
    第二个覆盖第一个, 渐变退化成纯色且 hover 几乎无变化。现为正常的
    stop:0/stop:1 两档。
    """
    return f"""
        QPushButton {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {bg}, stop:1 {bg2});
            color: white; border: none; border-radius: {Glass.RADIUS_SM};
            font-size: {Glass.FONT_SIZE_SM}; font-weight: 600;
        }}
        QPushButton:hover {{
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {hover_bg}, stop:1 {hover_bg2});
        }}
        QPushButton:pressed {{ background: {pressed}; padding-top: 1px; }}
    """


class _OverlayGlassContainer(QWidget):
    """小窗容器 - 玻璃背景(半透明着色, 底层由系统Acrylic模糊)"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('glass_overlay')
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def paintEvent(self, event):
        p = QPainter(self)
        paint_window_backdrop(p, self.width(), self.height())
        p.end()


class ReadyCheckPanel(QWidget):
    """ReadyCheck 控制面板（确认卡，可反悔）

    v5.0.x 重构(用户要求): 原"15 秒倒计时卡"是假倒计时 —— 本地 QTimer 从硬编码
    15 倒数, 与 core 真实接受延迟(auto_accept_delay)脱钩, 于是出现"已接受对局! 却
    还显示 12 秒后自动接受"。现改为纯确认卡:
      · 不自己倒数(真实倒计时由顶栏"⏳ 接受倒计时 N..."显示);
      · 接受/拒绝按钮在 ReadyCheck 期间常驻, 随时可反悔;
      · 响应状态由 core 经 sig_ready_response 回推, 不再本地 1.5s 后自动收起。
    """
    accept_clicked = pyqtSignal()
    decline_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._visible = False
        self._response = 'None'
        self._completed = False
        self._init_ui()

    @property
    def is_completed(self) -> bool:
        """本机已给出响应(接受/拒绝)。_do_ready 用它在轮询重推时阻止重弹。"""
        return self._completed

    def _init_ui(self):
        self.setVisible(False)
        self.setFixedHeight(52)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 4, 12, 4)
        lay.setSpacing(8)

        left = QVBoxLayout()
        left.setSpacing(1)
        self._state_label = QLabel('已找到对局')
        self._state_label.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;color:{Glass.TEXT_PRIMARY};background:transparent;")
        left.addWidget(self._state_label)
        self._countdown_label = QLabel('点击接受/拒绝，可随时反悔')
        self._countdown_label.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        left.addWidget(self._countdown_label)
        lay.addLayout(left, 1)

        self._accept_btn = QPushButton('接受')
        self._accept_btn.setFixedSize(56, 26)
        self._accept_btn.setCursor(Qt.PointingHandCursor)
        self._accept_btn.setStyleSheet(_pill_btn_qss(
            Glass.ACCENT, Glass.ACCENT_DARK,
            Glass.ACCENT_HOVER, Glass.ACCENT, Glass.ACCENT_PRESSED))
        self._accept_btn.clicked.connect(self._on_accept)
        lay.addWidget(self._accept_btn)

        self._decline_btn = QPushButton('拒绝')
        self._decline_btn.setFixedSize(56, 26)
        self._decline_btn.setCursor(Qt.PointingHandCursor)
        self._decline_btn.setStyleSheet(_pill_btn_qss(
            Glass.DANGER, Glass.DANGER_DARK,
            Glass.DANGER_HOVER, Glass.DANGER, Glass.DANGER_PRESSED))
        self._decline_btn.clicked.connect(self._on_decline)
        lay.addWidget(self._decline_btn)

    @ui_slot
    def _on_accept(self):
        self._response = 'Accepted'
        self._completed = True
        self._update_ui()
        self.accept_clicked.emit()

    @ui_slot
    def _on_decline(self):
        self._response = 'Declined'
        self._completed = True
        self._update_ui()
        self.decline_clicked.emit()

    def _set_hint_text(self, text, tone='muted'):
        """提示行文案; 仅在色调变化时重写样式表(与旧 _set_countdown_text 同纪律)。"""
        self._countdown_label.setText(text)
        color = {'muted': Glass.TEXT_MUTED,
                 'accent': Glass.ACCENT,
                 'danger': Glass.DANGER}.get(tone, Glass.TEXT_MUTED)
        if color != getattr(self, '_hint_color', None):
            self._hint_color = color
            self._countdown_label.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{color};background:transparent;")

    def _update_ui(self):
        # v5.0.x: 面板在确认窗口内常驻, 可反悔 —— 已接受仍可点拒绝、已拒绝仍可点接受。
        # 已选择的动作按钮禁用, 另一个保持可点。
        if self._response == 'Accepted':
            self._state_label.setText('已接受')
            self._state_label.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;color:{Glass.ACCENT};background:transparent;")
            self._accept_btn.setEnabled(False)
            self._decline_btn.setEnabled(True)
            self._countdown_label.setVisible(True)
            self._set_hint_text('已接受，仍可点「拒绝」反悔', tone='accent')
        elif self._response == 'Declined':
            self._state_label.setText('已拒绝')
            self._state_label.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;color:{Glass.DANGER};background:transparent;")
            self._decline_btn.setEnabled(False)
            self._accept_btn.setEnabled(True)
            self._countdown_label.setVisible(True)
            self._set_hint_text('已拒绝，仍可点「接受」反悔', tone='danger')
        else:
            self._state_label.setText('已找到对局')
            self._state_label.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;color:{Glass.TEXT_PRIMARY};background:transparent;")
            self._accept_btn.setEnabled(True)
            self._decline_btn.setEnabled(True)
            self._countdown_label.setVisible(True)
            self._set_hint_text('点击接受/拒绝，可随时反悔', tone='muted')

    def show_check(self, countdown=None):
        # v5.0.x: countdown 参数保留仅为兼容旧调用(_do_ready 传 15), 不再使用 ——
        # 面板不做本地假倒计时, 真实倒计时由顶栏"⏳ 接受倒计时 N..."显示。
        if self._completed:
            return
        self._visible = True
        self._response = 'None'
        self._update_ui()
        self._accept_btn.setEnabled(True)
        self._decline_btn.setEnabled(True)
        self._countdown_label.setVisible(True)
        self.setVisible(True)

    def hide_check(self):
        self._visible = False
        self.setVisible(False)

    def reset_check(self):
        self._completed = False
        self._response = 'None'
        self._visible = False
        self.setVisible(False)

    def pause_timers(self):
        """v5.0.x: 面板不再持有任何定时器, 保留空实现供 _pause_timers 调用。"""
        pass

    def resume_timers(self):
        """v5.0.x: 同上, 空实现。"""
        pass

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = Glass.RADIUS_CARD  # 10

        # 半透明玻璃背景
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(16, 18, 28, 140))
        p.drawRoundedRect(0, 0, w, h, r, r)

        # 顶部折射高光
        _draw_top_highlight(p, w, h, r, intensity=3)

        p.end()

    @ui_slot
    def set_response(self, response):
        self._response = response
        if response in ('Accepted', 'Declined'):
            self._completed = True
        self._update_ui()
        # v5.0.x: 不再 1.5s 后自动收起 —— 面板保持到 ReadyCheck 结束, 方便反悔。


class ChampionIcon(QPushButton):
    """小窗英雄头像按钮"""
    clicked_id = pyqtSignal(int)

    def __init__(self, cid, parent=None):
        super().__init__(parent)
        self.cid = cid
        self._is_selected = False
        self._is_preinput = False
        self.setFixedSize(52, 52)
        self.setCursor(Qt.PointingHandCursor)
        # v17:不设 tooltip —— PyQt5 默认悬停 tooltip 是系统黑框,观感突兀
        self._apply_normal()
        self.clicked.connect(lambda: self.clicked_id.emit(cid))

    @property
    def is_selected(self) -> bool:
        """v4.4.2(P2-7): _is_selected 的只读公开入口 —— 原来外部直读私有字段
        (_refresh_preferred_highlight 里 icon._is_selected), 跨文件读私有属性
        破坏封装且 grep 困难。"""
        return self._is_selected

    @property
    def is_preinput(self) -> bool:
        """v4.9.13:是否处于预输入(蓝光)态。"""
        return self._is_preinput

    def _apply_normal(self):
        self.setStyleSheet(f"""
            QPushButton {{
                background: rgba(18, 20, 30, 130);
                border: 1px solid rgba(99, 102, 241, 0.10);
                border-radius: {Glass.RADIUS_BTN};
            }}
            QPushButton:hover {{
                background: rgba(99, 102, 241, 0.08);
                border-color: rgba(99, 102, 241, 0.25);
            }}
            QPushButton:pressed {{ padding-top: 1px; }}
        """)

    def set_selected(self, sel, is_preferred: bool = False, is_preinput: bool = False):
        """v13:同时管选中态与偏好态; v4.9.13:再加预输入态。

        视觉优先级: 预输入(蓝) > 当前选中(靛) > 偏好(绿) > 普通。
        为什么预输入排最前: 它是用户**手动点出来**的本局目标, 而"选中"只是
        客户端当前恰好锁着谁 —— 排后面的话, 用户刚点亮蓝光的那个头像会被
        "当前英雄"的靛框盖住, 看起来像没点上。
        """
        self._is_selected = sel
        self._is_preinput = is_preinput
        if is_preinput:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {Glass.PREINPUT_SUBTLE};
                    border: 2px solid {Glass.PREINPUT_BORDER};
                    border-radius: {Glass.RADIUS_BTN};
                }}
                QPushButton:hover {{
                    background: {Glass.PREINPUT_SOFT};
                    border-color: {Glass.PREINPUT_BORDER_HOVER};
                }}
            """)
        elif sel:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(99, 102, 241, 0.12);
                    border: 1px solid rgba(99, 102, 241, 0.35);
                    border-radius: {Glass.RADIUS_BTN};
                }}
                QPushButton:hover {{
                    background: rgba(99, 102, 241, 0.18);
                }}
            """)
        elif is_preferred:
            # v4.4.1(UI-A6): 原来硬编 rgba(34,197,94,*)(=#22c55e), 与 Glass.ACCENT
            # (#34d399) 是两个不同的绿, 同屏出现时偏色。改用 ACCENT 派生的令牌。
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {Glass.ACCENT_SUBTLE};
                    border: 2px solid {Glass.ACCENT_BORDER};
                    border-radius: {Glass.RADIUS_BTN};
                }}
                QPushButton:hover {{
                    background: {Glass.ACCENT_SOFT};
                    border-color: {Glass.ACCENT_BORDER_HOVER};
                }}
            """)
        else:
            self._apply_normal()

    def set_icon_data(self, data: bytes):
        # v4.4.0 修复(P1-16/P1-17): 原来 QPixmap().loadFromData + scaled(46,46)
        # 每次解码 + 平滑缩放; 而且 46 是逻辑像素, 125%/150% 缩放下被 Qt 放大成
        # 模糊位图。改走 DPR 感知 + LRU 缓存的工厂(同一份英雄图标字节重复到达
        # 时直接命中缓存)。
        self.setIcon(QIcon(icon_pixmap(data, CHAMP_ICON_PX, widget=self)))
        self.setIconSize(QSize(CHAMP_ICON_PX, CHAMP_ICON_PX))


class OverlayWindow(DragMixin, GlassEffectMixin, QWidget):
    """液态玻璃风格小窗 v12

    v12 还原 v10 的核心 UI（备选英雄头像宫格 + 5 行 mini_log），
    仅在 main.py 端加严日志过滤（[WS]/[Core]/code= 等监控类不再刷小窗）。
    """
    swap_signal = pyqtSignal(int)
    accept_signal = pyqtSignal()
    decline_signal = pyqtSignal()
    # v4.6.11 说明: stop_signal **全项目从未 emit**(小窗的"运行/停止"走下面的
    # toggle_script_signal)。main.py 里那条 connect 已删除, 避免"已连未发"误导
    # 排查方向。信号声明保留 —— 它是公开 API, 将来做独立的"停止"入口可直接用。
    stop_signal = pyqtSignal()
    toggle_script_signal = pyqtSignal()
    show_main_signal = pyqtSignal()
    close_signal = pyqtSignal()
    toggle_setting_signal = pyqtSignal(str, bool)

    sig_conn = pyqtSignal(bool)
    sig_status = pyqtSignal(str)
    sig_conn_top = pyqtSignal(bool, str)  # 连接状态顶部指示器
    # v4.4.1(UI-B5): 三态连接指示器('connected'/'connecting'/'disconnected', 文案)。
    # 原 sig_conn_top 只有 bool, 于是"正在扫客户端"与"客户端没开"渲染完全相同。
    # 新信号走同一条 _do_conn_state, 旧的 sig_conn_top 保留并转调, 向后兼容。
    sig_conn_state = pyqtSignal(str, str)
    sig_champ_name = pyqtSignal(str)
    sig_bench = pyqtSignal(list, object)
    sig_icon = pyqtSignal(int, object)
    sig_clear = pyqtSignal()
    sig_ready = pyqtSignal(bool)
    sig_ready_response = pyqtSignal(str)
    sig_show = pyqtSignal()
    sig_hide = pyqtSignal()
    sig_live_status = pyqtSignal(str, str)
    sig_mini_log = pyqtSignal(str)
    # v4.9.13: 用户单击备选英雄 → 请求切换预输入。携带 cid(int)。
    # 小窗**不自己记账**, 只 emit + 等 main 回推 set_preinput();
    # 否则"界面已变蓝、脚本没收到"这种漂移会很难查。
    sig_preinput = pyqtSignal(int)
    # v4.9.14: core 回推"当前预输入英雄"状态 → 主线程更新蓝光。
    # 为什么必须是信号: _on_bench 在 core 的 poll/WS **后台线程**里跑, 若像
    # 上一版那样直调 set_preinput(), 会从非 GUI 线程遍历 _icons 并 setStyleSheet
    # (违反 Qt 线程模型, 且与主线程 _do_bench 的 icons.pop 并发会抛
    # "dictionary changed size during iteration")。走 pyqtSignal, Qt 会按接收者
    # (主线程)亲和自动选 QueuedConnection, 与 update_bench_champions 同构。
    sig_preinput_state = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._icons: Dict[int, ChampionIcon] = {}
        # v4.x(P3): _champ_data 死数据流已删 —— ChampionIcon 的 name 参数从不
        # 被使用(只存 cid), set_champion_data 保留为兼容空操作(见下方注释)。
        self._settings = {}
        # v13 优化项 1:偏好英雄集合(用于头像描边高亮)
        self._preferred: set = set()
        # v4.9.13: 预输入英雄 id(0=无)。由 main 从 core 回推, 小窗只读。
        self._preinput_cid = 0
        # S3:顶部 _live_dot 只表达"连接态"; _live_text 由连接态**权威写入**
        # (三态文案), 脚本运行态只在连接态之外的间隙显示 —— 见 _do_conn_state,
        # 该行为有回归测试钉死, 勿拆走。
        # v4.4.1(UI-B5): 由 bool 改为三态字符串('connected'/'connecting'/'disconnected')
        # —— "应用刚起来正在扫客户端"和"客户端根本没开"原来渲染得一模一样。
        # v4.4.0 修复(P2 拖拽/P1-21 玻璃): _drag_pos 必须显式 None
        # (QPoint(0,0) 为假 → 从窗口左上角拖不动); 玻璃效果走脏标志去重。
        self._init_drag()
        self._init_glass()
        self._init_ui()
        self._connect()

    def _init_ui(self):
        self.setWindowTitle('大蛋小助手')
        # 不用 Qt.Tool（Qt.Tool 会阻止 DWM Acrylic/系统Backdrop），
        # 改为普通无边框窗口 + WS_EX_TOOLWINDOW 隐藏任务栏
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        # v4.4.2(P2-4): 小窗是挂机悬浮层, 不该抢游戏焦点。原 _do_show_animated 里
        # 显式 activateWindow() 会在每次显示(对局开始)时把焦点从游戏/客户端抢过来。
        # 设 WA_ShowWithoutActivating: show() 不再激活窗口, 但鼠标仍可正常点按。
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet('background:transparent;')
        # v12:恢复 v10 的 300×420（容下备选宫格）
        self.setFixedSize(300, 420)

        # v4.4.0 修复(P1-15): 原来 `QApplication.primaryScreen().geometry()` 拿的是
        # **设备像素**的整屏矩形, 而 move() 吃的是**逻辑像素**, 二者在 125%/150%
        # 缩放下差 1.25/1.5 倍 → 窗口跑到屏幕外; 且偏移写死主屏, 副屏(尤其副屏
        # 在主屏左侧、坐标为负)必然跑偏。改走 screen_utils 的逻辑坐标
        # availableGeometry(避开任务栏), 由当前屏幕决定落点。
        screen_utils.place_bottom_right(self, margin_right=OVERLAY_MARGIN_RIGHT,
                                        margin_bottom=OVERLAY_MARGIN_BOTTOM)

        container = _OverlayGlassContainer()
        self._container = container
        container.setStyleSheet('background:transparent;')

        ml = QVBoxLayout(container)
        ml.setContentsMargins(0, 0, 0, 0)
        ml.setSpacing(0)

        # 标题栏
        ml.addWidget(self._make_title())
        # 开关栏
        ml.addWidget(self._make_toggles())
        # 实时状态
        ml.addWidget(self._make_live_status())
        # ReadyCheck
        self._ready_panel = ReadyCheckPanel()
        self._ready_panel.accept_clicked.connect(
            lambda: (self.accept_signal.emit(), self._ready_panel.set_response('Accepted')))
        self._ready_panel.decline_clicked.connect(
            lambda: (self.decline_signal.emit(), self._ready_panel.set_response('Declined')))
        ml.addWidget(self._ready_panel)
        # 备选英雄区
        ml.addWidget(self._make_champ_area(), 1)
        # 最近日志（v12：5 行）
        ml.addWidget(self._make_mini_log())
        # 底部
        ml.addWidget(self._make_bottom())

        self.setLayout(QVBoxLayout())
        self.layout().addWidget(container)
        self.layout().setContentsMargins(0, 0, 0, 0)

        # 延迟应用 Windows 液态玻璃效果
        QTimer.singleShot(200, self._apply_glass_effect)

    def _hide_from_taskbar(self):
        """用 WS_EX_TOOLWINDOW 隐藏任务栏图标(不再依赖 Qt.Tool)"""
        try:
            import ctypes
            hwnd = int(self.winId())
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            user32 = ctypes.windll.user32
            # v4.x(P3): 64 位下 Get/SetWindowLongW 是 32 位截断版本, 按指针位宽
            # 选择 Ptr 版本, 避免高 32 位被丢弃。
            if ctypes.sizeof(ctypes.c_void_p) == 8:
                get_style = user32.GetWindowLongPtrW
                set_style = user32.SetWindowLongPtrW
            else:
                get_style = user32.GetWindowLongW
                set_style = user32.SetWindowLongW
            set_style.argtypes = (ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong)
            set_style.restype = ctypes.c_longlong
            get_style.argtypes = (ctypes.c_void_p, ctypes.c_int)
            get_style.restype = ctypes.c_longlong
            style = get_style(hwnd, GWL_EXSTYLE)
            set_style(hwnd, GWL_EXSTYLE, style | WS_EX_TOOLWINDOW)
        except Exception as e:
            # v4.4.2(P2-20): 原 `except Exception: pass` 静默吞 —— 失败时任务栏
            # 会多出一个图标(用户可见)却无任何日志。补打印, 便于定位。
            print(f'[Overlay] 隐藏任务栏按钮失败: {type(e).__name__}: {e}')

    # v4.6.11 删除 _apply_glass_effect 的空覆写: 原先它只写了一句
    # `super()._apply_glass_effect()` —— 基类已提供完全相同的调度(可见性+
    # 几何去重), 覆写没有任何附加行为, 属纯噪音。

    def _glass_apply_core(self):
        """真正应用系统 Acrylic 模糊 + 系统圆角（无阴影）"""
        try:
            self._hide_from_taskbar()
            from .glass_effect import apply_liquid_glass
            # 小窗默认隐藏：只在真正显示时应用玻璃效果，避免被强制显示
            if self.isVisible():
                apply_liquid_glass(self, enable_shadow=False)
        except Exception as e:
            print(f'[GlassEffect] 小窗失败: {type(e).__name__}: {e}')

    def showEvent(self, event):
        super().showEvent(event)
        # v4.4.0 修复(P1-14): 显示时按需重启被 hideEvent 停掉的动画定时器
        self._resume_timers()
        # showEvent后确保glass effect已应用（hide后DWM会重置backdrop，延迟重新应用）
        # v4.4.0 修复(P1-21): 原来这里直接排两次 _apply_glass_effect(加上
        # _init_ui 的 200ms 共 3 次), 每次都是
        # SetWindowLongW + SetWindowPos(SWP_FRAMECHANGED) + 4×DwmSetWindowAttribute。
        # 交给基类的脏标志去重, 观感不变但只做一次。
        self._mark_glass_dirty()
        self._schedule_glass()

    def hideEvent(self, event):
        """v4.4.0 修复(P1-14): 原来根本没实现 hideEvent/closeEvent ——
        小窗藏起来以后 40ms 的连接呼吸定时器、500ms 的三点跳动、30ms 的
        脉冲定时器全部继续空转, 每秒 25 次 setStyleSheet + 12 次重绘,
        藏着的窗口照样吃 CPU。这里全部停掉, showEvent 时按需重启。
        """
        super().hideEvent(event)
        self._pause_timers()

    def closeEvent(self, event):
        """关闭 = 彻底停掉所有定时器(与 hide 不同: 不再重启)。"""
        self._pause_timers()
        super().closeEvent(event)

    def _pause_timers(self):
        """停止全部动画定时器(可重入, 不存在也不会炸)。"""
        for name in ('_conn_breath_timer', '_dots_timer', '_pulse_timer'):
            t = getattr(self, name, None)
            try:
                if t is not None and t.isActive():
                    t.stop()
            except Exception:
                pass
        try:
            self._ready_panel.pause_timers()
        except Exception:
            pass

    def _resume_timers(self):
        """按当前状态重启需要跑的定时器: 只有在"连接着"或"正在跑三点动画"
        时才重启, 避免显示出来就无条件空转(这正是 P1-14 的病根)。"""
        try:
            if getattr(self, '_conn_breathing', False):
                t = getattr(self, '_conn_breath_timer', None)
                if t is not None:
                    t.start()
            if getattr(self, '_dots_active', False):
                self._dots_timer.start()
            self._ready_panel.resume_timers()
        except Exception as e:
            print(f'[Overlay] 恢复定时器失败: {type(e).__name__}: {e}')

    def resizeEvent(self, event):
        # v4.4.0 修复(P1-21): 尺寸变了需要按新 geometry 重算原生圆角/backdrop,
        # 置脏 + 排出一次(命中脏标志后不会与 showEvent 的那次重复做)。
        super().resizeEvent(event)
        self._mark_glass_dirty()
        self._schedule_glass()

    def _connect(self):
        self.sig_conn.connect(self._do_conn)
        self.sig_status.connect(self._do_clear_status)
        self.sig_conn_top.connect(self._do_conn_top)  # 连接状态顶部指示器
        self.sig_conn_state.connect(self._do_conn_state)  # v4.4.1(UI-B5) 三态
        self.sig_champ_name.connect(lambda n: self._champ_label.setText(n if n else ''))
        self.sig_bench.connect(self._do_bench)
        self.sig_icon.connect(self._do_icon)
        self.sig_preinput_state.connect(self._do_preinput_state)
        self.sig_clear.connect(self._do_clear)
        self.sig_ready.connect(self._do_ready)
        self.sig_ready_response.connect(lambda r: self._ready_panel.set_response(r))
        self.sig_show.connect(self._do_show_animated)
        self.sig_hide.connect(self._do_hide_animated)
        self.sig_live_status.connect(self._do_live_status)
        self.sig_mini_log.connect(self.append_mini_log)

    # ==================== 构建UI ====================
    def _make_title(self):
        bar = QWidget()
        bar.setFixedHeight(36)
        bar.setStyleSheet(f'background:{Glass.BG_OVERLAY_DOT};')  # 不能用 transparent（会穿透事件）
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 8, 0)

        t = QLabel('大蛋小助手')
        t.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};font-weight:700;color:{Glass.TEXT_PRIMARY};background:transparent;")
        lay.addWidget(t)
        lay.addStretch()

        # 窗口控制按钮（统一工厂：主窗口/小窗共用同一样式）
        # v4.4.19: 新增「返回主界面」按钮, 位置在**关闭左边**(用户要求)。
        #   与「关闭」的区别(这是刻意设计的):
        #     返回主界面 = 只把主界面调出来, **不停脚本** —— 对局中想看一眼主界面
        #                  时可以随手点, 不会把正在跑的脚本打断;
        #     关闭       = 停脚本 + 回主界面(破坏性), 保持原语义不变。
        #   若两者都只是"回主界面", 那多出来的按钮就没意义了。
        for b in make_title_buttons(close_cb=self.close_signal,
                                    main_cb=self.show_main_signal):
            lay.addWidget(b)

        # v4.4.0 修复(P2): 原来标题栏拖拽写 `if self._drag_pos:` —— QPoint(0,0)
        # 布尔值为假, 从窗口左上角按下时偏移量正好是 (0,0), 窗口拖不动。
        # 现统一走 DragMixin(显式 is not None)。
        self._install_drag(bar)
        return bar

    def _make_toggles(self):
        row = QWidget()
        row.setFixedHeight(30)
        row.setStyleSheet('background:transparent;')
        lay = QHBoxLayout(row)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(0)
        self._toggle_btns = {}

        for key, label in [('auto_queue', '匹配'), ('auto_accept', '接受'), ('auto_pick_champion', '换英雄')]:
            grp = QHBoxLayout()
            grp.setSpacing(4)
            grp.setContentsMargins(0, 0, 8, 0)

            btn = AnimatedToggle()
            btn.setChecked(self._settings.get(key, True))
            btn.toggled.connect(lambda checked, k=key: self._on_toggle(k, checked))
            self._toggle_btns[key] = btn
            grp.addWidget(btn)

            lbl = QLabel(label)
            lbl.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_SECONDARY};background:transparent;")
            lbl.setCursor(Qt.PointingHandCursor)
            # v4.4.2(P2-18): 原 lbl.mousePressEvent 调 b.toggle() —— toggle() 内部
            # 走 AnimatedToggle.setChecked 覆写(直接赋值 _circle_x, 不走动画), 而
            # 按钮本身走 clicked() → _on_clicked → _animate_to 有滑动动画。点标签
            # 是硬跳、点按钮有动画, 同一开关两种手感。改 click() 让标签与按钮一致。
            lbl.mousePressEvent = lambda e, b=btn: b.click()
            grp.addWidget(lbl)

            grp_w = HoverRow()
            grp_w.setStyleSheet('background:transparent;')
            grp_w.setLayout(grp)
            lay.addWidget(grp_w)

        return row

    @ui_slot
    def _on_toggle(self, key, checked):
        self.toggle_setting_signal.emit(key, checked)

    def _make_live_status(self):
        w = QWidget()
        w.setFixedHeight(24)
        w.setStyleSheet('background:transparent;')
        lay = QHBoxLayout(w)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        # H3(v15):圆点 6px→9px,连接态一眼可辨(原 6px 在悬浮窗上近似色斑)
        # v4.4.0 修复(P1-14): QLabel + setStyleSheet 换成 paintEvent 画的 StatusDot。
        # 这个点原本被 40ms 的呼吸定时器每秒改 25 次样式表, 是这台机器上
        # 最贵的一个 9 像素控件。
        self._live_dot = StatusDot(9, color=(154, 160, 184), alpha=255)
        lay.addWidget(self._live_dot)

        # 跳动动画（三点）
        self._dots = 0
        self._dots_base = ''  # 动画前缀文字
        self._dots_active = False  # v4.4.0(P1-14): hide 后据此决定是否重启
        self._dots_timer = QTimer(self)
        self._dots_timer.timeout.connect(self._tick_dots)
        self._dots_timer.setInterval(500)

        self._live_text = QLabel('等待中')
        self._live_text.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_SECONDARY};background:transparent;")
        lay.addWidget(self._live_text, 1)

        self._champ_label = QLabel('')
        # H2(v15):#6366f1 在深玻璃底约 4.2:1 略低于 AA,提亮到 PRIMARY_HOVER
        self._champ_label.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.PRIMARY_HOVER};font-weight:700;background:transparent;")
        lay.addWidget(self._champ_label)
        return w

    def _make_champ_area(self):
        """备选英雄区：PulseGlow + ScrollArea + Grid + ChampionIcon(v10 完整回归)"""
        # v4.4.0 修复(P2 设计令牌): 原写死 color='#6366f1', 与 Glass.PRIMARY 是同一色
        self._bench_glow = PulseGlow(color=Glass.PRIMARY)
        self._bench_glow.setStyleSheet('background:transparent;')
        lay = QVBoxLayout(self._bench_glow)
        lay.setContentsMargins(12, 6, 12, 4)
        lay.setSpacing(2)

        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(6)
        hdr = QLabel('备选英雄')
        hdr.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        hdr_row.addWidget(hdr)
        hdr_row.addStretch()
        self._bench_hint = QLabel('等待进入选人界面…')
        self._bench_hint.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        hdr_row.addWidget(self._bench_hint)
        lay.addLayout(hdr_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + Glass.SCROLL_QSS)

        self._grid_w = QWidget()
        self._grid_w.setStyleSheet('background:transparent;')
        self._grid = QGridLayout(self._grid_w)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(4)
        self._grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        scroll.setWidget(self._grid_w)
        self._bench_scroll = scroll
        lay.addWidget(scroll)
        return self._bench_glow

    def _make_mini_log(self):
        """v12：mini_log 从 4 行改 5 行（用户明确指定）。其余按 v10 形态。"""
        w = QWidget()
        # 高度按 5 行 11px 字 + padding 算：5 * 14 + 6 ≈ 76，原 4 行 64 → 76
        # v4.4.0 修复(P2 硬编码): 上面这行算术原来直接写 76, 与 5/14/6 三个
        # 魔数脱钩, 改一处忘一处。现在由 MINI_LOG_* 常量推导(值不变 = 76)。
        w.setFixedHeight(MINI_LOG_HEIGHT)
        w.setStyleSheet('background:transparent;')
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 2, 12, 2)
        lay.setSpacing(0)

        # 分隔线（与上方英雄区域分开层次）
        sep = QWidget()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f'background:{Glass.BG_SEPARATOR};')
        lay.addWidget(sep)
        lay.addSpacing(2)

        self._mini_log_lines = []
        self._mini_log_labels = []
        for _ in range(MINI_LOG_ROWS):  # v12：4 → 5
            lbl = QLabel('')
            lbl.setStyleSheet(f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_SECONDARY};background:transparent;")
            lbl.setWordWrap(False)
            lay.addWidget(lbl)
            self._mini_log_labels.append(lbl)
        return w

    @ui_slot
    def append_mini_log(self, text):
        if not text:
            return
        self._mini_log_lines.append(text)
        if len(self._mini_log_lines) > MINI_LOG_ROWS:  # v12：4 → 5
            self._mini_log_lines = self._mini_log_lines[-MINI_LOG_ROWS:]
        for i, lbl in enumerate(self._mini_log_labels):
            if i < len(self._mini_log_lines):
                t = self._mini_log_lines[i]
                # v4.4.0 修复(P2 硬编码): 36/33 原来写死, 依赖"..."
                # 恰好 3 个字符; 现在由 MINI_LOG_MAX_CHARS / MINI_LOG_KEEP_CHARS
                # 明确表达(截断后总长仍 ≤ 36, 值不变)。
                if len(t) > MINI_LOG_MAX_CHARS:
                    t = MINI_LOG_ELLIPSIS + t[-MINI_LOG_KEEP_CHARS:]
                lbl.setText(t)
            else:
                lbl.setText('')

    def _make_bottom(self):
        bar = QWidget()
        bar.setFixedHeight(34)
        bar.setStyleSheet('background:transparent;')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 0, 12, 0)
        lay.setSpacing(6)

        self._conn_dot = StatusDot(9, color=(248, 113, 113), alpha=170)
        lay.addWidget(self._conn_dot)

        self._conn_lbl = QLabel('未连接')
        self._conn_lbl.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XS};color:{Glass.TEXT_MUTED};background:transparent;")
        lay.addWidget(self._conn_lbl)
        lay.addStretch()

        self._script_running = False
        self._run_btn = QPushButton('启动')
        self._run_btn.setFixedSize(52, 24)
        self._run_btn.setCursor(Qt.PointingHandCursor)
        self._run_btn.setStyleSheet(_run_btn_qss(
            Glass.PRIMARY_SUBTLE, Glass.PRIMARY, 'rgba(99,102,241,0.15)',
            Glass.PRIMARY, 'white', Glass.PRIMARY))
        self._run_btn.clicked.connect(self.toggle_script_signal)
        lay.addWidget(self._run_btn)

        return bar

    # ==================== 外部接口 ====================
    # v4.6.11 删除 update_connection_status(): 全项目零调用(仅被注释提及)。
    # 它只驱动底部 _conn_dot, 而真正在用的是下面 set_conn_indicator /
    # set_conn_state(顶部+底部一起翻转) —— 留着会让人误以为有两个并行入口。

    def set_conn_indicator(self, connected: bool, text: str = ''):
        """统一驱动小窗连接状态指示器:顶部 _live_dot + 底部 _conn_dot + 两处文字同步翻转。
        """
        self.sig_conn_top.emit(connected, text or ('已连接' if connected else '未连接'))

    def set_conn_state(self, state: str, text: str = ''):
        """v4.4.1(UI-B5): 三态连接指示入口('connected'/'connecting'/'disconnected')。

        与 `set_conn_indicator` 的关系: 后者是旧的 bool 版(签名不变, 内部转调本条
        三态链路), 仍可用。需要表达"正在连接"这个中间态时用本接口 —— bool 版
        表达不了, 会把"扫客户端中"渲染成和"客户端没开"相同的红色。
        """
        self.sig_conn_state.emit(state, text or '')

    def update_status(self, text):
        """脚本运行态文本,驱动顶栏 _live_text(连接态三态文案会覆盖它, 属有意设计)。"""
        self.sig_status.emit(text)

    def update_current_champion(self, name):
        self.sig_champ_name.emit(name)

    def update_bench_champions(self, ids, current_id=None):
        self.sig_bench.emit(ids, current_id)

    def set_preinput(self, cid):
        """v4.9.14:由 main 回推"当前预输入的英雄"(0=无)。

        本方法**必须从任意线程都可安全调用** —— _on_bench 在 core 的 poll/WS 后台
        线程里跑。因此这里只做信号发射, 真正的控件操作由主线程槽 _do_preinput_state
        执行。这样界面上亮蓝光的那个英雄, 一定就是 core 正在盯的那个。
        """
        try:
            cid = int(cid or 0)
        except (TypeError, ValueError):
            cid = 0
        self.sig_preinput_state.emit(cid)

    @ui_slot
    def _do_preinput_state(self, cid):
        """v4.9.14:主线程执行预输入蓝光更新(信号槽)。幂等: 值没变不重绘。

        这是 `_preinput_cid` 的**唯一视觉写入点**。core 是权威源, 本槽只照画。
        """
        try:
            cid = int(cid or 0)
        except (TypeError, ValueError):
            cid = 0
        if cid == self._preinput_cid:
            return
        self._preinput_cid = cid
        for c, icon in self._icons.items():
            # icon.is_selected 已是"当前英雄"态, 这里原样传回(不重算)
            icon.set_selected(icon.is_selected, c in self._preferred, c == cid)

    def set_champion_icon(self, cid, data):
        if data:
            self.sig_icon.emit(cid, data)

    def clear_champions(self):
        self.sig_clear.emit()

    def show_ready_check_buttons(self, visible):
        self.sig_ready.emit(visible)

    def set_ready_response(self, response):
        self.sig_ready_response.emit(response)

    def safe_show(self):
        self.sig_show.emit()

    def safe_hide(self):
        self.sig_hide.emit()

    # ==================== 动画 ====================
    # v4.4.0 修复(P2): 原来每次 show/hide 都 `QPropertyAnimation(self, b'windowOpacity')`
    # 新建一个对象 —— 每次点开/收起小窗都造一个 QObject + 连一次 finished 信号,
    # 而这两个动画的参数(时长/缓动/起止值)其实是常量。改成创建一次、复用,
    # 重设起止值后 start()。两条动画分开持有, 免得 finished 槽互相覆盖。
    def _ensure_animations(self):
        # v4.4.1(UI-B8): 缓动曲线改 OutCubic。
        # 原来 show 用 OutBack —— 它会先冲过 1.0 再回弹(windowOpacity 越界被
        # 截断成 1.0), 表现为"亮到底、卡住、再亮一次", 在小窗这种小面积上尤其
        # 像掉帧; 而 DWM Acrylic 在 opacity 动画期间是被禁用的, 越界那几帧
        # 恰好落在"玻璃没应用"的窗口里, 观感更差。改用全程单调的 OutCubic。
        # 时长方向: 开比关短(220ms < 260ms) —— 用户主动打开时希望立刻看到,
        # 收起时慢一点显得从容。
        if getattr(self, '_show_anim', None) is None:
            a = QPropertyAnimation(self, b'windowOpacity', self)
            a.setDuration(220)
            a.setStartValue(0.0)
            a.setEndValue(1.0)
            a.setEasingCurve(QEasingCurve.OutCubic)
            # windowOpacity 动画会临时禁用 Acrylic，动画结束(opacity=1)后重新应用玻璃
            # v4.4.2(P1-11): 直接连 _apply_glass_effect 是死路径 —— showEvent 排的
            # 120ms 那次已把 _glass_applied 置真, 动画结束时 geometry 未变, 基类
            # 缓存短路直接 return, "动画结束后重挂玻璃"永不执行。改为先置脏再应用。
            a.finished.connect(self._reapply_glass_after_show_anim)
            self._show_anim = a
        if getattr(self, '_hide_anim', None) is None:
            b = QPropertyAnimation(self, b'windowOpacity', self)
            b.setDuration(260)
            b.setStartValue(1.0)
            b.setEndValue(0.0)
            b.setEasingCurve(QEasingCurve.OutCubic)
            b.finished.connect(self.hide)
            self._hide_anim = b

    @ui_slot
    def _reapply_glass_after_show_anim(self):
        """v4.4.2(P1-11): show 动画结束后重挂玻璃(动画期间 DWM Acrylic 被禁用)。

        先置脏再应用 —— 否则基类 _apply_glass_effect 的缓存短路会让这次重挂
        变成空操作(showEvent 排的那次已把 _glass_applied 置真)。
        """
        self._mark_glass_dirty()
        self._apply_glass_effect()

    @ui_slot
    def _do_show_animated(self):
        self.show()
        self.raise_()
        # v4.4.2(P2-4): 去掉 activateWindow() —— 配合 WA_ShowWithoutActivating,
        # 挂机悬浮层显示时不再抢游戏/客户端焦点。show() 已足够置顶显示。
        self._ensure_animations()
        a = self._show_anim
        a.stop()
        a.setStartValue(0.0)
        a.setEndValue(1.0)
        a.start()

    @ui_slot
    def _do_hide_animated(self):
        self._ensure_animations()
        a = self._hide_anim
        a.stop()
        a.setStartValue(1.0)
        a.setEndValue(0.0)
        a.start()

    def set_live_status(self, icon, text, detail=''):
        msg = f'{icon} {text}'.strip() if icon else text
        self.sig_live_status.emit(msg, detail)

    @ui_slot
    def set_script_running(self, running):
        self._script_running = running
        if running:
            self._run_btn.setText('停止')
            self._run_btn.setStyleSheet(_run_btn_qss(
                Glass.DANGER_SUBTLE, Glass.DANGER, 'rgba(248,113,113,0.15)',
                Glass.DANGER, 'white', Glass.DANGER))
        else:
            self._run_btn.setText('启动')
            self._run_btn.setStyleSheet(_run_btn_qss(
                Glass.PRIMARY_SUBTLE, Glass.PRIMARY, 'rgba(99,102,241,0.15)',
                Glass.PRIMARY, 'white', Glass.PRIMARY))

    @ui_slot
    def update_settings(self, settings):
        # v5.0.0(#3 P3): 判空必须排在 .copy() 之前 —— 上一版声称修了 None,
        # 实际只修了 core 侧; 这里 None.copy() 会抛 AttributeError。
        if settings is not None:
            self._settings = settings.copy()
        else:
            settings = {}
        # v4.9.14: 关掉「预输入选英雄」开关时, 本地蓝光同帧收起。
        # core 的 update_settings 也会清权威标记并随 _on_bench 回推, 这里是
        # UI 侧的即时兜底 —— 用户关开关的瞬间不等下一轮轮询就看到蓝光消失。
        if settings is not None and settings.get('auto_preinput_champion') is False \
                and self._preinput_cid:
            self._preinput_cid = 0
            self._refresh_preferred_highlight()
        for key, btn in self._toggle_btns.items():
            if key in self._settings:
                v = self._settings[key]
                btn.blockSignals(True)
                btn.setChecked(v)
                btn.blockSignals(False)
        # v13 优化项 1:同步偏好集合,触发宫格头像重描边
        # v4.4.0 修复(P1-1 真实崩溃点): 原来直接
        #     set(int(x) for x in (self._settings.get('preferred_champions') or []))
        # 只要 settings.json 里混进一个非数字项(手改配置 / 旧版本写坏的字符串),
        # 这里就抛 ValueError; PyQt5 默认异常处理是 qFatal → **整个进程直接被杀**,
        # 用户看到的是"小窗一刷设置就没了"。现在逐项过滤, 跳过坏值并提示。
        new_preferred = set()
        raw_preferred = self._settings.get('preferred_champions')
        # 字符串会被逐字符迭代('notalist' → 8 个单字符警告), 与"列表"语义不符,
        # 直接当空处理; 其余不可迭代类型同样退化。
        if not isinstance(raw_preferred, (list, tuple, set)):
            if raw_preferred:
                print(f'[Overlay] preferred_champions 类型异常, 已忽略: '
                      f'{type(raw_preferred).__name__}')
            raw_preferred = ()
        for x in raw_preferred:
            try:
                new_preferred.add(int(x))
            except (TypeError, ValueError):
                print(f'[Overlay] preferred_champions 含非数字项, 已跳过: {x!r}')
        if new_preferred != self._preferred:
            self._preferred = new_preferred
            self._refresh_preferred_highlight()

    def _refresh_preferred_highlight(self):
        """v13 优化项 1:遍历 _icons 重设每个头像的偏好描边状态(选中态优先级 > 偏好态)
        v4.9.13:预输入态一起重描(优先级最高)。"""
        for cid, icon in self._icons.items():
            # v4.4.2(P2-7): 走只读 property, 不再直读私有 _is_selected。
            icon.set_selected(icon.is_selected, cid in self._preferred,
                              cid == self._preinput_cid)

    def set_champion_data(self, data):
        """v4.x(P3): 保留的兼容空操作。

        main.py 的 _on_champ_data 仍会调用本方法传入英雄名映射, 但小窗内
        ChampionIcon 从不使用 name(死数据流已删除), 故此处不再存储。
        删除本方法需要同步改 main.py, 不在本次改动范围内。
        """

    # ==================== 实际更新 ====================
    @ui_slot
    def _do_clear_status(self, text):
        self._live_text.setText(text if text else '')

    @ui_slot
    def _apply_bottom_conn(self, state: str):
        """v4.4.1(UI-B5): 底部 _conn_dot + _conn_lbl 的三态驱动(顶部不动)。

        呼吸只给 'connected' —— "还在连"不该有稳定心跳, 那会像已经连上。
        """
        color = CONN_STATE_COLORS.get(state, Glass.DANGER)
        if state == 'connected':
            self._conn_breathing = True
            self._conn_breath_val = 0.0
            self._conn_breath_dir = 1
            if not hasattr(self, '_conn_breath_timer'):
                self._conn_breath_timer = QTimer(self)
                self._conn_breath_timer.timeout.connect(self._conn_breath_tick)
                self._conn_breath_timer.setInterval(40)
            # v4.4.0 修复(P1-14): 隐藏状态下不起定时器(hideEvent 已停, 这里别又点燃)
            if self.isVisible():
                self._conn_breath_timer.start()
            self._conn_dot.set_state(color=color)
        else:
            self._conn_breathing = False
            if hasattr(self, '_conn_breath_timer'):
                self._conn_breath_timer.stop()
            # 连接中给个比 idle 更实的 alpha, 免得琥珀点看着比红点还淡
            self._conn_dot.set_state(color=color, alpha=200 if state == 'connecting' else 170)
        self._conn_lbl.setText(CONN_STATE_TEXTS.get(state, '未连接'))

    @ui_slot
    def _do_conn_state(self, state: str, text: str):
        """v4.4.1(UI-B5): 三态连接指示的唯一实现, 顶部 + 底部一起翻转。

        为什么要三态: 原来只有 bool。应用刚启动、后台线程正在扫客户端(可能全盘
        探测, 十几秒)时只能渲染成"未连接", 和"客户端根本没开"长得一模一样 ——
        用户会以为软件坏了。现在 'connecting' 走琥珀 + 半透明琥珀 ring, 与
        'disconnected' 的红色危险语义区分开。

        连接态是顶栏 _live_text 的权威写入方(会覆盖脚本运行态文案) —— 这是有意
        设计且有回归测试钉死(TestOverlayConnState 断言 _live_text 显示"已连接/
        连接中/未连接")。脚本运行态只在连接态之外的间隙显示, 请勿拆走。
        """
        if state not in CONN_STATE_COLORS:
            state = 'disconnected'
        label = text or CONN_STATE_TEXTS[state]
        color = CONN_STATE_COLORS[state]
        # H3(v15):绿态加外描边 ring(半透明绿),红/灰态实心即可
        # v4.4.0 修复(P1-18): 圆点改走 StatusDot 属性 + update(), 不再 setStyleSheet
        self._live_dot.set_state(color=color)
        if state == 'connected':
            self._live_dot.set_ring(QColor(52, 211, 153, 153))   # rgba(52,211,153,0.60)
        elif state == 'connecting':
            self._live_dot.set_ring(QColor(251, 191, 36, 130))   # 琥珀描边 = "在连, 还没通"
        else:
            self._live_dot.set_ring(None)
        self._live_text.setText(label)
        # 停掉运行态动画,免得覆盖颜色
        if hasattr(self, '_dots_timer') and self._dots_timer.isActive():
            self._dots_timer.stop()
        self._dots_active = False
        # 底部连接指示器
        self._apply_bottom_conn(state)

    @ui_slot
    def _do_conn_top(self, connected: bool, text: str):
        """v8 显式驱动:顶部 _live_dot + 底部 _conn_dot + 底部 _conn_lbl
        同步翻转,圆点颜色只表达连接态。

        v4.4.1(UI-B5): 本体已挪到 `_do_conn_state`(三态)。这里保留 bool 入口并
        转调, 只是为了不破坏既有调用方(`set_conn_indicator` / 旧信号)的语义。
        """
        self._do_conn_state('connected' if connected else 'disconnected', text)

    @ui_slot
    def _do_conn(self, connected):
        """仅驱动底部(顶部 _live_dot 归连接态)。v4.4.1(UI-B5): 走三态的底部实现。"""
        self._apply_bottom_conn('connected' if connected else 'disconnected')

    @ui_slot
    def _conn_breath_tick(self):
        """v4.4.0 修复(P1-14): 原来这个 40ms 的 tick 里
        `self._conn_dot.setStyleSheet(f"background:rgba(52,211,153,{alpha});...")`
        —— 每秒 25 次 QSS 解析 + 整棵控件树 style 缓存重建, 就为了改一个
        9 像素圆点的透明度。现在只改 StatusDot 的 alpha 属性后 update()。
        """
        if not self._conn_breathing:
            return
        self._conn_breath_val += 0.03 * self._conn_breath_dir
        if self._conn_breath_val >= 1.0:
            self._conn_breath_val = 1.0
            self._conn_breath_dir = -1
        elif self._conn_breath_val <= 0.3:
            self._conn_breath_val = 0.3
            self._conn_breath_dir = 1
        alpha = int(150 + 105 * self._conn_breath_val)
        self._conn_dot.set_state(color=Glass.ACCENT, alpha=alpha)

    @ui_slot
    def _tick_dots(self):
        """三点跳动动画"""
        self._dots = (self._dots + 1) % 4
        dots = '.' * self._dots if self._dots > 0 else ''
        self._live_text.setText(self._dots_base + dots)

    def _start_dots(self, base_text):
        """启动三点跳动"""
        self._dots_base = base_text
        self._dots = 0
        self._live_text.setText(base_text)
        self._dots_active = True  # v4.4.0(P1-14): 供 showEvent 决定是否重启
        if not self._dots_timer.isActive():
            self._dots_timer.start()

    def _stop_dots(self):
        """停止三点跳动"""
        self._dots_active = False  # v4.4.0(P1-14): 供 showEvent 决定是否重启
        self._dots_timer.stop()

    @ui_slot
    def _do_live_status(self, text, detail=''):
        """脚本运行态:写顶栏 _live_text(无三点动画时不带)/ 启动或停止三点动画。
        v12 恢复 v10 行为 — S3 圆点颜色归 _do_conn_top 唯一维护,_do_live_status
        不再写 _live_dot 颜色/启动 _pulse_timer(脉冲已废弃,只有三点动画)。
        """
        clean = text or ''
        for emoji in STATUS_EMOJI_STRIP:
            clean = clean.replace(emoji, '')
        clean = clean.strip()
        if not clean:
            clean = text

        # v4.4.0 修复(P2 硬编码): 原为 `if '等待' in clean or '启动' in clean`,
        # 用中文文案当状态分支 —— 文案一改逻辑就静默失效。抽成具名词表。
        if any(w in clean for w in DOTS_TRIGGER_WORDS):
            self._start_dots(clean)
        else:
            self._stop_dots()
            self._live_text.setText(clean)

    @ui_slot
    def _do_bench(self, ids, current_id):
        # 空状态占位 / 脉冲光晕切换
        has = bool(ids)
        self._bench_hint.setVisible(not has)
        if hasattr(self, '_bench_scroll') and self._bench_scroll is not None:
            self._bench_scroll.setVisible(has)
        if has:
            self._bench_glow.start_pulse()
        else:
            self._bench_glow.stop_pulse()
        cur_set = set(ids)
        old = set(self._icons.keys())
        # v13 优化项 1:把偏好状态一起传给 set_selected(selected, preferred),
        # 选中态优先级 > 偏好态,选中时不被偏好绿框覆盖。
        # v4.9.13:再加第三态 preinput(优先级最高, 见 set_selected)。
        # v4.9.14:预输入"已不在池中"的蓝光收起**不在这里做** —— core 的
        # _do_preinput 分支③是权威源, 会通过 _on_bench → set_preinput → 信号
        # 推 0 过来, 本槽只负责照画, 避免与权威源形成双写入点。
        if cur_set == old:
            for cid, icon in self._icons.items():
                icon.set_selected(cid == current_id, cid in self._preferred,
                                  cid == self._preinput_cid)
            return
        for cid in list(self._icons.keys()):
            if cid not in cur_set:
                icon = self._icons.pop(cid)
                self._grid.removeWidget(icon)
                icon.deleteLater()
        r, c = 0, 0
        for cid in ids:
            if cid not in self._icons:
                icon = ChampionIcon(cid)
                icon.clicked_id.connect(self._on_click)
                self._icons[cid] = icon
            self._icons[cid].set_selected(cid == current_id, cid in self._preferred,
                                          cid == self._preinput_cid)
            self._grid.addWidget(self._icons[cid], r, c)
            c += 1
            if c >= 4:
                c = 0
                r += 1

    @ui_slot
    def _on_click(self, cid):
        """v4.9.13: 单击备选英雄 —— 开关开=标记预输入(蓝光), 开关关=旧的立即换。

        为什么不做成"两个都用, 加右键": 右键换英雄是隐藏操作, 用户记不住;
        而开关关闭时退回旧行为, 需要立即换的人关掉开关即可, 语义干净。
        """
        if self._settings.get('auto_preinput_champion', True):
            # 只发请求, 不在这里改视觉 —— 由 main 回调 set_preinput() 统一重描,
            # 保证"看到的"与"core 记的"永远是同一个值。
            self.sig_preinput.emit(cid)
            return
        # 开关关闭: 完全保持 v4.9.12 及以前的行为(单击立即换)
        for c, icon in self._icons.items():
            icon.set_selected(c == cid, c == cid and c in self._preferred)
        self.swap_signal.emit(cid)

    @ui_slot
    def _do_icon(self, cid, data):
        if cid in self._icons:
            self._icons[cid].set_icon_data(data)

    @ui_slot
    def _do_clear(self):
        self._bench_glow.stop_pulse()
        self._bench_hint.setVisible(True)
        if hasattr(self, '_bench_scroll') and self._bench_scroll is not None:
            # v4.4.2(P2-6): 原这里把 _bench_scroll 设回可见, 但网格已清空, 可见的
            # 滚动区是空的(冗余状态)。与 _do_bench 的空态口径对齐: 清空后隐藏滚动区。
            self._bench_scroll.setVisible(False)
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._icons.clear()

    @ui_slot
    def _do_ready(self, visible):
        if visible:
            # v5.0.x: 面板在确认窗口内常驻可反悔; 已给出响应(is_completed)后,
            # 即使轮询每秒重推 True 也不重弹/重置, 直到离开 ReadyCheck 由 core
            # 推 False → hide + reset, 下一局再重弹。
            if not self._ready_panel.isVisible() and not self._ready_panel.is_completed:
                self._ready_panel.reset_check()
                self._ready_panel.show_check()
        else:
            self._ready_panel.hide_check()
            self._ready_panel.reset_check()