"""Agreement Dialog - 首次启动强制阅读弹窗

液态玻璃风格与主窗一致：无边框 + 系统 Acrylic 模糊 + 靛蓝霓虹描边。
内容为功能介绍 + 免责声明，强制阅读倒计时 30s，倒计时结束前无法关闭
（拦截 Alt+F4 / Esc / 关闭事件）。
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QFrame
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPainter

from .window_base import ui_slot
from .styles import Glass
from .animated_widgets import paint_window_backdrop
# v4.4.0 修复(P1-15): 定位统一走 screen_utils(QApplication 在本文件已不再需要)
from . import screen_utils


# 首次启动须知正文（HTML 富文本，与主界面文字风格一致）
_AGREEMENT_HTML = """
<style>
  h2 { color: #ffffff; font-size: 15px; font-weight: 700; margin: 14px 0 6px; }
  p  { color: #c8c8d8; font-size: 13px; line-height: 1.7; margin: 4px 0; }
  li { color: #c8c8d8; font-size: 13px; line-height: 1.7; margin: 3px 0 3px 4px; }
  .hl { color: #a5b4fc; }
  .warn { color: #fbbf24; }
  .danger { color: #f87171; }
</style>

<p>一个免安装的单文件小工具，通过客户端本地接口连接游戏，帮你自动完成<b>海克斯大乱斗</b>的排队、接受、选人与结算流程。</p>

<h2>⚡ 六大自动化</h2>
<ul>
  <li><span class="hl">自动匹配</span> — 进房自动开排，无需手动点击</li>
  <li><span class="hl">自动接受</span> — 匹配到对局后自动接受</li>
  <li><span class="hl">自动选英雄</span> — 按你偏好的英雄点卡，没有就随机兜底</li>
  <li><span class="hl">自动换英雄</span> — 锁定后按偏好更换英雄</li>
  <li><span class="hl">自动返回</span> — 打完自动回房再排，无需手动操作</li>
  <li><span class="hl">自动点赞</span> — 结算后自动为队友点赞</li>
</ul>

<h2>📋 怎么用</h2>
<ul>
  <li>先登录英雄联盟（国服），停留在大厅</li>
  <li>打开大蛋小助手，自动连接客户端</li>
  <li>在「英雄」页勾选想玩的偏好英雄（支持拼音搜索）</li>
  <li>点「⚡ 启动脚本」开始自动化流程</li>
</ul>

<h2>⚠️ 使用须知</h2>
<ul>
  <li><span class="warn">「自动选英雄」需要以管理员身份运行</span>，才能模拟点击选人界面。</li>
  <li>本工具通过客户端<b>本地接口（LCU）</b>实现自动化，<b>不注入游戏、不修改内存</b>。</li>
  <li class="danger">任何第三方辅助工具都存在账号风险，请理性使用，相关后果由使用者自行承担。</li>
</ul>
"""


class AgreementDialog(QWidget):
    """首次启动强制阅读弹窗"""

    _COUNTDOWN = 30  # 强制阅读秒数
    accepted = pyqtSignal()  # 用户倒计时结束后点「我已知晓」时发出

    def __init__(self, parent=None):
        super().__init__(parent)
        self._remaining = self._COUNTDOWN
        self._accepted = False
        self.setWindowTitle('大蛋小助手 · 使用须知')
        # v4.4.2(P2-5): 原 Qt.Dialog 会带 WS_EX_DLGMODALFRAME 并重置 DWM backdrop,
        # 与主窗/详情弹窗的玻璃路线冲突(同项目 recent_detail.py 已改用 Qt.Window)。
        # 模态语义已由下方 setWindowModality(ApplicationModal) 独立表达, 不需要
        # Dialog flag。改 Qt.Window 后系统圆角/Acrylic 才能正常打上。
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowModality(Qt.ApplicationModal)
        self.setFixedSize(600, 580)

        # v4.4.0 修复(P1-15): 原 `QApplication.primaryScreen().geometry()` 是
        # **设备像素**(3840×2160), 直接喂给 **逻辑像素** 的 move() —— 150% 缩放下
        # 弹窗被推到屏幕右下角之外; 且硬绑主屏, 副屏(可带负坐标)上必错。
        # screen_utils.center_on_screen 用逻辑 availableGeometry, 并优先用
        # 父窗口所在屏(主窗可能已被用户拖到副屏)。
        screen_utils.center_on_screen(self)

        self._init_ui()

        # 倒计时
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000)

        # v4.4.2(P2-16): 玻璃应用统一交给 showEvent(单次)。原来这里排一次
        # singleShot(60) + showEvent 里再排两次(60/300ms) = 一次显示打 3 次 DWM,
        # 而本类不含 GlassEffectMixin, 脏标志去重对它无效 —— 每次都真打一遍。

    def _apply_glass(self):
        try:
            # v4.4.2(P2-5/P2-16): 加 isVisible 保护 —— 原实现无条件调
            # apply_liquid_glass, 与主窗/小窗的可见性守卫不一致。隐藏状态下
            # SetWindowPos 可能把窗口重新弹出来。
            if not self.isVisible():
                return
            from .glass_effect import apply_liquid_glass
            apply_liquid_glass(self, enable_shadow=False)
        except Exception as e:
            print(f'[Agreement] 玻璃效果失败: {type(e).__name__}: {e}')

    def _init_ui(self):
        # 外层容器（居中玻璃面板）
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        panel = _GlassPanel()
        outer.addWidget(panel)

        lay = QVBoxLayout(panel)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(12)

        # 标题区
        title = QLabel('欢迎使用大蛋小助手')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:800;color:{Glass.TEXT_PRIMARY};background:transparent;")
        lay.addWidget(title)

        sub = QLabel(f'首次使用请阅读以下说明，{self._COUNTDOWN} 秒后可开始使用')
        sub.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};color:{Glass.TEXT_MUTED};background:transparent;")
        lay.addWidget(sub)

        # 分隔线
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet('background:rgba(99,102,241,0.22);border:none;')
        lay.addWidget(line)

        # 滚动正文
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea{{background:rgba(12,14,24,0.55);border:1px solid {Glass.NEON_BORDER};"
            f"border-radius:{Glass.RADIUS_CARD};}}"
            f"QScrollArea > QWidget > QWidget {{background:transparent;}}")
        body = QLabel(_AGREEMENT_HTML)
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        body.setStyleSheet("background:transparent;")
        body.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        # 底部：倒计时 + 按钮
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        self._hint = QLabel(f'请完整阅读 · {self._remaining}s')
        self._hint.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_MD};color:{Glass.WARNING};background:transparent;font-weight:600;")
        bottom.addWidget(self._hint)
        bottom.addStretch()

        self._btn = QPushButton(f'我已知晓（{self._remaining}s）')
        self._btn.setFixedSize(200, 44)
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setEnabled(False)
        self._btn.clicked.connect(self._accept)
        bottom.addWidget(self._btn)

        lay.addLayout(bottom)

    @ui_slot
    def _tick(self):
        self._remaining -= 1
        if self._remaining <= 0:
            self._remaining = 0
            self._timer.stop()
            self._hint.setText('阅读完成，可以继续')
            self._hint.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_MD};color:{Glass.SUCCESS};background:transparent;font-weight:600;")
            self._btn.setText('我已知晓')
            self._btn.setEnabled(True)
        else:
            self._hint.setText(f'请完整阅读 · {self._remaining}s')
            self._btn.setText(f'我已知晓（{self._remaining}s）')
        self._refresh_btn_style()

    def _refresh_btn_style(self):
        if self._btn.isEnabled():
            self._btn.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 {Glass.PRIMARY_HOVER}, stop:1 {Glass.PRIMARY});
                    color: white; border: none; border-radius: {Glass.RADIUS_BTN};
                    font-size: {Glass.FONT_SIZE_LG}; font-weight: 700;
                }}
                QPushButton:hover {{
                    background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                        stop:0 {Glass.PRIMARY}, stop:1 {Glass.PRIMARY_PRESSED});
                }}
                QPushButton:pressed {{
                    background: {Glass.PRIMARY_PRESSED}; padding-top: 2px;
                }}
            """)
        else:
            self._btn.setStyleSheet(f"""
                QPushButton {{
                    background: rgba(255,255,255,0.06);
                    color: {Glass.TEXT_MUTED}; border: 1px solid rgba(255,255,255,0.08);
                    border-radius: {Glass.RADIUS_BTN};
                    font-size: {Glass.FONT_SIZE_LG}; font-weight: 600;
                }}
            """)

    @ui_slot
    def _accept(self):
        self._accepted = True
        self.accepted.emit()
        self.close()

    # 强制阅读：倒计时结束前禁止任何方式关闭
    def closeEvent(self, e):
        if getattr(self, '_accepted', False):
            e.accept()
        else:
            e.ignore()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Escape, Qt.Key_Enter, Qt.Key_Return):
            # 未倒计时结束前吞掉 Esc/回车，禁止绕过
            if not getattr(self, '_accepted', False):
                return
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        # v4.4.2(P2-16): 一次显示只打一次 DWM。原这里还排 60ms + 300ms 两发,
        # 加上 __init__ 里的 60ms 共 3 发, 本类又无 GlassEffectMixin 去重。
        # 玻璃需在窗口真正可见后才能打(否则 SetWindowPos 会把窗口弹出来),
        # 故排 60ms 这一发即可。
        QTimer.singleShot(60, self._apply_glass)


class _GlassPanel(QWidget):
    """玻璃背景容器（与主窗一致的半透明着色 + 顶部柔光）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def paintEvent(self, event):
        p = QPainter(self)
        paint_window_backdrop(p, self.width(), self.height())
        p.end()
