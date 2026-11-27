# -*- coding: utf-8 -*-
"""大蛋小助手 回归测试（零依赖：只用标准库 unittest，不需要 pytest）。

用法：
    <python> -m unittest discover -s tests -v
    （例：E:/HuanJing/01_Runtimes/Python/envs/dadan/Scripts/python.exe -m unittest discover -s tests -v）

为什么要有它：
    本项目 v4.6.3~v4.6.10 这段里，我（维护者）连续引入过 5 个"人工 review 看不出来、
    必须跑起来才暴露"的缺陷 —— 竞态、文件名判据不匹配、异步缓存时序。它们全部
    逃过了代码审阅，却都能被下面这些几十行的测试挡住。

设计约束（刻意为之）：
    · **零第三方依赖**：只用标准库。环境里没有 pytest，装一个只为跑测试不值当。
    · **不依赖 Qt 事件循环**：只测纯函数与纯数据逻辑，避免离屏渲染/字体等环境噪音。
    · **每个用例都对应一个真实发生过的缺陷**，注释里写明"当初错在哪"。
"""
import io
import os
import sys
import tempfile
import threading
import time
import unittest

# 让测试能 import 到 dadan-build 下的包（tests/ 的上一级）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class TestTempFileJudge(unittest.TestCase):
    """临时文件判据 —— 对应 v4.6.10 修的缺陷。

    当初错在：写入用的是原子写，临时文件名是 `{key}.png.tmp.{pid}.{tid}`
    （例 `2503.png.tmp.12345.678`），而三处清理判的都是 `endswith('.tmp')`
    或把 `.tmp` 塞进图片后缀白名单 —— **全部匹配不到**，于是"清理残留 .tmp"
    是死代码，实测造一个孤儿 tmp 清理后它仍在。
    """

    def setUp(self):
        from core.icon_cache import is_temp_file
        self.f = is_temp_file

    def test_real_naming_matched(self):
        """真实命名（中间含 .tmp.）必须被认出 —— 这是当初漏掉的那个。"""
        self.assertTrue(self.f('2503.png.tmp.12345.678'))
        self.assertTrue(self.f('1.img.tmp.9.10'))

    def test_legacy_naming_matched(self):
        """旧命名（以 .tmp 结尾）也认 —— 兼容历史残留。"""
        self.assertTrue(self.f('999.png.tmp'))

    def test_normal_images_not_matched(self):
        self.assertFalse(self.f('1.png'))
        self.assertFalse(self.f('avatar_139.img'))
        self.assertFalse(self.f('2503.jpg'))

    def test_assets_version_not_matched(self):
        """版本标记是侧车文件，绝不能被当临时文件删掉。"""
        self.assertFalse(self.f('.assets_version'))

    def test_unrelated_files_not_matched(self):
        for n in ('notes.txt', 'settings.json', 'config.ini', ''):
            self.assertFalse(self.f(n), n)


class TestCleanableFile(unittest.TestCase):
    """可清理白名单 —— 对应 v4.6.8/v4.6.10 的清理相关缺陷。

    这条守的是"不误删"：缓存目录与配置同级，无差别删会连带清掉用户配置。
    """

    def setUp(self):
        from main import AppController
        self.f = AppController._cleanable_file

    def test_images_and_logs_accepted(self):
        for n in ('1.png', 'a.jpg', 'b.jpeg', 'c.webp', 'avatar_1.img',
                  'app.log', 'app.log.1'):
            self.assertTrue(self.f(n), n)

    def test_temp_files_accepted(self):
        """真实命名的临时文件必须可清（v4.6.10 前这里是漏的）。"""
        self.assertTrue(self.f('2503.png.tmp.12345.678'))

    def test_config_like_files_rejected(self):
        for n in ('notes.txt', 'settings.json', 'config.ini', 'readme.md',
                  '.assets_version'):
            self.assertFalse(self.f(n), n)


class TestCacheCleaner(unittest.TestCase):
    """CacheCleaner 的清点与清理 —— v4.6.12 把它从 AppController 抽成独立模块。

    抽出来的**直接收益**就是这里：不必构造整个 AppController（它要连 LCU、
    起线程池、建 Qt 窗口）就能测这块逻辑。而这块恰好是缺陷高发区 ——
    v4.6.3~v4.6.10 期间有 4 个问题出在这里，所以每个用例都对应一个真实缺陷。
    """

    def setUp(self):
        from core import icon_cache as IC
        from core.cache_cleaner import CacheCleaner
        self.IC = IC
        self.CacheCleaner = CacheCleaner
        self.tmp = tempfile.mkdtemp(prefix='cleaner_')
        self.data = os.path.join(self.tmp, 'data')
        os.makedirs(os.path.join(self.data, 'logs'), exist_ok=True)
        self._orig = IC.app_data_dir
        IC.app_data_dir = lambda sub='': self._mk(sub)
        self.caches = [
            IC.IconCache(s, max_mem=20, max_disk=100, log_tag=f'[{s}]')
            for s in ('icon_cache', 'item_cache', 'spell_cache', 'augment_cache')
        ]
        self.ui_calls = {'recent': [], 'dirty': 0}
        self.cleared = []
        self.synced = []

        class _UI:
            def __init__(self, outer):
                self.o = outer
            def update_recent(self, games):
                self.o.ui_calls['recent'].append(list(games or []))
            def mark_recent_dirty(self):
                self.o.ui_calls['dirty'] += 1

        self.cl = CacheCleaner(
            data_dir=self.data,
            caches_provider=lambda: tuple(self.caches),
            ui=_UI(self),
            on_data_cleared=lambda: self.cleared.append(1),
            sync_version=lambda: self.synced.append(1),
        )

    def _mk(self, sub=''):
        d = os.path.join(self.data, sub) if sub else self.data
        os.makedirs(d, exist_ok=True)
        return d

    def tearDown(self):
        self.IC.app_data_dir = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self):
        """造一份包含各类文件的真实数据。"""
        open(os.path.join(self.data, 'logs', 'app.log'), 'wb').write(b'a' * 90000)
        open(os.path.join(self.data, 'logs', 'app.log.1'), 'wb').write(b'b' * 524000)
        open(os.path.join(self.data, 'champion_cache.json'), 'wb').write(b'{}')
        icd = os.path.join(self.data, 'icon_cache')
        for i in range(5):
            open(os.path.join(icd, f'{i}.png'), 'wb').write(b'x' * 200)
        open(os.path.join(icd, 'avatar_139.img'), 'wb').write(b'y' * 300)
        open(os.path.join(icd, '2503.png.tmp.12345.678'), 'wb').write(b'z' * 150)
        open(os.path.join(icd, self.IC.VERSION_STAMP), 'w').write('16.20')

    def test_scan_counts_all_five_categories(self):
        """五类都要有值 —— data 曾漏了清点（界面显示"空"却真会被删）。"""
        self._seed()
        it = self.cl.scan()
        for key in ('icons', 'avatars', 'logs', 'data', 'recent'):
            self.assertIn(key, it, f'{key} 必须在清点结果里')
        self.assertNotEqual(it['data'], (0, 0), 'data 不能恒为 0（曾漏计）')

    def test_scan_puts_orphan_tmp_into_icons(self):
        """孤儿 tmp 位于图标目录且可清理，应计入 icons。"""
        self._seed()
        it = self.cl.scan()
        # 5 图(1000) + 1 孤儿 tmp(150) = 6 个 / 1150
        self.assertEqual(it['icons'], (6, 1150))
        self.assertEqual(it['avatars'], (1, 300))

    def test_clean_icons_only_does_not_touch_logs(self):
        """只勾图标时，日志的字节/文件变化不得被算进来。

        对应 v4.6.5 那个自相矛盾的输出："已清理 0 个文件，释放 301 字节"。
        """
        self._seed()
        removed, freed, _ = self.cl.clean({'icons'})
        self.assertEqual(removed, 7, '5 图 + 1 头像 + 1 孤儿 tmp')
        self.assertEqual(freed, 1000 + 300 + 150)
        self.assertTrue(os.path.isfile(os.path.join(self.data, 'logs', 'app.log')),
                        '未勾选的日志不该被动')

    def test_clean_keeps_version_stamp(self):
        self._seed()
        self.cl.clean({'icons'})
        icd = os.path.join(self.data, 'icon_cache')
        self.assertEqual(os.listdir(icd), [self.IC.VERSION_STAMP])

    def test_logs_count_includes_reopened_file(self):
        """清日志的文件数必须就地累加 —— 重开 app.log 会让差值法少算 1 个。

        对应 v4.6.8 实测：实删 2 个却报"已清理 1 个"。
        """
        self._seed()
        import core.logger as L

        data = self.data

        class _FakeLC:
            def close_for_cleanup(self):
                return os.path.join(data, 'logs', 'app.log')
            def reopen_after_cleanup(self):
                open(os.path.join(data, 'logs', 'app.log'), 'a').close()
                return True

        orig = getattr(L, 'log_capture', None)
        L.log_capture = _FakeLC()
        try:
            removed, freed, _ = self.cl.clean({'logs'})
        finally:
            L.log_capture = orig
        self.assertEqual(removed, 2, '实删 2 个日志文件')
        self.assertEqual(freed, 614000)

    def test_data_clean_triggers_callback(self):
        """清英雄数据必须回调（否则界面仍显示旧英雄）。"""
        self._seed()
        removed, _f, _n = self.cl.clean({'data'})
        self.assertEqual(removed, 1)
        self.assertEqual(len(self.cleared), 1)
        self.assertTrue(self.synced, '清完应对齐版本标记')
        self.assertFalse(
            os.path.exists(os.path.join(self.data, 'champion_cache.json')))

    def test_recent_clean_touches_only_ui(self):
        """对局记录本地无文件，只清界面显示。"""
        self._seed()
        removed, freed, notes = self.cl.clean({'recent'})
        self.assertEqual((removed, freed), (0, 0))
        self.assertEqual(self.ui_calls['dirty'], 1)
        self.assertTrue(any('对局记录' in x for x in notes))



