# -*- coding: utf-8 -*-
"""Activation Dialog - 激活弹窗（机器绑定 + 授权码）

风格与 agreement_dialog 一致：无边框 + Acrylic 玻璃 + 靛蓝霓虹描边，
走 ui_slot（槽内异常只吞这一次刷新，不炸进程）。

## 交互设计上的取舍（都是踩过的坑）

1. **机器码用只读 QLineEdit + 「复制」按钮**，不放 QLabel。
   只读输入框天然可全选、可 Ctrl+C，用户能自己复制；配一个显式复制按钮
   是为了照顾"不知道能选中"的用户。实测中长串码手抄错误率极高，
   所以界面上直接写明"请用复制按钮，不要手抄"。

2. **授权码输入用 QPlainTextEdit 而不是 QLineEdit**。
   授权码 164 字符，QLineEdit 单行会横向滚动，用户粘完看不到全貌、
   会以为没粘上。多行框自动换行，能一眼看到整段。
   粘贴时自动清理连字符/空格/大小写（licensing.b32d 负责）。

3. **激活成功与否都留在同一个弹窗**，不弹二级提示框。
   失败原因（抄错 / 不是本机 / 过期）必须原地可见并可立即重试，
   弹窗套弹窗会让用户关掉后就找不到重试入口。

4. **倒计时/强制阅读那套不适用于这里** —— 激活是可选动作
   （已激活用户直接进主界面），只有"未激活"时才会被拉起。
"""
import threading

from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QGuiApplication, QPainter
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QLineEdit, QPlainTextEdit, QScrollArea
)

from .window_base import ui_slot
from .styles import Glass
from .animated_widgets import paint_window_backdrop
from . import screen_utils
from core import license_crypto as LC

# v4.9.3: 删除 `CARD_BYTES_LEN = 20` 与 `_HELP_ONLINE_HTML` ——
# 它们是"在线激活（卡密 → 服务器换码）"这条路径的产物。按用户要求整个在线
# 服务端已移除（license-server/src/worker.js 等），客户端现在**只认授权码**：
# 卖家在本机用 tools/make_license.py 签发，买家粘贴 → 离线验签。
# 留着卡密分支会让界面告诉买家"粘卡密、几秒搞定"，而他手里根本没有卡密。

# 未激活时展示的引导文案（HTML）。语气与 agreement_dialog 保持一致。
_HELP_OFFLINE_HTML = """
<style>
  p  { color: #c8c8d8; font-size: 12px; line-height: 1.65; margin: 3px 0; }
  li { color: #c8c8d8; font-size: 12px; line-height: 1.65; margin: 2px 0 2px 2px; }
  .hl   { color: #a5b4fc; }
  .warn { color: #fbbf24; }
</style>
<p><span class="hl">怎么激活（需要卖家给你授权码）：</span></p>
<ul>
  <li>① 点下面「复制机器码」，把机器码发给你购买时的卖家</li>
  <li>② 卖家会回你一串<span class="hl">授权码</span>（约 160 个字符）</li>
  <li>③ 把授权码粘到下面的输入框，点「立即激活」</li>
</ul>
<p><span class="warn">注意：</span>授权码是<b>绑定这台电脑</b>的。换电脑、换主板或重装系统后
需要重新找卖家要一个码。</p>
"""


