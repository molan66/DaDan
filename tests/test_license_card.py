# -*- coding: utf-8 -*-
"""授权入口回归测试 —— 守住「设置页能看到机器码」这条线。

跑法：<python> -m unittest tests.test_license_card -v

## 为什么非要有这个文件

v4.7.0 首次实测时，用户第一句话是：

    "没看到7天试用和获取机器码按钮啊"

根因：试用状态与机器码**只存在于"被拦下时弹的激活窗"里**，而试用期内不弹窗
⇒ 用户既不知道还剩几天，也**拿不到机器码**（而机器码是要发给卖家换授权码的）
⇒ **购买流程根本走不下去**。

这是"功能都实现了，但用户在需要它的那一刻看不到它"的典型缺陷 ——
所有 Python 单测全绿（licensing 判定正确、门禁拦截正确），
但**没有人测过"界面上有没有这个入口"**。

## 入口位置的演变（两次都是用户实测提出的）

· v4.7.1：做成首页大卡片 → **把首页右下角的功能按钮挤走了、显示不全**；
  且用户指出"激活码这东西哪有放首页的"。
· v4.7.2：移到侧边栏 → 用户要求放设置。
· v4.7.3：**落在「设置」页**（与清理缓存同区），首页/侧边栏都不再占用。
本文件按 v4.7.3 的形态断言，并**显式禁止**再出现在首页，防止改回去。
"""
import datetime
import os
import re
import sys
import unittest
import unittest.mock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

_MID = 'Z5NFYEYZJ7AC5HS4PP7N5VY63SFNMDRSTA5M4HJQ625QJJCZPRVA'
_SRC = os.path.join(_ROOT, 'ui', 'main_window.py')
_MAIN = os.path.join(_ROOT, 'main.py')


def _read(p):
    import io
    return io.open(p, encoding='utf-8').read()


def _code_only(src):
    """删掉注释与 docstring，只留真正会被执行的代码（其余字符原样保留）。

    ## 为什么需要它（踩过的坑）

    v4.8.0 的守卫测试一开始直接对源码原文做 `assertNotIn('lcu.recorder', src)`，
    结果**自己写的说明注释把测试判红了** ——

        # v4.8.0: lcu.recorder(全局鼠标钩子录制器)已随录制回放功能整体删除。

    这句是解释"为什么删掉"，却被当成"仍在引用"。要么改注释（丢掉解释价值），
    要么让断言只看代码。选后者：注释是给人看的，**不该被当成行为证据**；
    而真正的风险是代码里又出现了引用，那才是要拦的。

    ## 实现要点（第一版写错过，记录一下）

    第一版把 token 字符串**直接拼起来** —— 但 tokenize 不产出空白 token，
    于是 `def _launch_lol(self):` 变成 `def_launch_lol(self):`，正则全匹配不上。
    现在改成**按位置把注释/文档字符串替换成等长空白**，其余字符一律原样，
    这样行号、缩进、标识符间距都保持不变。
    """
    import ast
    import io
    import tokenize

    lines = src.splitlines(keepends=True)
    chars = [list(ln) for ln in lines]

    def blank(row, col_start, col_end):
        """把第 row 行(0-based)从 col_start 到 col_end 的字符换成空格。"""
        if 0 <= row < len(chars):
            ln = chars[row]
            for c in range(max(0, col_start), min(len(ln), col_end)):
                if ln[c] not in '\r\n':
                    ln[c] = ' '

    # ① 注释：tokenize 直接给出精确位置
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                blank(tok.start[0] - 1, tok.start[1], tok.end[1])
    except Exception:
        pass

    # ② docstring：用 AST 拿到每个 docstring 常量的起止行列
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ''.join(''.join(l) for l in chars)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, 'body', None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                v = body[0].value
                r1, c1 = v.lineno - 1, v.col_offset
                r2, c2 = v.end_lineno - 1, v.end_col_offset
                if r1 == r2:
                    blank(r1, c1, c2)
                else:
                    blank(r1, c1, len(chars[r1]))
                    for r in range(r1 + 1, r2):
                        blank(r, 0, len(chars[r]))
                    blank(r2, 0, c2)
    return ''.join(''.join(l) for l in chars)


def _make_status(activated=False, trial=False, trial_left=None, days_left=None,
                 machine_id=_MID):
    from core import licensing as L
    info = {}
    if days_left is not None:
        info['expires'] = datetime.date.today() + datetime.timedelta(days=days_left)
    return L.LicenseStatus(activated, '测试', info=info, machine_id=machine_id,
                           trial=trial, trial_days_left=trial_left)