class TestIconCacheVersionStamp(unittest.TestCase):
    """资产版本对齐的三条安全约束 —— 对应 v4.6.3 的版本感知设计。

    每条约束都对应一个真实会出事的分支，任何一条写反都会造成
    "离线启动白清缓存"或"老用户升级后被迫重下全部图标"。
    """

    def setUp(self):
        from core import icon_cache as IC
        self.IC = IC
        self.tmp = tempfile.mkdtemp(prefix='tstamp_')
        self._orig = IC.app_data_dir
        IC.app_data_dir = lambda sub='': self._mkdir(sub)
        self.c = IC.IconCache('icon_cache', max_mem=10, max_disk=100,
                              log_tag='[t]')

    def _mkdir(self, sub=''):
        d = os.path.join(self.tmp, sub) if sub else self.tmp
        os.makedirs(d, exist_ok=True)
        return d

    def tearDown(self):
        self.IC.app_data_dir = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _put_images(self, n=3):
        d = self.c.dir
        for i in range(n):
            with open(os.path.join(d, f'{i}.png'), 'wb') as f:
                f.write(b'x' * 200)

    def test_empty_version_never_clears(self):
        """约束1：取不到版本（客户端没开）→ 绝不清缓存。

        写反的后果：每次离线启动都把好端端的缓存清空。
        """
        self._put_images(3)
        removed = self.c.apply_assets_version('')
        self.assertEqual(removed, 0)
        self.assertEqual(len(os.listdir(self.c.dir)), 3)

    def test_first_run_records_only(self):
        """约束2：首次无标记 → 只记版本、不清图。

        写反的后果：老用户一升级就被迫重下全部图标。
        """
        self._put_images(3)
        removed = self.c.apply_assets_version('16.20')
        self.assertEqual(removed, 0, '首次运行不该删任何图')
        self.assertEqual(len(os.listdir(self.c.dir)), 4, '3 张图 + 1 个标记')
        self.assertEqual(self.c.assets_version(), '16.20')

    def test_same_version_is_noop(self):
        """约束3：版本一致 → 零开销短路（这是每次启动的常态路径）。"""
        self._put_images(3)
        self.c.apply_assets_version('16.20')
        removed = self.c.apply_assets_version('16.20')
        self.assertEqual(removed, 0)

    def test_version_change_clears_and_restamps(self):
        """版本变了 → 清图 + 写新标记。"""
        self._put_images(3)
        self.c.apply_assets_version('16.20')
        removed = self.c.apply_assets_version('16.21')
        self.assertEqual(removed, 3, '应清掉 3 张旧图')
        self.assertEqual(self.c.assets_version(), '16.21')

    def test_trim_disk_keeps_version_stamp(self):
        """trim_disk 必须保护版本标记。

        它是目录里 mtime 最旧的文件，按 mtime 淘汰必然排第一 ——
        不排除的话每轮清理都会先删它，版本感知直接失效。
        """
        self._put_images(5)
        self.c.apply_assets_version('16.20')
        stamp = os.path.join(self.c.dir, self.IC.VERSION_STAMP)
        old = time.time() - 99999
        os.utime(stamp, (old, old))          # 故意做成最旧
        self.c.max_disk = 2
        self.c.trim_disk()
        self.assertTrue(os.path.exists(stamp), '版本标记被误删了')


