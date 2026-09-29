"""ScreenUtils - 多屏 / 高 DPI 安全定位工具 (v4.4.0 新增)

历史事故(本次修复的真实根因):
    三处窗口定位都写成 `QApplication.primaryScreen().geometry()`,
    再拿它的 width()/height() 去算 `move()` 的坐标。

    问题有两层:
    1) **坐标系混用**。`QScreen.geometry()` 返回的是**设备像素**(physical),
       而 `QWidget.move()` 接受的是**逻辑像素**(logical)。125% 缩放下二者差
       1.25 倍 → 窗口偏出去一截; 150% 偏差更大。必须用
       `QScreen.availableGeometry()`, 它和新版 Qt 的 move() 同处逻辑坐标系。
    2) **硬绑主屏**。偏移量 30/120 是"贴着主屏右下角"量出来的。副屏在主屏
       **左侧**时副屏的 x 坐标是负数, 用主屏坐标算出来的位置会把窗口甩到
       屏外(或者落在两屏之间的缝里, 用户看不到窗口但进程还在跑)。

    所以这里统一提供两个入口:
    - `active_screen()`      : 鼠标所在屏(用户当下在看的屏), 取不到退回主屏
    - `available_rect(...)`  : 逻辑坐标的可用区域(已扣任务栏)

    所有需要定位的窗口都走这两个函数, 不要再自己 `primaryScreen().geometry()`。
"""
from PyQt5.QtCore import QRect
from PyQt5.QtGui import QCursor, QGuiApplication


def screen_at_point(point):
    """逻辑坐标点所在屏。

    v4.4.0: 多屏非对齐摆放时点可能落在屏幕之间的缝隙 → screenAt 返回 None,
    此时退回主屏, 绝不让调用方拿到 None 再 AttributeError。
    """
    if point is not None:
        scr = QGuiApplication.screenAt(point)
        if scr is not None:
            return scr
    return QGuiApplication.primaryScreen()


def active_screen():
    """鼠标所在的那块屏 —— 用户"正在用"的屏。

    比 primaryScreen() 靠谱: 用户把主窗拖到副屏后再开小窗/协议框,
    期望它们出现在副屏, 而不是弹回主屏。
    """
    try:
        return screen_at_point(QCursor.pos())
    except Exception:
        return QGuiApplication.primaryScreen()


def screen_for_window(widget, point=None):
    """窗口定位用屏: 优先窗口当前中心所在屏, 否则用鼠标屏。

    v4.4.0: 给"已存在且被拖动过"的窗口做二次定位(如详情窗居中于主窗)
    时, 必须认窗口自己所在的那块屏, 否则会在副屏上又弹回主屏中央。
    窗口尚未显示时 frameGeometry 是默认值(0,0 附近), screenAt 可能返回
    主屏 —— 这与旧行为一致, 不算回归。
    """
    try:
        if widget is not None:
            geo = widget.frameGeometry()
            if geo.width() > 0 and geo.height() > 0:
                scr = QGuiApplication.screenAt(geo.center())
                if scr is not None:
                    return scr
    except Exception:
        pass
    return screen_at_point(point) if point is not None else active_screen()


def available_rect(widget=None, point=None, screen=None):
    """逻辑坐标的可用区域(已扣除任务栏)。

    始终返回一个 QRect; 取不到屏时给一个 1920x1080 的安全兜底,
    避免调用方在 `rect.width()` 上崩掉。
    """
    scr = screen or screen_for_window(widget, point)
    if scr is not None:
        try:
            rect = scr.availableGeometry()
            if rect.width() > 0 and rect.height() > 0:
                return rect
        except Exception:
            pass
    return QRect(0, 0, 1920, 1080)


def center_on_screen(widget, screen=None, point=None):
    """把窗口居中到某块屏的可用区域(逻辑坐标)。

    原来的 `(screen.width()-w)//2, (screen.height()-h)//2` 在副屏(尤其
    坐标为负的左侧副屏)上会把窗口放到主屏坐标系的原点附近, 即屏外。
    这里用 `rect.x() +` 把屏幕原点带进去, 负坐标屏也正确。
    """
    rect = available_rect(widget, point, screen)
    x = rect.x() + (rect.width() - widget.width()) // 2
    y = rect.y() + (rect.height() - widget.height()) // 2
    widget.move(max(rect.x(), x), max(rect.y(), y))
    return widget.pos()


def place_bottom_right(widget, margin_right=0, margin_bottom=0,
                       screen=None, point=None):
    """贴某块屏可用区域的右下角(带边距), 逻辑坐标。

    边距是"从右下角往里推多少", 但会先夹紧到屏内: 小屏/高缩放下
    margin_bottom=120 可能把窗口顶上屏幕外, 此时宁可牺牲边距也不要丢窗口。
    """
    rect = available_rect(widget, point, screen)
    w, h = widget.width(), widget.height()
    x = rect.x() + rect.width() - w - int(margin_right)
    y = rect.y() + rect.height() - h - int(margin_bottom)
    # v4.4.0: 夹到可用区域内; 窗口比屏幕还大时也只保证左上角可见
    x = min(max(rect.x(), x), max(rect.x(), rect.x() + rect.width() - w))
    y = min(max(rect.y(), y), max(rect.y(), rect.y() + rect.height() - h))
    widget.move(x, y)
    return widget.pos()
