"""styles.py - 设计令牌系统（ULTRA 旗舰浅色 · 深青绿强调）

规范依据：ULTRA 设计系统 v6.0
参考来源：Linear（信息密度与视觉注意力）、Vercel Geist（10 级色彩分层与
组件背景/边框/文字三级递进）、Microsoft Fluent 2（组件状态完整性与可访问性）、
WCAG 2.2 AA（文字对比度 ≥4.5:1、交互目标尺寸）。

核心：雾白底 + 暖白卡片 + 浅灰分区 + 深青绿品牌色。四级层次（窗口/内容/
卡片/浮层）明确区分，禁用纯白铺底、大面积渐变、荧光色与粗重描边。

结构：
  - HIG     —— 语义令牌（单一事实来源）
  - Glass   —— 兼容层：保留全部历史字段名（全项目引用不破），值对齐 ULTRA
"""


class HIG:
    """ULTRA 浅色语义令牌（单一事实来源）。"""

    # ===== 品牌色（深青绿 #087F73，沉稳旗舰感）=====
    BRAND = '#087F73'             # 主色 / 选中 / 连接
    BRAND_HOVER = '#07685F'
    BRAND_PRESSED = '#06574F'
    ON_BRAND = '#FFFFFF'          # 主色上的文字（白，对比度 ≥5:1）
    BRAND_GLOW = 'rgba(8, 127, 115, 0.16)'

    # ===== 语义色（浅底上保证 WCAG 对比度）=====
    SUCCESS = '#19845F'           # 成功 / 胜利
    SUCCESS_SUBTLE = 'rgba(25, 132, 95, 0.12)'   # 成功/胜利 浅底
    WARN = '#B77928'              # 警告 / 连接中 / 预输入
    PREF = '#2E6E9E'              # 偏好 / 收藏（浅底可读蓝）
    DANGER = '#C34F55'            # 危险 / 停止 / 失败
    DANGER_HOVER = '#AF444A'
    DANGER_PRESSED = '#9C3A40'
    DANGER_SUBTLE = '#FBEAEA'     # 危险浅色背景（验收文档指定）
    BRAND_BG = '#DDF2EC'          # 品牌浅色背景（验收文档指定）

    # ===== 背景四级（窗口 → 内容 → 卡片 → 浮层）=====
    BG_WINDOW = '#F2F4F1'         # L0 应用主背景（雾白）
    BG_SIDEBAR = '#E9EFEC'        # L0b 侧边导航（浅灰绿）
    BG_CONTENT = '#F5F7F4'        # L1 主要内容背景
    BG_CARD = '#FAFBF8'           # L2 主卡片（暖白）
    BG_CARD_ALT = '#EAF0ED'       # L2b 次级内容 / 分区
    BG_ELEVATED = '#FDFEFC'       # L3 弹层 / 菜单
    BG_INSET = '#EDF1EE'          # 内凹（输入框 / 日志 / 占位）
    BG_HOVER = 'rgba(8, 127, 115, 0.08)'      # 悬停：淡青绿提示
    BG_PRESSED = 'rgba(8, 127, 115, 0.14)'

    # ===== 边框（弱化，三级递进：默认 / hover / active）=====
    BORDER = '#D8E1DC'            # 常规边框
    BORDER_HOVER = '#C2CFC8'      # hover 边框
    BORDER_STRONG = '#ADBFB6'     # 强调边框
    SEPARATOR = '#D8E1DC'

    # ===== 文字层级 =====
    TEXT_PRIMARY = '#202D35'      # 深灰蓝（非纯黑）
    TEXT_SECONDARY = '#52616D'
    TEXT_MUTED = '#5E6C75'
    TEXT_DISABLED = '#9AA6AC'
    TEXT_ON_PRIMARY = '#FFFFFF'

    # ===== 间距（4px 基础体系）=====
    SPACE_SMALL = 4
    SPACE_MEDIUM = 8
    SPACE_LARGE = 12
    SPACE_XL = 16
    SPACE_XX = 20
    SPACE_XXL = 24
    GRID_UNIT = 4

    # ===== 圆角档位 =====
    RADIUS_WIN = 16               # 窗口外框
    RADIUS_CARD = 14              # 大面板 / 卡片
    RADIUS_BTN = 10               # 按钮
    RADIUS_SM = 8                 # 小元素 / 内部数据卡
    RADIUS_PILL = 999             # 胶囊

    # ===== 字号（WCAG 可读，逐级清晰）=====
    FONT_SIZE_XS = '12px'         # 辅助说明
    FONT_SIZE_SM = '13px'         # 辅助
    FONT_SIZE_MD = '14px'         # 正文 / 数据
    FONT_SIZE_LG = '16px'         # 卡片标题 / 强调正文
    FONT_SIZE_XL = '18px'         # 区域标题
    FONT_SIZE_XXL = '22px'        # 页面标题