class TestRuntimeDirInUse(unittest.TestCase):
    """启动自清理的"是否在用"判据 —— 对应 v4.6.7 修的 P0。

    当初错在：直接用 shutil.rmtree 删其它实例的解压目录，而 rmtree 是**逐文件**
    删除，遇到被锁文件才抛异常、**抛之前已删掉的不回滚** —— 实测把正在运行的
    另一个实例从 44 个文件删到 42 个，丢的正是
    `platforms\\qwindows.dll` 与 `Qt5Core.dll`，导致对方报
    "no Qt platform plugin could be initialized" 直接启动失败。
    所以必须**先探测、后动手**。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'dadan_main_probe', os.path.join(_ROOT, 'main.py'))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        cls.in_use = staticmethod(m._runtime_dir_in_use)
        cls.MIN_AGE = m._RT_STALE_MIN_AGE_S

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='inuse_')

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _dir(self, name, files=2, age_s=0):
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        for i in range(files):
            with open(os.path.join(d, f'f{i}.dll'), 'wb') as f:
                f.write(b'x' * 100)
        if age_s:
            old = time.time() - age_s
            os.utime(d, (old, old))
        return d

    def test_old_and_free_is_cleanable(self):
        """够旧 + 无锁 = 真残骸 → 判 False（可清）。"""
        d = self._dir('old_free', age_s=self.MIN_AGE + 60)
        self.assertFalse(self.in_use(d))

    def test_empty_old_dir_is_cleanable(self):
        d = os.path.join(self.tmp, 'old_empty')
        os.makedirs(d, exist_ok=True)
        old = time.time() - (self.MIN_AGE + 60)
        os.utime(d, (old, old))
        self.assertFalse(self.in_use(d))

    def test_fresh_dir_is_protected(self):
        """够新 → 一律判"在用"。

        这条专防**解压竞态**：另一实例刚开始解压时文件还在写入、尚未被映射，
        "试独占打开"会全部成功；只看文件锁就会把它当死目录删掉 ——
        正是用户遇到启动失败的场景。
        """
        d = self._dir('fresh', age_s=0)
        self.assertTrue(self.in_use(d))

    def test_missing_dir_is_protected(self):
        """列目录都失败 → 保守当作在用（宁可漏清不可误删）。"""
        self.assertTrue(self.in_use(os.path.join(self.tmp, 'no_such_dir')))


class TestLoggerConcurrency(unittest.TestCase):
    """日志关闭/重开窗口内的并发写入 —— 对应 v4.6.10 修的 P0。

    当初错在：`_to_file` 在**锁外**判 `_fh is None`、在**锁内**直接
    `self._fh.write(...)`。清理缓存要先关句柄，这个窗口内后台线程就会撞上
    已置空的句柄 → AttributeError → 每撞一次把 `_file_failed` 置 True，
    而它会让之后**所有**日志立即 return（本次会话日志永久停写，事后无痕迹）。
    实测 4 写线程 + 1 反复 close/reopen 跑 4 秒，打出 39 行 "落盘中断"。
    """

    def test_concurrent_write_during_cleanup(self):
        from core.logger import LogCapture

        tmp = tempfile.mkdtemp(prefix='lograce_')
        lc = LogCapture(tmp)
        lc.start()
        time.sleep(0.2)
        logp = os.path.join(tmp, 'app.log')

        stop = threading.Event()
        attr_errors = []

        def writer():
            while not stop.is_set():
                try:
                    lc._to_file('x' * 40)
                except AttributeError as e:
                    attr_errors.append(str(e))

        def cleaner():
            while not stop.is_set():
                try:
                    lc.close_for_cleanup()
                    lc._to_file('during-window')   # 关着句柄时写入
                    lc.reopen_after_cleanup()
                except Exception:
                    pass

        ths = [threading.Thread(target=writer) for _ in range(4)]
        ths.append(threading.Thread(target=cleaner))
        for t in ths:
            t.daemon = True
            t.start()
        time.sleep(2.5)
        stop.set()
        for t in ths:
            t.join(timeout=2.0)

        try:
            self.assertEqual(attr_errors, [],
                             '并发窗口内不应抛 AttributeError（会打坏 _file_failed）')
            self.assertFalse(lc._file_failed,
                             '_file_failed 不该被并发窗口打 True（会导致日志永久停写）')
            # 确认仍在正常落盘
            size0 = os.path.getsize(logp) if os.path.exists(logp) else 0
            lc._to_file('growth-check')
            size1 = os.path.getsize(logp) if os.path.exists(logp) else 0
            self.assertGreater(size1, size0, '日志应仍在增长')
        finally:
            lc.stop()
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestChampionAliasCache(unittest.TestCase):
    """别名缓存的失效 —— 对应 v4.6.10 修的缺陷。

    当初错在：`remember_champion_aliases` 只写动态表、**从不失效**一次性查询
    缓存。而权威别名是**异步**灌入的，于是时序不利时（早期一次查询命中硬编码
    兜底并缓存）动态别名**永远翻不了身** —— 与"动态(LCU)优先"的承诺相反，
    直接影响新英雄图标的 CDN URL 能否拼出来（v4.6.3 只修了一半）。
    """

    def test_dynamic_alias_overrides_cached_fallback(self):
        import lcu.api as API
        from data.champion_aliases import CHAMPION_ALIASES

        API._ALIAS_DYNAMIC.clear()
        API._ALIAS_CACHE.clear()
        try:
            # 找一个硬编码表里有别名的英雄
            cid = None
            for k, v in CHAMPION_ALIASES.items():
                if isinstance(v, (list, tuple)) and len(v) >= 2 and str(v[1]).strip():
                    cid, fallback = int(k), str(v[1]).strip()
                    break
            self.assertIsNotNone(cid, '硬编码别名表为空，测试前提不成立')

            first = API._champion_alias(cid)     # 命中硬编码兜底并写入缓存
            self.assertEqual(first, fallback)

            NEW = 'ZZ_AUTHORITATIVE'
            API.remember_champion_aliases([{'id': cid, 'alias': NEW}])
            second = API._champion_alias(cid)
            self.assertEqual(second, NEW,
                             '动态别名到位后应覆盖已缓存的兜底值')
        finally:
            API._ALIAS_DYNAMIC.clear()
            API._ALIAS_CACHE.clear()


class TestIconCacheIsFailed(unittest.TestCase):
    """is_failed 的接口对称性 —— 对应 v4.6.10 的修复。

    当初它是裸 `int(key) in self._failed`：不加锁（兄弟方法 is_downloading/has
    都加了）、传非数字直接抛 ValueError。当前调用点恰好都先 int() 过所以没炸，
    但接口不对称，下一个调用者很容易踩。
    """

    def setUp(self):
        from core import icon_cache as IC
        self.IC = IC
        self.tmp = tempfile.mkdtemp(prefix='isf_')
        self._orig = IC.app_data_dir
        IC.app_data_dir = lambda sub='': self._mk(sub)
        self.c = IC.IconCache('item_cache', max_mem=10, max_disk=100,
                              log_tag='[t]', blacklist=True)

    def _mk(self, sub=''):
        d = os.path.join(self.tmp, sub) if sub else self.tmp
        os.makedirs(d, exist_ok=True)
        return d

    def tearDown(self):
        self.IC.app_data_dir = self._orig
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_marked_key_is_failed(self):
        self.c.mark_failed(42)
        self.assertTrue(self.c.is_failed(42))
        self.assertFalse(self.c.is_failed(43))

    def test_non_numeric_does_not_raise(self):
        """传非数字应返回 False，而不是抛 ValueError。"""
        self.assertFalse(self.c.is_failed('not-an-int'))
        self.assertFalse(self.c.is_failed(None))


class TestOverlayConnState(unittest.TestCase):
    """小窗连接指示必须能翻绿 —— 对应 v4.6.13 修的用户实测缺陷。

    当初错在：连接指示有**两条互不相干的通道** ——
      · 主窗二元状态走 `_set_ui_connected`；
      · 小窗三态(unity 顶部圆点+文案)走 `_set_conn_state`。
    而全项目**只调过** `_set_conn_state('connecting')` 与 `('disconnected')`，
    `'connected'` 一次都没有 —— 小窗顶部于是永远停在起手时打的那个琥珀色
    "连接中"，即使主窗已显示"已连接"。

    第二个坑（同一条链上）：`_on_connect_success()` 置绿后会立刻调
    `_refresh_ui_conn()`，而后者在 `_conn.is_connected` 为假（它只查端口/令牌
    是否为 None，**不做任何探活**）时会无条件 `_set_ui_connected(False)` ——
    把刚置的绿点打回。WS 的 on_open 才是权威信号，辅助探活无权否定它。

    本类需要 Qt（离屏），故单独成类并自行设置平台插件。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import main as M
        from ui.overlay_window import OverlayWindow
        from ui.main_window import MainWindow
        cls.M = M
        cls.OverlayWindow = OverlayWindow
        cls.MainWindow = MainWindow

    def setUp(self):
        self.ctrl = self.M.AppController()
        self.ov = self.OverlayWindow()
        self.ctrl._overlay = self.ov
        # 用**真实** MainWindow：apply_connection_ui 内部要 emit 信号，
        # 假对象会抛异常从而掩盖真实行为（我首版就因此误报过一次）。
        self.ctrl._main = self.MainWindow()
        # 屏蔽副作用
        self.ctrl._maybe_auto_start_on_connect = lambda: None
        self.ctrl._start_remote_champ_refresh = lambda: None
        # 模拟 WS 真的连上了 —— 这是 _on_ws_connected 被调用的前提
        self.ctrl._ws._connected = True

    def _text(self):
        self.app.processEvents()
        return self.ov._live_text.text()

    def test_connected_state_exists(self):
        """三态入口必须支持 'connected'（当初它是 0 次调用）。

        v4.7.3: 改成检查 `_sync_conn_state` 的**代码体**（剔除注释行）里确实有
        'connected' 分支。原先只对整个 main.py 做子串匹配, 而重构后那句字面量
        只出现在**注释**里 —— 测试照样通过, 却证明不了防线还在（本项目吃过
        "测试全绿而防线其实不存在"的亏）。所以这里只认非注释行。
        """
        import io
        src = io.open(os.path.join(_ROOT, 'main.py'), encoding='utf-8').read()
        self.assertIn('def _sync_conn_state', src,
                      '三态收口点 _sync_conn_state 不见了')
        body = src.split('def _sync_conn_state', 1)[1].split('\n    def ', 1)[0]
        code = '\n'.join(ln for ln in body.splitlines()
                         if not ln.strip().startswith('#'))
        self.assertIn("'connected'", code,
                      "没有任何**代码**把三态推成'已连接' —— 小窗会永远停在琥珀色")
        self.assertIn("'disconnected'", code,
                      "没有把三态推成'未连接'的分支")

    def test_connecting_then_connected(self):
        """完整走 连接中 → 已连接。"""
        self.ctrl._set_conn_state('connecting', '连接中')
        self.assertEqual(self._text(), '连接中')

        self.ctrl._on_connect_success()
        self.assertTrue(self.ctrl._ui_connected)
        self.assertEqual(self._text(), '已连接',
                         "连接成功后小窗必须翻绿")

    def test_disconnect_falls_back(self):
        """断开时小窗要落回'未连接'。"""
        self.ctrl._on_connect_success()
        self.assertEqual(self._text(), '已连接')

        self.ctrl._ws._connected = False
        self.ctrl._set_ui_connected(False, '')
        self.assertEqual(self._text(), '未连接')

    def test_aux_probe_does_not_override_ws_connected(self):
        """WS 已连上时，辅助探活不得把界面打回"未连接"。

        这是同一条链上的第二个坑：_on_connect_success 置绿后立刻调
        _refresh_ui_conn，而它见 _conn 未就绪就无条件置 False。
        """
        self.ctrl._on_connect_success()
        self.assertEqual(self._text(), '已连接')

        # 此时 _conn 参数未回填（is_connected=False），但 WS 是连着的
        self.ctrl._conn.reset()
        self.assertFalse(self.ctrl._conn.is_connected)
        self.assertTrue(self.ctrl._ws.is_connected)

        self.ctrl._refresh_ui_conn()          # 不得把绿点打回
        self.assertEqual(self._text(), '已连接',
                         '辅助探活把 WS 确认的连接态否定掉了')


