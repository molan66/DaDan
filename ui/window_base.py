"""WindowBase - 无边框窗口的拖拽 / 玻璃效果 / 槽异常保护的共用实现 (v4.4.0 新增)

抽出这个模块是为了消掉三份被复制粘贴了三次、且各自都带同一个 bug 的逻辑:

1) **拖拽判定 bug（P2）**
   主窗(main_window.py)、小窗(overlay_window.py)、详情窗(recent_detail.py)
   都写 `if self._drag_pos:`。而 `_drag_pos` 是 `QPoint`,`QPoint(0, 0)` 的
   布尔值是 **False** —— 也就是说当用户按住标题栏**最左上的那个像素**开始拖,
   偏移量正好是 (0,0), 判定失败, 窗口纹丝不动。
   正确写法是显式的 `is not None`(并且 `__init__` 里必须先置 None,
   否则老代码用 `QPoint()` 默认值也同样是假)。三个窗口统一走本模块。

2) **玻璃效果重复应用（P1-21）**
   三个窗口的 showEvent 里都排了 2~3 个 `QTimer.singleShot` 去调
   `apply_liquid_glass`(main_window 还有一处 `:478` 的 200ms)。
   每次应用 = `SetWindowLongW` + `SetWindowPos(SWP_FRAMECHANGED)`
   + 4 次 `DwmSetWindowAttribute`, 而同一 geometry 下重复应用对观感
   毫无影响 —— 纯浪费, 且 SWP_FRAMECHANGED 会强制一次非客户区重算。
   这里用"已应用 + geometry 键"的脏标志把它压成**每次显示只做一次**,
   hide 之后 DWM 会重置 backdrop, 所以 hideEvent 里重新置脏, 语义不变。

3) **UI 槽异常 = 进程直接死（P1-1）**
   PyQt5 默认的 `sys.excepthook` 会把槽里漏出去的 ValueError 交给
   `qFatal`(abort) —— 连托盘都来不及收, 用户看到的是"程序莫名消失"。
   `ui_slot` 装饰器把槽包起来, 打印完整 traceback 后吞掉, 保证"出错的
   只是这一次刷新"。
   ⚠️ 两道防线的关系(别搞混, 实测结论):
     · **真正保命的是 main.py 的全局 `_install_excepthooks()`** —— 它在
       `QApplication` 之前装好自定义 `sys.excepthook`, 覆盖**全部**槽
       (含本模块没装饰到的), 并且**不重抛**。所以"漏包一个槽"不等于会崩。
     · `ui_slot` 是**局部加固 + 可读性**: 让"这个函数自己知道异常不该外泄"
       写在函数定义处, 而不是散在全局。它**不是**崩溃修复。
     · 因此装饰器内必须打印**完整 traceback**(而不是一行摘要): 否则异常被
       就地吞掉, 永远到不了全局 hook, uncaught.log 反而少一条完整现场 ——
       那是诊断能力的净损失。
   ⚠️ 全局 `sys.excepthook` 不在这里装 —— 按约定那属于 main.py 的职责。
   ⚠️ **包装器必须按原函数的参数个数裁剪实参(v4.4.0 第三轮事故)**:
   `def wrapper(*args, **kwargs)` 的签名是 `*args`, 而 PyQt5 是**按可调用对象的
   签名**决定把信号的几个实参传出去 —— 于是 `clicked` 那个 `checked` 布尔值
   会被原样塞进只接受 `self` 的槽, 变成
   `TypeError: MainWindow._on_start() takes 1 positional argument but 2 were given`,
   启动按钮整段函数体一行都没执行(实测回归里连报 11 次)。
   对照探针(A=裸 0 参方法 → PyQt5 自行裁剪, 正常; B=`@ui_slot` 包 0 参方法 →
   全量透传, 抛错; C=裸 `*args` 方法 → 正常): **判据是"包装器带不带 `*args`",
   与 `functools.wraps` 无关**。所以这里在调用原函数前先裁到它的位置参数上限,
   精确等价于"不加装饰器时 PyQt5 会做的事"; 原函数本身是 `*args` 时不裁。
"""
import functools
import inspect
import sys
import traceback

