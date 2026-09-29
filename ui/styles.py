"""Styles - 液态玻璃设计系统 v2

灵感: 小米澎湃OS / iOS液态玻璃
核心: 半透明背景 + 霓虹描边 + 统一圆角 + 深度层次
"""


class Glass:
    """液态玻璃配色系统 - 统一规范"""

    # ===== 背景层级（由底到顶，透明度递增）=====
    BG_BASE = 'rgba(14, 16, 24, 0.60)'         # 窗口底层 - 60%
    BG_GLASS = 'rgba(16, 18, 26, 0.65)'        # 玻璃层 - 65%
    BG_CARD = 'rgba(16, 18, 28, 0.90)'         # 卡片层 - 90%（确保文字可读）
    BG_ELEVATED = 'rgba(22, 24, 36, 0.92)'     # 顶层 - 92%
    BG_HOVER = 'rgba(255, 255, 255, 0.05)'     # 悬浮反馈
    BG_PRESSED = 'rgba(255, 255, 255, 0.08)'   # 按下反馈
    BG_INPUT = 'rgba(14, 16, 24, 0.95)'        # 输入框

    # v4.4.0 修复(P2 设计令牌): 以下三个值原本散落在 main_window/champion_picker/
    # overlay_window 里当内联字符串硬写, 同一种底色出现 3~4 份拷贝, 改主题要满项目搜。
    # 集中到这里, 调用点一律用 Glass.XXX 引用, 不要再各页各写一份。
    BG_SIDEBAR = 'rgba(10, 12, 20, 0.65)'      # 主窗左侧导航条
    BG_LIST_ITEM = '#1c1c28'                   # 英雄选择器右侧面板底
    BG_LIST_ROW = '#131320'                    # 英雄选择器已选列表 / 排序按钮底
    BG_OVERLAY_DOT = 'rgba(10, 12, 20, 0.01)'  # 小窗标题栏(近全透明, 仅占位接收拖拽)
    BG_SEPARATOR = 'rgba(255, 255, 255, 0.07)'  # 小窗 mini_log 上方的 1px 分隔线
    # v4.4.1(UI-B7): 托盘右键菜单底 —— 故意用**不透明**色。
    # 菜单是独立的顶层弹出窗口, 半透明底在部分系统主题/关掉合成的环境下会露出
    # 桌面或渲染成灰块; 这种小面积弹层不值得冒该风险。
    BG_MENU = '#191b25'

    # ===== 霓虹描边 =====
    NEON_BORDER = 'rgba(99, 102, 241, 0.18)'     # 靛蓝霓虹
    NEON_BORDER_HOVER = 'rgba(99, 102, 241, 0.30)'
    NEON_GLOW = 'rgba(99, 102, 241, 0.08)'       # 外发光
    BORDER_GLASS = 'rgba(255, 255, 255, 0.06)'   # 玻璃折射边
    BORDER_HOVER = 'rgba(255, 255, 255, 0.10)'

    # ===== 品牌色（靛蓝）=====
    PRIMARY = '#6366f1'
    PRIMARY_HOVER = '#818cf8'
    PRIMARY_PRESSED = '#4f46e5'
    PRIMARY_GLOW = 'rgba(99, 102, 241, 0.15)'
    PRIMARY_SUBTLE = 'rgba(99, 102, 241, 0.10)'

    # ===== 语义色 =====
    ACCENT = '#34d399'
    DANGER = '#f87171'
    DANGER_SUBTLE = 'rgba(248, 113, 113, 0.10)'
    WARNING = '#fbbf24'
    SUCCESS = '#34d399'

    # v4.4.1(UI-B5): 连接"中间态"专用色(与 WARNING 同值, 但语义独立)。
    # 原来连接指示只有红/绿两态, 于是"应用刚启动、正在扫客户端"和"客户端真的
    # 没开"显示得一模一样(都是红色未连接), 用户会以为软件坏了。中间态用琥珀,
    # 与"断开"的红色"危险语义"区分开。
    CONNECTING = '#fbbf24'

    # v4.4.1(UI-A2): 按钮三档色。原来 ACCENT/DANGER 的 hover/pressed/渐变末端
    # 散落在 overlay_window 里硬写成 #6ee7b7/#059669/#047857/#fca5a5/#dc2626/#b91c1c,
    # 且 hover 渐变写成 `stop:0 X, stop:0 Y`(两档同一个 stop, 后半段被丢弃 → 描边
    # 颜色不生效/渐变退化成纯色)。集中到这里, 语义色只有一处定义。
    ACCENT_HOVER = '#6ee7b7'
    ACCENT_DARK = '#059669'
    ACCENT_PRESSED = '#047857'
    DANGER_HOVER = '#fca5a5'
    DANGER_DARK = '#dc2626'
    DANGER_PRESSED = '#b91c1c'

    # v4.4.1(UI-A1): 主窗启动按钮的"红色(停止)"三档 —— 原先 3 处硬编 hex
    STOP_RED = '#dc2626'
    STOP_RED_HOVER = '#b91c1c'
    STOP_RED_PRESSED = '#991b1b'

    # v4.4.1(UI-A6): 偏好态翠绿。原来 overlay_window 的偏好英雄框用
    # rgba(34,197,94,*) = #22c55e, 与 ACCENT(#34d399) 并存 —— 全项目第三种绿。
    # 统一由 ACCENT 派生(54→52 微调到 ACCENT 的 52,211,153), 语义绿只此一处。
    ACCENT_SUBTLE = 'rgba(52, 211, 153, 0.10)'
    ACCENT_SOFT = 'rgba(52, 211, 153, 0.18)'
    ACCENT_BORDER = 'rgba(52, 211, 153, 0.85)'
    ACCENT_BORDER_HOVER = 'rgba(52, 211, 153, 1.0)'

    # ===== 文字色（对比度 7:1+）=====
    TEXT_PRIMARY = '#ffffff'        # 主文字 - 纯白
    TEXT_SECONDARY = '#c8c8d8'     # 次文字 - 亮灰
    TEXT_MUTED = '#9aa0b8'         # 弱化文字 - 中亮灰（v15：原 #8888a0 在玻璃透底时跌破 4.5:1，提亮一档保 AA）
    TEXT_ON_PRIMARY = '#ffffff'

    # ===== 统一圆角规范 =====
    RADIUS_WIN = 12      # 窗口级（DWM 原生圆角）
    RADIUS_CARD = 10     # 卡片/面板
    RADIUS_BTN = 8       # 按钮/开关
    RADIUS_SM = 6        # 小元素（输入框、进度条）

    # 兼容旧代码
    RADIUS_MD = RADIUS_BTN
    RADIUS_LG = RADIUS_CARD
    RADIUS_XL = 14
    RADIUS_XS = 6

    # ===== 字号 =====
    FONT_SIZE_XS = '11px'
    FONT_SIZE_SM = '12px'
    FONT_SIZE_MD = '13px'
    FONT_SIZE_LG = '14px'
    FONT_SIZE_XL = '16px'

    # ===== 霓虹描边颜色值（QColor 用）=====
    NEON_QCOLOR = (99, 102, 241)     # #6366f1 RGB
    NEON_ALPHA_MIN = 15              # 静态最低透明度
    NEON_ALPHA_MAX = 40              # 脉冲最高透明度

    # ===== 输入框 =====
    INPUT_STYLE = f"""
        QLineEdit {{
            background-color: {BG_INPUT};
            color: {TEXT_PRIMARY};
            border: 1px solid {NEON_BORDER};
            border-radius: {RADIUS_SM};
            padding: 8px 12px;
            font-size: {FONT_SIZE_SM};
            selection-background-color: {PRIMARY};
        }}
        QLineEdit:focus {{
            border-color: {PRIMARY};
        }}
    """

    # ===== 统一细滚动条（全项目唯一来源，勿再各页各写一份）=====
    # v4.3.0: 之前首页/设置页各写一份、英雄页偏好列表和小窗干脆没写 → 露出 Windows 原生
    # 粗滚动条(带箭头/凹槽), 各页观感不一致。这里集中一份, 所有
    # QScrollArea / QListWidget / QTextEdit 复用: `bg_qss + Glass.SCROLL_QSS`。
    SCROLL_QSS = """
        QScrollBar:vertical {
            background: transparent;
            width: 4px;
            margin: 0;
        }
        QScrollBar::handle:vertical {
            background: rgba(255, 255, 255, 0.10);
            border-radius: 2px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(255, 255, 255, 0.22);
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            background: transparent;
            height: 4px;
            margin: 0;
        }
        QScrollBar::handle:horizontal {
            background: rgba(255, 255, 255, 0.10);
            border-radius: 2px;
            min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover {
            background: rgba(255, 255, 255, 0.22);
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: transparent;
        }
    """

    # ===== 托盘右键菜单（v4.4.1 UI-B7）=====
    # 原托盘 QMenu 裸用系统默认渲染: 浅色底 + 系统字体, 与整个应用的深色玻璃
    # 观感完全脱节。这里给一份深色 QSS —— 注意 QMenu 是**系统弹出窗口**,
    # 部分系统主题/高对比度模式下 Qt 会忽略自定义样式, 那属于可接受的降级
    # (回落到原生菜单, 不会渲染坏), 所以只做"锦上添花"式着色, 不依赖它承重。
    MENU_QSS = f"""
        QMenu {{
            background-color: {BG_MENU};
            color: {TEXT_SECONDARY};
            border: 1px solid {NEON_BORDER};
            border-radius: {RADIUS_SM}px;
            padding: 4px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 6px 22px 6px 12px;
            border-radius: 4px;
            font-size: {FONT_SIZE_SM};
        }}
        QMenu::item:selected {{
            background-color: {PRIMARY_SUBTLE};
            color: {TEXT_PRIMARY};
        }}
        QMenu::item:disabled {{
            color: {TEXT_MUTED};
        }}
        QMenu::separator {{
            height: 1px;
            background: {BG_SEPARATOR};
            margin: 4px 8px;
        }}
        QMenu::icon {{
            padding-left: 8px;
        }}
    """

    # ===== 日志 =====
    LOG_STYLE = f"""
        QTextEdit {{
            background-color: rgba(12, 14, 20, 0.85);
            color: {TEXT_SECONDARY};
            border: 1px solid {NEON_BORDER};
            border-radius: {RADIUS_CARD};
            font-size: {FONT_SIZE_XS};
            font-family: 'Cascadia Code', 'Consolas', 'Microsoft YaHei UI', 'Microsoft YaHei', monospace;
            padding: 12px;
            selection-background-color: {PRIMARY};
        }}
        {SCROLL_QSS}
    """