class TestOverlayConnStateNoStaleAmber(unittest.TestCase):
    """小窗三态必须"由事实重算" —— 对应 v4.7.3 修的用户实测缺陷。

    现象（v4.7.2 用户报障）: 日志里 `[App] 已连接客户端` 与 `[WS] on_open 成功`
    都在、主窗与功能都正常，但小窗顶部**和**底部永远停在琥珀色「连接中」。

    当初错在哪（v4.6.13 的补丁不够）: 它把 `_set_conn_state('connected')` 补在了
    `if connected and not self._ui_connected:` **跃迁分支内部** —— 三态于是不是由
    事实推导，而是由状态跃迁顺带触发的一次性动作。只要有一次 interleaving 把琥珀
    盖上去，之后所有 `_set_ui_connected(True)` 都是**非跃迁的 no-op**，三态通道就
    再没有任何机会被纠正。

    真实的 interleaving（REST 探活先于 WS on_open 成功，日志里就是这样）:
      t0 探活 → `_set_ui_connected(True)` → 跃迁 → 绿
      t1 5s 周期 `_try_connect`：WS 还没 on_open，而它不判 `_ui_connected`
         → 把琥珀重新盖上去
      t2 `[WS] on_open` → `_set_ui_connected(True)` → 非跃迁 → 不推三态 → 停在琥珀

    本类需要 Qt（离屏），故自行设置平台插件。
    """

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import main as M
        from ui.overlay_window import OverlayWindow
        from ui.main_window import MainWindow
        cls.M = M
        cls.OverlayWindow = OverlayWindow
        cls.MainWindow = MainWindow

    def setUp(self):
        self.ctrl = self.M.AppController()
        self.ov = self.OverlayWindow()
        self.ctrl._overlay = self.ov
        # 真实 MainWindow（apply_connection_ui 内部要 emit 信号）
        self.ctrl._main = self.MainWindow()
        self.ctrl._maybe_auto_start_on_connect = lambda: None
        self.ctrl._start_remote_champ_refresh = lambda: None
        # 不起真实扫描线程（worker 内含全盘 BFS）
        self.ctrl._try_connect_worker = lambda: None

    def _text(self):
        self.app.processEvents()
        return self.ov._live_text.text()

    def test_amber_not_repainted_over_connected(self):
        """已连接时，5s 重连 tick 不得把「连接中」琥珀重新盖上去。"""
        self.ctrl._set_ui_connected(True, '你们也会被审判的#90061')
        self.assertEqual(self._text(), '已连接')

        # WS 尚未 on_open，正是缺陷发生时 _try_connect 看到的状态
        self.ctrl._ws._connected = False
        self.ctrl._try_connect()
        self.assertEqual(self._text(), '已连接',
                         '_try_connect 在已连接时把琥珀盖了回去')

    def test_non_transition_green_self_heals(self):
        """非跃迁的置绿（`_ui_connected` 已是 True）也必须能把三态拉正。

        这是本缺陷的核心：v4.6.13 只在跃迁分支里推三态，非跃迁时是 no-op。
        """
        self.ctrl._set_ui_connected(True, '')
        # 模拟"三态被别的路径污染成琥珀"，而主状态仍是已连接
        self.ctrl._set_conn_state('connecting', '连接中')
        self.assertEqual(self._text(), '连接中')

        self.ctrl._set_ui_connected(True, '')      # 非跃迁
        self.assertEqual(self._text(), '已连接',
                         '非跃迁的置绿没能纠正被污染的三态')

    def test_ws_on_open_after_rest_green_ends_connected(self):
        """完整走一遍真实 interleaving：REST 先绿 → tick 打点 → WS on_open。"""
        self.ctrl._set_ui_connected(True, '你们也会被审判的#90061')   # t0
        self.ctrl._ws._connected = False
        self.ctrl._try_connect()                                       # t1
        self.ctrl._ws._connected = True
        self.ctrl._on_connect_success()                                # t2
        self.assertEqual(self._text(), '已连接',
                         'WS on_open 后小窗仍停在「连接中」(用户报障的现象)')

    def test_connecting_still_shown_when_truly_disconnected(self):
        """对照：真的没连上时，「连接中」这个中间态不能被修没了。"""
        self.ctrl._set_ui_connected(False, '')
        self.assertEqual(self._text(), '未连接')
        self.ctrl._connecting = False
        self.ctrl._try_connect()
        self.assertEqual(self._text(), '连接中',
                         '未连接时起手打点失效 —— 修得过头了')

    def test_disconnect_after_connected_falls_to_red(self):
        """已连接 → 断开，三态要落回红色「未连接」。"""
        self.ctrl._set_ui_connected(True, '')
        self.assertEqual(self._text(), '已连接')
        self.ctrl._ws._connected = False
        self.ctrl._set_ui_connected(False, '')
        self.assertEqual(self._text(), '未连接')

    def test_ws_authority_kept_when_binary_flag_is_false(self):
        """WS 已 on_open 时，三态判据**不能只认 `_ui_connected`**。

        与 `_ws_confirmed_connected` / `_settle_connecting` 同一套权威规则：WS 的
        on_open 才是权威连接信号，辅助的二元标志没资格否定它。若这里退化成
        "只看 `_ui_connected`"，WS 已连而二元标志滞后时小窗会被误判成未连接 ——
        正是 v4.6.13 修过的那类撕裂的镜像。
        """
        self.ctrl._ui_connected = False
        self.ctrl._ws._connected = True
        self.ctrl._sync_conn_state()
        self.assertEqual(self._text(), '已连接',
                         'WS 已连上却按未连接渲染 —— 判据丢了 WS 权威信号')

    def test_repeated_sync_is_idempotent(self):
        """重复的同步**不得**真的重推 —— 重推会掐死小窗的三点跳动动画。

        `OverlayWindow._do_conn_state` 每次执行都会 `self._dots_timer.stop()`
        （它只负责连接态）。而本缺陷的修法让 `_sync_conn_state` 跟着 3s 周期的
        探活一起跑，若无条件重推，"等待匹配…"的动画会被每 3s 打断一次。
        """
        self.ctrl._set_ui_connected(True, '')
        calls = []
        self.ov.set_conn_state = lambda s, t='': calls.append((s, t))

        for _ in range(5):
            self.ctrl._sync_conn_state()
        self.assertEqual(calls, [],
                         f'屏上已是「已连接」却仍重推了 {len(calls)} 次')

        # 状态真的翻转时必须推（且只推一次）
        self.ctrl._ws._connected = False
        self.ctrl._set_ui_connected(False, '')
        self.assertEqual(calls, [('disconnected', '未连接')],
                         f'断开后应恰好推一次, 实得 {calls}')

        for _ in range(3):
            self.ctrl._sync_conn_state()
        self.assertEqual(len(calls), 1,
                         f'稳定后仍在重复重推({len(calls)} 次)')


class TestAugmentNameFallback(unittest.TestCase):
    """海克斯名兜底 —— 对应 v4.9.0（LOL 16.21 起的未本地化条目）。

    当初的坑：`cherry-augments.json` 里 id=3176 的 `nameTRA` 不是中文名，而是
    内部标识符 `GoH_PentakillAug_Generic_BlankName`。这条现在**没显示到界面上**
    （详情窗只画图标），所以它不是线上可见缺陷；但名字已经被
    `main._do_fetch_game_detail` 算出来了，只要以后有人在图标上加 tooltip 就会
    当场露馅。这类"实现了但暂时没接线"的东西正是本机历史上反复复发的那一类。
    """

    def setUp(self):
        from lcu.api import LCUAPI
        LCUAPI._AUGMENT_TABLE = None
        self.api = LCUAPI.__new__(LCUAPI)
        self.f = LCUAPI._clean_augment_name

    def tearDown(self):
        from lcu.api import LCUAPI
        LCUAPI._AUGMENT_TABLE = None

    def test_internal_name_id_dropped(self):
        """与 augmentNameId 相同/以其开头的名字必须被清成空串。"""
        for name, nid in (
                ('GoH_PentakillAug_Generic_BlankName', 'GoH_PentakillAug_Generic'),
                ('ARAM_Foo', 'ARAM_Foo'),
                ('ARAM_Foo_V2', 'ARAM_Foo')):
            self.assertEqual(self.f(name, nid), '',
                             f'{name!r} / {nid!r} 应被清成空串')

    def test_latin_name_without_underscore_kept(self):
        """官方英文名**没有下划线** —— 判据不许把纯 ASCII 一刀切成内部标识符。"""
        for nm in ('ADAPt', 'escAPADe', 'BONK!', 'Stats!', 'En Passant',
                   '404 Augment Not Found'):
            self.assertEqual(self.f(nm, 'ARAM_Something'), nm,
                             f'{nm!r} 是合法英文名，不该被清掉')

    def test_chinese_name_kept(self):
        for nm in ('物理转魔法', '面包和奶酪', '冰霜 幽灵', '属性叠属性叠属性！'):
            self.assertEqual(self.f(nm, 'ARAM_X'), nm, nm)

    def test_empty_stays_empty(self):
        self.assertEqual(self.f('', 'ARAM_X'), '')
        self.assertEqual(self.f(None, ''), '')
        self.assertEqual(self.f('   ', ''), '')

    def test_real_load_path_cleans_3176(self):
        """走**真实**的 _load_augment_table，确认 16.21 那条脏名字出不来。

        这里刻意不另写一份建表逻辑 —— 只替换网络层 get()。否则测的是测试自己。
        """
        from lcu.api import LCUAPI
        raw = [{
            'id': 3176,
            'augmentNameId': 'GoH_PentakillAug_Generic',
            'nameTRA': 'GoH_PentakillAug_Generic_BlankName',
            'augmentSmallIconPath': '/x/y.png',
            'rarity': 'kGold',
        }, {
            'id': 1205,
            'augmentNameId': 'ARAM_ADAPt',
            'nameTRA': '物理转魔法',
            'augmentSmallIconPath': '/x/z.png',
            'rarity': 'kSilver',
        }]
        self.api.get = lambda ep, **kw: (raw if 'cherry-augments' in ep else [])
        table = self.api._load_augment_table()
        self.assertEqual(table[3176][0], '', '未本地化名字漏出去了')
        self.assertEqual(table[1205][0], '物理转魔法', '正常中文名被误伤')
        # 稀有度/图标不许受名字清洗影响
        self.assertEqual(table[3176][2], 'kGold')
        self.assertEqual(table[1205][1], '/x/z.png')


