"""Icons - 矢量图标绘制 & 动画工具"""
import hashlib
import math
from collections import OrderedDict

from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush, QPainterPath,\
    QRadialGradient, QLinearGradient, QImage
from PyQt5.QtCore import Qt, QPropertyAnimation, QEasingCurve, pyqtProperty, QRectF, QThread
from PyQt5.QtWidgets import QPushButton, QApplication
from .window_base import ui_slot
from .styles import Glass


def _draw_icon(size, draw_fn, color=None):
    """通用图标绘制器。

    v4.4.2(P2-1/P2-15): 原 `QPixmap(size, size)` 只栅格化固定物理 32×32 且不设
    devicePixelRatio, 于是 QIcon 的 availableSizes 恒为 [32×32]。icon_to_pixmap
    在 150%/200% 缩放下请求更大的物理位图时拿不到(实测 dpr=2.0 请求 44px 只回
    32px → 逻辑 16px, 比预算 22px 小 27%), 侧边栏/托盘图标发糊。改为 **2x 栅格化**:
    物理 size*2 + setDevicePixelRatio(2), draw_fn 仍在逻辑 size 坐标里画 ——
    pixmap 已带 DPR, QPainter 会自动套设备变换把逻辑坐标放大到物理像素。

    ⚠️ v4.4.2-r2 回归修复: 上面最初写成 setDevicePixelRatio(2) **又** p.scale(2,2),
    DPR 被应用了两次(合计 4x) —— 图标内容画到 size*4, 而画布只有 size*2,
    右下角整片被裁。实测 20px close 图标墨迹从居中 (8,8)-(31,31) 变成贴边
    (16,16)-(39,39) 且越出画布; 侧边栏 / 标题栏 / 托盘右键菜单图标全部显示不全
    (标题栏 16px 关闭图标只剩一条斜线)。去掉 p.scale 即恢复。
    """
    dpr = 2.0
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    # 注意: 这里**不能**再 p.scale(dpr, dpr) —— pixmap 已带 DPR,
    # QPainter 会自动套用设备变换(=DPR), 手动再 scale 一次就是 DPR² = 4x。
    if color:
        p.setPen(_icon_pen(color))
        p.setBrush(Qt.NoBrush)
    draw_fn(p, size)
    p.end()
    return QIcon(pm)


# ============================================================
# v4.4.1(UI-A7): 线性图标统一笔宽 / 线帽 / 线连接
# ============================================================
# 原来同一排侧边栏图标笔宽四处不同(1.8 / 1.5 / 1.4), 且只有 minimize/close
# 带 RoundCap, 其余是方头方角 —— 在 22px 的侧边栏里粗细与转角观感不齐。
# 现在只有这一个来源: 全部 1.8 + RoundCap + RoundJoin。
_ICON_STROKE = 1.8


def _icon_pen(color, width=None):
    """线性图标的统一画笔。"""
    return QPen(QColor(color),
                _ICON_STROKE if width is None else width,
                Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)


