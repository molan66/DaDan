# -*- coding: utf-8 -*-
"""v4.9.3 缺陷回归测试（授权保护 + 交互语义 + 源码级护栏）。

这个文件的存在理由：v4.9.2 审计发现的问题里，有 3 条是**实测能复现的真缺陷**，
而项目原有的 73 条授权测试**全部通过** —— 说明原测试只覆盖了"设计意图内的行为"，
没覆盖"作弊者会怎么绕"。这里把每一条都写成**会咬人**的断言：

  · 删 trial.json 不能重开试用
  · 把 trial 起点改到未来不能白拿 3650 天
  · 系统时间回拨不能让过期年卡复活
  · 点「输入授权码」不能启动脚本
  · 激活窗不能在 GUI 线程同步 spawn PowerShell
  · worker.js 必须"先签发、后写绑定"

每条都依赖 `fix-with-teeth` 的纪律：把缺陷放回去，这些测试必须变红。
"""
import datetime
import json
import os
import re
import sys
import tempfile
import time
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import licensing as L                      # noqa: E402
from core import license_crypto as LC                # noqa: E402

_ROOT_DIR = _ROOT


def _read(rel_path):
    with open(os.path.join(_ROOT_DIR, rel_path), encoding='utf-8') as f:
        return f.read()


def _strip_docs_and_comments(text):
    """去掉注释与 docstring，只留**真正会执行**的代码行。

    为什么必须这么做（本文件自己踩过）：第一版断言直接用正则扫整段函数体，
    结果把"注释里提到 `self._start()`"和"docstring 里解释 `check()` 被调两次"
    都算成了违规 —— 两个用例假红。源码断言最怕这个：它会把**文档写得好**
    变成一种惩罚，进而诱导后来人把解释删掉。

    做法：逐行扫，维护"是否在 docstring / 三引号字符串里"，并剥掉行尾注释。
    够用即可 —— 这不是通用 Python 词法器，只服务本文件的几个断言。
    """
    out = []
    triple = None
    for raw in text.splitlines():
        line = raw
        # 处理三引号状态机（成对则在同一行内闭合）
        if triple is None:
            # 找本行是否开启 docstring
            m = re.search(r'(\"\"\"|\'\'\')', line)
            if m:
                q = m.group(1)
                after = line[m.end():]
                if q in after:
                    # 同行闭合：把这段整段删掉
                    line = line[:m.start()] + after[after.index(q) + 3:]
                else:
                    triple = q
                    line = line[:m.start()]
        else:
            if triple in line:
                line = line[line.index(triple) + 3:]
                triple = None
            else:
                continue
        # 剥行尾注释（粗略：不处理字符串里的 #，本文件用不到）
        line = re.sub(r'\s+#.*$', '', line)
        if line.strip():
            out.append(line)
    return '\n'.join(out)


def _func_body(src, name):
    """取某个方法的源码文本（从 def 到下一个同级 def / 装饰器）。"""
    m = re.search(r'\n(    (?:@[\w.]+\(?[^)]*\)?\n    )?)def %s\(.*?(?=\n    @|\n    def |\nclass |\Z)'
                  % re.escape(name), src, re.S)
    return m.group(0) if m else ''