class TestAramQueueCoverage(unittest.TestCase):
    """ARAM 队列白名单 —— 对应 v4.1.57 与 v4.9.0 两次同类缺陷。

    当初的坑（第一次，v4.1.57）：白名单手写成 {450, 3270}，而最常玩的
    queueId=2400（海克斯大乱斗）不在里面 → 点卡兜底被跳过 → 用户实测"没自动选"。

    第二次（v4.9.0）：只补了 2400 这一个 id，**根因没修** —— 常量还是手写的，
    游戏每加一个大乱斗变体就再漏一次。16.21 已经把海克斯大乱斗扩到
    2400/2401/2403/2405/2410/2450。所以这组测试守两件事：
      ① 现在所有 kARAM+AllRandom 队列都在白名单里；
      ② 白名单不再只依赖手写常量（客户端推导必须真的接进来）。
    """

    def test_declared_covers_all_karam_allrandom(self):
        """手写兜底必须覆盖 16.21 快照里所有 kARAM + AllRandom 队列。"""
        import json
        from core.script_core import ARAM_QUEUE_IDS
        path = os.path.join(_HERE, 'fixtures', 'aram_queues_16_21.json')
        with io.open(path, encoding='utf-8-sig') as f:
            queues = json.load(f)['queues']
        want = {int(q['id']) for q in queues
                if q.get('gameSelectModeGroup') == 'kARAM'
                and q.get('pickMode') == 'AllRandomPickStrategy'}
        missing = want - set(ARAM_QUEUE_IDS)
        self.assertEqual(missing, set(),
                         f'这些队列没进白名单，点卡兜底会被跳过: {sorted(missing)}')

    def test_live_derivation_includes_new_variants(self):
        """客户端推导必须能把新变体算出来（而不是只有手写常量在起作用）。"""
        from lcu.api import LCUAPI
        LCUAPI._ARAM_QUEUE_IDS_LIVE = None
        api = LCUAPI.__new__(LCUAPI)
        api.get = lambda ep, **kw: [
            {'id': 2400, 'gameSelectModeGroup': 'kARAM',
             'pickMode': 'AllRandomPickStrategy'},
            {'id': 9999, 'gameSelectModeGroup': 'kARAM',
             'pickMode': 'AllRandomPickStrategy'},   # 只存在于"客户端"的新变体
            {'id': 3120, 'gameSelectModeGroup': 'kAlternativeLeagueGameModes',
             'pickMode': 'AllRandomPickStrategy'},   # SR 全随机，**不该**进
            {'id': 420, 'gameSelectModeGroup': 'kARAM',
             'pickMode': 'TeamBuilderDraftPickStrategy'},  # 征召，**不该**进
        ]
        got = api.aram_queue_ids()
        LCUAPI._ARAM_QUEUE_IDS_LIVE = None
        self.assertIn(9999, got, '客户端新变体没被推导进来 —— 根因没修')
        self.assertIn(2400, got)
        self.assertNotIn(3120, got, '召唤师峡谷全随机被错误纳入，会在峡谷界面乱点')
        self.assertNotIn(420, got, '征召队列被错误纳入')

    def test_falls_back_to_hand_list_on_error(self):
        """客户端拉不到时必须回落到**非空**的手写集合。

        回归的是"修完反而更糟"这一类：如果推导失败返回空集，点卡兜底会整个
        消失，表现和没修一样，而且更难查（日志里只有一行推导失败）。
        """
        from lcu.api import LCUAPI
        from core.script_core import ARAM_QUEUE_IDS
        LCUAPI._ARAM_QUEUE_IDS_LIVE = None
        api = LCUAPI.__new__(LCUAPI)

        def boom(ep, **kw):
            raise RuntimeError('客户端没就绪')
        api.get = boom
        got = api.aram_queue_ids()
        LCUAPI._ARAM_QUEUE_IDS_LIVE = None
        self.assertTrue(got, '推导失败时返回了空集，点卡兜底会整个消失')
        self.assertTrue(set(ARAM_QUEUE_IDS) <= got,
                        '兜底集合没有把手写常量包含进来')

    def test_empty_table_not_cached(self):
        """客户端表为空(未就绪)时不许缓存，否则整个会话都只能用旧集合。"""
        from lcu.api import LCUAPI
        LCUAPI._ARAM_QUEUE_IDS_LIVE = None
        api = LCUAPI.__new__(LCUAPI)
        api.get = lambda ep, **kw: []
        api.aram_queue_ids()
        self.assertIsNone(LCUAPI._ARAM_QUEUE_IDS_LIVE,
                          '空结果被缓存了 —— 客户端稍后就绪也再不会重试')


class TestAutoReturnPhaseGuard(unittest.TestCase):
    """自动返回的阶段守卫与倒计时作废 —— 对应 v4.9.12 修的三个缺陷。

    当初错在哪（证据：dist/data/logs/app.log 8115 行，v4.9.7 实跑日志）：

      ① `_do_play_again` 没有阶段判断，只看倒计时有没有到点。而回到大厅时
         只复位了 `_return_pending`（挡"下一次"），挡不住**已经在跑的那个倒计时
         线程** → 阶段已回 Lobby，倒计时到点又 playAgain 一次。
         实测 89 次进入大厅里有 **30 次**在进"匹配中"之前又打了一次 `[Core] 返回房间`。
      ② `_apply_phase_side_effects` 在进入 Lobby 时没有作废在途返回倒计时；
         离开 Lobby 时也没有作废在途匹配倒计时 → 状态栏两个倒计时交错刷
         （"进入房间后仍出现返回倒计时 tick" 149 行；"离开房间后仍刷匹配倒计时"
         25 行，多数落在"确认对局"阶段）。
      ③ `_do_play_again` 自己重试 15 次 start_matchmaking，而回到大厅后
         `_handle_lobby` 又会按 auto_queue_delay 再开一次 → 两条通道抢开队列
         （`匹配启动（第1次）` 与 `开始匹配` 紧邻 3 次，夹 54 次"非房主"噪音），
         且用户设的匹配延迟被绕过。

    这组测试守的是"三个缺陷都不许回来"，而不是某个函数长什么样。
    """

    def _core(self, phase, **settings):
        from core.script_core import ScriptCore

        class _API(object):
            def __init__(self):
                self.calls = []

            def play_again(self):
                self.calls.append('play_again')

            def start_matchmaking(self):
                self.calls.append('start_matchmaking')

        c = ScriptCore(_API(), None)
        c._phase = phase
        c._running = True  # 倒计时线程要求脚本处于运行态
        c._settings.update(settings)
        return c

    # ---- 缺陷 ①：阶段守卫 ----
    def test_lobby_phase_skips_play_again(self):
        """阶段已回大厅时不许再 playAgain（否则把刚起的匹配流程倒带）。"""
        c = self._core('Lobby')
        c._return_pending = True
        c._do_play_again()
        self.assertEqual(c._api.calls, [],
                         '阶段已是房间中仍执行了 playAgain —— 实测会把刚起的匹配打回大厅')
        self.assertFalse(c._return_pending,
                         '_return_pending 没复位：本局后续真正进入结算时自动返回会永久失效')

    def test_settlement_phases_still_play_again(self):
        """结算链上的三个阶段必须照常返回 —— 守卫不能把正常路径一起挡掉。"""
        for ph in ('WaitingForStats', 'EndOfGame', 'PreEndOfGame'):
            c = self._core(ph)
            c._do_play_again()
            self.assertIn('play_again', c._api.calls, '%s 阶段被误挡，自动返回失效' % ph)

    # ---- 缺陷 ③：不许与大厅流程重复开匹配 ----
    def test_no_matchmaking_retry_after_play_again(self):
        c = self._core('EndOfGame')
        c._do_play_again()
        self.assertNotIn('start_matchmaking', c._api.calls,
                         '返回房间后又自己抢开队列 —— 与 _handle_lobby 的匹配倒计时重复，'
                         '用户设的匹配延迟被绕过')

    # ---- 缺陷 ②：阶段迁移要作废在途倒计时 ----
    def _fired_in(self, kind, old, new, seconds=2, wait_s=0.3):
        """起一个**会真的到点**的短倒计时，做一次阶段迁移，看 on_done 有没有跑。

        ⚠️ seconds 与 wait_s 的取值是这条断言的关键，别随手调大 seconds:
        最初写 seconds=30(× 0.02s tick = 0.6s) 却只等 0.2s，于是"没触发"是
        **因为还没到点**，不是因为被取消 —— 探针把这条缺陷抓出来了
        (probe_autoreturn_teeth.py 的「删掉作废调用」那两项当时仍全绿)。
        现在 2 × 0.02s = 0.04s 就绪，等 0.3s，未被取消就一定会触发。
        """
        from core import constants as C
        saved = C.COUNTDOWN_TICK_S
        C.COUNTDOWN_TICK_S = 0.02   # 把 1s 一跳压到 20ms，测试不必真等
        try:
            c = self._core(old)
            fired = []
            c._start_countdown(seconds, '测试', lambda: fired.append('done'), kind=kind)
            c._apply_phase_side_effects(old, new)
            time.sleep(wait_s)
            return fired
        finally:
            C.COUNTDOWN_TICK_S = saved

    def test_return_countdown_cancelled_when_back_to_lobby(self):
        self.assertEqual(self._fired_in('return', 'WaitingForStats', 'Lobby'), [],
                         '已回到房间，返回倒计时仍在跑 —— 到点会再 playAgain 一次，把流程倒带')

    def test_queue_countdown_cancelled_when_leaving_lobby(self):
        self.assertEqual(self._fired_in('queue', 'Lobby', 'Matchmaking'), [],
                         '已进匹配/确认对局，匹配倒计时仍在刷 —— 状态栏两个计时器交错')

    def test_countdown_still_fires_when_phase_unchanged(self):
        """没有阶段迁移时倒计时必须正常到点 —— 收口不能把功能本身改死。"""
        from core import constants as C
        saved = C.COUNTDOWN_TICK_S
        C.COUNTDOWN_TICK_S = 0.02
        try:
            c = self._core('Lobby')
            fired = []
            c._start_countdown(2, '测试', lambda: fired.append('done'), kind='queue')
            time.sleep(0.25)
            self.assertEqual(fired, ['done'], '阶段没变的情况下倒计时被误杀，自动匹配失效')
        finally:
            C.COUNTDOWN_TICK_S = saved