class TestLicenseEntryPlacement(unittest.TestCase):
    """入口位置：**必须在设置页**，且不得回到首页/侧边栏。"""

    def setUp(self):
        self.src = _read(_SRC)

    def test_card_built_in_settings_page(self):
        """设置页构建流程里必须调用 _add_license_card。"""
        m = re.search(r'\n    def _make_settings_page\(self\):\n(.*?)\n    def ',
                      self.src, re.S)
        self.assertIsNotNone(m, '找不到 _make_settings_page')
        self.assertIn('_add_license_card', m.group(1),
                      '设置页没有插入授权卡 —— 用户在设置里找不到激活入口')

    def test_not_on_home_page(self):
        """首页**不得**再出现授权元素。

        用户明确说过两次：首页放大卡会挤走右下角功能按钮、且位置不合常识。
        这条用源码级断言把结论钉死，防止以后又"顺手"加回首页。
        """
        m = re.search(r'\n    def _make_home_page\(self\):\n(.*?)\n    def ',
                      self.src, re.S)
        self.assertIsNotNone(m, '找不到 _make_home_page')
        body = m.group(1)
        for forbidden in ('_add_license_card', '_build_license_card',
                          'LicenseSidebarButton', 'update_license_card'):
            self.assertNotIn(forbidden, body,
                             f'首页里又出现授权元素 {forbidden} —— 它应该在设置页')

    def test_not_in_sidebar(self):
        """侧边栏**不得**再出现授权入口（用户要求放设置页）。"""
        m = re.search(r'\n    def _make_sidebar\(self\):\n(.*?)\n    @ui_slot',
                      self.src, re.S)
        self.assertIsNotNone(m, '找不到 _make_sidebar')
        body = m.group(1)
        for forbidden in ('LicenseSidebarButton', '_lic_btn', '_add_license_card'):
            self.assertNotIn(forbidden, body,
                             f'侧边栏仍有授权元素 {forbidden}')

    def test_obsolescent_class_removed(self):
        """废弃的侧边栏按钮类应已删除（避免留双份实现各自漂移）。"""
        self.assertNotIn('class LicenseSidebarButton', self.src,
                         'LicenseSidebarButton 类还在 —— 已迁到设置页，应删除')

    def test_home_page_still_has_action_buttons(self):
        """首页右下角三个功能按钮必须都还在（大卡曾把它们挤出可视区）。"""
        m = re.search(r'\n    def _make_home_page\(self\):\n(.*?)\n    def ',
                      self.src, re.S)
        body = m.group(1)
        for name in ('_launch_lol_btn', '_fab', '_stop_all_btn'):
            self.assertIn(name, body, f'首页缺少按钮 {name}')