# ============================================================
# v4.4.0 修复(P1-16/P1-17): DPR 感知 pixmap 工厂 + LRU 缓存
# ============================================================
#
# 两个独立的老问题, 一起解决:
#
# (1) P1-16 全项目零 devicePixelRatio()
#     旧 `rounded_pixmap` 建 `QPixmap(size, size)`, 即**物理** size×size 像素。
#     在 150% 缩放的屏上, 一个 size=48 的位图会被 DWM 拉伸到 72 物理像素显示
#     → 头像/图标全部发糊, 边缘一圈灰。正确做法是按 DPR 放大位图尺寸再
#     `setDevicePixelRatio(dpr)`: Qt 之后按逻辑尺寸摆放、用 1:1 物理像素绘制,
#     在 125%/150%/200% 下都是清晰的原生分辨率。
#
# (2) P1-17 每次调用都重新解码
#     旧实现每次 `QImage().loadFromData(data)` + `SmoothTransformation` 缩放。
#     而 `main_window.py:378`、`recent_detail.py:80` 位于 500~900ms 的图标轮询
#     定时器里, `champion_picker` 一次选中要调 3 次(28px/28px/50px)。同一份
#     PNG 字节被反复解码+缩放, 是 UI 线程上一个稳定的小卡顿源。
#     这里用 `(sha1(字节), size, dpr, radius)` 做键缓存, 手写 LRU(OrderedDict),
#     不引第三方依赖。
#
# ⚠️ 线程约束: QPixmap 只能在 GUI 线程创建/使用。本项目的图标字节统一由
#    main.py 的 `_icon_pool` 下载线程 emit 信号排回主线程后才建 pixmap,
#    所以下面所有入口只要在主线程被调用就是安全的; 为了不让将来有人在
#    工作线程里误用, `_assert_gui_thread()` 会在非 GUI 线程时打一次警告
#    并**绕过缓存**(不缓存坏线程产物), 而不是静默产生未定义行为。
_PIXMAP_CACHE_MAX = 256
_PIXMAP_CACHE = OrderedDict()          # key -> QPixmap
_thread_warned = [False]


def _assert_gui_thread():
    """非 GUI 线程返回 False(调用方据此绕过缓存)。只警告一次, 不刷屏。"""
    try:
        app = QApplication.instance()
        if app is None:
            return True
        return QThread.currentThread() is app.thread()
    except Exception:
        return True


def _warn_once_non_gui():
    if not _thread_warned[0]:
        _thread_warned[0] = True
        print('[icons] 警告: pixmap 工厂在非 GUI 线程被调用, 已绕过缓存(结果请勿复用)')


def _default_dpr():
    """取当前应用所在屏的 DPR; 取不到回 1.0(等价旧行为, 不会更差)。"""
    try:
        app = QApplication.instance()
        if app is not None:
            scr = app.primaryScreen()
            if scr is not None:
                dpr = float(scr.devicePixelRatio())
                if dpr > 0:
                    return dpr
    except Exception:
        pass
    return 1.0


def _resolve_dpr(widget=None, dpr=None):
    """DPR 解析优先级: 显式参数 > 控件所在屏 > 主屏 > 1.0。"""
    if dpr is not None:
        try:
            d = float(dpr)
            if d > 0:
                return d
        except Exception:
            pass
    if widget is not None:
        try:
            d = float(widget.devicePixelRatioF())
            if d > 0:
                return d
        except Exception:
            pass
    return _default_dpr()


def _cache_get(key):
    pm = _PIXMAP_CACHE.get(key)
    if pm is None:
        return None
    _PIXMAP_CACHE.move_to_end(key)      # LRU: 命中即刷成最新
    # v4.4.0: 缓存里的 QPixmap 可能已被底层隐式分离/清空, 这里兜一层
    if pm.isNull():
        _PIXMAP_CACHE.pop(key, None)
        return None
    return pm


def _cache_put(key, pm):
    if pm is None or pm.isNull():
        return pm
    _PIXMAP_CACHE[key] = pm
    _PIXMAP_CACHE.move_to_end(key)
    # v4.4.0 上限淘汰: 超 256 项丢最旧(OrderedDict 头部)
    while len(_PIXMAP_CACHE) > _PIXMAP_CACHE_MAX:
        _PIXMAP_CACHE.popitem(last=False)
    return pm


def _digest(data):
    """sha1 前 16 字节十六进制 —— 够区分内容, 比存整个 bytes 当键省内存。"""
    try:
        return hashlib.sha1(data).hexdigest()[:32]
    except Exception:
        return repr(id(data))


def _new_dpr_pixmap(size, dpr):
    """建一张逻辑 size×size、物理 size*dpr 的透明位图。

    关键: 先 `setDevicePixelRatio(dpr)`, 之后在它上面用 QPainter 绘制时
    坐标系就是**逻辑坐标**(0..size), Qt 自动放大到物理像素, 不需要调用方
    自己乘 dpr。
    """
    px = max(1, int(round(size * dpr)))
    pm = QPixmap(px, px)
    pm.fill(Qt.transparent)
    pm.setDevicePixelRatio(dpr)
    return pm