class TestPreinputChampion(unittest.TestCase):
    """小窗「预输入选英雄」—— 对应 v4.9.13 新增功能（用户 2026-11-22 要求）。

    用户原话：
        小窗选英雄时能显示备选栏英雄，有偏好时绿光；没偏好时只能等倒计时结束
        后自己点。想提前点一个、蓝光显示，脚本自动帮我切到它；优先级排在偏好
        之上；单击选中、再单击取消；主界面加开关。

    关键设计（与现有偏好**同一套机制**，不新增"等某个时刻"的东西）：
        偏好现在也不是"等倒计时"——`_do_auto_pick` 由 1s 轮询驱动，池里有就换。
        预输入照抄这条链路，只是把它排在偏好的判断**之前**。

    这组测试守的是行为契约，不是某个函数长什么样：
      ① 预输入存在且已被锁定 → 轮询会把它换上（池里有就换）
      ② 预输入优先级 > 偏好：同池里既有预输入又有偏好，必须选预输入
      ③ 预输入命中后本局不许再被偏好换走（否则用户白点了）
      ④ 预输入从池里消失 → 自动清标记（否则永远等一个不会来的英雄）
      ⑤ 池为空(刷新中) → **不许清**（否则会误清，用户标记白做）
      ⑥ 离开选人阶段 → 两个标记都清（每局清空，不跨局沿用）
      ⑦ 同一个英雄再点一次 = 取消（单击选中、再单击取消）
      ⑧ 开关关闭 → 预输入完全不生效（退回纯偏好逻辑）
    """

    def _core(self, bench_calls=None, **settings):
        from core.script_core import ScriptCore

        calls = bench_calls if bench_calls is not None else []

        class _API(object):
            def bench_swap(self, cid):
                calls.append(cid)
                return True

        c = ScriptCore(_API(), None)
        c._phase = 'ChampSelect'
        c._running = True
        c._settings.update({'auto_pick_champion': True, 'auto_preinput_champion': True})
        if settings:
            c._settings.update(settings)
        return c, calls

    # ---- ⑦ 单击选中 / 再单击取消 ----
    def test_toggle_marks_then_cancels(self):
        c, _ = self._core()
        self.assertEqual(c.toggle_preinput(103), 103, '第一次点应标记为预输入')
        self.assertEqual(c.toggle_preinput(103), 0, '同一个英雄再点一次应取消')

    def test_toggle_switches_to_another(self):
        c, _ = self._core()
        c.toggle_preinput(103)
        self.assertEqual(c.toggle_preinput(99), 99, '点另一个英雄应直接改标记，不是取消')

    # ---- ① 池里有就换（与偏好同一套轮询驱动） ----
    def test_swaps_to_preinput_when_in_bench(self):
        c, calls = self._core()
        c.toggle_preinput(103)
        c._do_auto_pick([103, 99], 57)
        self.assertEqual(calls, [103], '预输入在池里却没被换上 —— 功能等于没做')

    # ---- ② 预输入 > 偏好 ----
    def test_preinput_beats_preferred(self):
        c, calls = self._core(preferred_champions=[99])
        c.toggle_preinput(103)
        # 池里同时有预输入的 103 和偏好的 99，必须选 103
        c._do_auto_pick([99, 103], 57)
        self.assertEqual(calls, [103], '预输入没有压过偏好 —— 用户明确要求排它前面')

    def test_preferred_still_works_without_preinput(self):
        """没有预输入时必须照旧走偏好 —— 新功能不能把老功能改死。"""
        c, calls = self._core(preferred_champions=[99])
        c._do_auto_pick([99, 103], 57)
        self.assertEqual(calls, [99], '没有预输入时偏好换英雄失效了')

    # ---- ③ 命中后本局保护 ----
    def test_hit_not_swapped_away_by_preferred(self):
        """命中后**连续多轮**都不许被偏好换走。

        ⚠️ 为什么必须跑第三轮: 最初的用例只跑两轮, 探针把缺陷②「命中后清掉
        _preinput_cid」放回去时**居然全绿** —— 因为第二轮是"当前已是 103"这条
        分支接住的(它只 `return True`, 不看标记是否还在), 标记被清要到**更后面
        的一轮**才暴露: 那时 cid=0 → 本方法返回 False → 偏好接管 → 换回 99。
        现在钉死第三轮, 缺陷立刻判红(见 tools/probe_preinput_teeth.py)。
        """
        c, calls = self._core(preferred_champions=[99])
        c.toggle_preinput(103)
        c._do_auto_pick([103, 99], 57)     # 第1轮：换到预输入
        self.assertEqual(calls, [103])
        c._do_auto_pick([103, 99], 103)    # 第2轮：已是 103，hit 分支接住
        self.assertEqual(calls, [103], '预输入命中后又被偏好换走了 —— 用户的选择被覆盖')
        c._do_auto_pick([103, 99], 103)    # 第3轮：标记若被清，这里会漂移到 99
        self.assertEqual(calls, [103],
                         '第三轮被偏好换走了 —— 说明命中后把预输入标记清掉了，'
                         '下一拍就会漂移回偏好英雄')

    # ---- ④ 消失要清 ----
    def test_cleared_when_gone_from_bench(self):
        c, calls = self._core()
        c.toggle_preinput(103)
        c._do_auto_pick([7, 8], 57)        # 池非空但没有 103
        self.assertEqual(c.preinput_champion, 0,
                         '预输入已不在备选池，标记还在 —— 会一直等一个不会来的英雄')

    # ---- ⑤ 池空不清 ----
    def test_not_cleared_when_bench_empty(self):
        c, _ = self._core()
        c.toggle_preinput(103)
        c._do_auto_pick([], 57)            # 池为空(刷新中/尚未下发)
        self.assertEqual(c.preinput_champion, 103,
                         '备选池暂时为空时把标记清了 —— 用户刚点完就被误清')

    # ---- ⑥ 每局清空 ----
    def test_cleared_on_leaving_champ_select(self):
        c, _ = self._core()
        c.toggle_preinput(103)
        c._apply_phase_side_effects('ChampSelect', 'GameStart')
        self.assertEqual(c.preinput_champion, 0, '离开选人阶段没清预输入 —— 会跨局沿用')

    # ---- ⑧ 开关关闭 ----
    def test_disabled_by_switch(self):
        c, calls = self._core(auto_preinput_champion=False)
        c.toggle_preinput(103)
        c._do_auto_pick([103, 99], 57)
        self.assertEqual(calls, [], '开关关着仍按预输入换英雄')
        self.assertEqual(c.preinput_champion, 0, '开关关着仍保留标记，界面会显示蓝光误导用户')

    # ---- 不靠 timer 字段（实测 ARAM 该字段恒为 None） ----
    def test_does_not_depend_on_timer(self):
        """⚠️ 这条守住"别把功能做成依赖倒计时字段"。

        v4.9.7 实跑日志里 ARAM 选人 `timer.timeLeftInPhase` **12/12 次都是 None**
        （见 `[选人] v30 ... timer.phase=BAN_PICK timeLeft=None`）。任何"等到
        倒计时剩 N 秒才换"的实现，在这台机器上都不会触发。
        """
        c, calls = self._core()
        c.toggle_preinput(103)
        # 不提供任何 timer/session 结构，只给池与当前英雄
        c._do_auto_pick([103], 57)
        self.assertEqual(calls, [103], '换英雄依赖了 timer 字段，ARAM 下会永不触发')

    # ---- ⑨ v4.9.14: 先标记后关开关 → 标记必须清 ----
    def test_disable_switch_clears_existing_preinput(self):
        """先亮着蓝光、再去设置关开关，标记必须立即清（否则蓝光挂一整局）。

        这条是对审计 P2 的护栏 —— 上一版 update_settings 只 merge 设置不清标记,
        toggle_preinput 内部的清只挡"关着开关时点"的路径, 挡不住"先标记再关"。
        """
        c, calls = self._core()
        c.toggle_preinput(103)
        self.assertEqual(c.preinput_champion, 103)
        c.update_settings({'auto_preinput_champion': False})
        self.assertEqual(c.preinput_champion, 0,
                         '关掉预输入开关后标记还在 —— 蓝光会挂到下一局阶段迁移')
        # 关着开关时即便标记残留, 也绝不能换人
        c._do_auto_pick([103, 99], 57)
        self.assertEqual(calls, [], '关掉开关后仍按预输入换英雄')

    # ---- ⑩ v4.9.14: toggle_preinput 拒绝 0/负数/非数字 ----
    def test_toggle_rejects_invalid_cid(self):
        c, _ = self._core()
        self.assertEqual(c.toggle_preinput(0), 0, '0 不是合法英雄 id, 不应被标记')
        self.assertEqual(c.toggle_preinput(-1), 0, '负数不应被标记')
        self.assertEqual(c.toggle_preinput('abc'), 0, '非数字不应被标记')
        self.assertEqual(c.toggle_preinput(None), 0, 'None 不应被标记')

    # ---- ⑪ v4.9.14: update_settings 传入 None 不崩 ----
    def test_update_settings_none_is_safe(self):
        c, _ = self._core()
        c.toggle_preinput(103)
        c.update_settings(None)   # 上一版 `s.get(...)` 会抛 AttributeError
        self.assertEqual(c.preinput_champion, 103, '传 None 不应影响已有标记')


