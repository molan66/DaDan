# -*- coding: utf-8 -*-
"""启动门禁回归测试 —— 守住"未授权就绝不启动脚本"这条线。

跑法：
    <python> -m unittest tests.test_license_gate -v
    （或随 `-m unittest discover -s tests` 一起跑）

## 为什么非要有这个文件

`core/licensing.py` 的单元测试（tests/test_licensing.py）只证明"**判定**是对的"，
但**判定被不被使用**是另一回事 —— 真正的收益来自 `main.py:_start()` 里那句：

    if not self._license_ok():
        self._prompt_activation()
        return

**把这三行删掉，整套授权就归零、软件白送**，而 licensing 的 73 个测试
**全都还是绿的**（它们根本不碰 main.py）。这类"逻辑正确但没接线"的缺陷
正是本项目历史上反复出现过的（见 test_regressions.py 里那条
「三态支持 connected 但从没人推过」）。

所以这里从**三个层次**分别钉住，任何一层被拆都会当场判红：
  ① 源码级：`_start()` 里必须存在门禁调用，且**在派发线程之前**；
  ② 静态级：`_core.start()` 全项目**只有一个调用点**（多出来的就是绕过）；
  ③ 行为级：用真实 AppController 驱动 `_start()`，验证
     未授权 → 不派发、已授权 → 派发、试用中 → 派发。

## Qt 依赖说明

②③ 需要真实 AppController（要 QApplication），故用 offscreen 平台。
装了 PyQt5 就真跑；没装则**明确跳过**（skipTest 并说明），
不会伪装成"通过"—— 这与 test_licensing.py"零 Qt"的约束是分开的两个文件。
"""
import os
import re
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _read_main_source():
    with open(os.path.join(_ROOT, 'main.py'), encoding='utf-8') as f:
        return f.read()