def _decode_scaled(data, size, dpr, keep_aspect=True):
    """字节 → 按 DPR 缩放的 QPixmap(未做圆角)。失败返回 None。"""
    img = QImage()
    if not img.loadFromData(data or b''):
        return None
    # v4.4.0: 缩放目标按 dpr 放大, 否则等于"先缩小再被系统放大"=双倍模糊
    target = max(1, int(round(size * dpr)))
    mode = Qt.KeepAspectRatio if keep_aspect else Qt.KeepAspectRatioByExpanding
    src = QPixmap.fromImage(img)
    scaled = src.scaled(target, target, mode, Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(dpr)
    return scaled


def icon_pixmap(data, size, widget=None, dpr=None, keep_aspect=True,
                cache=True):
    """字节 → DPR 感知的方形 QPixmap(不裁圆角)。解析失败返回 None。

    用于需要原始方形图标的场景(小窗英雄格 46px、选择器 28/50px)。
    """
    if not data:
        return None
    if not _assert_gui_thread():
        _warn_once_non_gui()
        try:
            return _decode_scaled(data, size, _resolve_dpr(widget, dpr),
                                  keep_aspect)
        except Exception:
            return None
    d = _resolve_dpr(widget, dpr)
    key = ('sq', _digest(data), int(size), round(d, 3), 1 if keep_aspect else 0)
    if cache:
        hit = _cache_get(key)
        if hit is not None:
            return hit
    try:
        pm = _decode_scaled(data, size, d, keep_aspect)
    except Exception:
        return None
    return _cache_put(key, pm) if cache else pm


def rounded_pixmap(data, size, radius=6, widget=None, dpr=None, cache=True):
    """图片字节 → 圆角裁剪的方形 QPixmap(居中铺满, 超出部分裁掉)。

    英雄头像/装备图标共用: 解析失败(空数据/坏字节/非图片)返回 None,
    由调用方决定是继续轮询补图还是显示占位。

    v4.4.0: 保持原签名向后兼容(前三个参数不变), 新增可选 widget/dpr/cache。
    内部改为 DPR 感知 + LRU 缓存, 调用点无需改动即自动变清晰、变快;
    有控件引用的调用点建议传 `widget=` 让 DPR 跟屏走(多屏不同缩放时更准)。
    """
    if not data:
        return None
    if not _assert_gui_thread():
        _warn_once_non_gui()
        try:
            return _decode_rounded(data, size, radius, _resolve_dpr(widget, dpr))
        except Exception:
            return None
    d = _resolve_dpr(widget, dpr)
    # radius 也进键: 同一份字节 24px 圆角 r=4 与 50px 圆角 r=6 不能互串
    key = ('rr', _digest(data), int(size), round(d, 3), int(radius))
    if cache:
        hit = _cache_get(key)
        if hit is not None:
            return hit
    try:
        pm = _decode_rounded(data, size, radius, d)
    except Exception:
        return None
    return _cache_put(key, pm) if cache else pm


def _decode_rounded(data, size, radius, dpr):
    """内部: 解码 → DPR 缩放 → 圆角裁剪(在逻辑坐标系里画)。"""
    scaled = _decode_scaled(data, size, dpr, keep_aspect=False)
    if scaled is None:
        return None
    out = _new_dpr_pixmap(size, dpr)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(path)
    # scaled 的逻辑尺寸可能因 KeepAspectRatioByExpanding 稍大于 size, 居中裁。
    # v4.4.2(P0-4): 原先直接 `(size - scaled.width()) // 2`, 但 scaled.width()
    # 是**物理**宽度(设过 setDevicePixelRatio(dpr) 后返回物理像素), 与逻辑
    # 坐标系的 size 相减把偏移多算了 dpr 倍 —— 高缩放下头像歪斜、200% 时
    # 整个元素被裁出画布。改按逻辑宽度(物理 / dpr)算居中偏移。
    off = int(round((size - scaled.width() / dpr) / 2.0))
    p.drawPixmap(off, off, scaled)
    p.end()
    return out


def icon_to_pixmap(icon, size, widget=None, dpr=None):
    """QIcon → DPR 感知 QPixmap(侧边栏三态图标缓存用)。

    v4.4.0(P1-22): SidebarButton 原本在 paintEvent 里每次 `icon.pixmap(22,22)`,
    悬停/选中重绘就重新栅格化一遍矢量图标。这里统一走工厂, 由调用方
    按"状态"缓存一次即可。

    v4.4.2(P2-2): 删除从未被读取的死形参 `cache` —— 全项目唯一调用点
    (ui/main_window.py:272) 由 SidebarButton._build_icon_cache 自行缓存结果,
    这里的 `cache` 参数从未实现也从未使用, 留着会误导后续维护。
    """
    if icon is None:
        return None
    d = _resolve_dpr(widget, dpr)
    try:
        target = max(1, int(round(size * d)))
        # v4.4.0: QIcon.pixmap 传物理尺寸 + 设 DPR, 得到清晰的原生位图
        pm = icon.pixmap(target, target)
        if pm is None or pm.isNull():
            return None
        pm.setDevicePixelRatio(d)
        return pm
    except Exception:
        return None



# ========== 侧边栏图标 ==========
def icon_home(color=None):
    c = color or Glass.TEXT_SECONDARY
    def draw(p, s):
        m = s * 0.22  # margin
        # 房子主体
        path = QPainterPath()
        path.moveTo(s/2, m)
        path.lineTo(s - m, s*0.42)
        path.lineTo(s - m, s - m)
        path.lineTo(m, s - m)
        path.lineTo(m, s*0.42)
        path.closeSubpath()
        p.setPen(_icon_pen(c))
        p.drawPath(path)
        # 门
        p.drawRect(int(s*0.38), int(s*0.55), int(s*0.24), int(s*0.25))
    return _draw_icon(32, draw, c)

def icon_settings(color=None):
    """齿轮/设置图标"""
    c = color or Glass.TEXT_SECONDARY
    def draw(p, s):
        cx, cy = s / 2, s / 2
        r_out = s * 0.40  # 齿轮外半径
        r_in = s * 0.30   # 齿根半径
        r_hole = s * 0.12  # 中心孔
        n_teeth = 6
        # 绘制齿轮轮廓
        path = QPainterPath()
        for i in range(n_teeth):
            a0 = i * 2 * math.pi / n_teeth
            a1 = a0 + math.pi / n_teeth * 0.4
            a2 = a0 + math.pi / n_teeth * 0.6
            a3 = a0 + math.pi / n_teeth * 1.4
            a4 = a0 + math.pi / n_teeth * 1.6
            # 齿顶
            pts = [
                (cx + r_in * math.cos(a0), cy + r_in * math.sin(a0)),
                (cx + r_out * math.cos(a1), cy + r_out * math.sin(a1)),
                (cx + r_out * math.cos(a2), cy + r_out * math.sin(a2)),
                (cx + r_in * math.cos(a3), cy + r_in * math.sin(a3)),
            ]
            if i == 0:
                path.moveTo(pts[0][0], pts[0][1])
            else:
                path.lineTo(pts[0][0], pts[0][1])
            for px, py in pts[1:]:
                path.lineTo(px, py)
        path.closeSubpath()
        # 减去中心孔
        hole = QPainterPath()
        hole.addEllipse(int(cx - r_hole), int(cy - r_hole), int(r_hole * 2), int(r_hole * 2))
        result = path.subtracted(hole)
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        p.drawPath(result)
        # 绘制中心小圆
        p.setPen(_icon_pen(c))
        p.drawEllipse(int(cx - r_hole), int(cy - r_hole), int(r_hole * 2), int(r_hole * 2))
    return _draw_icon(32, draw, c)

def icon_hero(color=None):
    c = color or Glass.TEXT_SECONDARY
    def draw(p, s):
        # 星形
        cx, cy = s/2, s/2
        r_out, r_in = s*0.38, s*0.16
        path = QPainterPath()
        for i in range(5):
            angle = i * 2 * math.pi / 5 - math.pi / 2
            x = cx + r_out * math.cos(angle)
            y = cy + r_out * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
            angle2 = angle + math.pi / 5
            x2 = cx + r_in * math.cos(angle2)
            y2 = cy + r_in * math.sin(angle2)
            path.lineTo(x2, y2)
        path.closeSubpath()
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    return _draw_icon(32, draw, c)

def icon_log(color=None):
    c = color or Glass.TEXT_SECONDARY
    def draw(p, s):
        m = s * 0.22
        p.setPen(_icon_pen(c))
        # 文档外框
        p.drawRoundedRect(int(m), int(m), int(s-2*m), int(s-2*m), 3, 3)
        # 横线
        y_off = s * 0.35
        for i in range(3):
            y = y_off + i * s * 0.14
            p.drawLine(int(m+5), int(y), int(s-m-5), int(y))
    return _draw_icon(32, draw, c)

def icon_chart(color=None):
    """战绩 - 柱状图(三根高低柱 + 基线)

    v4.4.1(UI-A7): 原来三根柱子是 **填充**(setPen(NoPen) + setBrush 实心),
    而全项目其余图标都是 **描线** 风格 —— 同一排侧边栏里一实一虚很跳, 且填充柱
    在 22px 下与白线图标比显得更重。改为空心圆角矩形描边, 笔宽/线帽与其余图标
    一致; 基线也并入 _icon_pen 统一宽度(原来单独用 1.4)。
    """
    c = color or Glass.TEXT_SECONDARY
    def draw(p, s):
        m = s * 0.22
        span = s - 2 * m
        base = s - m
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        # (中心x比例, 顶部y比例, 宽度比例)
        for cx_f, top_f, w_f in ((0.22, 0.46, 0.16), (0.5, 0.24, 0.16), (0.78, 0.06, 0.16)):
            x = m + span * cx_f
            w = span * w_f
            y = m + span * top_f
            p.drawRoundedRect(QRectF(x - w / 2.0, y, w, base - y), 1.5, 1.5)
        # 基线
        p.drawLine(int(m), int(base), int(s - m), int(base))
    return _draw_icon(32, draw, c)

def icon_info(color=None):
    """关于 - 圆环内的信息符号(i: 上点 + 下竖线, 描线风格)。

    v4.5.0: 「关于」页的侧边栏图标。笔宽/线帽走 _icon_pen, 与其余线性图标一致;
    圆环留 m=s*0.20 的余量, 保证在 16/20/22/32 四档缩放后都不触边。
    """
    c = color or Glass.TEXT_MUTED
    def draw(p, s):
        m = s * 0.20
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        # 外圆环
        p.drawEllipse(QRectF(m, m, s - 2 * m, s - 2 * m))
        # i 的上点(实心小圆)
        r = max(1.0, s * 0.045)
        p.setBrush(QBrush(QColor(c)))
        p.drawEllipse(QRectF(s / 2.0 - r, s * 0.30 - r, r * 2, r * 2))
        # i 的下竖线
        p.setBrush(Qt.NoBrush)
        p.drawLine(int(s / 2), int(s * 0.44), int(s / 2), int(s * 0.72))
    return _draw_icon(32, draw, c)

# ========== 功能图标 ==========
def icon_minimize(color=None):
    """最小化 - 横线(矢量)"""
    c = color or Glass.TEXT_MUTED
    def draw(p, s):
        m = s * 0.25
        p.setPen(_icon_pen(c))
        p.drawLine(int(m), int(s * 0.5), int(s - m), int(s * 0.5))
    return _draw_icon(20, draw, c)

def icon_close(color=None):
    """关闭 - 两条交叉斜线(Win11 风格矢量)"""
    c = color or Glass.TEXT_MUTED
    def draw(p, s):
        m = s * 0.25
        p.setPen(_icon_pen(c))
        p.drawLine(int(m), int(m), int(s - m), int(s - m))
        p.drawLine(int(s - m), int(m), int(m), int(s - m))
    return _draw_icon(20, draw, c)


# ========== 托盘菜单图标 (v4.4.1 UI-B7) ==========
# 托盘右键菜单原先只有纯文字, 是全应用唯一一处"裸系统观感"的地方。
# 三个图标与侧边栏图标同一套笔宽/线帽(走 _icon_pen), 尺寸 20 与标题栏一致。
def icon_tray_show(color=None):
    """显示主界面 - 窗口外框 + 标题栏分隔线。"""
    c = color or Glass.TEXT_SECONDARY
    def draw(p, s):
        m = s * 0.18
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(m, m + s * 0.06, s - 2 * m, s - 2 * m - s * 0.06), 3, 3)
        # 标题栏分隔线
        ty = m + s * 0.06 + (s - 2 * m - s * 0.06) * 0.26
        p.drawLine(int(m), int(ty), int(s - m), int(ty))
    return _draw_icon(20, draw, c)