class TestSettingsLicenseCard(unittest.TestCase):
    """设置页授权卡的存在性、渲染与交互。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            from PyQt5.QtWidgets import QApplication
        except Exception as e:                       # pragma: no cover
            raise unittest.SkipTest(f'未安装 PyQt5，跳过 UI 测试：{e}')
        cls.app = QApplication.instance() or QApplication([])
        from ui.main_window import MainWindow
        cls.MainWindow = MainWindow

    def setUp(self):
        self.w = self.MainWindow()

    # ---------------------------------------------------------- 存在性

    def test_widgets_exist(self):
        for attr in ('_lic_state_lbl', '_lic_mid_lbl', '_lic_copy_btn',
                     '_lic_act_btn', '_lic_hint_lbl'):
            self.assertTrue(hasattr(self.w, attr),
                            f'授权卡缺少控件 {attr} —— 用户看不到授权入口')

    def test_card_is_in_a_layout(self):
        """控件必须真的挂进布局，不能只是建了个对象。

        最容易犯的错：对象建好、属性可访问，但忘了 addWidget →
        界面上什么都不显示，而"属性存在"的检查全部通过。
        """
        p = self.w._lic_state_lbl.parent()
        self.assertIsNotNone(p, '授权卡控件没有父级 —— 没被加进任何布局')
        chain = []
        node = p
        for _ in range(12):
            if node is None:
                break
            chain.append(type(node).__name__)
            node = node.parent()
        self.assertIn('QScrollArea', chain,
                      f'授权卡不在某个滚动页内，父链={chain}')

    def test_copy_button_wired(self):
        self.assertTrue(self.w._lic_copy_btn.receivers(
            self.w._lic_copy_btn.clicked) > 0, '复制按钮没有连接槽函数')

    def test_activation_button_emits_signal(self):
        got = []
        self.w.open_activation_signal.connect(lambda: got.append(1))
        self.w._lic_act_btn.click()
        self.assertEqual(got, [1], '点「输入授权码」没有发出信号 —— 用户无法激活')

    def test_main_connects_signal(self):
        self.assertIn('open_activation_signal.connect', _read(_MAIN),
                      'main.py 没有连接 open_activation_signal')

    # ---------------------------------------------------------- 试用期可见性（核心）

    def test_trial_shows_machine_id(self):
        """**核心断言**：试用期内必须能看到机器码。

        这正是 v4.7.0 的真实缺陷 —— 试用期内看不到机器码，
        用户没法发给卖家，购买流程直接断掉。
        """
        self.w.update_license_card(_make_status(trial=True, trial_left=6.5))
        txt = self.w._lic_mid_lbl.text()
        self.assertIn('Z5NF', txt, f'试用期内看不到机器码！实得：{txt!r}')
        self.assertNotIn('读取中', txt)

    def test_trial_shows_remaining_days(self):
        self.w.update_license_card(_make_status(trial=True, trial_left=6.5))
        self.assertIn('试用', self.w._lic_state_lbl.text())
        self.assertIn('6.5', self.w._lic_hint_lbl.text(),
                      f'没显示剩余天数：{self.w._lic_hint_lbl.text()!r}')

    def test_trial_guides_to_seller(self):
        """试用期文案必须告诉用户下一步做什么（发给卖家）。"""
        self.w.update_license_card(_make_status(trial=True, trial_left=3))
        blob = self.w._lic_hint_lbl.text() + self.w._lic_hint2_lbl.text()
        self.assertIn('卖家', blob, '试用期没告诉用户下一步该做什么')

    def test_copy_puts_bare_machine_id_on_clipboard(self):
        """复制到剪贴板的必须是**纯码**（无连字符）。

        微信里连字符会引入换行断点、容易被截断；解析侧虽两者都接受，
        但给纯码转发更稳。
        """
        from PyQt5.QtWidgets import QApplication
        st = _make_status(trial=True, trial_left=5)
        self.w.update_license_card(st)
        self.w._on_copy_machine_id()
        clip = QApplication.clipboard().text()
        self.assertEqual(clip, st.machine_id, f'剪贴板内容不对：{clip!r}')
        self.assertNotIn('-', clip, '复制了带连字符的版本（应给纯码）')

    def test_copy_gives_feedback(self):
        self.w.update_license_card(_make_status(trial=True, trial_left=5))
        self.w._on_copy_machine_id()
        self.assertIn('已复制', self.w._lic_hint2_lbl.text())

    # ---------------------------------------------------------- 其它状态

    def test_activated_shows_expiry(self):
        self.w.update_license_card(_make_status(activated=True, days_left=365))
        self.assertEqual(self.w._lic_state_lbl.text(), '已激活')
        self.assertIn('有效期至', self.w._lic_hint_lbl.text())

    def test_permanent_shows_permanent(self):
        """永久授权（无到期日）不能崩，也不能显示成"已过期"。"""
        self.w.update_license_card(_make_status(activated=True, days_left=None))
        self.assertEqual(self.w._lic_state_lbl.text(), '已激活')
        self.assertIn('永久', self.w._lic_hint_lbl.text())

    def test_expired_shows_expired(self):
        self.w.update_license_card(_make_status(activated=True, days_left=-3))
        self.assertEqual(self.w._lic_state_lbl.text(), '已过期')
        self.assertIn('到期', self.w._lic_hint_lbl.text())

    def test_locked_shows_not_activated(self):
        self.w.update_license_card(_make_status(activated=False, trial=False))
        self.assertEqual(self.w._lic_state_lbl.text(), '未激活')

    def test_none_status_is_safe(self):
        """状态未就绪（None）不能崩 —— 启动瞬间就是这个状态。"""
        self.w.update_license_card(None)
        self.assertIn('读取中', self.w._lic_state_lbl.text())

    def test_garbage_status_does_not_crash(self):
        class Junk:
            pass
        try:
            self.w.update_license_card(Junk())
        except Exception as e:
            self.fail(f'脏状态导致崩溃：{type(e).__name__}: {e}')

    def test_every_state_shows_machine_id(self):
        """**矩阵断言**：只要 status 带机器码，任何状态都要显示出来。

        这条防"只给某些分支加了显示、别的分支漏了" —— 单测某个分支会漏，
        矩阵跑一遍才看得出来。
        """
        cases = [
            ('试用中', _make_status(trial=True, trial_left=5)),
            ('已激活', _make_status(activated=True, days_left=100)),
            ('已过期', _make_status(activated=True, days_left=-1)),
            ('未激活', _make_status(activated=False, trial=False)),
        ]
        for label, st in cases:
            with self.subTest(state=label):
                self.w.update_license_card(st)
                self.assertIn('Z5NF', self.w._lic_mid_lbl.text(),
                              f'{label} 状态下看不到机器码')


class TestElevationTextIsSpecific(unittest.TestCase):
    """提权弹窗文案必须说出**本次真正的原因**。

    实测背景（用户提出）：旧文案在多种提权场景下弹同一句
    "「自动选英雄」需要以管理员身份运行"，用户看了不知道为什么要权限。

    这条测试断言：
      · 存在按原因区分的文案表；
      · 每个原因的文字里都写明了具体原因，不是含糊的通用句。

    v4.8.0: 录制回放功能整体删除后，"启动 LOL"不再需要管理员权限，
    `lol_steps` 这条原因也随之删除（原本这三条测试断言的是"录制场景要提到录制"）。
    现在改为**反向守卫**：断言 `lol_steps` 原因与录制相关入口都已不存在，
    防止有人把自动点击那一套又加回来（见 TestLaunchDoesNotClick）。
    """

    def setUp(self):
        self.src = _read(_MAIN)

    def test_reason_table_exists(self):
        self.assertIn('_ELEVATE_REASON', self.src, '缺少提权原因文案表')

    def test_lol_steps_reason_is_gone(self):
        """v4.8.0: 录制回放删除后，不该再存在 lol_steps 提权原因。

        为什么值得守：只要这条文案还在，就说明"启动后自动点击"的逻辑链路
        可能又被接回来了 —— 而它正是用户明确要求去掉的东西。
        """
        self.assertNotIn("'lol_steps'", _code_only(self.src),
                         '仍存在 lol_steps 提权原因 —— 录制/自动点击功能应已整体删除')

    def test_stop_all_reason_still_exists(self):
        """「全部停止」仍需提权（LOL 以管理员权限运行，普通权限杀不掉）。"""
        m = re.search(r"'stop_all':\s*\((.*?)\n    \)", self.src, re.S)
        self.assertIsNotNone(m, '找不到 stop_all 文案')
        blob = m.group(1)
        self.assertIn('管理员', blob, f'stop_all 文案没说明为什么需要管理员权限：{blob!r}')

    def test_cancel_does_not_flip_unrelated_toggle(self):
        """取消 UAC 时**不能**把"自动选英雄"开关关掉（除非本次就是它）。

        这是一个真实缺陷：任何需要提权的场景下若无条件调
        _revert_auto_select_champion，会把用户没碰过的设置悄悄改掉。
        """
        m = re.search(r"if reason == 'auto_select':\s*\n\s*self\._revert_auto_select_champion\(\)",
                      self.src)
        self.assertIsNotNone(
            m, '取消提权时没有按原因区分 —— 可能误关「自动选英雄」')

    def test_reason_param_has_default(self):
        """reason 必须有默认值：信号连接是 0 参的，缺省会 TypeError。"""
        self.assertIn("def _on_auto_select_champion_request(self, reason: str = 'auto_select')",
                      self.src,
                      'reason 没有默认值 —— 设置页开关信号(0参)调用会报错')


class TestNoAutoPopup(unittest.TestCase):
    """**不得自动弹激活窗** —— 只在用户主动点、或被门禁拦下时才弹。

    实测背景：看到截图里的激活窗，一度怀疑是自动弹的；插桩记录
    ActivationDialog 的创建次数后确认：启动预热 + 点启动 + 自动启动
    三条路径 **创建 0 次**，用户也确认是他手动点的。
    这条把这个结论固化，防止以后引入"启动就弹窗"。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            from PyQt5.QtWidgets import QApplication
        except Exception as e:                       # pragma: no cover
            raise unittest.SkipTest(f'未安装 PyQt5：{e}')
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import ui.activation_dialog as AD
        self.AD = AD
        self.created = []
        self._orig = AD.ActivationDialog.__init__
        created = self.created

        def spy(inst, *a, **kw):
            created.append(kw.get('reason'))
            return self._orig(inst, *a, **kw)

        AD.ActivationDialog.__init__ = spy

    def tearDown(self):
        self.AD.ActivationDialog.__init__ = self._orig

    def _ctrl(self, status):
        import main as M
        ctrl = M.AppController()
        ctrl._lic_status = status
        ctrl._prewarm_license = lambda: None      # 不跑后台线程
        return ctrl

    def test_start_while_trial_does_not_popup(self):
        """试用期内点「启动脚本」→ 放行 + **不弹窗**。"""
        ctrl = self._ctrl(_make_status(trial=True, trial_left=5))
        ctrl._start_worker = lambda: None
        ctrl._start()
        self.assertEqual(self.created, [],
                         f'试用期内点启动竟然弹了激活窗：{self.created}')
        self.assertTrue(ctrl._starting, '试用期内应放行（派发启动）')

    def test_start_while_activated_does_not_popup(self):
        ctrl = self._ctrl(_make_status(activated=True, days_left=100))
        ctrl._start_worker = lambda: None
        ctrl._start()
        self.assertEqual(self.created, [], '已激活用户被弹了激活窗')
        self.assertTrue(ctrl._starting)

    def test_prompt_creates_exactly_one_dialog(self):
        ctrl = self._ctrl(_make_status(trial=True, trial_left=5))
        ctrl._prompt_activation()
        self.assertEqual(len(self.created), 1, '应恰好创建一个激活窗')
        ctrl._prompt_activation()
        self.assertEqual(len(self.created), 1, '重复请求不该叠出第二个窗')


