"""Champion Picker - 液态玻璃风格英雄偏好选择器（左网格 + 右已选列表）"""

from typing import Dict, List
from PyQt5.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QLineEdit, QScrollArea, QGridLayout, QWidget, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize, QTimer, QRectF
from PyQt5.QtGui import QPixmap, QIcon, QColor, QPainter, QLinearGradient

from .window_base import ui_slot
from .styles import Glass
# v4.4.0 修复(P1-16/P1-17): 图标统一走 DPR 感知 + LRU 缓存的工厂
from .icons import icon_pixmap

# v4.4.0 修复(P2 硬编码): 搜索防抖窗与图标边长抽成具名常量
SEARCH_DEBOUNCE_MS = 150        # 搜索防抖(原 0ms, 每字符全量重建网格)
CARD_ICON_PX = 50               # 英雄卡图标边长
LIST_ICON_PX = 28               # 右侧已选列表图标边长

# ===== 模块级一次性加载搜索数据(v17) =====
# 旧实现在 _match 里对每个卡片每次筛选都 try import,重建网格时重复开销大。
# 这里 import 失败(如打包遗漏)也静默降级,不影响功能。
try:
    from data.champion_aliases import CHAMPION_ALIASES as _ALIASES
except Exception:
    _ALIASES = {}
try:
    from data.champion_pinyin import CHAMPION_PINYIN as _PINYIN
except Exception:
    _PINYIN = {}


class ChampionCard(QPushButton):
    """英雄头像卡片（液态玻璃风格）"""

    def __init__(self, cid, name, parent=None):
        super().__init__(parent)
        self.cid = cid
        self.name = name
        self._sel = False
        self._drag_start = None
        self._has_icon = False
        # v4.4.0 修复(P2 死代码): 原此处 `setMinimumSize(48, 52)` 紧接
        # `setFixedSize(56, 60)` —— setFixedSize 同时设 min/max, 前一句被完全覆盖,
        # 永远不生效。已删除(确认 56×60 确实更大且是期望尺寸)。
        self.setFixedSize(56, 60)
        self.setCursor(Qt.PointingHandCursor)
        # v17:不设 tooltip —— PyQt5 默认悬停 tooltip 是系统黑框,观感突兀
        self.setAcceptDrops(True)
        self._apply_normal()

    def _text_qss(self) -> str:
        """无图标时占位文字的样式片段(有图标则返回空串)。

        v4.4.1(UI-B3): 无图标的卡片原来只是一块 rgba(20,22,30,120) 纯色矩形,
        光看墙上的格子分不出是哪位英雄(数据没加载完/图标缺失时尤其明显)。
        现在画名字首二字。严禁在此加 setToolTip —— 见 __init__ 的 v17 注释。
        """
        if self._has_icon:
            return ''
        return (f'color:{Glass.TEXT_SECONDARY};font-size:{Glass.FONT_SIZE_SM};'
                f'font-weight:600;padding:0px;')

    def _apply_normal(self):
        if self._has_icon:
            self.setText('')
            self.setStyleSheet(f"""
                QPushButton{{background:rgba(20,22,30,140);border:none;border-radius:8px;padding:1px;}}
                QPushButton:hover{{background:rgba(99,102,241,0.06);}}
            """)
        else:
            self.setText(self.name[:2] if self.name else '?')
            self.setStyleSheet(f"""
                QPushButton{{background:rgba(20,22,30,120);border:none;border-radius:8px;padding:1px;{self._text_qss()}}}
            """)

    def set_selected(self, sel):
        self._sel = sel
        if sel:
            self.setStyleSheet(f"""
                QPushButton{{background:rgba(99,102,241,0.12);border:1px solid rgba(99,102,241,0.30);border-radius:8px;padding:1px;{self._text_qss()}}}
                QPushButton:hover{{background:rgba(99,102,241,0.18);}}
            """)
        else:
            self._apply_normal()

    def set_icon(self, pix: QPixmap):
        self.setIcon(QIcon(pix))
        self.setIconSize(QSize(CARD_ICON_PX, CARD_ICON_PX))
        self._has_icon = True
        # v4.4.1(UI-B3): 有图标后清掉名字占位文字; 选中态下 _apply_normal 不会被调用,
        # 必须在这里显式清, 否则图标与文字会叠在一起。
        self.setText('')
        if not self._sel:
            self._apply_normal()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.underMouse() or self._sel:
            return
        # 液态玻璃 hover 发光效果
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # 外层光晕
        glow = QColor(99, 102, 241, 25)
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow)
        painter.drawRoundedRect(QRectF(0, 0, w, h), 8, 8)

        # 内层高光
        hl = QLinearGradient(0, 0, 0, h * 0.4)
        hl.setColorAt(0, QColor(255, 255, 255, 12))
        hl.setColorAt(1, QColor(255, 255, 255, 0))
        painter.setBrush(hl)
        painter.drawRoundedRect(QRectF(1, 1, w - 2, h * 0.4), 8, 8)

        painter.end()

    def mousePressEvent(self, event):
        self._drag_start = event.pos() if event.button() == Qt.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._sel and self._drag_start and (event.buttons() & Qt.LeftButton):
            if (event.pos() - self._drag_start).manhattanLength() > 20:
                from PyQt5.QtGui import QDrag, QMimeData
                drag = QDrag(self)
                mime = QMimeData()
                mime.setData('application/x-champion-id', str(self.cid).encode())
                drag.setMimeData(mime)
                drag.exec_(Qt.MoveAction)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat('application/x-champion-id'):
            event.acceptProposedAction()

    def dropEvent(self, event):
        data = event.mimeData().data('application/x-champion-id')
        if data:
            src_cid = int(data.data().decode())
            if src_cid != self.cid:
                picker = self.parent()
                while picker and not isinstance(picker, ChampionPickerWidget):
                    picker = picker.parent()
                if picker:
                    picker._swap_by_drag(src_cid, self.cid)
        event.acceptProposedAction()