class Glass:
    """兼容层：保留历史字段名，值统一对齐 ULTRA（不再维护旧浅色色板）。

    新代码请直接用 HIG.*；旧引用点无需改动即可获得新视觉。
    """

    # ===== 背景层级 =====
    BG_BASE = HIG.BG_WINDOW
    BG_GLASS = HIG.BG_WINDOW
    BG_CONTENT = HIG.BG_CONTENT
    BG_CARD = HIG.BG_CARD
    BG_CARD_ALT = HIG.BG_CARD_ALT
    BG_ELEVATED = HIG.BG_ELEVATED
    BG_HOVER = HIG.BG_HOVER
    BG_PRESSED = HIG.BG_PRESSED
    BG_INPUT = HIG.BG_INSET
    BG_INSET = HIG.BG_INSET

    BG_SIDEBAR = HIG.BG_SIDEBAR
    BG_LIST_ITEM = HIG.BG_CARD_ALT
    BG_LIST_ROW = 'rgba(0, 0, 0, 0.025)'
    BG_OVERLAY_DOT = 'rgba(242, 244, 241, 0.02)'
    BG_SEPARATOR = HIG.SEPARATOR
    BG_MENU = HIG.BG_ELEVATED

    # ===== 边框（浅色弱化）=====
    BORDER = HIG.BORDER
    BORDER_STRONG = HIG.BORDER_STRONG
    NEON_BORDER = HIG.BORDER
    NEON_BORDER_HOVER = HIG.BORDER_HOVER
    NEON_GLOW = 'transparent'
    BORDER_GLASS = HIG.BORDER
    BORDER_HOVER = HIG.BORDER_HOVER

    # ===== 品牌色（深青绿 #087F73）=====
    PRIMARY = HIG.BRAND
    PRIMARY_HOVER = HIG.BRAND_HOVER
    PRIMARY_PRESSED = HIG.BRAND_PRESSED
    PRIMARY_GLOW = HIG.BRAND_GLOW
    PRIMARY_SUBTLE = 'rgba(8, 127, 115, 0.10)'

    PRIMARY_06 = 'rgba(8, 127, 115, 0.06)'
    PRIMARY_10 = 'rgba(8, 127, 115, 0.10)'
    PRIMARY_12 = 'rgba(8, 127, 115, 0.12)'
    PRIMARY_18 = 'rgba(8, 127, 115, 0.18)'
    PRIMARY_20 = 'rgba(8, 127, 115, 0.20)'
    PRIMARY_30 = 'rgba(8, 127, 115, 0.30)'
    PRIMARY_40 = 'rgba(8, 127, 115, 0.40)'

    ACCENT = HIG.BRAND
    ACCENT_BG = HIG.BRAND_BG
    DANGER = HIG.DANGER
    DANGER_BG = HIG.DANGER_SUBTLE
    DANGER_SUBTLE = 'rgba(195, 79, 85, 0.10)'
    DANGER_30 = 'rgba(195, 79, 85, 0.30)'
    WARNING = HIG.WARN
    SUCCESS = HIG.SUCCESS
    SUCCESS_SUBTLE = HIG.SUCCESS_SUBTLE
    CONNECTING = HIG.WARN

    ACCENT_HOVER = HIG.BRAND_HOVER
    ACCENT_DARK = HIG.BRAND_PRESSED
    ACCENT_PRESSED = HIG.BRAND_PRESSED
    DANGER_HOVER = HIG.DANGER_HOVER
    DANGER_DARK = '#B0484E'
    DANGER_PRESSED = HIG.DANGER_PRESSED

    STOP_RED = HIG.DANGER
    STOP_RED_HOVER = HIG.DANGER_HOVER
    STOP_RED_PRESSED = HIG.DANGER_PRESSED

    ACCENT_SUBTLE = 'rgba(8, 127, 115, 0.09)'
    ACCENT_SOFT = 'rgba(8, 127, 115, 0.14)'
    ACCENT_BORDER = 'rgba(8, 127, 115, 0.85)'
    ACCENT_BORDER_HOVER = 'rgba(8, 127, 115, 1.0)'

    # 预输入 = 温和橙（非默认状态），配合形状角标，不单靠颜色
    PREINPUT = HIG.WARN
    PREINPUT_SUBTLE = 'rgba(183, 121, 40, 0.12)'
    PREINPUT_SOFT = 'rgba(183, 121, 40, 0.20)'
    PREINPUT_BORDER = 'rgba(183, 121, 40, 0.90)'
    PREINPUT_BORDER_HOVER = 'rgba(183, 121, 40, 1.0)'

    # 偏好 = 浅底可读蓝
    PREF_BORDER = 'rgba(46, 110, 158, 0.85)'

    # 海克斯强化稀有度色（游戏内容色，非 UI 状态色，保留）
    AUG_SILVER = '#6F7E86'
    AUG_GOLD = '#9A742E'
    AUG_PRISMATIC = '#8A5FA0'
    AUG_EVENT = '#9A742E'

    # ===== 文字色 =====
    TEXT_PRIMARY = HIG.TEXT_PRIMARY
    TEXT_SECONDARY = HIG.TEXT_SECONDARY
    TEXT_MUTED = HIG.TEXT_MUTED
    TEXT_DISABLED = HIG.TEXT_DISABLED
    TEXT_ON_PRIMARY = HIG.TEXT_ON_PRIMARY

    # ===== 统一圆角 =====
    RADIUS_WIN = HIG.RADIUS_WIN
    RADIUS_CARD = HIG.RADIUS_CARD
    RADIUS_BTN = HIG.RADIUS_BTN
    RADIUS_SM = HIG.RADIUS_SM
    RADIUS_MD = HIG.RADIUS_BTN
    RADIUS_LG = HIG.RADIUS_CARD
    RADIUS_XL = HIG.RADIUS_WIN
    RADIUS_XS = HIG.RADIUS_SM

    # ===== 字号 =====
    FONT_SIZE_XS = HIG.FONT_SIZE_XS
    FONT_SIZE_SM = HIG.FONT_SIZE_SM
    FONT_SIZE_MD = HIG.FONT_SIZE_MD
    FONT_SIZE_LG = HIG.FONT_SIZE_LG
    FONT_SIZE_XL = HIG.FONT_SIZE_XL
    FONT_SIZE_XXL = HIG.FONT_SIZE_XXL

    # ===== 描边颜色值（QColor 用）=====
    NEON_QCOLOR = (8, 127, 115)      # 对齐深青绿 #087F73
    NEON_ALPHA_MIN = 15
    NEON_ALPHA_MAX = 40

    # ===== 输入框 =====
    INPUT_STYLE = f"""
        QLineEdit {{
            background-color: {BG_INPUT};
            color: {TEXT_PRIMARY};
            border: 1px solid {NEON_BORDER};
            border-radius: {RADIUS_BTN};
            padding: 8px 12px;
            font-size: {FONT_SIZE_MD};
            selection-background-color: {PRIMARY};
            selection-color: {TEXT_ON_PRIMARY};
        }}
        QLineEdit:focus {{
            border-color: {PRIMARY};
        }}
        QLineEdit:disabled {{
            color: {TEXT_MUTED};
            background-color: {BG_INPUT};
        }}
    """

    # ===== 统一细滚动条（全项目唯一来源）=====
    SCROLL_QSS = """
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
            margin: 2px;
        }
        QScrollBar::handle:vertical {
            background: rgba(32, 45, 53, 0.18);
            border-radius: 4px;
            min-height: 30px;
        }
        QScrollBar::handle:vertical:hover {
            background: rgba(32, 45, 53, 0.32);
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            background: transparent;
            height: 8px;
            margin: 2px;
        }
        QScrollBar::handle:horizontal {
            background: rgba(32, 45, 53, 0.18);
            border-radius: 4px;
            min-width: 30px;
        }
        QScrollBar::handle:horizontal:hover {
            background: rgba(32, 45, 53, 0.32);
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: transparent;
        }
    """

    # ===== 托盘右键菜单 =====
    MENU_QSS = f"""
        QMenu {{
            background-color: {BG_MENU};
            color: {TEXT_PRIMARY};
            border: 1px solid {NEON_BORDER};
            border-radius: {RADIUS_SM}px;
            padding: 4px;
        }}
        QMenu::item {{
            background: transparent;
            padding: 7px 24px 7px 14px;
            border-radius: 5px;
            font-size: {FONT_SIZE_MD};
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
            background-color: {BG_INPUT};
            color: {TEXT_SECONDARY};
            border: 1px solid {NEON_BORDER};
            border-radius: {RADIUS_CARD};
            font-size: {FONT_SIZE_SM};
            font-family: 'Cascadia Code', 'Consolas', 'Microsoft YaHei UI', 'Microsoft YaHei', monospace;
            padding: 12px;
            selection-background-color: {PRIMARY};
        }}
        {SCROLL_QSS}
    """