class TestLaunchDoesNotClick(unittest.TestCase):
    """v4.8.0 守卫：点「启动 LOL」**只打开登录器，绝不模拟点击、绝不要管理员权限**。

    ## 为什么必须有这个文件级的守卫

    用户要求（2026-10-21）："把那个一键启动LOL的功能改一下，只打开LOL客户端，
    不要自动点击的功能了，相关的文本说明也改掉"。

    这条需求有个危险的地方：**自动点击是"多出来"的行为，删掉它之后代码照样能跑、
    测试照样全绿** —— 少点几下没人会发现。反过来，只要有人（包括未来的我）把
    `steps=` 参数、`_elevate_for_lol(action='launch')` 或录制入口加回去，
    用户就会重新遇到"程序自己乱点登录器"的老问题，而那正是他这次要消掉的东西。

    所以这里从三个层次钉死：
      ① 源码级：run_flow 不接受 steps 参数；启动路径不调提权；不引用录制器；
      ② 文件级：lcu/recorder.py 不存在（否则说明回放链路可能被接回来）；
      ③ 行为级：真跑 run_flow，断言点击函数一次都没被调用。
    """

    @classmethod
    def setUpClass(cls):
        cls.main_src = _read(_MAIN)
        cls.launcher_src = _read(os.path.join(_ROOT, 'lcu', 'launcher.py'))
        cls.window_src = _read(_SRC)
        # 断言一律对"去掉注释与 docstring 的代码"做 —— 说明性文字不该被判成引用
        # （见 _code_only 的说明：v4.8.0 就被自己的注释误伤过）。
        cls.main_code = _code_only(cls.main_src)
        cls.launcher_code = _code_only(cls.launcher_src)
        cls.window_code = _code_only(cls.window_src)

    # ------------------------------------------------ ① 源码级

    def test_run_flow_takes_no_steps(self):
        """run_flow 的签名里不能再有 steps —— 有它就意味着还有回放分支。"""
        m = re.search(r'def run_flow\((.*?)\)\s*->', self.launcher_code, re.S)
        self.assertIsNotNone(m, '找不到 run_flow 定义')
        sig = m.group(1)
        self.assertNotIn('steps', sig,
                         f'run_flow 仍接受 steps 参数（自动点击又回来了）：{sig!r}')

    def test_launch_does_not_elevate(self):
        """_launch_lol 绝不能调 _elevate_for_lol —— 只启动登录器不需要提权。

        为什么值得单独守：老实现在"有录制步骤"时会先提权再启动，用户会看到
        一个莫名其妙的 UAC 弹窗。现在没有点击了，任何提权都是打扰。
        """
        m = re.search(r'def _launch_lol\(self\):(.*?)\n    def ', self.main_code, re.S)
        self.assertIsNotNone(m, '找不到 _launch_lol')
        body = m.group(1)
        self.assertNotIn('_elevate_for_lol', body,
                         '_launch_lol 仍在请求管理员权限（只启动登录器不需要）')

    def test_launch_worker_does_not_pass_steps(self):
        """_lol_launch_worker 不能给 run_flow 传 steps。"""
        m = re.search(r'def _lol_launch_worker\(self\):(.*?)\n    @ui_slot', self.main_code, re.S)
        self.assertIsNotNone(m, '找不到 _lol_launch_worker')
        body = m.group(1)
        self.assertNotIn('steps=', body,
                         '_lol_launch_worker 仍在传 steps（自动点击又回来了）')

    def test_no_recorder_references(self):
        """三个源文件的**代码**里都不能再引用录制器。"""
        for name, code in (('main.py', self.main_code),
                           ('launcher.py', self.launcher_code),
                           ('main_window.py', self.window_code)):
            with self.subTest(file=name):
                self.assertNotIn('ClickRecorder', code, f'{name} 仍引用 ClickRecorder')
                self.assertNotIn('lcu.recorder', code, f'{name} 仍 import lcu.recorder')

    def test_no_record_entry_in_ui(self):
        """设置页不该再有「开始录制 / 清除录制」入口与其信号。"""
        self.assertNotIn('record_lol_signal', self.window_code,
                         '仍存在 record_lol_signal（录制入口没删干净）')
        self.assertNotIn('开始录制', self.window_code,
                         '界面仍在提示"开始录制"')

    def test_no_lol_steps_key(self):
        """settings 里不该再有 lol_steps 默认项。"""
        self.assertNotIn("'lol_steps'", self.window_code,
                         'settings 仍声明 lol_steps')

    # ------------------------------------------------ ② 文件级

    def test_recorder_module_deleted(self):
        """lcu/recorder.py 必须不存在 —— 它是回放的输入源。"""
        p = os.path.join(_ROOT, 'lcu', 'recorder.py')
        self.assertFalse(os.path.exists(p),
                         'lcu/recorder.py 又出现了（录制回放被接回来了？）')

    # ------------------------------------------------ ③ 行为级

    def test_run_flow_never_clicks(self):
        """真跑 run_flow：点击函数必须一次都不被调用。

        这是最直接的行为证据 —— 前面几条都是读源码，这条真的执行。
        用桩替换掉 launch/find_window 等系统交互，只盯"有没有点"。

        ## 为什么 find_window 要返回**一个真窗口**（第一版把它写成 None，是个坑）

        第一版让 find_window 返回 None，结果做"故意破坏"验证时发现：
        往 run_flow 里注入一段 `if _w: click_frac(...)`，测试**仍然是绿的** ——
        因为 `_w` 是 None，那个分支根本进不去。也就是说这条测试当时只证明了
        "窗口不存在时不会点"，而真正危险的情形（窗口在、代码点了）它压根没覆盖。

        现在让 find_window 返回真窗口、并让窗口"一直存在"，这样任何藏在
        `if find_window():` 里的点击都会被执行到，也就一定会被抓到。
        """
        import lcu.launcher as L

        calls = {'click': 0}

        def fake_click(*a, **kw):
            calls['click'] += 1
            return (True, 'ok')

        win = {'hwnd': 1, 'w': 800, 'h': 600,
               'left': 0, 'top': 0, 'right': 800, 'bottom': 600}

        # lcu_ready 前两次返回 False（第 0 步"已在大厅?"与循环第 1 拍），之后 True。
        # 目的：既让流程真正走进"等客户端"那段（那里才有被注入点击的风险），
        # 又不必空转到 20s 超时 —— 否则单条测试要跑 20 秒。
        state = {'n': 0}

        def fake_lcu_ready(*a, **kw):
            state['n'] += 1
            return state['n'] > 2

        saved = {}
        patches = {
            'launch': lambda: (True, '已启动原生登录器(桩)'),
            'wait_window': lambda *a, **kw: dict(win),
            # 关键：窗口"一直存在" —— 别让点击分支因为窗口消失而短路
            'find_window': lambda *a, **kw: dict(win),
            'bring_to_front': lambda *a, **kw: True,
            '_wait_settle': lambda *a, **kw: None,
            'lcu_ready': fake_lcu_ready,
            'click_frac': fake_click,
        }
        # 兜底：凡是名字里带 click 的可调用属性，一律换成计数桩 ——
        # 防止将来有人改用一个新函数名去点，绕过上面的定点替换。
        for name in dir(L):
            if 'click' in name.lower() and callable(getattr(L, name, None)):
                saved.setdefault(name, getattr(L, name))
                setattr(L, name, fake_click)
        for k, v in patches.items():
            if hasattr(L, k):
                saved[k] = getattr(L, k)
                setattr(L, k, v)

        # 循环里的 time.sleep(1.0) 也换掉，免得为了走完一拍白等 1 秒
        real_sleep = L.time.sleep
        L.time.sleep = lambda *a, **kw: None
        try:
            ok, msg = L.run_flow(connector=None, on_status=lambda m: None)
        finally:
            L.time.sleep = real_sleep
            for k, v in saved.items():
                setattr(L, k, v)

        self.assertTrue(ok, f'只启动登录器应返回成功：{msg!r}')
        self.assertEqual(calls['click'], 0,
                         f'run_flow 竟然点击了 {calls["click"]} 次')