class _IsolatedState(unittest.TestCase):
    """每条用例独立的授权状态目录（不碰真实 %APPDATA%）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state = L.state_path
        L.state_path = lambda: os.path.join(self._tmp.name, 'license.json')
        self._orig_days = L.TRIAL_DAYS

    def tearDown(self):
        L.state_path = self._orig_state
        L.TRIAL_DAYS = self._orig_days
        self._tmp.cleanup()


# ======================================================================
# P0-A: 试用期不能被单文件重置
# ======================================================================
class TestTrialCannotBeResetByDeletingOneFile(_IsolatedState):
    """旧实现：删掉 `trial.json` 就能拿满 7 天（实测复现过）。

    修法：试用记录同时写三份（trial.json / .trial_state / .trial_watermark），
    读取时取三者里**最早**的有效起点。
    """

    def test_three_records_exist(self):
        """必须真的落了三份 —— 否则"删一份还有兜底"是空话。"""
        L.TRIAL_DAYS = 7
        L.trial_state()
        paths = L._trial_paths()
        self.assertEqual(len(paths), 3)
        for p in paths:
            self.assertTrue(os.path.exists(p), f'试用记录缺失: {p}')

    def test_delete_visible_file_does_not_reset_trial(self):
        """**核心用例**：只删 trial.json，试用不能重开。

        放回缺陷的验证方式：把 `_read_trial_record` 改回只读 trial.json，
        本用例会立刻变红（started 变 None → 重建 → remaining 回到满值）。
        """
        L.TRIAL_DAYS = 7
        # 造一个"20 天前开始"的试用（早已过期）
        L._write_trial(time.time() - 20 * 86400, max_seen=time.time())
        self.assertFalse(L.trial_state()[0], '前提：试用应已过期')

        os.remove(L.trial_path())          # 删掉那份"看得见"的

        ok, remaining, _ = L.trial_state()
        self.assertFalse(ok, '**删 trial.json 不能重开试用**（旧实现这里是 True）')
        self.assertEqual(remaining, 0)

    def test_delete_any_single_record_does_not_reset(self):
        """三份里任意删掉**一份**都不能重开（逐份验证，防只兜住一份）。"""
        L.TRIAL_DAYS = 7
        for victim in L._trial_paths():
            with self.subTest(victim=os.path.basename(victim)):
                for p in L._trial_paths():
                    if os.path.exists(p):
                        os.remove(p)
                L._write_trial(time.time() - 20 * 86400, max_seen=time.time())
                os.remove(victim)
                ok, _, _ = L.trial_state()
                self.assertFalse(
                    ok, f'删掉 {os.path.basename(victim)} 后试用被重开')


class TestTrialRejectsFutureStart(_IsolatedState):
    """旧实现：把 started_at 写成 10 年后 → 剩余 3650 天 + usable=True（实测）。"""

    def test_future_started_at_is_rejected(self):
        L.TRIAL_DAYS = 7
        p = L.trial_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            json.dump({'started_at': time.time() + 3650 * 86400}, f)

        ok, remaining, _ = L.trial_state()
        self.assertFalse(ok, '**起点在未来必须判失效**（旧实现显示还剩 3000+ 天）')
        self.assertEqual(remaining, 0)

    def test_remaining_never_exceeds_trial_days(self):
        """任何异常记录都不该显示出"剩余 3000 天"。"""
        L.TRIAL_DAYS = 7
        # 起点稍微提前一点点（合法范围内）也不该超过 TRIAL_DAYS
        L._write_trial(time.time() + 60, max_seen=time.time())
        ok, remaining, _ = L.trial_state()
        self.assertLessEqual(remaining if remaining is not None else 0,
                             float(L.TRIAL_DAYS) + 0.01)


class TestTrialRejectsClockRollback(_IsolatedState):
    """把系统时间往前调整，不能延长试用。"""

    def test_rollback_is_rejected(self):
        L.TRIAL_DAYS = 7
        now = time.time()
        # 试用是"3 天前"开始的（还剩 4 天），水位记着"现在"
        L._write_trial(now - 3 * 86400, max_seen=now)
        self.assertTrue(L.trial_state(now=now)[0], '前提：3 天前开始的试用应仍有效')

        # 模拟把系统时间调回 10 天前（早于水位）
        ok, remaining, _ = L.trial_state(now=now - 10 * 86400)
        self.assertFalse(ok, '**系统时间回拨必须判失效**（否则调时间就能续期）')


# ======================================================================
# P0-B: 到期不能只认本机时钟
# ======================================================================
class TestExpiryUsesClockWatermark(_IsolatedState):
    """到期判断必须考虑"本机见过的最大日期"，不能只信当下系统时间。"""

    def _make_code(self, exp_date):
        """造一张**指定到期日**的合法授权码。"""
        try:
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except ImportError:                                   # pragma: no cover
            self.skipTest('未安装 cryptography')
        sk = ed25519.Ed25519PrivateKey.generate()
        pk = sk.public_key().public_bytes_raw().hex()
        comps = ['BOARD-X', 'CPU-X', '', '']
        digs = [L._slot_digest(L.SLOTS[i][0], comps[i]) for i in range(4)]
        ad = (exp_date - datetime.date(2020, 1, 1)).days
        payload = (bytes([1]) + b''.join(digs)
                   + ad.to_bytes(4, 'big') + bytes([1]))
        code = LC.b32e(payload + sk.sign(payload))
        return code, comps, pk

    def test_watermark_overrides_current_date(self):
        """**核心用例（有牙齿的那个）**：水位比到期日更晚 → 必须判过期。

        构造方式很关键（第一版写错过，导致"放回缺陷测试也不红"）：
        - 到期日 = **真实今天 + 30 天** → 只看 `date.today()` 的话**会通过**；
        - 水位   = **真实今天 + 60 天** → 比到期日晚，说明"这台机器已经见过
          更晚的日期"，到期日早已过去。
        于是：旧实现（只认 today）→ 通过 → 本用例红；新实现（取 max）→ 拒绝。

        这模拟的就是真实攻击：买家先正常用一年（水位涨到 2026-10），
        再把系统时间调回 2025，想让年卡复活。
        """
        real_today = datetime.date.today()
        exp = real_today + datetime.timedelta(days=30)
        code, comps, pk = self._make_code(exp)

        # 前提校验：不给水位时，"今天"确实还没到期（否则用例失去区分力）
        info = L.verify_code(code, components=comps, public_key_hex=pk)
        self.assertEqual(info['expires'], exp, '前提：到期日应在未来')

        # 水位推到到期日之后 → 必须拒绝
        L._write_clock_watermark(real_today + datetime.timedelta(days=60))
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(code, components=comps, public_key_hex=pk)
        self.assertIn('过期', str(cm.exception))

    def test_watermark_is_monotonic(self):
        """水位只升不降：写一个更早的日期不会把它拉低。"""
        d1 = datetime.date(2026, 10, 24)
        d2 = datetime.date(2020, 1, 2)
        L._write_clock_watermark(d1)
        self.assertEqual(L._read_clock_watermark(), d1)
        # effective_today 用 d2 也不会把水位降下去
        self.assertEqual(L.effective_today(d2), d1)
        self.assertEqual(L._read_clock_watermark(), d1)

    def test_explicit_today_still_honoured(self):
        """显式传 today 时不受水位影响 —— 测试/诊断需要固定日期。"""
        real_today = datetime.date.today()
        exp = real_today + datetime.timedelta(days=5)
        code, comps, pk = self._make_code(exp)
        # 水位推到很远（若掺进显式路径就会拒绝）
        L._write_clock_watermark(real_today + datetime.timedelta(days=90))
        info = L.verify_code(code, components=comps, public_key_hex=pk,
                             today=real_today)
        self.assertEqual(info['expires'], exp)


# ======================================================================
# P0-C: 点「输入授权码」不能启动脚本
# ======================================================================
class TestActivationClickDoesNotStartScript(unittest.TestCase):
    """"想激活" ≠ "想启动"。旧代码把前者兑现成后者（试用期内真的会挂机）。"""

    @classmethod
    def setUpClass(cls):
        cls.src = _read('main.py')

    def test_activate_intent_branch_exists(self):
        """`_on_lic_result` 必须有 'activate' 分支，且**排在启动之前**。"""
        body = _strip_docs_and_comments(_func_body(self.src, '_on_lic_result'))
        self.assertTrue(body, '找不到 _on_lic_result')
        self.assertIn("intent == 'activate'", body,
                      "缺少 'activate' 意图分支 —— 点激活会被当成点启动")
        i_act = body.index("intent == 'activate'")
        i_start = body.index('self._start()')
        self.assertLess(i_act, i_start,
                        "'activate' 分支必须排在 self._start() **之前**生效")

    def test_prompt_activation_does_not_set_pending_user(self):
        """`_prompt_activation` 内部**不得**再偷偷把 pending 记成 'user'。"""
        body = _strip_docs_and_comments(_func_body(self.src, '_prompt_activation'))
        self.assertTrue(body, '找不到 _prompt_activation')
        self.assertNotIn("self._lic_pending_start = 'user'", body,
                         "「点激活」会被误当成「点启动」—— 这正是 v4.9.2 的缺陷")

    def test_open_activation_marks_manual(self):
        """主动点激活要标记 manual，且**不自己调** `_start()`。

        ⚠️ 断言要看**代码**而不是 docstring —— docstring 里为了说明缺陷会引用
        `self._start()` 这个字符串（第一版测试因此假红过一次）。
        """
        body = _strip_docs_and_comments(_func_body(self.src, '_on_open_activation'))
        self.assertTrue(body, '找不到 _on_open_activation')
        self.assertIn("_lic_activation_reason = 'manual'", body)
        self.assertNotIn('self._start()', body,
                         '「点输入授权码」的路径里不能出现启动脚本的调用')


# ======================================================================
# P1-E/F: 激活窗不能在 GUI 线程同步 spawn PowerShell
# ======================================================================
class TestActivationDialogDoesNotBlockGui(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = _read('ui/activation_dialog.py')

    def test_no_sync_collect_in_constructor(self):
        """构造期不能出现 `machine_id()` / `machine_id_grouped()` 的同步调用。"""
        body = _strip_docs_and_comments(_func_body(self.src, '__init__'))
        self.assertTrue(body, '找不到 __init__')
        self.assertNotRegex(
            body, r'\b_?L\.machine_id(_grouped)?\(',
            '构造期同步采集机器码（会阻塞 GUI 1.5s+）')

    def test_machine_probe_runs_in_thread(self):
        """机器码必须在后台线程采集，并经信号回主线程。"""
        body = _strip_docs_and_comments(_func_body(self.src, '_start_machine_probe'))
        self.assertTrue(body, '找不到 _start_machine_probe')
        self.assertIn('threading.Thread', body,
                      '机器码采集必须在后台线程，否则激活窗白屏')
        self.assertIn('_machine_ready', body, '必须经信号回主线程填控件')

    def test_check_called_at_most_once_per_build(self):
        """`_init_ui` 里 `check()` 最多一次（旧实现两次，各 spawn 一次 PowerShell）。"""
        body = _strip_docs_and_comments(_func_body(self.src, '_init_ui'))
        self.assertTrue(body, '找不到 _init_ui')
        n = len(re.findall(r'\.check\(\)', body))
        self.assertLessEqual(
            n, 1, f'_init_ui 里 check() 被调了 {n} 次（每次含一次 PowerShell 采集）')


# ======================================================================
# ======================================================================
# v4.9.3: 原 TestWorkerSignsBeforeBinding（worker.js 先签发后写绑定）已删除，
#         与 worker.js 一起移除 —— 在线激活服务整体下线，没有服务端可测。
#         那条测试曾经很有牙齿（把 devices.push 挪到 loadSigning 之前会红），
#         原文件已备份在: E:/HuanJing/09_Caches/temp/dadan-worker-removal-backup-20261024
#         （文件名 tests__test_v493_fixes.py）
# ======================================================================
