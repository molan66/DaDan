"""对局详情弹窗(v4.3.0 重写)。

数据链路: 战绩列表每局只有本人 1 条 participant(腾讯服实测), 全场 10 人必须按
gameId 单拉 `/lol-match-history/v1/games/{gameId}`。该请求由 main 侧**独立线程池**
执行并带**超时兜底**(见 main.AppController.fetch_game_detail) —— 上一版把它丢进
跟图标下载共用的池子 + 全程无超时, 结果弹窗永久停在"正在读取"。

打开流程约定(main_window 侧): **先 show() 再 fetch()**, 并把结果回调丢给
`set_detail`。`set_detail` 用显式 `_closed` 标志判活 —— 不要用 isVisible():
弹窗还没显示时回调就可能已到达(本机 IPC 只要十几毫秒), 会丢数据。

本模块只依赖 ui.match_fmt / ui.styles / ui.icons, 不 import ui.main_window
(否则环形导入)。
"""
import time as _time

from PyQt5.QtCore import Qt, QTimer, QThread
from PyQt5.QtWidgets import (QApplication, QGridLayout, QHBoxLayout, QLabel,
                             QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .styles import Glass
from .icons import rounded_pixmap, augment_pixmap
from .match_fmt import (dur_text, game_mode_text, kda_ratio, kda_text,
                        lane_text, num_text, rate_text, time_text, wan_text)
# v4.4.0 修复(P1-21/P2): 与主窗/小窗共用拖拽 + 玻璃去重实现
from .window_base import DragMixin, GlassEffectMixin, ui_slot

# 胜=红 / 负=绿(国服习惯: 红为正向) —— 令牌单一来源，不再写字面量
WIN_COLOR = Glass.DANGER
LOSE_COLOR = Glass.SUCCESS


def _safe_int(v, default=0):
    """不可靠数值字段安全转 int —— None/空串/非数字一律兜 default, 不再裸 int()。"""
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return default


def _lbl(text, size=Glass.FONT_SIZE_SM, color=Glass.TEXT_PRIMARY, bold=False):
    lb = QLabel(text)
    lb.setStyleSheet(
        f"font-size:{size};color:{color};"
        f"font-weight:{700 if bold else 400};background:transparent;")
    return lb


def _kv(label, width):
    """定宽键值小卡: 上值下标签。定宽必须按最长内容留够(中文 11px ≈ 11px/字)。

    v4.10.x: 加卡片底/圆角/内边距 —— 与主窗 chip 同源, 提升视觉精致度。
    """
    w = QWidget()
    w.setFixedWidth(width)
    w.setStyleSheet(
        f'background: {Glass.BG_CARD_ALT};'
        f'border: 1px solid {Glass.BORDER_GLASS};'
        f'border-radius: {Glass.RADIUS_SM}px;')
    lay = QVBoxLayout(w)
    lay.setContentsMargins(6, 5, 6, 5)
    lay.setSpacing(1)
    val = _lbl('—', Glass.FONT_SIZE_MD, Glass.TEXT_PRIMARY, True)
    # v4.10.x: 数值过长(elide)而不是溢出/被硬裁 —— 卡宽固定, 超长加省略号+tooltip
    val.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    key = _lbl(label, Glass.FONT_SIZE_XS, Glass.TEXT_MUTED)
    lay.addWidget(val)
    lay.addWidget(key)
    w.val = val

    def set_value(text):
        text = str(text)
        try:
            fm = val.fontMetrics()
            shown = fm.elidedText(text, Qt.ElideRight, max(1, width - 2))
        except Exception:
            shown = text
        val.setText(shown)
        val.setToolTip(text if shown != text else '')

    w.set_value = set_value
    return w


class _IconBox(QLabel):
    """头像 / 装备 / 技能 / 海克斯通用图标格: 自带占位 + 轮询补图。

    kind: 'champ' | 'item' | 'spell' | 'augment'; key 为对应 id(<=0 空槽位)。
    失败超窗后不再重试 —— 防某个 id 永远没有图时每 500ms 空打网络。

    v4.4.20: 新增 rarity 形参。仅 'augment' 用到 —— 官方海克斯小图标九成是
    **纯白剪影蒙版**, 必须按稀有度染色才看得出区别(详见 main 侧 _aug_rarity_cb)。
    """

    RETRY_WINDOW_S = 14.0

    def __init__(self, size, radius, kind, key, text='', parent=None, rarity=''):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.kind = kind
        self.key = _safe_int(key)
        # 稀有度随格子一起存: 图标是异步回填的, set_data 时才需要它,
        # 不能等到那时再去查表(会把一次纯 UI 刷新变成一次可能的网络请求)。
        # 只存字符串 —— 上游字段缺失/类型异常时兜成 ''(不上色), 不让脏数据
        # 一路传到 icons 层再炸(实测 int 会让 .strip() 抛 AttributeError)。
        self.rarity = rarity if isinstance(rarity, str) else ''
        self.loaded = self.key <= 0
        # v4.x(P1): 已发起过下载则不再重复请求 —— _tick_icons 轮询时检查,
        # 防止同一图标每 500ms 重复触发一次下载。
        self._requested = False
        self.deadline = _time.monotonic() + self.RETRY_WINDOW_S
        self.setStyleSheet(
            f'background: {Glass.BG_INSET};'
            f'border-radius: {radius}px;'
            f'color: {Glass.TEXT_MUTED}; font-size: {Glass.FONT_SIZE_XS};')
        if text:
            self.setText(text)

    def set_data(self, data) -> bool:
        # v4.4.0 修复(P1-16/P1-17): 传 widget=self 让 DPR 跟屏走, 并走上
        # icons.round_pixmap 的 LRU —— 详情窗重开时同一批头像/装备图标
        # (10 人 × 最多 7 件装备)不再逐张重新解码缩放。
        # v4.4.20: 海克斯走 augment_pixmap(白蒙版按稀有度染色); 其余 kind
        # 维持 rounded_pixmap 不变, 保证非海克斯图标零回归。
        radius = max(3, self.width() // 5)
        if self.kind == 'augment':
            pix = augment_pixmap(data, self.width(), self.rarity, radius,
                                 widget=self)
        else:
            pix = rounded_pixmap(data, self.width(), radius, widget=self)
        if pix is None:
            return False
        self.setText('')
        self.setPixmap(pix)
        self.loaded = True
        return True


class _PlayerRow(QWidget):
    """一方阵容里的一行。

    v4.3.2 列布局(从左到右):
      英雄头像 + (名字/英雄名) | 等级 | 召唤师技能×2 | KDA | 补刀 | 经济 |
      输出 | 承伤 | 装备×7 | 海克斯×N
    列宽按最长内容定死: KDA "12 / 10 / 13" 需 74px; 万单位数值 46px;
    技能/装备/海克斯格各 17px。
    """

    NAME_W = 96
    ROW_H = 34
    ICON = 24
    ITEM = 18          # ds 意见：装备/技能/海克斯图标统一尺寸（原 17 略挤）
    SPELL = 18
    AUG = 18
    # 图标格间距: 行内与表头**必须一致**, 否则列错位(第一版就是这里不一致)
    STRIP_GAP = 3      # ds 意见：图标间距放宽，避免糊在一起
    # 列间距: 同上, 表头与行共用
    COL_GAP = 4
    # 名字/英雄名截断阈值(魔法数字收口): 超长截断并悬停显示全名
    NAME_MAX = 10
    NAME_CUT = 9
    CHAMP_NAME_MAX = 5
    CHAMP_NAME_CUT = 4

    # 数值列: (表头文案, 宽度, 颜色) —— 表头与行共用, 保证永远对齐
    NUM_COLS = (
        ('等级', 28, Glass.TEXT_MUTED),
        ('KDA', 74, Glass.TEXT_PRIMARY),
        ('补刀', 30, Glass.TEXT_SECONDARY),
        ('经济', 44, Glass.TEXT_SECONDARY),
        ('输出', 44, '#f0a4a4'),
        ('承伤', 44, '#93c5fd'),
    )

    @classmethod
    def spell_strip_w(cls):
        return cls.SPELL * 2 + cls.STRIP_GAP

    @classmethod
    def item_strip_w(cls):
        return cls.ITEM * 7 + cls.STRIP_GAP * 6

    @classmethod
    def aug_strip_w(cls):
        return cls.AUG * 4 + cls.STRIP_GAP * 3

    @classmethod
    def header_cols(cls, show_aug):
        """表头列: [(文案, 宽度)] —— 与 _PlayerRow 的排布严格一一对应。"""
        cols = [('召唤师', cls.NAME_W + cls.ICON + cls.COL_GAP)]
        cols += [(t, w) for t, w, _c in cls.NUM_COLS]
        cols.append(('技能', cls.spell_strip_w()))
        cols.append(('出装', cls.item_strip_w()))
        if show_aug:
            cols.append(('海克斯', cls.aug_strip_w()))
        return cols

    def __init__(self, p, is_me, icon_boxes, on_grab, on_request,
                 aux_grab=None, aux_request=None, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.ROW_H)
        self.setAttribute(Qt.WA_StyledBackground, True)
        if is_me:
            self.setStyleSheet(
                f'background: {Glass.PRIMARY_SUBTLE};'
                f'border-radius: {Glass.RADIUS_SM}px;')
        else:
            self.setStyleSheet('background: transparent;')

        lay = QHBoxLayout(self)
        # 左右边距与 COL_GAP 必须与表头(见 _fill_roster 的 hl)完全一致, 否则列偏移
        lay.setContentsMargins(3, 2, 3, 2)
        lay.setSpacing(self.COL_GAP)

        cid = _safe_int(p.get('cid'))
        box = _IconBox(self.ICON, 5, 'champ', cid)
        if cid > 0 and not box.loaded:
            data = on_grab('champ', cid)
            if not data or not box.set_data(data):
                on_request('champ', cid)
                box._requested = True
        icon_boxes.append(box)
        lay.addWidget(box)

        name = (p.get('name') or '').strip() or '未知召唤师'
        name_full = name
        if len(name) > self.NAME_MAX:
            name = name[:self.NAME_CUT] + '…'
        cand = (p.get('champion_name') or '').strip()
        if not cand and cid > 0:
            cand = f'英雄#{cid}'
        cand_full = cand
        if len(cand) > self.CHAMP_NAME_MAX:
            cand = cand[:self.CHAMP_NAME_CUT] + '…'
        nm_w = QWidget()
        nm_w.setFixedWidth(self.NAME_W)
        nm_w.setStyleSheet('background:transparent;')
        nl = QVBoxLayout(nm_w)
        nl.setContentsMargins(0, 0, 0, 0)
        nl.setSpacing(0)
        # v4.4.1(UI-A4): 同上 —— "我" 用品牌色标识, 不再借用胜负色
        name_lbl = _lbl(name, Glass.FONT_SIZE_SM,
                        Glass.PRIMARY if is_me else Glass.TEXT_PRIMARY)
        if name != name_full:
            name_lbl.setToolTip(name_full)
        cand_lbl = _lbl(cand, Glass.FONT_SIZE_XS, Glass.TEXT_MUTED)
        if cand != cand_full:
            cand_lbl.setToolTip(cand_full)
        nl.addWidget(name_lbl)
        nl.addWidget(cand_lbl)
        lay.addWidget(nm_w)

        # 数值列(与表头共用 NUM_COLS 的宽度/顺序)
        values = (
            f'Lv{_safe_int(p.get("level"))}',
            kda_text(p.get('kills'), p.get('deaths'), p.get('assists')),
            num_text(p.get('cs')),
            wan_text(p.get('gold')),
            wan_text(p.get('dmg')),
            wan_text(p.get('taken')),
        )
        for (head_text, width, color), text in zip(self.NUM_COLS, values):
            # 浅色主题：高亮行底是淡青，文字保持深色即可读（不再用白字）
            lb = _lbl(text, Glass.FONT_SIZE_XS, color)
            lb.setFixedWidth(width)
            lb.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            lay.addWidget(lb)

        # 召唤师技能 ×2 —— 放在出装之前(与表头 header_cols 顺序严格对应)
        if aux_grab is not None:
            sp_w = self._icon_strip(
                [p.get('spell1'), p.get('spell2')], 'spell', self.SPELL,
                icon_boxes, aux_grab, aux_request, self.spell_strip_w(),
                names=[p.get('spell1_name') or '', p.get('spell2_name') or ''])
            lay.addWidget(sp_w)

        # 装备 ×7 —— v4.4.1(UI-A5): 补 placeholder=7。原来不传 placeholder,
        # 装备少于 7 件的行会少渲染格位(只是靠 fixed_w 撑住总宽), 图标之间
        # 的间距被 stretch 均摊, 与"海克斯"列的处理不一致 → 图标列看着错位。
        # 补满 7 个格位后, 每行图标坐标完全一致。
        # v4.10.x: 装备名无轻量表(items.json 1MB+, 项目刻意不加载), 不设 tooltip。
        raw_items = p.get('items')
        if not isinstance(raw_items, (list, tuple)):
            if raw_items:
                print(f'[详情] items 类型异常({type(raw_items).__name__}), 已按空处理')
            raw_items = []
        items_w = self._icon_strip(
            raw_items[:7], 'item', self.ITEM,
            icon_boxes, on_grab, on_request, self.item_strip_w(), placeholder=7)
        lay.addWidget(items_w)

        # 海克斯强化(大乱斗/竞技场才有)。**始终占位**定宽 —— 否则某些行没有
        # 海克斯时列宽塌缩, 同一屏里的行会左右错位。
        raw_augs = p.get('augments')
        if not isinstance(raw_augs, (list, tuple)):
            if raw_augs:
                print(f'[详情] augments 类型异常({type(raw_augs).__name__}), 已按空处理')
            raw_augs = []
        augs = [a for a in raw_augs if a]
        if aux_grab is not None:
            # v4.4.20: 稀有度与 id 一一对应传下去 —— 官方白蒙版图标必须按它染色
            aug_rar = p.get('augment_rarity')
            if not isinstance(aug_rar, (list, tuple)):
                if aug_rar:
                    print(f'[详情] augment_rarity 类型异常'
                          f'({type(aug_rar).__name__}), 已按空处理')
                aug_rar = []
            # v4.10.x: augment_names 与 augs 同序同过滤条件(main 侧已算好),
            # 补齐空串后按下标对齐传给图标条做 tooltip —— 让"有图无名"变有名。
            aug_names = (p.get('augment_names') or [])[:len(augs)]
            aug_names += [''] * (len(augs) - len(aug_names))
            aug_w = self._icon_strip(augs[:4], 'augment', self.AUG,
                                     icon_boxes, aux_grab, aux_request,
                                     self.aug_strip_w(), placeholder=4,
                                     rarities=aug_rar, names=aug_names)
            lay.addWidget(aug_w)
        lay.addStretch()

    def _icon_strip(self, keys, kind, size, icon_boxes, grab, request,
                    fixed_w=None, placeholder=0, rarities=None, names=None):
        """一排等尺寸图标格(技能/装备/海克斯共用)。

        fixed_w 给定则整条定宽 —— 表头与行都按同一个宽度排, 列才不会错位。
        placeholder: 至少渲染这么多个格位(不足补空), 用于"有的行没有、有的行有"
        的列(海克斯), 保证行高与列宽稳定。
        rarities: v4.4.20 新增, 与 keys **下标对齐**的稀有度列表(仅 augment 用)。
        长度不足的位置按 ''(不上色)处理, 不会 IndexError。
        names: v4.10.x 新增, 与 keys **下标对齐**的中文名列表(技能/海克斯), 有值
        则设到图标格的 tooltip —— 让小图标悬停可见名字, 消除"有图无名"。
        """
        w = QWidget()
        w.setStyleSheet('background:transparent;')
        if fixed_w:
            w.setFixedWidth(fixed_w)
        il = QHBoxLayout(w)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(self.STRIP_GAP)
        # 图标条在定宽容器里居中 —— 表头文字是居中的, 不居中会看着像错位
        il.addStretch()
        if kind == 'item':
            def _grab(k):
                return grab('item', k)

            def _req(k):
                return request('item', k)
        else:
            def _grab(k):
                return grab(kind, k)

            def _req(k):
                return request(kind, k)
        keys = list(keys or [])
        while len(keys) < placeholder:
            keys.append(0)
        rar = list(rarities or [])
        nms = list(names or [])
        for i, k in enumerate(keys):
            try:
                k = int(k or 0)
            except (TypeError, ValueError):
                k = 0
            box = _IconBox(size, 4, kind, k,
                           rarity=(rar[i] if i < len(rar) else ''))
            if k > 0 and not box.loaded:
                data = _grab(k)
                if not data or not box.set_data(data):
                    _req(k)
                    box._requested = True
            nm = nms[i] if i < len(nms) else ''
            if k > 0 and isinstance(nm, str) and nm.strip():
                box.setToolTip(nm.strip())
            icon_boxes.append(box)
            il.addWidget(box)
        il.addStretch()
        return w


class RecentDetailDialog(DragMixin, GlassEffectMixin, QWidget):
    """对局详情: 摘要(战绩列表已有)立即渲染, 10 人明细由 main 异步回填。

    v4.3.2: 补齐召唤师技能 / 海克斯强化 / 承伤三组信息, 并把窗口放大、背景与主窗
    对齐(原来 770×640 + 近不透明深色底, 10 行挤在上半区、下半截空着且比主窗更暗)。
    """

    W = 820
    H = 700
    H_MIN = 360
    H_MAX = 820          # 10 人 + 队伍汇总全展开也够; 超出才出滚动条
    ICON_TICK_MS = 500
    # v4.10.x: 左右侧边「切换战绩」按钮(图片查看器式)。正文左右留 NAV_GUTTER
    # 宽的空道放 ◀/▶ —— 按钮贴窗边、垂直居中, 不再挤在标题栏。
    NAV_GUTTER = 40
    NAV_W = 28
    NAV_H = 60
    NAV_EDGE = 6

    def __init__(self, game, champ_icon_cb=None, champ_dl_cb=None,
                 item_icon_cb=None, item_dl_cb=None, fetch_cb=None,
                 spell_icon_cb=None, spell_dl_cb=None,
                 aug_icon_cb=None, aug_dl_cb=None,
                 parent=None, games=None, index=0, on_nav=None):
        super().__init__(parent)
        self._game = dict(game or {})
        # v4.10.x: 上/下一局导航上下文。games 为战绩列表(按显示顺序), index 为
        # 当前局下标, on_nav(idx) 由主窗注入 —— 弹窗只负责"换到第几局"并自我重载。
        self._games = list(games or [])
        self._index = int(index or 0)
        self._on_nav = on_nav
        self._champ_icon_cb = champ_icon_cb
        self._champ_dl_cb = champ_dl_cb
        self._item_icon_cb = item_icon_cb
        self._item_dl_cb = item_dl_cb
        self._spell_icon_cb = spell_icon_cb
        self._spell_dl_cb = spell_dl_cb
        self._aug_icon_cb = aug_icon_cb
        self._aug_dl_cb = aug_dl_cb
        self._fetch_cb = fetch_cb
        self._detail = None
        self._closed = False
        self._icon_boxes = []
        # v4.4.0 修复(P1-21/P2): 拖拽状态与玻璃去重状态统一由 window_base 的混入
        # 维护。原 `self._drag = None` + 手写 mouseXxxEvent 的判定已经是
        # `is not None`(没踩 (0,0) 那个假值坑), 但三窗口各写一份没人知道哪天会
        # 被改回 `if self._drag:`; 混入里带注释锁住这个语义。
        self._init_drag()
        self._init_glass()

        # v4.3.3: 用 Qt.Window 而不是 Qt.Dialog —— Qt.Dialog 会带 WS_EX_DLGMODALFRAME,
        # 与"Qt.Tool 阻止 DWM Acrylic"是同一类问题: 系统按"对话框"给窗口做框,
        # backdrop 容易被 frame 重置。主窗/小窗都用的普通 Window + 无边框, 这里对齐。
        # 保持置顶(详情是从主窗点开的, 不该被主窗盖住)。
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window
                            | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self._panel = QWidget()
        self._panel.setObjectName('panel')
        # v4.3.2 背景与主窗对齐: 主窗是 paint_window_backdrop 的 QColor(8,10,18,155)
        # 半透明底 + DWM 系统 Acrylic 模糊。原来这里用 rgba(16,18,28,0.97) ——
        # 底色更亮但透明度接近 1, 结果就是"比主界面发黑"。改成同色系的低透明度
        # (保留一点提亮, 否则小窗透底下文字对比度不够)。
        self._panel.setStyleSheet(
            'QWidget#panel{background: rgba(250, 251, 248, 0.98);'
            f'border: 1px solid {Glass.NEON_BORDER};'
            f'border-radius: {Glass.RADIUS_CARD}px;}}')
        outer.addWidget(self._panel)

        lay = QVBoxLayout(self._panel)
        # v4.10.x: 左右各留 NAV_GUTTER 的空道给「切换战绩」大箭头(图片查看器式),
        # 上下多留一点让内边距与原来观感一致。
        lay.setContentsMargins(self.NAV_GUTTER, 10, self.NAV_GUTTER, 12)
        lay.setSpacing(8)

        lay.addWidget(self._make_title_bar())
        lay.addWidget(self._make_head())
        lay.addWidget(self._make_hint())
        lay.addWidget(self._make_stats())
        lay.addWidget(self._make_roster_area(), 1)

        # v4.10.x: 左右贴边、垂直居中的「切换战绩」按钮(图片查看器式)。
        # 挂在 outer(整窗)上, 覆盖在面板 gutter 之上, 不会占正文宽度。
        self._make_nav_buttons()
        # v4.10.x: ✕ 也提到窗口层, 与 ◀/▶ 同轴定位, 修复正文边距改动后错位
        self._make_close_button()
        self._place_nav_buttons()

        self._icon_timer = QTimer(self)
        self._icon_timer.setInterval(self.ICON_TICK_MS)
        self._icon_timer.timeout.connect(self._tick_icons)
        self._icon_timer.start()

    # ---------------- 结构 ----------------
    def _make_title_bar(self):
        bar = QWidget()
        bar.setStyleSheet('background:transparent;')
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(2, 0, 2, 0)
        # v4.10.x: ◀/▶ 已移到左右贴边的大按钮(_make_nav_buttons), ✕ 也改为
        # 窗口层绝对定位(见 _make_close_button), 标题栏只留标题。
        lay.addSpacing(6)
        lay.addWidget(_lbl('对局详情', Glass.FONT_SIZE_MD,
                           Glass.TEXT_PRIMARY, True))
        lay.addStretch()
        return bar

    def _make_close_button(self):
        """右上角 ✕: 窗口层绝对定位, 与 ◀/▶ 同轴贴右边缘。

        v4.10.x: 修复"切换按钮把正文边距改宽后 ✕ 跟着右缩、与 ▶ 不同列"的
        偏移问题 —— 三枚按钮统一由 _place_nav_buttons 按窗口宽定位。
        """
        self._close_btn = QPushButton('✕', self)
        self._close_btn.setFixedSize(26, 22)
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setStyleSheet(
            f'QPushButton{{background:transparent;color:{Glass.TEXT_MUTED};'
            f'border:none;border-radius:{Glass.RADIUS_SM}px;font-size:12px;}}'
            f'QPushButton:hover{{background: {Glass.DANGER_BG};color:{Glass.DANGER};}}')
        self._close_btn.clicked.connect(self.close)
        self._close_btn.raise_()

    def _make_nav_buttons(self):
        """左右贴边、垂直居中的「切换战绩」大箭头(图片查看器式)。

        ◀ 在最左、▶ 在最右, 居中于窗口高度。按钮覆盖在正文左右空道
        (NAV_GUTTER)之上, 视觉上是"翻页"而不是标题栏小按钮。
        """
        nav_qss = (
            f'QPushButton{{background:rgba(237,241,238,0.95);'
            f'color:{Glass.TEXT_MUTED};'
            f'border:1px solid {Glass.NEON_BORDER};'
            f'border-radius:{Glass.RADIUS_CARD}px;font-size:16px;}}'
            f'QPushButton:hover{{background:{Glass.PRIMARY_12};'
            f'color:{Glass.TEXT_PRIMARY};'
            f'border-color:{Glass.NEON_BORDER_HOVER};}}'
            f'QPushButton:disabled{{color:rgba(0,0,0,0.25);'
            f'background:rgba(0,0,0,0.04);'
            f'border-color:rgba(0,0,0,0.08);}}')
        self._nav_prev = QPushButton('◀', self)
        self._nav_prev.setFixedSize(self.NAV_W, self.NAV_H)
        self._nav_prev.setCursor(Qt.PointingHandCursor)
        self._nav_prev.setStyleSheet(nav_qss)
        self._nav_prev.clicked.connect(lambda: self._navigate(-1))
        self._nav_next = QPushButton('▶', self)
        self._nav_next.setFixedSize(self.NAV_W, self.NAV_H)
        self._nav_next.setCursor(Qt.PointingHandCursor)
        self._nav_next.setStyleSheet(nav_qss)
        self._nav_next.clicked.connect(lambda: self._navigate(1))
        # 覆盖在面板之上(不用布局托管, 由 resize/show 时手动定位)
        self._nav_prev.raise_()
        self._nav_next.raise_()
        self._place_nav_buttons()
        self._update_nav_buttons()

    def _place_nav_buttons(self):
        """把 ◀/▶/✕ 放到窗口最左/最右、垂直居中/顶部贴边的位置。

        v4.10.x: ✕ 与 ▶ 的 x 轴对齐规则统一(右边缘 - 宽度 - NAV_EDGE),
        确保两枚按钮右侧边缘同列, 不再因为正文边距改动而错位。
        """
        try:
            w = self.NAV_W
            y = (self.height() - self.NAV_H) // 2
            self._nav_prev.move(self.NAV_EDGE, y)
            self._nav_next.move(self.width() - w - self.NAV_EDGE, y)
            cb = getattr(self, '_close_btn', None)
            if cb is not None:
                cb.move(self.width() - cb.width() - self.NAV_EDGE, 10)
        except Exception as e:
            print(f'[详情] 导航按钮定位失败: {type(e).__name__}: {e}')

    def _update_nav_buttons(self):
        """按当前下标启停 ◀/▶。"""
        try:
            if self._nav_prev is not None:
                self._nav_prev.setEnabled(self._index > 0)
            if self._nav_next is not None:
                self._nav_next.setEnabled(self._index < len(self._games) - 1)
        except Exception as e:
            print(f'[详情] 导航按钮状态失败: {type(e).__name__}: {e}')

    def _navigate(self, delta):
        """切到相邻对局。

        委托 _on_nav(主窗注入)重开弹窗 —— 主窗 _open_recent_detail 会先关旧窗
        再按新对局建窗, 复用既有的"先 show 后 fetch"约定与资源回收逻辑,
        避免就地清空/重建两套状态机各写一份。若 _on_nav 未注入(独立使用),
        退回本地 _games 就地切。
        """
        try:
            if self._closed:
                return
            ni = self._index + int(delta)
            if ni < 0 or ni >= len(self._games):
                return
            g = self._games[ni]
            if not isinstance(g, dict):
                return
            if self._on_nav is not None:
                self._on_nav(ni)
                return
            # 无主窗回调: 就地切(保持本类可独立使用)
            self._index = ni
            self._game = dict(g)
            self._detail = None
            self._reload_head()
            self._clear_roster()
            self._clear_stats()
            self._hint.setVisible(True)
            self._hint_lbl.setText('正在读取本局 10 人详细数据…')
            self._retry_btn.setVisible(False)
            self._update_nav_buttons()
            if self._fetch_cb is not None:
                self._fetch_cb(self._game.get('game_id'), self.set_detail)
            else:
                self.set_detail(None, '详情功能未就绪')
        except Exception as e:
            print(f'[详情] 导航失败: {type(e).__name__}: {e}')

    def _make_head(self):
        head = QWidget()
        head.setStyleSheet('background:transparent;')
        lay = QHBoxLayout(head)
        self._head_lay = lay   # v4.10.x: 记录引用, 导航换大图标时按 index 0 替换
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(10)

        cid = _safe_int(self._game.get('champion_id'))
        self._big_icon = _IconBox(56, 8, 'champ', cid,
                                  text=(self._game.get('champion_name') or '')[:1])
        if cid > 0:
            data = self._grab('champ', cid)
            if not data or not self._big_icon.set_data(data):
                self._request('champ', cid)
                self._big_icon._requested = True
        self._icon_boxes.append(self._big_icon)
        lay.addWidget(self._big_icon)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(2)
        self._ov_name = _lbl(self._game.get('champion_name') or '未知英雄',
                             Glass.FONT_SIZE_LG, Glass.TEXT_PRIMARY, True)

        sub = [game_mode_text(self._game)]
        d = dur_text(self._game.get('duration_s'))
        if d:
            sub.append(d)
        lane = lane_text(self._game.get('lane'))
        if lane:
            sub.append(lane)
        t = time_text(self._game.get('created_ms'))
        if t:
            sub.append(t)
        self._ov_sub = _lbl(' · '.join([x for x in sub if x]),
                            Glass.FONT_SIZE_XS, Glass.TEXT_MUTED)
        left.addWidget(self._ov_name)
        left.addWidget(self._ov_sub)
        self._ov_badge = _lbl('', Glass.FONT_SIZE_XS, WIN_COLOR, True)
        # QLabel 在竖排布局里默认被拉伸到整行宽 —— 限宽避免"胜负 pill 变全宽横条"
        self._ov_badge.setMaximumWidth(64)
        self._ov_badge.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        left.addWidget(self._ov_badge)
        lay.addLayout(left, 1)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(1)
        self._ov_kda = _lbl(kda_text(self._game.get('kills'),
                                     self._game.get('deaths'),
                                     self._game.get('assists')),
                            Glass.FONT_SIZE_XL, Glass.TEXT_PRIMARY, True)
        self._ov_kda.setAlignment(Qt.AlignRight)
        self._ov_kda_lbl = _lbl('KDA', Glass.FONT_SIZE_XS, Glass.TEXT_MUTED)
        self._ov_kda_lbl.setAlignment(Qt.AlignRight)
        right.addWidget(self._ov_kda)
        right.addWidget(self._ov_kda_lbl)
        lay.addLayout(right)
        self._paint_badge(self._game.get('win'))
        return head

    def _reload_head(self):
        """导航切换后重刷摘要区(英雄名/模式/KDA/胜负), 无需销毁重建弹窗。"""
        try:
            g = self._game
            cid = _safe_int(g.get('champion_id'))
            if self._ov_name is not None:
                self._ov_name.setText(g.get('champion_name') or '未知英雄')
            sub = [game_mode_text(g)]
            d = dur_text(g.get('duration_s'))
            if d:
                sub.append(d)
            lane = lane_text(g.get('lane'))
            if lane:
                sub.append(lane)
            t = time_text(g.get('created_ms'))
            if t:
                sub.append(t)
            if self._ov_sub is not None:
                self._ov_sub.setText(' · '.join([x for x in sub if x]))
            if self._ov_kda is not None:
                self._ov_kda.setText(kda_text(g.get('kills'), g.get('deaths'),
                                              g.get('assists')))
            if self._ov_kda_lbl is not None:
                self._ov_kda_lbl.setText('KDA')
            self._paint_badge(g.get('win'))
            # 大英雄头像: 重建一个 _IconBox 换掉旧的
            self._swap_big_icon(cid, (g.get('champion_name') or '')[:1])
        except Exception as e:
            print(f'[详情] 摘要重载失败: {type(e).__name__}: {e}')

    def _swap_big_icon(self, cid, text):
        """换大英雄头像格: 移除旧格, 建新格加入布局并注册进轮询表。"""
        try:
            old = getattr(self, '_big_icon', None)
            if old is not None:
                try:
                    old.setParent(None)
                    old.deleteLater()
                except Exception:
                    pass
                try:
                    self._icon_boxes.remove(old)
                except Exception:
                    pass
            box = _IconBox(56, 8, 'champ', cid, text=text)
            self._big_icon = box
            if cid > 0:
                data = self._grab('champ', cid)
                if not data or not box.set_data(data):
                    self._request('champ', cid)
                    box._requested = True
            self._icon_boxes.append(box)
            # _make_head 里用的是 left 布局的上一级(head 的水平布局), 这里直接
            # 找到 head 布局第 0 项替换。更稳妥: 由 _make_head 记录该布局。
            hl = getattr(self, '_head_lay', None)
            if hl is not None:
                hl.insertWidget(0, box)
        except Exception as e:
            print(f'[详情] 大图标替换失败: {type(e).__name__}: {e}')

    def _clear_roster(self):
        """清空 10 人阵容区(导航切换时调用)。"""
        try:
            lay = self._roster_lay
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
            lay.addStretch()
            self._icon_boxes = [b for b in self._icon_boxes
                                if b is getattr(self, '_big_icon', None)]
        except Exception as e:
            print(f'[详情] 阵容清空失败: {type(e).__name__}: {e}')

    def _clear_stats(self):
        """清空统计小卡区(导航切换时调用)。"""
        try:
            lay = self._stats_lay
            while lay.count():
                it = lay.takeAt(0)
                w = it.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        except Exception as e:
            print(f'[详情] 统计清空失败: {type(e).__name__}: {e}')

    def _make_hint(self):
        self._hint = QWidget()
        self._hint.setStyleSheet('background:transparent;')
        lay = QHBoxLayout(self._hint)
        lay.setContentsMargins(2, 0, 2, 0)
        lay.setSpacing(8)
        self._hint_lbl = _lbl('正在读取本局 10 人详细数据…',
                              Glass.FONT_SIZE_XS, Glass.TEXT_MUTED)
        self._retry_btn = QPushButton('重试')
        self._retry_btn.setFixedHeight(22)
        self._retry_btn.setCursor(Qt.PointingHandCursor)
        self._retry_btn.setStyleSheet(
            f'QPushButton{{background:{Glass.PRIMARY_SUBTLE};color:{Glass.PRIMARY};'
            f'border:1px solid {Glass.NEON_BORDER};'
            f'border-radius:{Glass.RADIUS_SM}px;padding:0 10px;font-size:{Glass.FONT_SIZE_XS};}}'
            f'QPushButton:hover{{background:{Glass.PRIMARY_12};}}')
        self._retry_btn.clicked.connect(self._retry)
        self._retry_btn.setVisible(False)
        lay.addWidget(self._hint_lbl)
        lay.addWidget(self._retry_btn)
        lay.addStretch()
        return self._hint

    def _make_stats(self):
        holder = QWidget()
        holder.setStyleSheet('background:transparent;')
        self._stats_lay = QGridLayout(holder)
        self._stats_lay.setContentsMargins(2, 0, 2, 0)
        self._stats_lay.setHorizontalSpacing(8)
        self._stats_lay.setVerticalSpacing(4)
        return holder

    def _make_roster_area(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(
            "QScrollArea{background:transparent;border:none;}" + Glass.SCROLL_QSS)
        # _fit_height 依赖 sizeHint 求和, 而 QScrollArea 的默认 sizeHint 很小,
        # 会把窗口高度算得过小。给一个下限, 让"10 人"这种内容能贡献出真实高度。
        scroll.setMinimumHeight(180)
        holder = QWidget()
        holder.setStyleSheet('background:transparent;')
        self._roster_lay = QVBoxLayout(holder)
        self._roster_lay.setContentsMargins(2, 2, 6, 2)
        self._roster_lay.setSpacing(10)
        scroll.setWidget(holder)
        self._scroll_holder = holder
        self._scroll = scroll
        return scroll

    # ---------------- 数据回填 ----------------
    @ui_slot
    def set_detail(self, detail, err=''):
        """main 侧异步拉取完成后回填(主线程调用)。

        err == '__slow__' 是进度信号("还在读, 只是慢"; waiter 未摘除, 结果仍会回来),
        只更新提示, 不当失败处理。

        v5.1(ultra M10): 加线程守卫 —— 本方法全程是 UI 操作(填表/setStyleSheet/
        deleteLater), 必须在主线程执行。若非主线程被调(未来的新调用方踩坑),
        经 QTimer.singleShot(0) 转投主线程, 而不是在后台线程直接碰控件。
        """
        try:
            if QThread.currentThread() is not QApplication.instance().thread():
                QTimer.singleShot(0, lambda: self.set_detail(detail, err))
                return
        except Exception:
            pass
        if self._closed:
            return
        try:
            self._detail = detail if isinstance(detail, dict) else None
            has = bool(self._detail and self._detail.get('players'))
            if not has and err == '__slow__':
                self._hint.setVisible(True)
                self._hint_lbl.setText(
                    '读取较慢（客户端正在拼本局数据），仍在等待…')
                self._retry_btn.setVisible(self._fetch_cb is not None)
                return
            if not has:
                self._show_error(err or '该对局详细数据不可用')
                return
            self._hint.setVisible(False)
            self._fill_stats()
            self._fill_roster()
            # 自适应高度必须等 Qt 完成一次真实布局(holder 的 layout 在首次
            # activate 前 sizeHint 是 0/极小), 否则会算出"内容 4px"这种错值。
            # 用 singleShot(0) 排到本轮事件循环之后。
            QTimer.singleShot(0, self._fit_height)
            self._tick_icons()
        except Exception as e:
            print(f'[详情] 回填失败: {type(e).__name__}: {e}')
            self._show_error(f'渲染失败: {type(e).__name__}')

    def _fit_height(self):
        """按实际内容高度调整窗口 —— 避免"10 行挤在上半区、下半截空着"。

        设计: 先算"除滚动区以外的固定部分高度", 再加上滚动内容的真实高度,
        夹到 [H_MIN, H_MAX]; 内容确实超高才让滚动区出滚动条。
        窗口保持居中(按中心点平移), 避免调整高度后跳到屏幕角落。
        """
        try:
            lay = self._panel.layout()
            if lay is None:
                return
            lay.activate()
            scroll = getattr(self, '_scroll', None)
            # 固定部分 = 除滚动区外所有块的 sizeHint 之和 + 布局内边距/间距。
            # 不用"总 hint 减去滚动区 hint" —— 滚动区在 setWidgetResizable 下
            # 的 hint 很不稳定, 相减会连带把固定部分算错。
            fixed = 0
            n_blocks = 0
            for i in range(lay.count()):
                w = lay.itemAt(i).widget()
                if w is None or w is scroll:
                    continue
                fixed += max(w.sizeHint().height(), w.minimumSizeHint().height())
                n_blocks += 1
            fixed += lay.spacing() * max(0, n_blocks - 1 + (1 if scroll else 0))
            fixed += lay.contentsMargins().top() + lay.contentsMargins().bottom()

            # 内容高度: 优先用 holder 的实际高度(布局跑完后由 Qt 自己撑出来, 最准)。
            # 兜底才累加子块 sizeHint —— 后者在含 stretch 的布局里容易估大/估小。
            content = 0
            holder = getattr(self, '_scroll_holder', None)
            if holder is not None and holder.layout() is not None:
                lay_h = holder.layout()
                lay_h.activate()
                # 用子块的 y 坐标 + 高度算真实内容底边, 比累加 sizeHint 精确
                bottom = 0
                for i in range(lay_h.count()):
                    it = lay_h.itemAt(i)
                    w = it.widget()
                    if w is None:
                        continue
                    try:
                        bottom = max(bottom, w.geometry().bottom() + 1)
                    except Exception:
                        pass
                if bottom > 0:
                    content = bottom
                else:
                    for i in range(lay_h.count()):
                        w = lay_h.itemAt(i).widget()
                        if w is not None:
                            content += w.sizeHint().height()
                    content += lay_h.spacing() * max(0, lay_h.count() - 1)
                content += (lay_h.contentsMargins().top()
                            + lay_h.contentsMargins().bottom())
            want = fixed + max(0, content) + 6
            want = max(self.H_MIN, min(self.H_MAX, want))
            if abs(want - self.height()) < 4:
                return
            center = self.frameGeometry().center()
            self.setFixedSize(self.W, want)
            self.move(center - self.rect().center())
            # 窗口高度变化后, 左右翻页按钮要保持垂直居中
            self._place_nav_buttons()
            # 改尺寸会让 DWM 重新评估窗口 frame, backdrop 有可能被清掉 → 补一次
            # v4.4.0 修复(P1-21): 改走脏标志调度, 同尺寸重复调用不再重复打 DWM
            self._mark_glass_dirty()
            self._schedule_glass()
            # v4.x(P3): 移除每次高度变化都打印的行 —— 保留失败分支打印即可
        except Exception as e:
            print(f'[详情] 自适应高度失败(忽略): {type(e).__name__}: {e}')

    def _show_error(self, msg):
        try:
            self._hint.setVisible(True)
            self._hint_lbl.setText(f'读取失败：{msg}')
            self._retry_btn.setVisible(self._fetch_cb is not None)
        except Exception as e:
            print(f'[详情] 错误提示失败: {type(e).__name__}: {e}')

    def _retry(self):
        """重试: 再走一次 main 侧的异步拉取(先 show 后请求, 回调仍是 set_detail)。"""
        if self._closed or self._fetch_cb is None:
            return
        self._retry_btn.setVisible(False)
        self._hint_lbl.setText('正在重新读取本局 10 人详细数据…')
        try:
            self._fetch_cb(self._game.get('game_id'), self.set_detail)
        except Exception as e:
            self._show_error(f'{type(e).__name__}: {e}')

    def _me(self):
        players = (self._detail or {}).get('players') or []
        my_pid = (self._detail or {}).get('my_pid', -1)
        for p in players:
            if p.get('pid') == my_pid:
                return p
        return players[0] if players else None

    def _team_players(self, team_id):
        players = (self._detail or {}).get('players') or []
        return [p for p in players if p.get('team') == team_id]

    def _fill_stats(self):
        me = self._me()
        if not me:
            return
        team = self._team_players(me.get('team'))
        team_kills = sum(_safe_int(x.get('kills')) for x in team)
        self._paint_badge(me.get('win'))
        self._ov_kda.setText(kda_text(me.get('kills'), me.get('deaths'),
                                      me.get('assists')))
        self._ov_kda_lbl.setText('KDA · 参团率 '
                                 + (rate_text(me.get('kills'), me.get('assists'),
                                              team_kills) or '—'))

        cells = [
            ('KDA 比', kda_ratio(me.get('kills'), me.get('deaths'),
                                 me.get('assists')) or '—'),
            ('参团率', rate_text(me.get('kills'), me.get('assists'),
                                 team_kills) or '—'),
            ('等级', f"Lv{_safe_int(me.get('level'))}"),
            ('补刀', num_text(me.get('cs'))),
            ('经济', wan_text(me.get('gold'))),
            ('输出', wan_text(me.get('dmg'))),
            ('承伤', wan_text(me.get('taken'))),
        ]        # 视野分: 大乱斗/竞技场恒为 0, 没意义 → 只有真拿到数据才显示
        vision = _safe_int(me.get('vision'))
        if vision > 0:
            cells.append(('视野', str(vision)))
        # v4.10.x: 插眼/排眼 —— 只有 >0 才显示(0 是常态, 避免刷屏)
        wards = _safe_int(me.get('wards'))
        wards_killed = _safe_int(me.get('wards_killed'))
        if wards > 0 or wards_killed > 0:
            cells.append(('插眼/排眼', f'{wards}/{wards_killed}'))
        multi = me.get('multi') or ''
        if multi:
            cells.append(('多杀', multi))

        while self._stats_lay.count():
            it = self._stats_lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        # 每行 5 个 + 列表格: 单行排太多会把内容顶宽(历史坑)
        for i, (k, v) in enumerate(cells):
            card = _kv(k, 66)
            card.set_value(v)
            self._stats_lay.addWidget(card, i // 5, i % 5)
        self._stats_lay.setColumnStretch(5, 1)

    def _fill_roster(self):
        lay = self._roster_lay
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._icon_boxes = [b for b in self._icon_boxes
                            if b is self._big_icon]

        me = self._me()
        my_team = me.get('team') if me else None
        teams = (self._detail or {}).get('teams') or []
        team_ids = []
        for tm in teams:
            tid = tm.get('team_id')
            if tid is not None and tid not in team_ids:
                team_ids.append(tid)
        if not team_ids:
            seen = []
            for p in (self._detail or {}).get('players') or []:
                tid = p.get('team')
                if tid not in seen:
                    seen.append(tid)
            team_ids = seen
        # 我方在前
        team_ids.sort(key=lambda t: 0 if t == my_team else 1)

        for tid in team_ids:
            block = QWidget()
            block.setStyleSheet('background:transparent;')
            bl = QVBoxLayout(block)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(3)

            is_my = (tid == my_team)
            # v4.4.1(UI-A4): 原来用 WIN_COLOR/LOSE_COLOR 上色, 那是"胜=红/负=绿"
            # 的语义色, 却被拿来表达"是不是我方" —— 输了时"我方"仍然是红, 语义打架。
            # 阵营改用品牌色 vs 次要文字色, 与胜负彻底脱钩。
            title = _lbl('我方' if is_my else '敌方', Glass.FONT_SIZE_SM,
                         Glass.PRIMARY if is_my else Glass.TEXT_SECONDARY, True)
            bl.addWidget(title)

            head = QWidget()
            head.setStyleSheet('background:transparent;')
            hl = QHBoxLayout(head)
            # 关键: 表头的缩进/间距必须与 _PlayerRow 完全一致(行内是 3/2/3/2 + COL_GAP),
            # 否则列会整体偏移。两边都从 _PlayerRow 的类常量取, 单一来源。
            hl.setContentsMargins(3, 0, 3, 0)
            hl.setSpacing(_PlayerRow.COL_GAP)
            for i, (text, width) in enumerate(
                    _PlayerRow.header_cols(self._has_augments())):
                lb = _lbl(text, Glass.FONT_SIZE_XS, Glass.TEXT_MUTED)
                lb.setFixedWidth(width)
                # 数值列表头右对齐，与行内数值一致；文本列居中
                lb.setAlignment(
                    Qt.AlignRight | Qt.AlignVCenter if 1 <= i <= 6
                    else Qt.AlignCenter)
                hl.addWidget(lb)
            hl.addStretch()
            bl.addWidget(head)

            for p in self._team_players(tid):
                bl.addWidget(_PlayerRow(p, p.get('pid') == (me or {}).get('pid'),
                                        self._icon_boxes, self._grab,
                                        self._request,
                                        self._aux_grab, self._aux_request))
            bl.addWidget(self._make_team_sum(tid))
            lay.addWidget(block)
        lay.addStretch()
        # v4.4.2(P1-16): _tick_icons 在"全部就位/全部超窗"时自停表, 但回填新
        # 阵容后从未重启 —— 定时器一旦停过, 新加入的图标框就再也不会被轮询下载
        # (表现: 第二次点开详情, 图标永远 loading)。这里在重建阵容后, 只要还有
        # 未就位且未超窗的框, 就确保定时器在跑。
        _now = _time.monotonic()
        if any((not b.loaded and b.key > 0 and _now <= b.deadline)
               for b in self._icon_boxes):
            if not self._icon_timer.isActive():
                self._icon_timer.start()

    def _has_augments(self):
        """本局是否有海克斯强化数据(峡谷没有 → 不占列宽, 避免空出一块)。"""
        for p in (self._detail or {}).get('players') or []:
            if any(p.get('augments') or []):
                return True
        return False

    def _aux_grab(self, kind, key):
        """_PlayerRow 里技能/海克斯图标的取图入口(kind 已在行内确定)。"""
        return self._grab(kind, key)

    def _aux_request(self, kind, key):
        return self._request(kind, key)

    def _make_team_sum(self, tid):
        tm = {}
        for t in (self._detail or {}).get('teams') or []:
            if t.get('team_id') == tid:
                tm = t
                break
        team = self._team_players(tid)
        kills = sum(_safe_int(p.get('kills')) for p in team)
        gold = sum(_safe_int(p.get('gold')) for p in team)
        dmg = sum(_safe_int(p.get('dmg')) for p in team)
        taken = sum(_safe_int(p.get('taken')) for p in team)
        parts = [f'击杀 {kills}', f'经济 {wan_text(gold)}',
                 f'输出 {wan_text(dmg)}', f'承伤 {wan_text(taken)}']
        if tm:
            # 与先锋/水晶一致: >0 才展示, 避免大乱斗/竞技场恒 0 字段无差别刷屏
            tower = _safe_int(tm.get('tower'))
            dragon = _safe_int(tm.get('dragon'))
            baron = _safe_int(tm.get('baron'))
            if tower > 0:
                parts.append(f'推塔 {tower}')
            if dragon > 0:
                parts.append(f'小龙 {dragon}')
            if baron > 0:
                parts.append(f'大龙 {baron}')
            # v4.10.x: 峡谷模式才展示先锋/水晶(大乱斗/竞技场这些字段恒 0, 不占空间)
            herald = _safe_int(tm.get('herald'))
            inhib = _safe_int(tm.get('inhib'))
            if herald > 0:
                parts.append(f'先锋 {herald}')
            if inhib > 0:
                parts.append(f'水晶 {inhib}')
        w = QWidget()
        w.setStyleSheet('background:transparent;')
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.addWidget(_lbl(' · '.join(parts), Glass.FONT_SIZE_XS,
                           Glass.TEXT_SECONDARY, True))
        lay.addStretch()
        return w

    def _paint_badge(self, win):
        """胜负徽章。

        v4.4.1(UI-B4): 原来是 XS 号纯文字, 视觉权重远低于右侧 XL 号 KDA,
        扫一眼看不到输赢。改为带底色/圆角的 pill(几何用令牌, 数值 11px/5px
        与主窗 rank chip 保持一致)。
        """
        try:
            if win is False:
                text, color = '失败', LOSE_COLOR
            elif win is True:
                text, color = '胜利', WIN_COLOR
            else:
                # None / 非 bool(脏数据): 中性"未定"灰色 pill, 不瞎猜胜负
                text, color = '未定', Glass.TEXT_MUTED
            self._ov_badge.setText(text)
            self._ov_badge.setStyleSheet(
                f"font-size:{Glass.FONT_SIZE_XS};font-weight:700;"
                f"color:{color};background:{Glass.BG_CARD_ALT};"
                f"border-radius:5px;padding:2px 7px;")
        except Exception as e:
            print(f'[详情] 胜负徽章失败: {type(e).__name__}: {e}')

    # ---------------- 图标轮询 ----------------
    def _grab(self, kind, key):
        """kind: champ | item | spell | augment。取缓存图(不发起下载)。"""
        if kind == 'champ':
            cb = self._champ_icon_cb
        elif kind == 'item':
            cb = self._item_icon_cb
        elif kind == 'spell':
            cb = self._spell_icon_cb
        else:
            cb = self._aug_icon_cb
        if cb is None:
            return None
        try:
            return cb(int(key))
        except Exception:
            return None

    def _request(self, kind, key):
        """kind: champ | item | spell | augment。触发后台下载。"""
        if kind == 'champ':
            cb = self._champ_dl_cb
        elif kind == 'item':
            cb = self._item_dl_cb
        elif kind == 'spell':
            cb = self._spell_dl_cb
        else:
            cb = self._aug_dl_cb
        if cb is None:
            return
        try:
            cb(int(key))
        except Exception as e:
            print(f'[详情] 图标请求失败({kind} {key}): {type(e).__name__}: {e}')

    @ui_slot
    def _tick_icons(self):
        if self._closed:
            return
        now = _time.monotonic()
        pending = False
        for box in list(self._icon_boxes):
            if box.loaded or box.key <= 0:
                continue
            if now > box.deadline:
                continue                      # 超窗放弃, 不再空打网络
            data = self._grab(box.kind, box.key)
            if data and box.set_data(data):
                continue
            # v4.x(P1): 已发起过下载则本轮跳过 —— 数据未就位前不再重复触发,
            # 防止同一图标每 500ms 空打一次下载。
            if box._requested:
                pending = True
                continue
            self._request(box.kind, box.key)
            box._requested = True
            pending = True
        if not pending and self._icon_timer.isActive():
            # 全部就位/全部超窗 → 停表, 直到下次回填再启
            self._icon_timer.stop()

    # ---------------- 窗口行为 ----------------
    def _glass_apply_core(self):
        """应用系统 Acrylic 模糊 + 系统圆角(无阴影)。

        v4.3.3: 之前这个弹窗**只设了半透明 QSS, 从没调过 DWM Acrylic** ——
        所以它表现为"纯透"(壁纸花纹原样透出来、字看不清), 而主窗/小窗是"磨砂"
        (背后被系统模糊过)。对齐主窗做法的关键就是这句 apply_liquid_glass。

        v4.4.0(P1-21): 原名 `_apply_glass_effect`, 现在由
        `window_base.GlassEffectMixin._apply_glass_effect` 做"可见性 + 几何去重"
        后再调到这里, 因此本方法只管真正打 DWM(重复调用的去重在外面)。
        """
        try:
            from .glass_effect import apply_liquid_glass
            if self.isVisible():
                apply_liquid_glass(self, enable_shadow=False)
        except Exception as e:
            print(f'[GlassEffect] 详情弹窗失败: {e}')

    def showEvent(self, event):
        super().showEvent(event)
        # 同主窗/小窗: show 后 DWM 可能重置 backdrop, 延迟补两次更稳。
        # v4.4.0 修复(P1-21): 原来排 2 次 singleShot 就是打 2 次 DWM
        # (SetWindowLongW + SetWindowPos(SWP_FRAMECHANGED) + 4×DwmSetWindowAttribute);
        # 改走脏标志调度后第二次命中缓存键直接返回, 观感不变。
        self._mark_glass_dirty()
        self._schedule_glass()
        # 首帧后按真实窗口尺寸定位左右翻页按钮
        self._place_nav_buttons()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 窗口尺寸变化(自适应高度)时, ◀/▶ 保持贴边、垂直居中
        self._place_nav_buttons()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    # v4.4.0 修复(P2): 顶部 46px 可拖, 与主窗/小窗共用 window_base.DragMixin。
    # 原实现直接写 `self._drag = ...`, 现字段名为混入所需的 `_drag_pos`
    # (判定已经是 `is not None`, 语义不变; 统一后避免哪天被改回 falsy 判定)。
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and event.pos().y() < 46:
            self._drag_mouse_press(event)
            event.accept()

    def mouseMoveEvent(self, event):
        self._drag_mouse_move(event)
        if event.buttons() & Qt.LeftButton:
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_mouse_release(event)

    def closeEvent(self, event):
        self._closed = True
        try:
            self._icon_timer.stop()
        except Exception:
            pass
        super().closeEvent(event)