class ActivationDialog(QWidget):
    """激活窗口：未激活时由启动流程拉起，激活成功后关闭。"""

    activated = pyqtSignal()          # 激活成功
    # v4.9.3: 机器码采集在后台线程完成 → 经本信号回主线程填控件。
    # 用信号而不是直接 setText：Qt 控件只能在 GUI 线程碰。
    _machine_ready = pyqtSignal(str)

    # v: 为了不打断"先试用后激活"的流程，提供"暂不激活"入口；
    #    是否允许跳过由调用方（main.py）决定，见 allow_skip 参数。
    def __init__(self, parent=None, allow_skip=True, machine_id=None, reason=''):
        super().__init__(parent)
        self._machine_id = machine_id
        self._reason = reason
        self._allow_skip = allow_skip
        self._done = False
        # v4.9.3: 删除 `self._online`（原来读 licensing.activation_available()）。
        # 在线激活服务已整体移除，这个标志恒为 False，界面上所有
        # "卡密 / 自助激活" 分支因此都是死分支 —— 现已一并删除。
        self._worker = None

        self.setWindowTitle('大蛋小助手 · 激活')
        # 与 agreement_dialog 同款：Qt.Window（非 Dialog）避免重置 DWM backdrop
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowModality(Qt.ApplicationModal)
        self.setFixedSize(620, 620)

        screen_utils.center_on_screen(self)
        self._init_ui()
        # v4.9.3: 机器码采集**不能**在构造期同步跑。
        # 实测 `collect_components()` 冷启动 spawn PowerShell 耗时 **1.51 s**
        # （subprocess 超时上限 30 s），而本方法在 GUI 线程 —— 窗口在这段时间里
        # 是白屏，Windows 可能直接标"未响应"。这与 main.py 里作者自己写的
        # "不能阻塞 GUI 线程"是同一条纪律，激活窗这条路径原来漏了。
        # 改为：先显示占位，再后台采集，最后经信号回填主线程。
        self._machine_ready.connect(self._on_machine_ready)
        self._mid_edit.setText('读取中…')
        QTimer.singleShot(0, self._start_machine_probe)

    def _start_machine_probe(self):
        """后台线程采集机器码，结果经信号回主线程（不阻塞 GUI）。

        v4.9.3 新增。`machine_id()` 内部会走 `collect_components()` →
        PowerShell 子进程；采集完后 `_CACHE` 里就有值，后续调用是纯内存。
        """
        def worker():
            try:
                from core import licensing as _L
                mid = self._machine_id or _L.machine_id_grouped()
            except Exception as e:
                mid = ''
                try:
                    print(f'[Activation] 机器码采集失败: {type(e).__name__}: {e}')
                except Exception:
                    pass
            try:
                self._machine_ready.emit(str(mid or ''))
            except Exception:
                pass

        try:
            threading.Thread(target=worker, daemon=True, name='lic-machine').start()
        except Exception as e:
            print(f'[Activation] 机器码采集线程启动失败: {type(e).__name__}: {e}')
            self._on_machine_ready('')

    @ui_slot
    def _on_machine_ready(self, mid):
        """主线程：把后台采集到的机器码填进控件。"""
        self._machine_id = mid
        if not mid:
            self._set_status('无法读取本机信息，请稍后重试或联系卖家。', 'err')
            self._mid_edit.setText('')
            return
        self._mid_edit.setText(mid)
        self._mid_edit.setCursorPosition(0)
        n = len(mid.replace('-', ''))
        self._sub.setText(f'本软件需激活后使用 · 机器码 {n} 字符，与本机绑定')

    # ------------------------------------------------------------ UI

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(_GlassPanel())

        panel = outer.itemAt(0).widget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(11)

        # ---- 标题 ----
        title = QLabel('激活大蛋小助手')
        title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_XL};font-weight:800;"
            f"color:{Glass.TEXT_PRIMARY};background:transparent;")
        lay.addWidget(title)

        self._sub = QLabel('本软件需激活后使用，授权与这台电脑绑定')
        self._sub.setWordWrap(True)
        self._sub.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};color:{Glass.TEXT_MUTED};background:transparent;")
        lay.addWidget(self._sub)

        # 试用期横幅：明确告诉买家"现在还能用"，把"必须立刻激活"的压力去掉。
        # 这也降低了老用户升级后被门禁吓到的概率。
        #
        # v4.9.3: 原来这里和 :280 各调一次 `licensing.check()` —— 两次都完整跑
        #   `collect_components()`（每次 spawn PowerShell，实测冷启动 1.51 s）
        #   + One-shot 纯 Python Ed25519 验签（实测 129.4 ms）。主线程连续两次，
        #   首开激活窗最坏 3 s+ 白屏。现在**一次 check() 结果复用两次**。
        self._lic_snapshot = None
        try:
            from core import licensing as _Lc
            self._lic_snapshot = _Lc.check()
        except Exception as e:
            print(f'[Activation] 试用状态读取失败: {type(e).__name__}: {e}')
        try:
            _s = self._lic_snapshot
            if _s is not None and _s.trial and _s.usable:
                self._sub.setText(
                    f'试用中 · 还可使用 {_s.trial_days_left:.1f} 天（激活为长期使用）')
                self._sub.setStyleSheet(
                    f"font-size:{Glass.FONT_SIZE_SM};color:{Glass.WARNING};"
                    f"background:transparent;font-weight:600;")
        except Exception:
            pass

        lay.addWidget(_hline())

        # ---- 机器码 ----
        # v4.9.3: 机器码是**必经步骤** —— 买家要把它发给卖家，卖家才能签出
        # 绑定这台机器的授权码。原先有一句"自助激活可不用管"（服务器会自动带
        # 机器码），那条路径已随在线服务一起移除。
        self._mid_title = _section_label('① 你的机器码（发给卖家）')
        lay.addWidget(self._mid_title)

        row = QHBoxLayout()
        row.setSpacing(8)
        self._mid_edit = QLineEdit()
        self._mid_edit.setReadOnly(True)
        self._mid_edit.setCursorPosition(0)
        self._mid_edit.setStyleSheet(Glass.INPUT_STYLE + f"""
            QLineEdit {{
                font-family: 'Cascadia Code','Consolas',monospace;
                font-size: {Glass.FONT_SIZE_SM};
                color: {Glass.PRIMARY_HOVER};
                letter-spacing: 0.5px;
            }}
        """)
        self._mid_edit.setMinimumHeight(36)
        row.addWidget(self._mid_edit, 1)

        self._btn_copy = QPushButton('复制')
        self._btn_copy.setFixedSize(74, 36)
        self._btn_copy.setCursor(Qt.PointingHandCursor)
        self._btn_copy.clicked.connect(self._copy_machine_id)
        row.addWidget(self._btn_copy)
        lay.addLayout(row)
        self._style_ghost_btn(self._btn_copy)

        # ---- 授权码输入 ----
        # v4.9.3: 原先是"卡密 / 授权码"双路径（`if self._online`），
        # 在线服务移除后只剩授权码一条路。
        lay.addWidget(_section_label('② 粘贴卖家给你的授权码'))

        self._code_edit = QPlainTextEdit()
        self._code_edit.setPlaceholderText(
            '粘贴授权码（连字符和空格会自动忽略，不区分大小写）')
        self._code_edit.setFixedHeight(96)
        self._code_edit.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {Glass.BG_INPUT};
                color: {Glass.TEXT_PRIMARY};
                border: 1px solid {Glass.NEON_BORDER};
                border-radius: {Glass.RADIUS_SM};
                padding: 8px 10px;
                font-family: 'Cascadia Code','Consolas',monospace;
                font-size: {Glass.FONT_SIZE_SM};
                selection-background-color: {Glass.PRIMARY};
            }}
            QPlainTextEdit:focus {{ border-color: {Glass.PRIMARY}; }}
            {Glass.SCROLL_QSS}
        """)
        lay.addWidget(self._code_edit)

        # ---- 状态反馈（成功/失败都在这） ----
        self._status = QLabel('')
        self._status.setWordWrap(True)
        self._status.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};color:{Glass.TEXT_MUTED};background:transparent;")
        self._status.setMinimumHeight(34)
        lay.addWidget(self._status)

        if self._reason:
            self._set_status(self._reason, 'warn')

        # 试用期提示：告诉买家"你现在还能用"，避免他以为必须马上激活。
        # 这条信息对转化很关键 —— 用户知道有缓冲，才不会因为激活麻烦而退款。
        # v4.9.3: 复用上面那份 `_lic_snapshot`，不再第二次调 check()（见那处说明）。
        try:
            _st = getattr(self, '_lic_snapshot', None)
            if _st is not None and _st.trial and _st.usable:
                self._set_status(
                    f'试用中：还可使用 {_st.trial_days_left:.1f} 天。'
                    '现在激活即可长期使用（激活后不受网络影响）。', 'info')
        except Exception as e:
            print(f'[Activation] 试用状态展示失败: {type(e).__name__}: {e}')

        # ---- 说明（可滚动，避免小屏被挤扁） ----
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            f"QScrollArea{{background:rgba(12,14,24,0.45);"
            f"border:1px solid {Glass.NEON_BORDER};border-radius:{Glass.RADIUS_CARD};}}"
            f"QScrollArea > QWidget > QWidget {{background:transparent;}}"
            + Glass.SCROLL_QSS)
        body = QLabel(_HELP_OFFLINE_HTML)
        body.setWordWrap(True)
        body.setTextFormat(Qt.RichText)
        body.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        body.setStyleSheet('background:transparent;')
        body.setContentsMargins(10, 8, 10, 8)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        # ---- 底部按钮 ----
        bottom = QHBoxLayout()
        bottom.setSpacing(10)
        bottom.addStretch()

        self._btn_skip = QPushButton('暂不激活')
        self._btn_skip.setFixedSize(110, 42)
        self._btn_skip.setCursor(Qt.PointingHandCursor)
        self._btn_skip.clicked.connect(self._skip)
        bottom.addWidget(self._btn_skip)
        self._style_ghost_btn(self._btn_skip)

        # ---- 按钮文字：自助激活时叫「激活」，离线时叫「激活」（同一个动作）
        self._btn_ok = QPushButton('立即激活')
        self._btn_ok.setFixedSize(150, 42)
        self._btn_ok.setCursor(Qt.PointingHandCursor)
        self._btn_ok.setDefault(True)
        self._btn_ok.clicked.connect(self._do_activate)
        bottom.addWidget(self._btn_ok)
        self._style_primary_btn(self._btn_ok)

        if not self._allow_skip:
            self._btn_skip.hide()

        lay.addLayout(bottom)
    # ------------------------------------------------------------ 样式

    @staticmethod
    def _style_primary_btn(btn):
        btn.setStyleSheet(f"""
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
            QPushButton:pressed {{ background: {Glass.PRIMARY_PRESSED}; padding-top: 2px; }}
            QPushButton:disabled {{
                background: rgba(255,255,255,0.06); color: {Glass.TEXT_MUTED};
            }}
        """)

    @staticmethod
    def _style_ghost_btn(btn):
        btn.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.06);
                color: {Glass.TEXT_SECONDARY};
                border: 1px solid {Glass.BORDER_GLASS};
                border-radius: {Glass.RADIUS_BTN};
                font-size: {Glass.FONT_SIZE_SM}; font-weight: 600;
            }}
            QPushButton:hover {{
                background: {Glass.BG_HOVER};
                border-color: {Glass.NEON_BORDER_HOVER};
                color: {Glass.TEXT_PRIMARY};
            }}
            QPushButton:pressed {{ background: {Glass.BG_PRESSED}; }}
        """)

    def _set_status(self, text, kind='info'):
        color = {'ok': Glass.SUCCESS, 'warn': Glass.WARNING,
                 'err': Glass.DANGER}.get(kind, Glass.TEXT_MUTED)
        self._status.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};color:{color};background:transparent;"
            f"font-weight:{'600' if kind in ('ok', 'err') else '400'};")
        self._status.setText(text)

    # ------------------------------------------------------------ 数据

    # v4.9.3: 删除同步版 `_refresh_machine_id()` —— 它在 GUI 线程直接 spawn
    # PowerShell（实测冷启动 1.51 s）。改为 `_start_machine_probe()` 后台采集 +
    # `_on_machine_ready()` 回填（见 showEvent 处）。保留旧名会让人以为还有
    # 一条同步路径可用。

    @ui_slot
    def _copy_machine_id(self):
        txt = self._mid_edit.text().strip()
        if not txt:
            self._set_status('机器码还没读取出来，稍等一下再试。', 'warn')
            return
        cb = QGuiApplication.clipboard()
        if cb is None:
            self._set_status('无法访问剪贴板，请手动选中机器码后按 Ctrl+C。', 'warn')
            return
        # 复制**不含连字符**的纯码 —— 连字符只是显示用的分组，
        # 卖家那边两种都能解析，但纯码更不容易在微信里被自动换行截断。
        cb.setText(txt.replace('-', ''))
        self._set_status('机器码已复制 ✓ 现在把它发给卖家，等他把授权码发回给你。', 'ok')

    @ui_slot
    def _do_activate(self):
        """点「激活」。

        两条路径：
          · 配了在线服务 → 走后台线程联网（**不能占 GUI 线程**，否则界面假死），
            拿到授权码后本地验签再落盘。
          · 没配 → 纯离线验签（同步即可，只算哈希，几毫秒）。

        两条路径最终都落到同一套存储与校验上，所以离线激活的码
        和在线激活的码在后续使用时完全等价。
        """
        from core import licensing as L

        raw = self._code_edit.toPlainText().strip()
        if not raw:
            self._set_status('请先粘贴授权码。', 'warn')
            self._code_edit.setFocus()
            return

        # 先做一次本地格式预检，能立刻拦掉"明显抄错"
        try:
            blob = LC.b32d(raw)
        except ValueError as e:
            self._set_status(f'内容格式不对：{e}', 'err')
            return
        if len(blob) != LC.BLOB_LEN:
            self._set_status(
                f'长度不对（解出 {len(blob)} 字节；授权码应为 {LC.BLOB_LEN} 字节）。'
                f'请确认整段都复制到了。\n'
                f'如果你拿到的是 32 位「卡密」，那是旧版在线激活用的 —— '
                f'现在请把机器码发给卖家换授权码。', 'err')
            return

        # ---- 离线验签（唯一路径）----
        self._btn_ok.setEnabled(False)
        self._btn_ok.setText('校验中…')
        try:
            st = L.try_activate(raw)
        except Exception as e:
            self._set_status(f'激活过程出错：{type(e).__name__}: {e}', 'err')
            self._reset_ok_btn()
            return
        self._finish_activate_result(st)

    # v4.9.3: 删除 _start_online_activate() 与 _on_online_result() ——
    # 它们是"卡密 → 联网换授权码"的实现（内含一个 QThread 子类）。
    # 在线服务端已整体移除，这两条路径永远不会被调用。

    def _finish_activate_result(self, st):
        """两条路径共用的收尾：成功则提示+发信号，失败则原地可重试。"""
        if st.activated:
            self._done = True
            self._set_status(
                f'激活成功 ✓ 有效期：{st.expires_text}'
                + (f'（剩余 {st.days_left} 天）' if st.days_left is not None else ''),
                'ok')
            self._btn_ok.setText('已完成')
            self.activated.emit()
            # 稍等一下再关，让用户看清"激活成功"（立即关会像是没反应）
            from PyQt5.QtCore import QTimer
            QTimer.singleShot(700, self._finish_close)
            return
        self._set_status(st.reason, 'err')
        self._reset_ok_btn()
        self._code_edit.setFocus()
        self._code_edit.selectAll()

    def _reset_ok_btn(self):
        self._btn_ok.setEnabled(True)
        self._btn_ok.setText('立即激活')

    @ui_slot
    def _skip(self):
        self.close()

    def _finish_close(self):
        self.close()

    # ------------------------------------------------------------ 生命周期

    def closeEvent(self, e):
        # 关窗即视为"未激活离开"；已激活时 activated 信号早已发出，
        # 调用方不该依赖 closeEvent 来判断结果（见更早那段注释的取舍 3）。
        e.accept()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self._allow_skip:
            self.close()
            return
        if (e.key() in (Qt.Key_Return, Qt.Key_Enter)
                and e.modifiers() & Qt.ControlModifier):
            self._do_activate()
            return
        super().keyPressEvent(e)

    def showEvent(self, e):
        super().showEvent(e)
        # 玻璃要在窗口真正可见后才打（否则 SetWindowPos 会把窗口弹出来）
        QTimer.singleShot(60, self._apply_glass)
        # v4.9.3: 机器码采集已在 __init__ 里排了 singleShot(0, _start_machine_probe)
        # 走后台线程；这里不再重复触发（重复触发只会多起一个采集线程）。
        # 首次显示时若采集尚未完成，控件上是"读取中…"占位，完成时经
        # _machine_ready 信号回填 —— 窗口在这期间**不卡**。

    def _apply_glass(self):
        try:
            if not self.isVisible():
                return
            from .glass_effect import apply_liquid_glass
            apply_liquid_glass(self, enable_shadow=False)
        except Exception as e:
            print(f'[Activation] 玻璃效果失败: {type(e).__name__}: {e}')


# ---------------------------------------------------------------- 小组件

def _hline():
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet('background:rgba(99,102,241,0.22);border:none;')
    return line


def _section_label(text):
    lb = QLabel(text)
    lb.setStyleSheet(
        f"font-size:{Glass.FONT_SIZE_SM};color:{Glass.TEXT_SECONDARY};"
        f"background:transparent;font-weight:600;")
    return lb


class _GlassPanel(QWidget):
    """玻璃背景容器（与主窗/须知弹窗一致的半透明着色 + 顶部柔光）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)

    def paintEvent(self, event):
        p = QPainter(self)
        paint_window_backdrop(p, self.width(), self.height())
        p.end()