class ChampionPickerWidget(QWidget):
    """英雄偏好选择器 - 液态玻璃风格"""
    champions_selected = pyqtSignal(list)
    # v4.4.0 修复(P2 死代码): 原此处有 `icon_ready = pyqtSignal(int, bytes)`,
    # 全项目 grep 确认只有定义行、**从未 emit 也从未 connect**(图标回填走
    # _tick_icon_load → _apply_icon 直调, 不经信号)。已删除。

    ROLES = ['全部', '射手', '法师', '战士', '刺客', '坦克', '辅助']
    ROLE_EN = {'射手': 'marksman', '法师': 'mage', '战士': 'fighter',
               '刺客': 'assassin', '坦克': 'tank', '辅助': 'support'}
    MAX_COLS = 6

    def __init__(self, champ_data, preferred, icon_cb=None, parent=None,
                 champion_roles=None, download_cb=None, **kw):
        super().__init__(parent)
        self._data = champ_data
        self._roles = champion_roles or {}
        self._preferred = list(preferred)
        self._icon_cb = icon_cb
        self._dl_cb = download_cb
        # v4.3.10: 可选注入"该 id 是否有下载在途"的查询函数 —— 用于避免把
        # 慢速下载误判成失败(见 _tick_icon_load)。未注入时退化为原来的行为。
        self._is_dl_inflight = kw.get('is_dl_inflight')
        self._cards: Dict[int, ChampionCard] = {}
        self._all_ids: List[int] = []
        self._role = '全部'
        self._search = ''
        # v4.4.0 修复(P1-20): 上一轮的可见集合/选中态, 用于"只更新发生变化的卡片"
        self._shown_ids = set()
        self._sel_state: Dict[int, bool] = {}
        self._pending_search = None
        self._init_ui()

    def _init_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # ===== 左侧：搜索 + 英雄网格 =====
        left = QWidget()
        left.setStyleSheet("background:transparent;")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)

        # 搜索 + 筛选
        fr = QHBoxLayout()
        fr.setSpacing(4)
        self._input = QLineEdit()
        self._input.setPlaceholderText('🔍 搜索')
        self._input.setMaximumWidth(140)
        self._input.setStyleSheet(Glass.INPUT_STYLE)
        # v4.4.0 修复(P1-20): 原来 textChanged 直连 _on_search —— 每敲一个字符
        # 都同步跑一遍 _rebuild_grid(), 对全部 ~170 张卡片做匹配 + addWidget +
        # setVisible + set_selected(内含 setStyleSheet)。中文输入法一次上屏会
        # 连发多个 textChanged, 手动关掉防抖时输入明显卡顿。改为 150ms 单次防抖。
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._on_search_debounced)
        self._input.textChanged.connect(self._on_search_text_changed)
        fr.addWidget(self._input)

        self._role_btns = {}
        for role in self.ROLES:
            b = QPushButton(role)
            b.setFixedHeight(24)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._role_style(role == '全部'))
            b.clicked.connect(lambda _, r=role: self._filter(r))
            self._role_btns[role] = b
            fr.addWidget(b)
        fr.addStretch()
        ll.addLayout(fr)

        # 网格（带滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + Glass.SCROLL_QSS)
        inner = QWidget()
        inner.setStyleSheet('background:transparent;')
        self._gl = QGridLayout(inner)
        self._gl.setContentsMargins(0, 0, 0, 0)
        self._gl.setSpacing(6)
        self._gl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        scroll.setWidget(inner)
        ll.addWidget(scroll, 1)

        lay.addWidget(left, 3)

        # ===== 右侧：已选偏好列表 =====
        right = QWidget()
        right.setFixedWidth(160)
        right.setStyleSheet(f"""
            QWidget{{
                background: {Glass.BG_LIST_ITEM};
                border: none;
                border-radius: {Glass.RADIUS_MD};
            }}
        """)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)
        rl.setSpacing(6)

        self._sel_title = QLabel(f'已选偏好 ({len(self._preferred)})')
        self._sel_title.setStyleSheet(
            f"font-size:{Glass.FONT_SIZE_SM};font-weight:600;color:{Glass.TEXT_PRIMARY};background:transparent;")
        rl.addWidget(self._sel_title)

        self._sel_list = QListWidget()
        self._sel_list.setDragDropMode(QListWidget.InternalMove)
        self._sel_list.setDefaultDropAction(Qt.MoveAction)
        self._sel_list.setStyleSheet(f"""
            QListWidget {{
                background: transparent;
                border: none;
                outline: none;
            }}
            QListWidget::item {{
                background: {Glass.BG_LIST_ROW};
                /* v4.4.1(UI-A3): 原为 border:none, 而 :selected/:hover 只改
                   border-color —— border-style 为 none 时颜色永不生效,
                   两态实际只剩背景微变。改成 1px 透明实线后描边即刻可见,
                   border-box 布局不变、不会抖动。 */
                border: 1px solid transparent;
                border-radius: 6px;
                padding: 4px 6px;
                margin-bottom: 2px;
                color: {Glass.TEXT_PRIMARY};
                font-size: {Glass.FONT_SIZE_XS};
            }}
            QListWidget::item:selected {{
                background: rgba(99, 102, 241, 0.1);
                border-color: rgba(99, 102, 241, 0.4);
            }}
            QListWidget::item:hover {{
                border-color: rgba(99, 102, 241, 0.2);
            }}
        """ + Glass.SCROLL_QSS)
        self._sel_list.setIconSize(QSize(28, 28))
        self._sel_list.setSpacing(2)
        self._sel_list.model().rowsMoved.connect(self._on_drag_reorder)
        rl.addWidget(self._sel_list, 1)

        # 排序按钮
        sort_row = QHBoxLayout()
        sort_row.setSpacing(4)

        btn_style = f"""
            QPushButton{{
                background: {Glass.BG_LIST_ROW}; color: {Glass.TEXT_SECONDARY};
                border: none; border-radius: 6px;
                font-size: {Glass.FONT_SIZE_LG}; font-weight: bold; min-width: 28px;
            }}
            QPushButton:hover{{ border-color: rgba(99,102,241,0.3); color: {Glass.TEXT_PRIMARY}; }}
            QPushButton:pressed{{ padding-top: 1px; }}
        """

        up_btn = QPushButton('↑')
        up_btn.setFixedSize(28, 24)
        up_btn.setCursor(Qt.PointingHandCursor)
        up_btn.setStyleSheet(btn_style)
        up_btn.clicked.connect(self._move_up)
        sort_row.addWidget(up_btn)

        dn_btn = QPushButton('↓')
        dn_btn.setFixedSize(28, 24)
        dn_btn.setCursor(Qt.PointingHandCursor)
        dn_btn.setStyleSheet(btn_style)
        dn_btn.clicked.connect(self._move_down)
        sort_row.addWidget(dn_btn)

        sort_row.addStretch()

        clear_btn = QPushButton('清空')
        clear_btn.setFixedHeight(24)
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.setStyleSheet(f"""
            QPushButton{{background:transparent;color:{Glass.TEXT_MUTED};border:1px solid rgba(255,255,255,0.04);border-radius:6px;font-size:{Glass.FONT_SIZE_XS};padding:0 6px;}}
            QPushButton:hover{{color:{Glass.DANGER};border-color:rgba(248,113,113,0.3);}}
        """)
        clear_btn.clicked.connect(self._clear)
        sort_row.addWidget(clear_btn)
        rl.addLayout(sort_row)

        save_btn = QPushButton('保存')
        save_btn.setFixedHeight(32)
        save_btn.setCursor(Qt.PointingHandCursor)
        save_btn.setStyleSheet(f'QPushButton{{background:{Glass.PRIMARY};color:#fff;border:none;border-radius:6px;font-weight:bold;font-size:{Glass.FONT_SIZE_MD};}} QPushButton:hover{{background:{Glass.PRIMARY_HOVER};}}')
        save_btn.clicked.connect(self._save)
        rl.addWidget(save_btn)

        lay.addWidget(right, 1)

        # 初始化
        self._all_ids = sorted(c for c in self._data if c > 0)
        self._build_grid()
        self._rebuild_grid()
        self._refresh_sel_list()

        # 图标加载
        if self._dl_cb:
            self._retry_ids = list(self._all_ids)
            self._fail_count: dict = {}  # G2:记录各英雄下载连续失败次数,超限放弃防无限重试
            self._it = QTimer(self)
            self._it.setInterval(1000)
            self._it.timeout.connect(self._tick_icon_load)
            QTimer.singleShot(500, self._it.start)

    def _role_style(self, active):
        if active:
            return f"""
                QPushButton{{
                    background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {Glass.PRIMARY_HOVER}, stop:1 {Glass.PRIMARY});
                    color: white; border: none; border-radius: 6px; font-size: {Glass.FONT_SIZE_XS}; padding: 0 6px; font-weight: bold;
                }}
            """
        return f"""
            QPushButton{{
                background: transparent; color: {Glass.TEXT_SECONDARY};
                border: none; border-radius: 6px; font-size: {Glass.FONT_SIZE_XS}; padding: 0 6px;
            }}
            QPushButton:hover{{ border-color: rgba(99,102,241,0.3); color: {Glass.TEXT_PRIMARY}; }}
        """

    @ui_slot
    def _filter(self, role):
        self._role = role
        for r, b in self._role_btns.items():
            b.setStyleSheet(self._role_style(r == role))
        self._rebuild_grid()

    @ui_slot
    def _on_search_text_changed(self, text):
        """v4.4.0 修复(P1-20): 只记录文本并重启 150ms 防抖定时器。

        另: 用 `_pending_search` 记住当前输入, 定时器到点时若文本没再变就只跑一次。
        """
        self._pending_search = text
        if not hasattr(self, '_search_timer') or self._search_timer is None:
            self._on_search(text)
            return
        self._search_timer.stop()
        self._search_timer.start()

    @ui_slot
    def _on_search_debounced(self):
        """防抖到点: 一次性应用搜索条件。"""
        text = getattr(self, '_pending_search', None)
        if text is None:
            return
        self._on_search(text)

    def _on_search(self, text):
        text = (text or '').strip()
        if text == self._search:
            # v4.4.0 修复(P1-20): 文本没变就不重建(输入法有时发重复 textChanged)
            return
        self._search = text
        self._rebuild_grid()

    def _match(self, cid):
        """命中规则(v17): 官方名 / 别名(中英文外号) / 拼音(全拼或缩写)
        覆盖 女枪、文森特、delaiwen、dlw、gailun、gl 这类输入:
        - 中文词(官方名/别名): 子串匹配,宽容
        - 拼音全拼 f / 缩写 a: 前缀匹配,避免子串跨音节误命中(如 'gl' 不应命中一大片)
        """
        if not self._search:
            return True
        q = self._search.lower()
        name = self._data.get(cid, '').lower()
        if q in name:
            return True
        for a in _ALIASES.get(cid, ()):
            if q in a.lower():
                return True
        py = _PINYIN.get(cid)
        if py:
            for f in py.get('f', ()):
                if f.startswith(q):
                    return True
            for a in py.get('a', ()):
                if a.startswith(q):
                    return True
        return False

    def _match_role(self, cid):
        if self._role == '全部':
            return True
        target = self.ROLE_EN.get(self._role)
        roles = self._roles.get(cid, [])
        return target in roles if target else False

    def _build_grid(self):
        for cid in self._all_ids:
            name = self._data.get(cid, f'ID:{cid}')
            card = ChampionCard(cid, name)
            card.clicked.connect(lambda _, c=cid: self._on_card_click(c))
            self._cards[cid] = card

    def _rebuild_grid(self):
        """按当前 搜索词+定位 过滤网格。

        v4.4.0 修复(P1-20): 原来对**全部**卡片无条件做两轮遍历
        (`setVisible(cid in shown)` + `set_selected(cid in self._preferred)`),
        而 set_selected 内含 setStyleSheet —— 敲一个字就触发几百次 QSS 解析。
        现在只动"可见性真正发生变化"的卡片, 并维持原有"只对可见卡片设选中态"
        之外的行为不变(选中态仅在与上次不同时才 set_selected)。
        """
        shown = set()
        r = c = 0
        for cid in self._all_ids:
            if self._match(cid) and self._match_role(cid) and cid in self._cards:
                self._gl.addWidget(self._cards[cid], r, c)
                self._cards[cid].show()
                shown.add(cid)
                c += 1
                if c >= self.MAX_COLS:
                    c = 0
                    r += 1
        prev_shown = getattr(self, '_shown_ids', None)
        for cid, card in self._cards.items():
            should_show = cid in shown
            if prev_shown is None or should_show != (cid in prev_shown):
                card.setVisible(should_show)
        self._shown_ids = shown
        # 选中态: 仅与上次不同时重写样式
        prev_sel = getattr(self, '_sel_state', None)
        for cid, card in self._cards.items():
            sel = cid in self._preferred
            if prev_sel is None or prev_sel.get(cid) != sel:
                card.set_selected(sel)
        self._sel_state = {cid: (cid in self._preferred) for cid in self._cards}

    def _set_card_selected(self, cid, sel):
        """v4.4.0 修复(P1-20): set_selected 带样式重写, 统一走这里保持 _sel_state 同步。"""
        card = self._cards.get(cid)
        if card is None:
            return
        sel = bool(sel)
        if self._sel_state.get(cid) == sel:
            return
        card.set_selected(sel)
        self._sel_state[cid] = sel

    def _on_card_click(self, cid):
        try:
            if cid in self._preferred:
                self._preferred.remove(cid)
            else:
                self._preferred.append(cid)
            self._set_card_selected(cid, cid in self._preferred)
            self._refresh_sel_list()
        except Exception as e:
            print(f'[Picker] 点击错误: {e}')

    def _refresh_sel_list(self):
        self._sel_list.clear()
        for i, cid in enumerate(self._preferred):
            name = self._data.get(cid, f'ID:{cid}')
            item = QListWidgetItem(f'≡ {name}')
            item.setData(Qt.UserRole, cid)
            item.setSizeHint(QSize(0, 32))
            if self._icon_cb:
                try:
                    d = self._icon_cb(cid)
                    if d and isinstance(d, (bytes, bytearray)):
                        # v4.4.0 修复(P1-16/P1-17): 走 DPR 感知 + LRU 缓存的工厂
                        pix = icon_pixmap(d, LIST_ICON_PX, widget=self,
                                          keep_aspect=True)
                        if pix is not None:
                            item.setIcon(QIcon(pix))
                except Exception:
                    pass
            self._sel_list.addItem(item)
        self._sel_title.setText(f'已选偏好 ({len(self._preferred)})')
        # v4.4.0 修复(P1-20): _preferred 可能被外部直接改写(如
        # main_window.init_champion_picker 里的 `picker._preferred = list(...)`),
        # 这里顺带把卡片的选中态补齐, 并保持 _sel_state 与之一致。
        for cid in self._cards:
            self._set_card_selected(cid, cid in self._preferred)

    def _on_drag_reorder(self):
        try:
            new_order = []
            for i in range(self._sel_list.count()):
                item = self._sel_list.item(i)
                cid = item.data(Qt.UserRole)
                if cid is not None:
                    new_order.append(cid)
            if len(new_order) == len(self._preferred):
                self._preferred = new_order
                self._refresh_sel_list_no_rebuild()
        except Exception as e:
            print(f'[Picker] 拖拽排序错误: {e}')

    def _refresh_sel_list_no_rebuild(self):
        for i in range(self._sel_list.count()):
            item = self._sel_list.item(i)
            cid = item.data(Qt.UserRole)
            if cid is not None:
                name = self._data.get(cid, f'ID:{cid}')
                item.setText(f'≡ {name}')

    @ui_slot
    def _move_up(self):
        row = self._sel_list.currentRow()
        if row <= 0 or row >= len(self._preferred):
            return
        self._preferred[row], self._preferred[row - 1] = self._preferred[row - 1], self._preferred[row]
        self._refresh_sel_list()
        self._sel_list.setCurrentRow(row - 1)

    @ui_slot
    def _move_down(self):
        row = self._sel_list.currentRow()
        if row < 0 or row >= len(self._preferred) - 1:
            return
        self._preferred[row], self._preferred[row + 1] = self._preferred[row + 1], self._preferred[row]
        self._refresh_sel_list()
        self._sel_list.setCurrentRow(row + 1)

    def _swap_by_drag(self, src_cid, dst_cid):
        if src_cid not in self._preferred or dst_cid not in self._preferred:
            return
        i = self._preferred.index(src_cid)
        j = self._preferred.index(dst_cid)
        self._preferred[i], self._preferred[j] = self._preferred[j], self._preferred[i]
        self._refresh_sel_list()

    def _tick_icon_load(self):
        try:
            loaded = 0
            new_dl = 0
            remaining = []
            for cid in self._retry_ids:
                if self._icon_cb:
                    d = self._icon_cb(cid)
                    if d and isinstance(d, (bytes, bytearray)):
                        self._apply_icon(cid, d)
                        self._refresh_sel_icon(cid, d)
                        self._fail_count.pop(cid, None)
                        loaded += 1
                        continue
                remaining.append(cid)
            self._retry_ids = remaining
            drops = []
            for cid in self._retry_ids[:16]:
                # G2:持续拿不到(下载真的失败)达上限就放弃该英雄, 避免 1s/16 个的
                # 网络请求无限重试到程序退出。
                # v4.3.10 修复: 原来**每 tick 无条件 +1** —— 网络慢时单张下载
                # 超过 8 秒就会被记满 8 次而"永久放弃", 本会话内该英雄图标再也
                # 不加载(用户看到的是"部分英雄没有图标")。现在只在
                # **确实没有下载在途**时才计失败, 慢速下载不计。
                if self._is_dl_inflight and self._is_dl_inflight(cid):
                    new_dl += 1          # 仍在下载中, 不算失败也不重复提交
                    continue
                self._fail_count[cid] = self._fail_count.get(cid, 0) + 1
                if self._fail_count[cid] >= 8:
                    drops.append(cid)
                elif self._dl_cb:
                    try:
                        self._dl_cb(cid)
                    except Exception:
                        pass
                    new_dl += 1
            if drops:
                for cid in drops:
                    try:
                        self._retry_ids.remove(cid)
                    except ValueError:
                        pass
                    self._fail_count.pop(cid, None)
                print(f'[Picker] {len(drops)}个图标下载超限放弃: {drops}')
            if loaded > 0 or new_dl > 0:
                print(f'[Picker] 加载{loaded}个 下载{new_dl}个 剩余{len(self._retry_ids)}个')
            if not self._retry_ids:
                self._it.stop()
                print('[Picker] 所有图标加载完毕')
        except Exception as e:
            print(f'[Picker] _tick_icon_load错误: {e}')

    def _refresh_sel_icon(self, cid, data):
        if cid not in self._preferred:
            return
        for i in range(self._sel_list.count()):
            item = self._sel_list.item(i)
            if item and item.data(Qt.UserRole) == cid:
                try:
                    # v4.4.0 修复(P1-16/P1-17): 走 DPR 感知 + LRU 缓存的工厂
                    pix = icon_pixmap(data, LIST_ICON_PX, widget=self,
                                      keep_aspect=True)
                    if pix is not None:
                        item.setIcon(QIcon(pix))
                except Exception:
                    pass
                break

    def _apply_icon(self, cid, data):
        try:
            if cid in self._cards and data:
                # v4.4.0 修复(P1-16/P1-17): 走 DPR 感知 + LRU 缓存的工厂
                pix = icon_pixmap(data, CARD_ICON_PX, widget=self,
                                  keep_aspect=True)
                if pix is not None:
                    self._cards[cid].set_icon(pix)
                else:
                    # v16:解析失败不再静默。坏数据理论上已被 main 层 PNG 魔数校验
                    # 拦截,这里仅打日志兜底暴露问题(不无限重试 —— 见下)
                    print(f'[Picker] 图标解析失败 cid={cid} size={len(data)}')
        except Exception as e:
            print(f'[Picker] 图标应用异常 cid={cid}: {type(e).__name__}: {e}')

    @ui_slot
    def _clear(self):
        self._preferred.clear()
        for card in self._cards.values():
            card.set_selected(False)
        self._refresh_sel_list()

    def _save(self):
        try:
            names = [self._data.get(cid, str(cid)) for cid in self._preferred]
            print(f'[英雄] 保存偏好: {len(self._preferred)}个 → {", ".join(names[:5])}{"..." if len(names) > 5 else ""}')
            self.champions_selected.emit(list(self._preferred))
        except Exception as e:
            print(f'[Picker] 保存失败: {e}')
