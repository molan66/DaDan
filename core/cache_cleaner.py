# -*- coding: utf-8 -*-
"""设置页「清理缓存」的全部逻辑（v4.6.12 从 main.AppController 抽出）。

为什么单独成模块：
    原先这 300 多行挤在 AppController（3200 行 / 115 方法）里，而它其实是**自成一
    体的一个功能**：只依赖"四个图标缓存实例 + data 目录 + 几个回调"，不碰连接、
    不碰脚本核心、不碰录制。抽出来之后：
      · AppController 少一块，职责边界清楚；
      · 这块逻辑可以在**不构造整个控制器**的情况下单测（这正是它最容易出问题、
        也最值得测的部分 —— v4.6.3~v4.6.10 期间它有 4 个缺陷在这里）；
      · "清理"与"清点"必须共用同一套判据这件事，被**物理地**保证在同一个文件里，
        不会再出现"两处白名单各写一份然后漂移"。

依赖是**显式传入**的（而不是把 AppController 塞进来当万能句柄）：
    data_dir         包外 data 目录
    caches_provider  () -> (IconCache, ...)  取四个缓存实例
    ui               可选，需提供 update_recent() / mark_recent_dirty()
    on_data_cleared  可选，清理英雄数据后回调（用于重置内存态并触发重拉）
    sync_version     可选，清理完对齐资产版本

设计纪律（这些是从真实缺陷里换来的，别改）：
    · 判据只有一份：is_cleanable_file() 复用 core.icon_cache 的 is_temp_file 与
      IMAGE_SUFFIXES。曾出现"两处白名单各写一份、已经漂移"，
      表现是"某类文件清点算得到、清理删不掉"这种极难复现的差异。
    · 日志**不能直接删**：core.logger 长期持有 app.log 句柄，直接 os.remove 要么
      抛 PermissionError，要么"删掉了但句柄仍指向已删除文件"，后续日志静默丢失。
      必须先关句柄 → 删 → 重开。
    · 统计**就地累加**优先于"两次扫描差值"：日志重开后会新建一个空 app.log、
      英雄数据删完立刻起后台重拉可能赶在扫描前写回来 —— 这两类用差值法都会少算
      （实测：日志实删 2 个却报 1 个）。图标/头像没有"删完立刻重建"行为，
      继续用差值，口径更稳。
"""
import os

from .icon_cache import IMAGE_SUFFIXES, is_temp_file


def is_cleanable_file(p: str) -> bool:
    """判断某文件是否属于"缓存/日志"范畴。

    只按后缀白名单删 —— 绝不能"目录里有什么删什么"：
    缓存目录与配置文件同级，无差别删除会连带清掉用户配置。

    图片类后缀与临时文件判据都复用 core.icon_cache 的**单一来源**常量；
    日志类后缀只在这里有意义（icon_cache 不碰日志）。
    """
    if not p:
        return False
    low = p.lower()
    # 临时文件 `{key}.png.tmp.{pid}.{tid}` 不以任何图片后缀结尾，必须单独判 ——
    # 曾因漏了这条，三处"清理残留 .tmp"实际全是死代码。
    if is_temp_file(os.path.basename(p)):
        return True
    return low.endswith(IMAGE_SUFFIXES) or low.endswith(('.log', '.log.1'))