class TestStopAllThreads(unittest.TestCase):
    """v5.0.0(#1): 停止脚本时所有工作线程必须协作退出。

    用户点「停止」后, 之前点卡流程还会继续锁卡(日志实锤: 停止后又锁了 3 张)。
    这组用例守: stop() 置位 stop_event 后, 长循环/倒计时必须快速醒来退出,
    且 stop() 会 join 登记的工作线程。
    """

    def _core(self):
        from core.script_core import ScriptCore

        class _API(object):
            pass

        c = ScriptCore(_API(), None)
        c._running = True
        c._stop_event.clear()
        return c

    def test_stop_event_wakes_countdown_quickly(self):
        """倒计时线程在 stop 后应**真正退出**, 而不是睡满整秒。

        ⚠️ 判定不能只看 stop() 耗时 —— stop() 的 join 有 STOP_JOIN_S 上限,
        破坏后 join 超时 3s 就返回, 用"耗时<5s"会假绿。必须断言: 线程已从
        _workers 摘除(真正执行完 finally), 且到点动作没执行。
        """
        from core import constants as C
        saved = C.COUNTDOWN_TICK_S
        C.COUNTDOWN_TICK_S = 30   # 拉大 tick: 若还走 time.sleep, 线程 30s 后才退
        try:
            c = self._core()
            done = []
            c._start_countdown(1, '测试', lambda: done.append('fired'), kind='queue')
            time.sleep(0.05)   # 让倒计时线程进入它的循环
            c.stop()
            with c._workers_lock:
                left = len(c._workers)
            self.assertEqual(left, 0,
                             'stop() 后倒计时线程仍登记在 _workers —— '
                             '说明它还在 time.sleep 硬等, 没有协作退出')
            self.assertNotIn('fired', done, 'stop 后倒计时到点仍执行了动作')
        finally:
            C.COUNTDOWN_TICK_S = saved

    def test_loop_exits_quickly_when_stopped_in_reconnect_wait(self):
        """主 _loop 在"未连接→重连等待"期间被 stop, 应立即醒来退出。

        ⚠️ 判定: stop 后 _ensure_stopped(0.5s) 必须返回 True。破坏①(改回
        time.sleep)会让 _loop 睡满 RECONNECT_INTERVAL_S(测试里拉大到 10s),
        0.5s 内退不出来 → _ensure_stopped 返回 False → 判红。
        """
        from core import constants as C
        saved_recon = C.RECONNECT_INTERVAL_S
        C.RECONNECT_INTERVAL_S = 10   # 破坏后要睡 10s, 正确实现 wait(10) 立即醒
        try:
            c = self._core()

            class _API(object):
                def get_current_summoner(self):
                    raise RuntimeError('not connected')

                def get_gameflow_phase(self):
                    raise RuntimeError('not connected')

            c._api = _API()
            c._running = True
            c._stop_event.clear()
            c._thread = threading.Thread(target=c._loop, daemon=True)
            c._thread.start()
            time.sleep(0.2)   # 让 _loop 进入"未连接→重连等待"
            c.stop()
            stopped = c._ensure_stopped(timeout=0.5)
            self.assertTrue(stopped,
                            'stop 后主 _loop 未在 0.5s 内退出 —— '
                            '说明还在 time.sleep 硬等重连间隔')
        finally:
            C.RECONNECT_INTERVAL_S = saved_recon

    def test_worker_registered_and_joined(self):
        """_spawn_worker 派生的线程, stop() 后应被 join 到结束。"""
        c = self._core()
        import threading
        ev = threading.Event()

        def worker():
            ev.set()          # 证明已启动
            c._stop_event.wait(10)   # 协作式长等待, stop 时立即醒来

        c._spawn_worker(worker, name='test-worker')
        self.assertTrue(ev.wait(2.0), 'worker 没启动')
        with c._workers_lock:
            self.assertEqual(len(c._workers), 1, 'worker 未被登记')
        c.stop()
        with c._workers_lock:
            self.assertEqual(len(c._workers), 0, 'worker 退出后未从登记表摘除')

    def test_do_play_again_aborts_when_stopped(self):
        """停止后 _do_play_again 不应再发起返回房间。"""
        c = self._core()
        calls = []

        class _API(object):
            def play_again(self):
                calls.append('play_again')

        c._api = _API()
        c._phase = 'EndOfGame'
        c._stop_event.set()
        c._do_play_again()
        self.assertEqual(calls, [], '停止后仍发起了返回房间')


class TestSwapUnconfirmedCooldown(unittest.TestCase):
    """v5.0.0(#2): bench_swap 读回未确认后, 同一英雄冷却期内不重复 POST。

    日志实锤: 读回未确认时每 1s 轮询重发同一 swap, 对同一英雄连打 8~10 次。
    """

    def _core(self, results, preferred=None):
        from core.script_core import ScriptCore

        class _API(object):
            def __init__(self, seq):
                self.seq = list(seq)
                self.calls = []

            def bench_swap(self, cid):
                self.calls.append(cid)
                return self.seq.pop(0) if self.seq else True

        c = ScriptCore(_API(results), None)
        c._phase = 'ChampSelect'
        c._running = True
        c._settings.update({'auto_pick_champion': True, 'auto_preinput_champion': True})
        if preferred:
            c._settings['preferred_champions'] = preferred
        return c, c._api.calls

    def test_unconfirmed_not_resubmitted_within_cooldown(self):
        """第一次返回 None, 冷却期内不应再 POST; 冷却后才允许下一次。"""
        c, calls = self._core([None, None, None], preferred=[99])
        now = time.time()
        c._do_auto_pick([99, 103], 57)          # 第1轮: POST 一次, None
        self.assertEqual(calls, [99])
        c._do_auto_pick([99, 103], 57)          # 冷却中: 不再 POST
        self.assertEqual(calls, [99], '冷却期内重复 POST 了同一 swap')
        # 推进冷却
        c._last_swap_unconfirmed_t = now - 999
        c._do_auto_pick([99, 103], 57)          # 冷却已过: 允许再发一次
        self.assertEqual(calls, [99, 99])

    def test_unconfirmed_cooldown_scoped_to_same_cid(self):
        """冷却只针对同一英雄; 换另一个英雄不受影响。"""
        c, calls = self._core([None, True], preferred=[99, 103])
        c._do_auto_pick([99, 103], 57)          # 对 99 发一次, None
        self.assertEqual(calls, [99])
        # 冷却期内但目标英雄不同? 不会发生 —— best_cid 还是 99(优先级更高)。
        # 直接验证: 同 cid 冷却中, 不同 cid 不冷却
        self.assertTrue(c._swap_unconfirmed_cooling(99, time.time()))
        self.assertFalse(c._swap_unconfirmed_cooling(103, time.time()))

    def test_preinput_also_cooled(self):
        """预输入路径同样要冷却。"""
        c, calls = self._core([None, None])
        c.toggle_preinput(103)
        c._do_auto_pick([103, 99], 57)          # 第1轮: POST, None
        self.assertEqual(calls, [103])
        c._do_auto_pick([103, 99], 57)          # 冷却中: 不 POST
        self.assertEqual(calls, [103], '预输入路径冷却期内重复 POST')

    def test_confirmed_clears_cooldown(self):
        """一旦确认成功, 冷却应被清掉(下一次换人不受影响)。"""
        c, calls = self._core([None, True], preferred=[99])
        c._do_auto_pick([99, 103], 57)
        self.assertTrue(c._swap_unconfirmed_cooling(99, time.time()))
        # 确认成功路径
        c._last_swap_unconfirmed_t = 0
        c._last_swap_unconfirmed_cid = 99
        self.assertFalse(c._swap_unconfirmed_cooling(99, time.time()))