def icon_tray_play(color=None):
    """启动脚本 - 圆角三角形(描线, 与其余图标同风格)。"""
    c = color or Glass.ACCENT
    def draw(p, s):
        m = s * 0.20
        path = QPainterPath()
        path.moveTo(m + s * 0.06, m)
        path.lineTo(s - m, s / 2.0)
        path.lineTo(m + s * 0.06, s - m)
        path.closeSubpath()
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    return _draw_icon(20, draw, c)

def icon_tray_stop(color=None):
    """停止脚本 - 圆角方块(与 play 三角同风格, 用于运行态的托盘项)。"""
    c = color or Glass.DANGER
    def draw(p, s):
        m = s * 0.26
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(m, m, s - 2 * m, s - 2 * m), 2, 2)
    return _draw_icon(20, draw, c)

def icon_tray_quit(color=None):
    """退出 - 电源符号 ⏻(不闭合圆弧 + 穿过缺口的竖线)。

    v4.4.4 修: 原参数 `drawArc(..., 60*16, 240*16)` 把缺口开在 **3 点钟(正右)**,
    而竖线画在正上方 —— 两者错位 90°, 语义直接丢失: 16px 下退化成"一个右侧
    开口的 C + 一根不相干的竖线", 看着像刷新 ↻, 与"退出"毫无关联(用户质询)。
    原注释也自相矛盾: 自称"留出顶部缺口(270° 跨度, 从 -60° 起)", 与代码参数
    对不上, 且那组参数算下来缺口落在左下, 同样不是顶部。

    正确画法: 缺口留在 **12 点钟**, 竖线从缺口正中穿下。
    Qt 角度约定: 0°=3 点钟, 正值逆时针(屏幕 y 轴向下, 视觉上为顺时针)。
    跳过 60°→120°(中心 90°=12 点钟) → start=120°, span=300°。
    """
    c = color or Glass.TEXT_MUTED
    def draw(p, s):
        m = s * 0.20
        p.setPen(_icon_pen(c))
        p.setBrush(Qt.NoBrush)
        # 覆盖 120°→420°(=60°), 缺口 = (60°,120°) 中心在 90°(正上)
        p.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 120 * 16, 300 * 16)
        # 竖线: 自圆外一点穿缺口落到圆心上方(与缺口同轴, x = 圆心)
        # ⚠️ 顶端系数必须留够余量: 曾试 m*0.70(=int(2)=2 逻辑), 20px 源图缩放
        #   到托盘菜单的 16px 后顶端墨迹落到 y=0.88 → 触上边被裁(探针实测)。
        #   该系数经 _probe_dpr_residue.py 在 16/20/22/32 四档验证无触边。
        p.drawLine(int(s / 2), int(m * 0.85), int(s / 2), int(s * 0.47))
    return _draw_icon(20, draw, c)