class CacheCleaner:
    """清点 + 清理本程序的缓存与日志。"""

    # 界面上的勾选项键（与 ui/main_window.py 的卡片一一对应）
    KEYS = ('icons', 'avatars', 'logs', 'data', 'recent')

    def __init__(self, data_dir, caches_provider, ui=None,
                 on_data_cleared=None, sync_version=None):
        self._data_dir = data_dir
        self._caches_provider = caches_provider
        self._ui = ui
        self._on_data_cleared = on_data_cleared
        self._sync_version = sync_version

    # ---------------- 清点 ----------------

    def walk(self, path, classify=None):
        """遍历一个目录，按白名单累加 (文件数, 字节数)。

        classify 给了就按它分类返回 {key: (n, sz)}；没给就返回单个 (n, sz)。
        原先 scan 内部有两份几乎相同的实现、删除日志时还有第三份 ——
        三份分叉正是"某个文件漏计"这类缺陷的土壤。
        """
        if classify is not None:
            out = {}
            try:
                for f in os.listdir(path):
                    p = os.path.join(path, f)
                    try:
                        if not os.path.isfile(p):
                            continue
                        sz = os.path.getsize(p)
                    except OSError:
                        continue
                    key = classify(f)
                    if key is None:
                        continue
                    n0, sz0 = out.get(key, (0, 0))
                    out[key] = (n0 + 1, sz0 + sz)
            except Exception:
                pass
            return out
        n = sz = 0
        try:
            for f in os.listdir(path):
                p = os.path.join(path, f)
                try:
                    if os.path.isfile(p) and is_cleanable_file(p):
                        n += 1
                        sz += os.path.getsize(p)
                except OSError:
                    continue
        except Exception:
            pass
        return (n, sz)

    def scan(self):
        """清点可清理的东西 → {类别: (文件数, 字节数)}。纯只读，不动任何文件。

        recent 恒为 (0,0)：对局记录本地不落盘（每次从 LCU 实时拉），保留这一项是
        为了让界面与用户的理解对齐 —— 用户看到"对局记录"能清，点了不报错、
        并明确告知"无本地缓存"。
        """
        items = {}
        items['logs'] = self.walk(os.path.join(self._data_dir, 'logs'))

        def _classify(f):
            if f.startswith('avatar_'):
                return 'avatars'
            if is_cleanable_file(f):
                return 'icons'
            return None

        icon_n = icon_sz = av_n = av_sz = 0
        seen = set()
        for c in self._caches_provider():
            d = c.dir
            if d in seen:
                continue          # 四类缓存目录可能重复，只算一次
            seen.add(d)
            got = self.walk(d, classify=_classify)
            n, sz = got.get('icons', (0, 0))
            icon_n += n
            icon_sz += sz
            n, sz = got.get('avatars', (0, 0))
            av_n += n
            av_sz += sz
        items['icons'] = (icon_n, icon_sz)
        items['avatars'] = (av_n, av_sz)

        dc = os.path.join(self._data_dir, 'champion_cache.json')
        try:
            if os.path.isfile(dc):
                items['data'] = (1, os.path.getsize(dc))
            else:
                items['data'] = (0, 0)
        except OSError:
            items['data'] = (0, 0)

        items['recent'] = (0, 0)
        return items

    # ---------------- 清理 ----------------

    def clean(self, targets):
        """执行清理。targets 是 scan() 的键集合。

        返回 (删除文件数, 释放字节数, 说明列表)。
        """
        targets = set(targets or ())
        notes = []
        # 就地累加量（不走结尾差值，原因见文件头设计纪律）
        logs_removed = logs_freed = 0
        data_removed = data_freed = 0
        try:
            before = self.scan()
        except Exception:
            before = {}

        # ---- 1) 图标 / 头像：走 IconCache.clear_images（磁盘 + 内存一起清）----
        # 头像与英雄图标同在 icon_cache 目录，无法只清其一；任一被勾选即清
        # （界面上已注明"英雄头像与图标同目录，一并清理"）。
        if targets & {'icons', 'avatars'}:
            for c in self._caches_provider():
                try:
                    c.clear_images()
                except Exception as e:
                    notes.append(f'{c.dir_name}: {type(e).__name__}')

        # ---- 2) 日志：先关句柄 → 删 → 重开 ----
        if 'logs' in targets:
            lc = None
            try:
                from .logger import log_capture as lc
            except Exception:
                lc = None
            closed = False
            try:
                if lc is not None and hasattr(lc, 'close_for_cleanup'):
                    lc.close_for_cleanup()
                    closed = True
            except Exception as e:
                notes.append(f'关闭日志句柄: {type(e).__name__}')
            try:
                logs_dir = os.path.join(self._data_dir, 'logs')
                for f in os.listdir(logs_dir):
                    p = os.path.join(logs_dir, f)
                    try:
                        if os.path.isfile(p) and is_cleanable_file(p):
                            sz = os.path.getsize(p)   # 先取大小，删后就取不到
                            os.remove(p)
                            logs_removed += 1
                            logs_freed += sz
                    except OSError:
                        continue
            except FileNotFoundError:
                pass
            except Exception as e:
                notes.append(f'日志目录: {type(e).__name__}')
            if closed:
                try:
                    lc.reopen_after_cleanup()
                except Exception as e:
                    notes.append(f'重开日志: {type(e).__name__}')

        # ---- 3) 英雄数据缓存（下次连上客户端会自动重拉）----
        if 'data' in targets:
            try:
                p = os.path.join(self._data_dir, 'champion_cache.json')
                if os.path.isfile(p):
                    sz = os.path.getsize(p)
                    os.remove(p)
                    data_removed += 1
                    data_freed += sz
                # 内存里那份也清掉，否则界面仍显示旧英雄（与"清理"语义不符）；
                # 由回调负责重置内存态并触发重拉。
                if self._on_data_cleared is not None:
                    self._on_data_cleared()
            except Exception as e:
                notes.append(f'英雄数据: {type(e).__name__}')

        # ---- 4) 对局记录：本地不落盘，只清掉界面上正在显示的那份 ----
        # 实测确认：对局记录没有本地缓存（每次探活从 LCU /lol-match-history 实时拉，
        # 进程退出即消失）。所以没有文件可删；但用户勾了这项，就得让界面上的旧战绩
        # 消失，否则点了没任何变化，会被当成功能坏了。
        if 'recent' in targets:
            try:
                if self._ui is not None:
                    self._ui.update_recent([])
                    self._ui.mark_recent_dirty()
                notes.append('对局记录: 本地无缓存文件(每次联网实时拉取), '
                             '已清空界面显示')
            except Exception as e:
                notes.append(f'对局记录: {type(e).__name__}')

        # ---- 统计 ----
        try:
            after = self.scan()
        except Exception:
            after = {}

        def _delta(key, idx):
            b = before.get(key, (0, 0))
            a = after.get(key, (0, 0))
            return max(0, b[idx] - a[idx])

        stat_keys = []
        if targets & {'icons', 'avatars'}:
            stat_keys += ['icons', 'avatars']
        # logs / data 不进这里 —— 用上面的就地累加值（差值法对这两类会少算）
        removed = sum(_delta(k, 0) for k in stat_keys)
        freed = sum(_delta(k, 1) for k in stat_keys)
        if 'logs' in targets:
            removed += logs_removed
            freed += logs_freed
        if 'data' in targets:
            removed += data_removed
            freed += data_freed

        # 清完顺手把版本标记对齐到当前游戏版本 —— 否则刚清空缓存、标记还写着旧版本，
        # 下次连上会再"发现版本变了"而白清一遍（多一次全量重下的日志噪音）。
        if self._sync_version is not None:
            try:
                self._sync_version()
            except Exception:
                pass
        return removed, freed, notes