class TestGateWiredInSource(unittest.TestCase):
    """① 源码级 —— 不需要 Qt，永远会跑。

    这一层的价值：即使在没有 PyQt5 的环境（CI/别人的机器）里，
    "有人把门禁删了"这种最致命的回归也能被立刻发现。
    """

    def setUp(self):
        self.src = _read_main_source()

    def test_start_calls_license_gate(self):
        """`_start()` 必须调用 _license_ok() 与 _prompt_activation()。"""
        self.assertIn('self._license_ok()', self.src,
                      'main.py 里找不到 _license_ok() 调用 —— 授权门禁没了，软件等于白送')
        self.assertIn('self._prompt_activation()', self.src,
                      'main.py 里找不到 _prompt_activation() —— 未授权用户拿不到激活入口')

    def test_gate_is_before_thread_dispatch(self):
        """门禁必须排在**派发后台线程之前** —— 顺序反了就等于没拦。

        为什么这条单独测：`return` 只有在派发之前才有意义。
        若有人把门禁挪到 `threading.Thread(...).start()` 之后，
        代码"看起来还在"，但脚本其实已经跑起来了。
        """
        # 定位 _start 的定义体（到下一个 def 之前）
        m = re.search(r'\n    def _start\(self\):\n(.*?)\n    def ', self.src, re.S)
        self.assertIsNotNone(m, '没找到 _start() 方法体 —— 方法名被改了？')
        body = m.group(1)

        i_gate = body.find('self._license_ok()')
        i_dispatch = body.find('threading.Thread(')
        self.assertGreater(i_gate, -1, '_start() 内没有门禁调用')
        self.assertGreater(i_dispatch, -1, '_start() 内没有线程派发 —— 结构变了，请复核本测试')
        self.assertLess(i_gate, i_dispatch,
                        '授权门禁排在了线程派发之后 —— 脚本已经跑起来了，return 失去意义')

        # 并且门禁分支里必须有 return（光调用不 return 等于放行）
        #
        # v4.9.3: 原来写 `body[i_gate:i_gate+200]` 再找 'return' —— 那是个**脆窗口**：
        #   只要门禁块里多一行注释/一行赋值，长度超过 200 字符，`return` 就落到窗口外，
        #   测试变红而代码其实是对的（本次改动就踩到了：加了 `_lic_pending_start` 与
        #   `_lic_activation_reason` 两行之后正好超出）。
        #   这类"用固定字符数截一段代码"的断言，惩罚的是写好注释的人，必须换掉。
        #
        # 改为按**缩进**切出 if 块：`if not self._license_ok():` 的块体缩进是 12 空格，
        # 遇到第一个"非空且缩进 < 12"的行就收尾。
        # ⚠️ 第一版按"是否以 8 空格开头"判断，结果把后面的 `if self._starting: ... return`
        #    也包了进来 —— 于是删掉门禁的 return 测试照样是绿的（齿是假的）。实测踩到。
        gate_start = body.rfind('\n', 0, i_gate) + 1
        block_lines = []
        for line in body[gate_start:].splitlines():
            if line.strip():
                indent = len(line) - len(line.lstrip(' '))
                if block_lines and indent < 12:
                    break              # 回到 if 的同级 → if 块结束
            block_lines.append(line)
        gate_block = '\n'.join(block_lines)
        self.assertIn('return', gate_block,
                      '门禁调用后没有 return —— 校验了却照样放行')
        # 反向护栏：切出来的块必须**只**是门禁块，不能把后文的 return 圈进来
        self.assertNotIn('threading.Thread(', gate_block,
                         '门禁块切片越界了（把后面的启动派发也包含进来）—— '
                         '这条断言本身失效，请修测试')

    def test_worker_not_gated_but_caller_is(self):
        """门禁应在 `_start()` 而非 `_start_worker()`。

        说明：`_start_worker` 跑在后台线程里，那里 `self._lic_status`
        的读取虽可行，但**失败时无法弹窗**（Qt 控件不能在非主线程碰），
        所以拦在 `_start()` 才是正确位置。这条防止有人"重构"到 worker 里。
        """
        self.assertIn('def _start_worker(self):', self.src, '结构变了，请复核本测试')
        m = re.search(r'\n    def _start_worker\(self\):\n(.*?)\n    def ', self.src, re.S)
        self.assertIsNotNone(m)
        self.assertNotIn('_prompt_activation', m.group(1),
                         '_start_worker 在后台线程里弹窗 —— Qt 控件不能跨线程，会崩')

    def test_gate_precedes_elevation_check(self):
        """门禁要排在提权检查之前。

        原因：提权会弹 UAC 并重启进程。对一个根本没授权的用户弹 UAC，
        既无意义、又容易被误解成"要管理员权限才能白嫖"，体验与观感都差。
        """
        m = re.search(r'\n    def _start\(self\):\n(.*?)\n    def ', self.src, re.S)
        body = m.group(1)
        i_gate = body.find('self._license_ok()')
        i_elev = body.find('is_admin()')
        self.assertGreater(i_gate, -1)
        if i_elev > -1:
            self.assertLess(i_gate, i_elev,
                            '门禁排在提权检查之后 —— 未授权用户会先被弹 UAC')

    def test_only_one_call_site_of_core_start(self):
        """② `self._core.start()` 全项目只能有一个调用点。

        多出来的任何一处都是**绕过门禁的后门**（直接起脚本、不经过 _start）。
        这条是最有价值的静态护栏：以后不管谁加了一条启动路径，都会在这里判红。
        """
        hits = []
        for dirpath, dirnames, filenames in os.walk(_ROOT):
            dirnames[:] = [d for d in dirnames
                           if d not in ('__pycache__', 'build', 'dist', 'data',
                                        'node_modules', '.wrangler')]
            for fn in filenames:
                if not fn.endswith('.py'):
                    continue
                if fn.startswith('test_') or fn.endswith('_test.py'):
                    continue          # 测试自己会引用，不算
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, encoding='utf-8', errors='replace') as f:
                        for i, line in enumerate(f, 1):
                            if re.search(r'\._core\.start\(\)', line):
                                hits.append(f'{os.path.relpath(p, _ROOT)}:{i}')
                except OSError:
                    continue
        self.assertEqual(
            len(hits), 1,
            f'self._core.start() 出现 {len(hits)} 次（应恰好 1 次，位于 main.py:_start_worker）。'
            f'多出来的调用点会绕过授权门禁：{hits}')

    def test_trial_days_configured(self):
        """试用期必须开着 —— 关掉它会让老买家升级后直接被锁死。

        4.7.0 之前没有任何授权逻辑、老买家都能用；若 TRIAL_DAYS=0，
        他们升级后点"启动"毫无反应，会当成软件坏了来退款。
        """
        from core import licensing as L
        self.assertGreater(L.TRIAL_DAYS, 0,
                           'TRIAL_DAYS <= 0：老买家升级后会被直接锁死（见 licensing.py 注释）')

    def test_trial_long_enough_for_manual_activation(self):
        """试用期不得短于 7 天 —— 这直接决定"卖家来不及回码"会不会砸单。

        为什么单独钉一个**下限**（而不只是"大于 0"）：
          离线模式下，买家要先把机器码发给卖家、卖家再回授权码，全程靠人。
          买家半夜下单、卖家在睡觉/上班/出差，几小时到一两天回不上都很正常。
          试用期一旦被改短（比如为了"促付费"改成 1~3 天），就会出现
          **买家付了钱、却卡在等码期间用不了** —— 直接换退款和差评。

        这条测试的作用是：任何把 TRIAL_DAYS 调小的改动都会在此处判红，
        迫使改动者先想清楚上面那个后果（而不是顺手改个数字）。
        """
        from core import licensing as L
        self.assertGreaterEqual(
            L.TRIAL_DAYS, 7,
            f'TRIAL_DAYS={L.TRIAL_DAYS} 太短：离线授权靠卖家手工回码，'
            '买家很可能在等码期间就被锁在门外（详见 licensing.py 的注释）。'
            '确实要调小，请先连带修改这条测试并说明理由。')


