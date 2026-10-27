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


if __name__ == '__main__':
    unittest.main(verbosity=2)