class _TitleBtn(QPushButton):
    """标题栏控制按钮:感知 hover 切换图标颜色。
    QSS 改不了 setIcon 的颜色,必须事件驱动切图。

    v4.4.2-r2: 移除 setToolTip —— 用户明确要求不要悬停时弹出的悬浮提示框
    (与 ui/main_window.py:415 / ui/champion_picker.py:58 的既有约定一致:
    本应用不加悬浮小提示)。原 tooltip 形参一并删除,避免留个永远不用的参数。
    """
    def __init__(self, icon_normal, icon_hover, parent=None):
        super().__init__(parent)
        self._ic_n, self._ic_h = icon_normal, icon_hover
        self.setIcon(icon_normal)
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, e):
        self.setIcon(self._ic_h); super().enterEvent(e)

    def leaveEvent(self, e):
        self.setIcon(self._ic_n); super().leaveEvent(e)

def make_title_buttons(minimize_cb=None, close_cb=None):
    """统一样式的标题栏控制按钮(主窗口/小窗共用,v15.2 矢量图标版)
    - 30×26 / RADIUS_SM 圆角 / 微弱 normal 底色
    - 矢量图标(icon_minimize / icon_close)由 QPainter 绘制,不是字符
    - hover 时由 _TitleBtn.enterEvent 自动切 icon color
      (因 QSS 切不了 setIcon 颜色)
    - minimize hover:浅白底 + 图标亮到纯白
    - close hover:Windows 经典红底 + 白图标
    - v4.4.2-r2: 不再带 tooltip（用户要求去掉悬停悬浮框）
    """
    btns = []
    if minimize_cb is not None:
        ic_n = icon_minimize(Glass.TEXT_MUTED)
        ic_h = icon_minimize(Glass.TEXT_PRIMARY)
        mb = _TitleBtn(ic_n, ic_h)
        mb.setFixedSize(30, 26)
        mb.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04);
                border: none; border-radius: {Glass.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background: rgba(255,255,255,0.12);
            }}
            QPushButton:pressed {{
                background: rgba(255,255,255,0.20);
            }}
        """)
        mb.clicked.connect(minimize_cb)
        btns.append(mb)
    if close_cb is not None:
        ic_n = icon_close(Glass.TEXT_MUTED)
        ic_h = icon_close('#ffffff')  # 红底上必须白图标,跟着 hover 切
        cb = _TitleBtn(ic_n, ic_h)
        cb.setFixedSize(30, 26)
        cb.setStyleSheet(f"""
            QPushButton {{
                background: rgba(255,255,255,0.04);
                border: none; border-radius: {Glass.RADIUS_SM}px;
            }}
            QPushButton:hover {{
                background: {Glass.DANGER};
            }}
            QPushButton:pressed {{
                background: #dc2626;
            }}
        """)
        cb.clicked.connect(close_cb)
        btns.append(cb)
    return btns




class AnimatedToggle(QPushButton):
    """带动画的开关按钮 - hover高亮 + pressed缩小反馈"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(42, 22)
        self.setCursor(Qt.PointingHandCursor)
        self._circle_x = 31.0
        self._thumb_scale = 1.0
        self._hovered = False
        self._pressed = False
        # 圆点滑动动画（OutBack 轻微回弹，更灵动）
        self._anim = QPropertyAnimation(self, b'circle_x')
        self._anim.setDuration(220)
        _bounce = QEasingCurve(QEasingCurve.OutBack)
        _bounce.setOvershoot(0.9)
        self._anim.setEasingCurve(_bounce)
        # 按压收缩动画
        self._press_anim = QPropertyAnimation(self, b'thumb_scale')
        self._press_anim.setDuration(130)
        self._press_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self.setChecked(True)
        self.clicked.connect(self._on_clicked)

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(e)

    def mousePressEvent(self, e):
        self._pressed = True
        self._scale_to(0.80)
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        self._pressed = False
        self._scale_to(1.0)
        super().mouseReleaseEvent(e)

    def _scale_to(self, target):
        self._press_anim.stop()
        self._press_anim.setStartValue(self._thumb_scale)
        self._press_anim.setEndValue(target)
        self._press_anim.start()

    @ui_slot
    def _on_clicked(self):
        self._animate_to(self.isChecked())

    def _animate_to(self, checked):
        target = 31.0 if checked else 11.0
        self._anim.stop()
        self._anim.setStartValue(self._circle_x)
        self._anim.setEndValue(target)
        self._anim.start()
        self.update()

    @pyqtProperty(float)
    def circle_x(self):
        return self._circle_x

    @circle_x.setter
    def circle_x(self, val):
        self._circle_x = val
        self.update()

    @pyqtProperty(float)
    def thumb_scale(self):
        return self._thumb_scale

    @thumb_scale.setter
    def thumb_scale(self, val):
        self._thumb_scale = max(0.6, min(1.2, val))
        self.update()

    def setChecked(self, v):
        super().setChecked(v)
        self._circle_x = 31.0 if v else 11.0
        self._thumb_scale = 1.0
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        r = 8  # 圆点基准半径

        # 轨道混色进度：跟随圆点滑动平滑切换 灰 <-> 靛蓝
        prog = max(0.0, min(1.0, (self._circle_x - 11.0) / 20.0))

        def _mix(c1, c2, t):
            return int(c1[0] + (c2[0] - c1[0]) * t), int(c1[1] + (c2[1] - c1[1]) * t), int(c1[2] + (c2[2] - c1[2]) * t)

        off_top, off_bot = (58, 58, 70), (40, 40, 52)
        on_top, on_bot = (129, 140, 248), (99, 102, 241)
        top_c = _mix(off_top, on_top, prog)
        bot_c = _mix(off_bot, on_bot, prog)
        a = 255 if self._hovered else 235

        # 轨道背景（渐变）
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor(top_c[0], top_c[1], top_c[2], a))
        bg.setColorAt(1, QColor(bot_c[0], bot_c[1], bot_c[2], a))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)

        # 轨道质感：顶部内嵌高光 + 微弱外圈
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), h / 2, h / 2)
        p.setClipPath(clip)
        sheen = QLinearGradient(0, 0, 0, h * 0.55)
        sheen.setColorAt(0, QColor(255, 255, 255, 26))
        sheen.setColorAt(1, QColor(255, 255, 255, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sheen))
        p.drawRect(0, 0, w, int(h * 0.55))
        p.setClipping(False)
        if prog >= 0.999:
            # 开启时外圈光晕
            glow_a = 70 if self._hovered else 34
            p.setPen(QColor(99, 102, 241, glow_a))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), h / 2, h / 2)
        else:
            p.setPen(QColor(255, 255, 255, 26))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), h / 2, h / 2)

        # 圆点（按压时收缩 + 高光渐变 + 阴影）
        cx = self._circle_x
        rr = max(5.5, r * self._thumb_scale)
        cy = h / 2
        # 阴影
        shadow = QRadialGradient(cx + 0.5, cy + 1, rr + 1)
        shadow.setColorAt(0, QColor(0, 0, 0, 70))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(shadow))
        p.drawEllipse(QRectF(cx - rr - 1.5, cy - rr - 0.5, rr * 2 + 3, rr * 2 + 1))
        # 本体
        grad = QRadialGradient(cx - 1, cy - 1, rr)
        grad.setColorAt(0, QColor(255, 255, 255, 252))
        grad.setColorAt(0.65, QColor(240, 240, 255, 225))
        grad.setColorAt(1, QColor(222, 222, 240, 185))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QRectF(cx - rr, cy - rr, rr * 2, rr * 2))
        p.end()