from PyQt5.QtCore import Qt, QTimer

# PyQt5 的默认 hook 收到槽异常会走 qFatal(abort) —— 绝不能转交给它。
# 只认"被替换过的" hook(main.py 会在 QApplication 之前装自定义的)。
_DEFAULT_EXCEPTHOOK = sys.excepthook


def _positional_arity(fn):
    """fn 能接受的最大位置参数个数; 含 `*args` 时返回 None(表示"别裁")。

    `@ui_slot` 拿到的是**类定义期的裸函数**(带 self), 所以 `_on_start(self)`
    得到 1、`_on_setting(self, key, value)` 得到 3 —— 与 PyQt5 看裸函数时
    的裁剪口径一致。
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):      # 内建/装饰过头的可调用对象, 不裁更安全
        return None
    n = 0
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_POSITIONAL:
            return None
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY,
                      inspect.Parameter.POSITIONAL_OR_KEYWORD):
            n += 1
    return n


# ============================================================
# 槽异常保护 (P1-1)
# ============================================================
def _report_slot_exception(fn, exc):
    """槽内异常的统一上报: 先交给全局 hook(完整 traceback 落 uncaught.log), 再补一行槽名摘要。

    为什么不能"就地吞掉就完事": 装饰器自己 try 住之后, 异常**永远到不了**
    `sys.excepthook`, main.py 那份完整 traceback / uncaught.log 记录就一条都没有 ——
    这是诊断能力的净损失(实测: 裸槽 uncaught.log 177 字符完整栈; 就地吞掉 0 字符)。
    所以这里主动把异常转交给全局 hook, 二者不冲突、信息只增不减。
    若全局 hook 仍是 Python/PyQt5 默认的那一个(没被 main.py 替换, 例如单独跑
    某个 ui 模块), 就**不要**转交 —— 默认 hook 对槽异常会 qFatal 直接 abort,
    那反倒是把"已经被拦下的异常"重新变成崩溃。此时只打印 traceback。
    """
    hook = sys.excepthook
    if hook is not _DEFAULT_EXCEPTHOOK:
        try:
            hook(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
    else:
        try:
            traceback.print_exception(type(exc), exc, exc.__traceback__)
        except Exception:
            pass
    try:
        print(f'[ui_slot] {fn.__qualname__} 异常已被拦下: '
              f'{type(exc).__name__}: {exc}')
    except Exception:
        pass


def ui_slot(fn):
    """把 UI 槽包成"出错不杀进程"的版本。

    只上报并吞异常, 不做重试/回滚 —— 目标是让界面在数据脏掉时继续可用,
    而不是掩盖问题: 既有全局 hook 的完整 traceback(落 uncaught.log),
    也有这里打出的槽名 + 异常类型 + 原始消息。

    ⚠️ 调用原函数前会把多余的位置实参裁掉(见 `_positional_arity` 与模块
    docstring 第三节): 包装器签名是 `*args`, 不裁就会让 PyQt5 把
    `clicked` 的 `checked` 塞给只接受 `self` 的槽, 槽体一行都不执行。
    """
    arity = _positional_arity(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            if arity is not None and len(args) > arity:
                args = args[:arity]
            return fn(*args, **kwargs)
        except Exception as e:
            _report_slot_exception(fn, e)
            return None
    # 留给自检脚本核对"每处装饰器的裁剪上限是否与原函数一致"
    wrapper.__ui_slot_arity__ = arity
    return wrapper


# ============================================================
# 拖拽混入
# ============================================================
class DragMixin:
    """无边框窗口拖拽。

    用法:
        class Foo(DragMixin, QWidget):
            def __init__(...):
                self._init_drag()                 # 必须: 把 _drag_pos 置 None
                self._install_drag(self._title_bar)      # 标题栏整块可拖
                # 或者自己接管按下/移动/释放(见 recent_detail 的顶部 46px 判定)
    """

    # v4.4.0: 主窗原实现不带按键掩码(移动事件仅在按住时到达, 行为等价),
    # 为"行为不变"保守起见保留该差异; 另两窗沿用原来的掩码判定。
    _DRAG_REQUIRE_BUTTON = True

    def _init_drag(self):
        # ⚠️ 必须是 None 而不是 QPoint(): QPoint(0,0) 为假, 见模块 docstring
        self._drag_pos = None

    def _install_drag(self, widget):
        """把整块 widget 变成本窗口的拖拽把手。"""
        widget.mousePressEvent = self._drag_mouse_press
        widget.mouseMoveEvent = self._drag_mouse_move
        widget.mouseReleaseEvent = self._drag_mouse_release
        return widget

    def _drag_mouse_press(self, event):
        try:
            if event.button() == Qt.LeftButton:
                self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        except Exception as e:
            print(f'[Drag] 按下处理异常: {type(e).__name__}: {e}')
            self._drag_pos = None

    def _drag_mouse_move(self, event):
        # v4.4.0 修复: `is not None`(原 `if self._drag_pos:` 在 (0,0) 处为假)
        if self._drag_pos is None:
            return
        if self._DRAG_REQUIRE_BUTTON and not (event.buttons() & Qt.LeftButton):
            return
        try:
            self.move(event.globalPos() - self._drag_pos)
        except Exception as e:
            print(f'[Drag] 移动处理异常: {type(e).__name__}: {e}')

    def _drag_mouse_release(self, event):
        self._drag_pos = None


# ============================================================
# 玻璃效果去重 (P1-21)
# ============================================================
class GlassEffectMixin:
    """把"每次显示 3 次 apply"压成"每次显示 1 次"。"""

    _GLASS_FIRST_DELAY = 120     # 首次延后: 等 DWM 把窗口真正映射出来
    _GLASS_RETRY_DELAY = 500     # 兜底重试: 首次因为还没可见而跳过时补打

    def _init_glass(self):
        self._glass_applied = False
        self._glass_last_key = None

    def _mark_glass_dirty(self):
        """置脏: 下次调度必须真正应用一次。

        showEvent 与 resizeEvent 调 —— 前者因为 hide 时 DWM 会清掉 backdrop,
        后者因为窗口尺寸变了, 原生圆角/backdrop 需要按新 geometry 重算。
        """
        self._glass_applied = False

    def _glass_geometry_key(self):
        try:
            g = self.geometry()
            return (g.width(), g.height())
        except Exception:
            return None

    def _glass_apply_core(self):
        """子类覆写: 真正调用 apply_liquid_glass 的地方。"""
        raise NotImplementedError

    def _apply_glass_effect(self):
        """调度入口。重复调用(同一 geometry 且已成功应用)直接返回。

        v4.4.0: 原实现每次 show 触发 3 次
        `SetWindowLongW + SetWindowPos(SWP_FRAMECHANGED) + 4×DwmSetWindowAttribute`;
        这里第 2/3 次命中缓存键后即刻返回, 观感完全一致。
        """
        try:
            if not self.isVisible():
                # 保持原子类行为: 不可见时不应用(否则 hide 状态下
                # SetWindowPos 会把窗口重新弹出来)
                return
            key = self._glass_geometry_key()
            if self._glass_applied and key is not None and key == self._glass_last_key:
                return
            self._glass_apply_core()
            self._glass_applied = True
            self._glass_last_key = key
        except Exception as e:
            print(f'[GlassEffect] {type(self).__name__} 失败: {e}')

    def _schedule_glass(self):
        """showEvent 里调用: 排首次 + 兜底两次调度(命中脏标志则只做一次)。"""
        QTimer.singleShot(self._GLASS_FIRST_DELAY, self._apply_glass_effect)
        QTimer.singleShot(self._GLASS_RETRY_DELAY, self._apply_glass_effect)