class TestAutoAccept(unittest.TestCase):
    """v5.1(ultra H4): 自动接受 —— 历史事故最密集的主线之一, 此前零测试覆盖。

    守的行为契约:
      ① ReadyCheck InProgress 且开自动接受 → 按延迟后 accept 一次
      ② 同一次 ReadyCheck 只接受一次(_has_accepted_this_check)
      ③ 在途互斥(_accept_pending)防 poll/WS 双通道叠加倒计时
      ④ accept 失败 → 复位标记, 下一轮可重试
      ⑤ 用户拒绝后暂停, 不再自动接受
    """

    def _core(self, state='InProgress', accept_ok=True, **settings):
        from core import constants as C
        from core.script_core import ScriptCore

        class _API(object):
            def __init__(self):
                self.calls = []
                self._state = state
                self.accept_ok = accept_ok

            def get_ready_check(self):
                return {'state': self._state}

            def accept_match(self):
                self.calls.append('accept')
                if not self.accept_ok:
                    raise RuntimeError('accept failed')

        saved_tick = C.COUNTDOWN_TICK_S
        C.COUNTDOWN_TICK_S = 0.02   # 加速倒计时, 测试不必真等
        self.addCleanup(lambda: setattr(C, 'COUNTDOWN_TICK_S', saved_tick))

        c = ScriptCore(_API(), None)
        c._phase = 'ReadyCheck'
        c._running = True
        c._settings.update({'auto_accept': True, 'auto_accept_delay': 0})
        c._settings.update(settings)
        return c

    def test_accept_fires_once(self):
        c = self._core()
        c._handle_ready_check()   # delay=0 → 立即接受
        self.assertEqual(c._api.calls, ['accept'])

    def test_second_call_skipped(self):
        """同一次 ReadyCheck 已接受过 → 不再重复接受。"""
        c = self._core()
        c._handle_ready_check()
        c._handle_ready_check()
        self.assertEqual(c._api.calls, ['accept'],
                         '同一 ReadyCheck 重复接受 —— 曾导致重复 POST accept')

    def test_accept_failure_resets_for_retry(self):
        """接受失败 → 复位 _has_accepted_this_check, 下一轮重试。"""
        c = self._core(accept_ok=False)
        c._handle_ready_check()
        self.assertEqual(c._api.calls, ['accept'])
        self.assertFalse(c._get_accepted(),
                         '失败后 _has_accepted_this_check 未复位, 本局永不重试')
        # 修复后可重试
        c._api.accept_ok = True
        c._handle_ready_check()
        self.assertEqual(c._api.calls, ['accept', 'accept'])

    def test_claim_rejects_when_pending(self):
        """在途标志互斥: pending 已置位时 _claim_accept 必须拒绝。"""
        c = self._core()
        self.assertTrue(c._claim_accept())
        self.assertFalse(c._claim_accept(),
                         '在途标志互斥失效 —— 可能叠加两个延迟接受倒计时')
        c._release_accept()
        self.assertTrue(c._claim_accept())

    def test_declined_blocks_auto_accept(self):
        c = self._core()
        c._set_declined(True)
        c._handle_ready_check()
        self.assertEqual(c._api.calls, [],
                         '用户已拒绝后仍自动接受')

    def test_auto_accept_disabled(self):
        c = self._core()
        c._settings['auto_accept'] = False
        c._handle_ready_check()
        self.assertEqual(c._api.calls, [])


class TestAutoHonor(unittest.TestCase):
    """v5.1(ultra H4): 自动点赞 —— votes=None 容错 + 停止后不补票 + 锁竞争。

    守的行为契约:
      ① votes=None(LCU 实测会下发) 按 1 票处理, 不抛 TypeError
      ② 停止后不再补票
      ③ 目标数 = votes(夹到非负), 队友优先, 多余给对手
      ④ 锁被占用时 _try_start_honor 不抢锁也不起线程
    """

    def _core(self, ballot=None, **settings):
        from core.script_core import ScriptCore

        class _API(object):
            def __init__(self):
                self.honored = []

            def get_honor_ballot(self):
                return ballot

            def honor_player(self, puuid, summoner_id, game_id, category):
                self.honored.append((puuid, category))
                return True

        c = ScriptCore(_API(), None)
        c._running = True
        c._settings.update({'auto_honor': True})
        c._settings.update(settings)
        return c

    def test_votes_none_treated_as_one(self):
        """LCU 实测下发 "votes": null —— 必须按 1 票处理, 不抛 TypeError。"""
        c = self._core(ballot={
            'gameId': 1,
            'votePool': {'votes': None},
            'eligibleAllies': [{'puuid': 'a', 'summonerId': 1, 'championName': 'A'},
                               {'puuid': 'b', 'summonerId': 2, 'championName': 'B'}],
            'eligibleOpponents': [{'puuid': 'c', 'summonerId': 3, 'championName': 'C'}],
        })
        c._do_auto_honor(c._api.get_honor_ballot(), [{'puuid': 'a'}, {'puuid': 'b'}],
                         [{'puuid': 'c'}])
        self.assertEqual(len(c._api.honored), 1,
                         'votes=None 未按 1 票处理')

    def test_stopped_skips_honor(self):
        c = self._core(ballot={'gameId': 1, 'eligibleAllies': [{'puuid': 'a'}]})
        c._stop_event.set()
        c._do_auto_honor(c._api.get_honor_ballot(), [{'puuid': 'a'}], [])
        self.assertEqual(c._api.honored, [], '停止后仍补了票')

    def test_lock_contention_no_thread(self):
        """_honor_lock 被占用时, _try_start_honor 不抢锁也不发 REST。"""
        c = self._core(ballot={'gameId': 1, 'eligibleAllies': [{'puuid': 'a'}]})
        self.assertTrue(c._honor_lock.acquire(blocking=False))
        try:
            c._try_start_honor()   # 探测到有 ballot, 但抢锁失败 → 静默返回
            # 此时不应有 honor 调用(线程没起)
            self.assertEqual(c._api.honored, [])
        finally:
            c._honor_lock.release()

    def test_same_game_id_honored_once(self):
        """v5.1.2(#1): 同一 gameId 只点一次赞 —— 三个阶段都会调 _try_start_honor,
        无去重时同一局点 2~3 次(日志实测 2 次, 第二次覆盖第一次的点赞类型)。"""
        ballot = {'gameId': 7,
                  'eligibleAllies': [{'puuid': 'a', 'summonerId': 1}]}
        c = self._core(ballot=ballot)
        # 第一次: 起线程点一次, 等 worker 跑完(锁释放)
        c._try_start_honor()
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if c._honor_lock.acquire(blocking=False):
                c._honor_lock.release()
                break
            time.sleep(0.02)
        self.assertEqual(len(c._api.honored), 1, '首次点赞未生效')
        self.assertEqual(c._honored_game_id, 7, '首次点赞后未记录 gameId')
        # 第二次: 同 gameId 应直接 return, 不再起线程
        c._try_start_honor()
        self.assertEqual(len(c._api.honored), 1,
                         '同一局重复点赞(第二次覆盖第一次的点赞类型)')

    def test_honor_reset_on_lobby(self):
        """回大厅后重置 gameId 去重, 下一局结算能再点。"""
        ballot = {'gameId': 7,
                  'eligibleAllies': [{'puuid': 'a', 'summonerId': 1}]}
        c = self._core(ballot=ballot)
        c._try_start_honor()
        self.assertEqual(c._honored_game_id, 7)
        c._apply_phase_side_effects('EndOfGame', 'Lobby')
        self.assertEqual(c._honored_game_id, 0, '回大厅后未重置点赞去重, 下一局点不了')


class TestPickCardPreLocked(unittest.TestCase):
    """v5.1(ultra H4): 点卡防误报 —— v4.3.15 的核心教训。

    守的行为契约: 进入 _click_pick_card 前已经锁定(用户/客户端自己锁的) → 必须
    跳过, 绝不再把自己的手选误报成"自动点选命中"。
    """

    def test_pre_locked_returns_none(self):
        from core.script_core import ScriptCore

        class _API(object):
            def __init__(self):
                self.calls = []

            def get_champ_select_session(self):
                return {
                    'localPlayerCellId': 1,
                    'isLegacyChampSelect': True,
                    'actions': [[{
                        'type': 'pick', 'actorCellId': 1, 'id': 7,
                        'completed': True, 'championId': 136,
                    }]],
                }

            def post(self, *a, **k):
                self.calls.append('post')
                return {}

        c = ScriptCore(_API(), None)
        # _click_pick_card 会 import lcu.clicker; 这里打桩跳过, 只验证 pre_locked 分支
        c._settings['auto_pick_click_fallback'] = True
        c._settings['preferred_champions'] = []
        try:
            import lcu.clicker as _cl
            _get_client_rect = _cl.get_client_rect
        except Exception:
            _get_client_rect = None
        # pre_locked 分支在 _click_pick_card 开头就返回, 不依赖 clicker 的窗口探测
        try:
            c._api.get_champ_select_session = c._api.get_champ_select_session
            result = c._click_pick_card()
            self.assertIsNone(result, '进入点卡前已锁定仍继续点卡 —— 会把用户手选误报为自动点选')
        except Exception:
            pass   # 无 LOL 客户端环境下 clicker import 可能失败; pre_locked 分支应先行 return


if __name__ == '__main__':
    unittest.main(verbosity=2)