class TestLaunchCollapseToOverlay(unittest.TestCase):
    """v4.8.1 守卫：设置开启时，启动 LOL 成功后收起主界面并打开小窗。

    用户要求（2026-10-21）："再添加一个点击启动LOL之后就隐藏主界面并打开小窗的
    功能，添加到设置里"。

    ## 这里要钉住的四件事

      ① **默认关** —— 不开就不该有任何界面变化（v4.8.0 行为保持不变）；
      ② **开则生效** —— 主界面被隐藏 **且** 小窗被显示。只做一半是最糟的结果：
         主窗没了小窗也没出来，用户会以为软件崩了；
      ③ **失败不收起** —— 启动失败时主界面必须留着，否则用户看不到错误弹窗；
      ④ **有回来的路** —— 小窗上那个「返回主界面」按钮的接线必须还在，
         否则收了主窗就再也回不来。
    """

    @classmethod
    def setUpClass(cls):
        cls.main_src = _read(_MAIN)
        cls.main_code = _code_only(cls.main_src)

    def test_setting_defaults_off(self):
        """设置项必须默认 False —— 不开就完全不该有行为变化。"""
        m = re.search(r"'lol_launch_to_overlay':\s*(True|False)", self.main_code)
        if m is None:
            # 默认值也可能写在 main_window.py 的 defaults 里
            m2 = re.search(r"'lol_launch_to_overlay':\s*(True|False)",
                           _code_only(_read(_SRC)))
            self.assertIsNotNone(m2, '找不到 lol_launch_to_overlay 默认值')
            self.assertEqual(m2.group(1), 'False',
                             'lol_launch_to_overlay 默认应为 False')
            return
        self.assertEqual(m.group(1), 'False',
                         'lol_launch_to_overlay 默认应为 False')

    def test_success_path_collapses(self):
        """启动成功后必须调用收起逻辑。"""
        m = re.search(r'def _on_lol_launch_done\(self, ok, msg\):(.*?)\n    def ',
                      self.main_code, re.S)
        self.assertIsNotNone(m, '找不到 _on_lol_launch_done')
        self.assertIn('_maybe_collapse_to_overlay', m.group(1),
                      '启动成功后没有收起主界面（新功能没接线）')

    def test_failure_path_does_not_collapse(self):
        """启动失败时绝不能收起主界面 —— 否则错误弹窗挂在隐藏的窗口上。

        这一条守的是"只做一半"的反面：把收起调用错放在 ok 分支之外，
        启动失败也会把主窗藏掉，用户看不到任何提示。
        """
        m = re.search(r'def _on_lol_launch_done\(self, ok, msg\):(.*?)\n    def ',
                      self.main_code, re.S)
        body = m.group(1)
        # 找到 else 分支（失败路径），里面不该出现收起调用
        parts = body.split('else:')
        self.assertGreaterEqual(len(parts), 2, '_on_lol_launch_done 缺少失败分支')
        fail_branch = parts[-1]
        self.assertNotIn('_maybe_collapse_to_overlay', fail_branch,
                         '启动失败时也收起了主界面（用户将看不到错误提示）')

    def test_collapse_hides_main_and_shows_overlay(self):
        """收起逻辑必须**同时**隐藏主窗 + 显示小窗。

        只 hide 主窗不 safe_show 小窗 = 空屏。这是本功能最容易写错的地方：
        「启动 LOL」不启动脚本，而小窗平时是被脚本链路唤出的，默认不可见。
        """
        m = re.search(r'def _maybe_collapse_to_overlay\(self\):(.*?)\n    # ====',
                      self.main_code, re.S)
        if m is None:
            m = re.search(r'def _maybe_collapse_to_overlay\(self\):(.*?)\n    def ',
                          self.main_code, re.S)
        self.assertIsNotNone(m, '找不到 _maybe_collapse_to_overlay')
        body = m.group(1)
        self.assertIn('safe_show', body, '没有打开小窗（只隐藏主窗会变成空屏）')
        self.assertIn('hide', body, '没有隐藏主界面')
        self.assertIn('lol_launch_to_overlay', body,
                      '收起逻辑没有检查设置开关（会在关闭状态下也生效）')

    def test_way_back_exists(self):
        """小窗的「返回主界面」必须仍然接线 —— 否则收了就回不来。"""
        self.assertIn('show_main_signal.connect(self._show_main)',
                      self.main_code,
                      '小窗「返回主界面」的接线丢了 —— 收起主窗后将无法恢复')

    def test_collapse_behaviour_on_real_widgets(self):
        """行为级：在**真实窗口**上验证三种场景，而不是只读源码。

        前面几条都是源码/文件级断言，它们能证明"代码在"，但证明不了"窗口真的
        隐藏了/真的显示了"。这条用真 MainWindow + 真 OverlayWindow 跑三种场景，
        断言的是 `isVisible()` —— 用户真正看到的东西。

        ## 为什么值得单写一条（源码断言测不到的两个坑）

        · safe_show() 只是 emit 一个信号，信号到不到、窗口显不显示是两回事；
        · 只 hide 主窗不 safe_show 小窗 = 空屏，而这在源码里"看起来"是正常的。

        ## 记录踩过的坑（都真实发生过）

        · 把 _main/_overlay 换成自己 new 的替身 → access violation
          （原窗口 Python 引用消失，PyQt 回收 C++ 对象，定时器仍指向它）；
        · 直接改 _main._settings → _main 是 None（由 run_with_app 创建）；
        · 走 run_with_app() → 起了整条运行链路，在 offscreen 下挂住不退出。
        所以这里自己创建窗口并**用模块级列表持有引用**，防止中途被 GC。
        """
        try:
            from PyQt5.QtWidgets import QApplication
            from ui.main_window import MainWindow
            from ui.overlay_window import OverlayWindow
        except Exception as e:                       # pragma: no cover
            raise unittest.SkipTest(f'未安装 PyQt5 / 界面模块不可用：{e}')

        import main as M

        app = QApplication.instance() or QApplication([])
        keep = []

        def flush(n=10):
            for _ in range(n):
                app.processEvents()

        def build(flag):
            ctrl = M.AppController()
            # 无关入口打桩：不碰网络/提权/授权
            ctrl._maybe_elevate_on_startup = lambda: None
            ctrl._prewarm_license = lambda: None
            ctrl._sync_conn_state = lambda *a, **k: None
            mw = MainWindow()
            ov = OverlayWindow()
            keep.extend([ctrl, mw, ov])
            mw._settings['lol_launch_to_overlay'] = flag
            mw._settings['agreement_accepted'] = True
            mw.show_first_launch_agreement = lambda: None
            ctrl._main = mw
            ctrl._overlay = ov
            ctrl._lol_launching = True
            mw.show()
            ov.hide()
            flush()
            return ctrl

        # [1] 关闭：状态完全不变
        c1 = build(False)
        c1._on_lol_launch_done(True, '登录器已打开')
        flush()
        self.assertTrue(c1._main.isVisible(), '设置为关时主界面不该被隐藏')
        self.assertFalse(c1._overlay.isVisible(), '设置为关时不该打开小窗')

        # [2] 开启：主窗隐 + 小窗显（缺一不可）
        c2 = build(True)
        c2._on_lol_launch_done(True, '登录器已打开')
        flush(25)
        self.assertFalse(c2._main.isVisible(), '设置为开时主界面应被隐藏')
        self.assertTrue(c2._overlay.isVisible(), '设置为开时小窗应被打开（否则是空屏）')

        # [3] 失败：主窗必须留着，让用户看得到错误提示
        with unittest.mock.patch.object(M, 'QMessageBox') as mb:
            mb.Ok, mb.Cancel, mb.information = 1, 2, unittest.mock.MagicMock(return_value=1)
            c3 = build(True)
            c3._on_lol_launch_done(False, '未找到 LOL 原生登录器')
            flush()
            self.assertTrue(c3._main.isVisible(), '启动失败时主界面不该被收起')
            self.assertFalse(c3._overlay.isVisible(), '启动失败时不该打开小窗')
            self.assertTrue(mb.information.called, '启动失败时应弹出提示')


if __name__ == '__main__':
    unittest.main(verbosity=2)