class TestGateBehavior(unittest.TestCase):
    """③ 行为级 —— 用真实 AppController 驱动 `_start()`。

    只验证"是否派发"，不真的去连 LCU（那需要真客户端）：
    把 `_start_worker` 换成记录器即可 —— 门禁决策发生在它**之前**。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        try:
            from PyQt5.QtWidgets import QApplication
        except Exception as e:                       # pragma: no cover
            raise unittest.SkipTest(f'未安装 PyQt5，跳过行为级门禁测试：{e}')
        cls.app = QApplication.instance() or QApplication([])
        import main as M
        cls.M = M

    def setUp(self):
        from core import licensing as L
        self.L = L
        self.ctrl = self.M.AppController()
        # 记录"门禁放行后是否真的派发了后台启动"
        self.dispatched = []
        self.ctrl._start_worker = lambda: self.dispatched.append('worker')
        # 弹窗会被换成记录器（真弹窗是 QWidget，测试里不必创建）
        self.prompted = []
        self.ctrl._prompt_activation = lambda: self.prompted.append('prompt')

    def _status(self, activated=False, trial=False, days_left=None, trial_left=None):
        """构造 LicenseStatus。注意 usable 由类自己算，这里只喂原始字段。"""
        info = {}
        if days_left is not None:
            import datetime
            info['expires'] = datetime.date.today() + datetime.timedelta(days=days_left)
        return self.L.LicenseStatus(activated, '测试', info=info,
                                    machine_id='MID', trial=trial,
                                    trial_days_left=trial_left)

    # ---- 未授权：必须拦住

    def test_unlicensed_blocks_start(self):
        """**核心断言**：未激活且试用已过 → 不派发、弹激活窗。"""
        self.ctrl._lic_status = self._status(activated=False, trial=False)
        self.ctrl._start()
        self.assertEqual(self.dispatched, [],
                         '未授权竟然派发了启动线程 —— 门禁失效，软件白送')
        self.assertEqual(self.prompted, ['prompt'],
                         '未授权时应弹出激活窗，否则用户不知道该怎么办')
        self.assertFalse(self.ctrl._starting,
                         '未授权时不应置 _starting（置了会让后续点击被单飞挡掉）')

    def test_status_none_blocks_start(self):
        """状态未就绪（None）→ **安全侧**：不放行，也不假装已授权。"""
        self.ctrl._lic_status = None
        self.ctrl._start()
        self.assertEqual(self.dispatched, [], '状态未就绪时不得放行')
        self.assertFalse(self.ctrl._starting)

    def test_expired_license_blocks_start(self):
        """已激活但**已过期** → 必须拦住（否则年卡=永久卡）。"""
        self.ctrl._lic_status = self._status(activated=True, days_left=-3)
        self.assertFalse(self.ctrl._lic_status.usable, 'usable 对过期授权应为 False')
        self.ctrl._start()
        self.assertEqual(self.dispatched, [], '过期授权竟然放行了 —— 年卡会变成永久卡')

    # ---- 已授权 / 试用中：必须放行

    def test_activated_allows_start(self):
        """已激活未过期 → 放行并派发。"""
        self.ctrl._lic_status = self._status(activated=True, days_left=365)
        self.ctrl._start()
        self.assertEqual(self.dispatched, ['worker'], '已激活用户被挡在门外')
        self.assertEqual(self.prompted, [], '已激活用户不该看到激活窗')
        self.assertTrue(self.ctrl._starting)

    def test_permanent_license_allows_start(self):
        """永久授权（无到期日）→ 放行。"""
        self.ctrl._lic_status = self._status(activated=True, days_left=None)
        self.ctrl._start()
        self.assertEqual(self.dispatched, ['worker'], '永久授权被挡')
        self.assertTrue(self.ctrl._starting)

    def test_trial_allows_start(self):
        """**试用期内 → 放行**（这是老买家升级后不被锁死的关键）。"""
        self.ctrl._lic_status = self._status(activated=False, trial=True,
                                             trial_left=2.5)
        self.assertTrue(self.ctrl._lic_status.usable, '试用期应可放行')
        self.ctrl._start()
        self.assertEqual(self.dispatched, ['worker'],
                         '试用期用户被拦住了 —— 老买家升级后会以为软件坏了')
        self.assertEqual(self.prompted, [], '试用期内不该强弹激活窗打扰')

    def test_last_moment_of_license_still_allows(self):
        """边界：到期日当天（days_left=0）仍应可用。"""
        self.ctrl._lic_status = self._status(activated=True, days_left=0)
        self.ctrl._start()
        self.assertEqual(self.dispatched, ['worker'],
                         '到期日当天就被锁了（应到最后一天结束才失效）')

    # ---- 异常与边界：一律安全侧

    def test_broken_status_object_fails_closed(self):
        """状态对象损坏 → 按未授权处理（**fail-closed**，不能 fail-open）。

        这是安全底线：任何意外都不允许变成"放行"。
        """
        class Boom:
            @property
            def usable(self):
                raise RuntimeError('模拟状态对象损坏')
        self.ctrl._lic_status = Boom()
        self.ctrl._start()
        self.assertEqual(self.dispatched, [],
                         '状态对象异常时竟然放行了 —— fail-open 会让软件白送')
        self.assertFalse(self.ctrl._starting)

    def test_license_ok_reads_only_usable(self):
        """`_license_ok()` 必须只认 `usable`，不得自己重写判定逻辑。

        若它自己拼 `activated or trial`，就会漏掉"已过期"那一条
        （激活过但过期 → activated=True → 会被误判为可用）。
        """
        self.ctrl._lic_status = self._status(activated=True, days_left=-1)
        self.assertFalse(self.ctrl._license_ok(),
                         '_license_ok 没走 usable —— 过期授权被判为可用')
        self.ctrl._lic_status = self._status(activated=False, trial=True, trial_left=1)
        self.assertTrue(self.ctrl._license_ok(), '试用期应被 _license_ok 认可')


class TestAutoStartRespectsGate(unittest.TestCase):
    """连上客户端"自动启动"这条路不能绕过门禁，也不能变成弹窗骚扰。"""

    def setUp(self):
        self.src = _read_main_source()

    def test_auto_start_checks_license(self):
        """`_maybe_auto_start_on_connect` 里必须有授权判断。"""
        m = re.search(r'\n    def _maybe_auto_start_on_connect\(self\):\n(.*?)\n    def ',
                      self.src, re.S)
        self.assertIsNotNone(m, '没找到 _maybe_auto_start_on_connect')
        body = m.group(1)
        self.assertIn('_license_ok', body,
                      '自动启动没检查授权 —— 未授权用户连上客户端就会自动跑脚本（等于白送）')
        self.assertIn('_prewarm_license', body,
                      '状态未就绪时应先预热，否则会误判为"未授权"而跳过自动启动')

    def test_auto_path_does_not_pop_dialog(self):
        """自动路径**不得**弹激活窗。

        否则每次连上客户端都会自动弹一次，像骚扰；弹窗应由用户主动点"启动"触发。
        """
        m = re.search(r'\n    def _maybe_auto_start_on_connect\(self\):\n(.*?)\n    def ',
                      self.src, re.S)
        body = m.group(1)
        self.assertNotIn('_prompt_activation', body,
                         '自动启动路径里弹窗了 —— 会变成每次连客户端都打扰用户')

    def test_pending_intent_distinguishes_user_and_auto(self):
        """未就绪时的"待兑现"必须区分 user / auto 两种意图。

        真实缺陷预防：早先用布尔 _lic_pending_start，结果**自动**路径在
        状态回来后也会走弹窗分支（布尔分不清是谁点的）。
        """
        self.assertIn("_lic_pending_start = 'auto'", self.src,
                      '自动路径没有标记 auto 意图（用布尔值会分不清来源）')
        self.assertIn("_lic_pending_start = 'user'", self.src,
                      '用户路径没有标记 user 意图')


if __name__ == '__main__':
    unittest.main(verbosity=2)
