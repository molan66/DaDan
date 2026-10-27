"""IconCache —— 统一的图标缓存(内存 LRU + 磁盘 + 失败黑名单 + 下载去重)。

## 为什么有这个东西(v4.3.6 结构整理)

此前 main.py 里有**四份同构实现**,各写一遍"目录解析 → 内存命中 → 磁盘命中 →
未命中下载 → 失败拉黑 → LRU 淘汰":

    - 英雄图标  _icon_cache / _icon_cache_order / _ICON_CACHE_MAX
    - 装备图标  _item_cache / _item_cache_order / _ITEM_CACHE_MAX
    - 召唤师技能 _spell_* 六个字典
    - 海克斯强化 _aug_*   六个字典

风险不是"丑",而是: 任何一处规则变更(比如改失败阈值、加原子写、改上限)
都要记得改四个地方;漏一处,症状是"某类图标在某些机器上刷不出来" —— 这种
bug 依赖网络时序,几乎无法复现。

现在四种图标 = 四个 IconCache 实例,**key 命名空间由落盘目录名天然隔离**
(装备 id 与英雄 id 数值会重叠,如 2503/6653;技能 id 4/32;海克斯 1030…)。

## 相对旧实现的三个行为增强(都不改变正常路径的结果)

1. **原子落盘**: 先写 `.tmp` 再 `os.replace`。旧实现直接 `open(path,'wb')`
   写一半被杀进程 → 留个截断文件 → 下次读到坏数据喂给 UI → 静默空白。
2. **磁盘容量上限**: `max_disk` 张,超出按 mtime 从旧到新删。旧实现只有内存
   LRU,磁盘无限增长。
3. **PNG 魔数校验可开关**: 英雄/装备/技能都是 PNG → 强校验 + 坏文件即删;
   头像来源格式不定(jpg/png/webp) → 必须关掉。

## 线程模型

`hit()` 由主线程调用;`request()` 提交到调用方给的线程池,worker 里调
`put()`/`mark_failed()`。所有内部可变状态由 `self._lock` 串行化。
"""

import os
import sys
import threading
from typing import Callable, Optional

# v4.4.0(P2-11): 已诊断过的"目录建不出来"路径集合 —— 同目录只报一次,
# 避免四个缓存 × 每个 key 反复刷同一句。仅在 app_data_dir 里读写。
_MAKEDIRS_REPORTED: set = set()

# PNG 魔数(8950 4E47 0D0A 1A0A)。v16 起用于源头拦截"CDN 返回 HTML 错误页"
# 这类坏数据,避免把坏 bytes 喂给 QPixmap 造成"加载中"假死。
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
JPEG_MAGIC = b'\xff\xd8\xff'


def is_valid_image(data) -> bool:
    """PNG 或 JPEG 都算有效图片。

    ⚠️ **不能只认 PNG**: gtimg 的 /act/img/champion/{别名}.png 对少数英雄
    (实测 Teemo/Udyr/Viktor/Hecarim/Ambessa 共 5 个)实际返回的是 **JPEG 字节**,
    但 HTTP 头仍写 Content-Type: image/png。若只认 PNG 魔数, 这几张会被判成
    "非法内容"丢弃 → 图标永远显示不出来, 而且每轮重下重丢、刷日志。
    Qt 是按内容嗅探格式解码的(qjpeg.dll 已随包), 落盘保留原始字节即可。
    """
    if not data or len(data) < 8:
        return False
    return data[:8] == PNG_MAGIC or data[:3] == JPEG_MAGIC

# 小于这个字节数的图直接当坏数据处理。真实图标最小约 1.6KB(技能图),
# 100 字节足以滤掉空响应与 HTML 错误页,又不会误杀小图。
MIN_BYTES = 100

# v4.6.3: 资产版本标记文件名(侧车文件, 放在各缓存目录内)。
#
# 起因: 图标按纯 id 落盘({id}.png), 命中即用 —— 游戏更新换了图标后,
# 只要 id 还在缓存里就**永远不会重新下载**。实测证据: 本机 icon_cache
# 236 张的 mtime 全部停在同一天, 之后再没变过。
#
# 做法: 记住"这批图是哪个游戏版本下载的"。版本一变, 清空该目录重下。
# 用侧车文件而不是把版本写进目录名, 是为了**保持既有路径不变** ——
# 目录名一改, _get_icon_cache_dir / 头像落盘 / 各处诊断路径全要跟着动,
# 迁移风险大于收益。
VERSION_STAMP = '.assets_version'


def is_temp_file(name: str) -> bool:
    """v4.6.10: 判断文件名是不是 `_write_atomic` 留下的临时文件。

    🔴 为什么需要这个函数(实测复现的缺陷): `_write_atomic` 把临时文件命名为
    `{path}.tmp.{pid}.{tid}`(见下方 548 行), 例如 `2503.png.tmp.12345.678` ——
    它**既不以 `.tmp` 结尾、也不以 `.png` 结尾**。而原先三处"清理 .tmp"的判据
    写的都是 `endswith('.tmp')` 或把 `.tmp` 塞进图片后缀白名单里:
      · `clear_images()` —— 实测: 造一个真实命名的孤儿 tmp, 清理后它**仍然在**,
        即文档承诺的"删除残留 .tmp"是死代码;
      · `trim_disk()` 的 `if f.endswith('.tmp')` 分支**永不进入**(两个分支体
        还完全相同, 属纯死代码);
      · `main._cleanable_file()` 同样匹配不到 → 设置页「清理缓存」也删不掉。
    目前没有造成无界增长(只被 trim_disk 的总数上限间接兜住), 但"清理"名不
    副实 —— 这里统一判据, 三处共用, 杜绝再次写歪。

    判据同时接受两种形态: 中间含 `.tmp.`(当前命名) 或 以 `.tmp` 结尾(旧命名,
    兼容历史残留)。
    """
    n = name or ''
    return '.tmp.' in n or n.endswith('.tmp')


# v4.6.10: 图片类文件后缀(清理时用)。**唯一来源** ——
# 原先 `icon_cache.clear_images()` 与 `main._cleanable_file()` 各写一份,
# 且已经漂移(main 那份还含 .log/.log.1)。两处口径不一致的后果是"某一类文件
# 清点算得到、清理删不掉"这种极难复现的差异(icon_cache 的旧注释自己就写过
# 这个风险, 只是没落实到代码)。
# ⚠️ 注意: 临时文件 `{key}.png.tmp.{pid}.{tid}` **不在**这个白名单里(它不以
# 任何图片后缀结尾), 需用上面的 is_temp_file() 单独判。
IMAGE_SUFFIXES = ('.png', '.jpg', '.jpeg', '.webp', '.img')


def app_data_dir(sub: str = '') -> str:
    """exe 同级(打包后)或项目根(源码运行)的 data 目录。

    图标缓存/日志都放这里,不放用户 AppData —— 保持"绿色免安装、删目录即清空"

    v4.4.0 修复(P2-11): 原先 `os.makedirs` 失败是 `except Exception: pass`,
    完全静默 —— 只读安装目录/磁盘满/杀软拦截时, 后面所有图标写入都会失败,
    而用户侧只看到"图标一直刷不出来", 没有任何线索。现在:
      - 打**一次**明确诊断(路径 + 异常类型 + 消息), 用模块级集合去重,
        避免多线程 × 四种缓存 × 每个 key 反复刷屏;
      - 仍然返回路径(调用方继续按"可能写不进去"运行, 不改变控制流)。
    """
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    d = os.path.join(base, 'data', sub) if sub else os.path.join(base, 'data')
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        # 同一个目录只报一次(按路径去重); 用原始 stdout 直写, 避免依赖调用方的日志器
        if d not in _MAKEDIRS_REPORTED:
            _MAKEDIRS_REPORTED.add(d)
            out = sys.__stdout__
            if out:
                try:
                    out.write(f'[图标] ⚠️ 缓存目录不可用(图标将无法落盘): {d} '
                              f'→ {type(e).__name__}: {e}\n')
                except Exception:
                    pass
    return d


class IconCache:
    """一种图标的缓存。四种图标 = 四个实例,行为完全一致。

    参数
    ----
    dir_name : 落盘子目录名(data/<dir_name>/),同时是 key 的命名空间隔离手段
    max_mem  : 内存最多缓存多少张(LRU 淘汰)
    max_disk : 磁盘最多保留多少张(按 mtime 淘汰,0 = 不限)
    log_tag  : 日志前缀,如 '[装备]'
    check_image: 是否校验内容确实是图片(PNG/JPEG 魔数)。英雄/装备/技能 True,
        头像 False。⚠️ 不要改成"只认 PNG" —— 见 is_valid_image 的说明。
    blacklist: 下载失败是否永久拉黑该 key。
        ⚠️ **英雄图标必须传 False**: 客户端没启动时 download_champion_icon
        返回 None, 若拉黑, 之后客户端连上了这批 id 也永远取不到图(整个会话内
        再也刷不出来)。黑名单只适用于"这个 id 在数据源里根本不存在"的场景 ——
        比如大乱斗/竞技场那批 6 位特殊装备 id, 以及表里查不到的法术/海克斯 id。
    """

    def __init__(self, dir_name: str, max_mem: int, max_disk: int = 0,
                 log_tag: str = '[图标]', check_image: bool = True,
                 blacklist: bool = False):
        self.dir_name = dir_name
        self.max_mem = max_mem
        self.max_disk = max_disk
        self.log_tag = log_tag
        self.check_image = check_image
        self.blacklist = blacklist

        self._dir = app_data_dir(dir_name)
        self._mem: dict = {}
        self._order: list = []          # LRU 顺序(队首最旧)
        self._downloading: set = set()
        self._failed: set = set()
        self._lock = threading.Lock()
        # v4.4.0 新增(P2-11): 落盘失败的**可查询**证据。
        # 原先 _write_atomic 静默失败, 用户只看到"图标刷不出来"却没有任何线索。
        # write_fail_count / write_fail_reason 是公开属性(诊断用), 测试与 UI 可读。
        self.write_fail_count = 0
        self.write_fail_reason: Optional[str] = None
        self._write_fail_reported: set = set()  # 已打印过的异常描述(去重)

    # ---------------- 路径 ----------------

    @property
    def dir(self) -> str:
        return self._dir

    def _path(self, key: int, ext: str = '.png') -> str:
        return os.path.join(self._dir, f'{key}{ext}')

    # ---------------- 读 ----------------

    def hit(self, key) -> Optional[bytes]:
        """内存 → 磁盘。命中即入内存 LRU。任何异常都返回 None(调用方按未命中处理)。"""
        try:
            key = int(key)
        except (TypeError, ValueError):
            return None
        if key <= 0:
            return None
        with self._lock:
            data = self._mem.get(key)
            if data:
                # v4.3.10 修复: 命中要把 key 移到队尾, 否则 _order 只是"插入顺序"
                # (FIFO) —— 热点图标(例如每局都在用的几个英雄)反而会先被淘汰,
                # 与注释声称的 LRU 语义不符。
                try:
                    self._order.remove(key)
                except ValueError:
                    pass
                self._order.append(key)
                return data
        # 读盘放在锁外: 几百 KB 的 IO 不该阻塞其它 worker
        path = self._path(key)
        try:
            if not os.path.exists(path):
                return None
            with open(path, 'rb') as f:
                head = f.read(8)
                if self.check_image and not is_valid_image(head):
                    bad = True
                else:
                    f.seek(0)
                    data = f.read()
                    bad = False
        except Exception:
            return None
        if bad:
            # ⚠️ 必须在 with 块**之外**删: Windows 上持有打开的句柄时
            # os.remove 会抛 PermissionError(共享冲突), 这里若不先关闭
            # 就删, 坏文件会原地留着、每次命中都白读一遍。
            self._discard_bad_file(path)
            return None
        if not data or len(data) < MIN_BYTES:
            return None
        self.put(key, data, persist=False)
        return data

    def is_failed(self, key) -> bool:
        """该 key 是否在失败黑名单里。

        v4.6.10 修: 原先只有一行 `return int(key) in self._failed` —— 既**不加锁**
        (而兄弟方法 is_downloading/has 都加了, `_failed` 又是被多线程写集合),
        也**没有类型保护**(传个非数字字符串会直接抛 ValueError)。
        当前两个调用点恰好都先 int() 过, 所以没有现场故障, 但接口不对称 ——
        下一个调用者很容易踩。这里照 is_downloading 的三段式对齐。
        """
        try:
            key = int(key)
        except (TypeError, ValueError):
            return False
        with self._lock:
            return key in self._failed

    def is_downloading(self, key) -> bool:
        """该 key 是否有下载正在后台进行。

        v4.3.10 新增: 给 UI 侧的"失败计数"用 —— 只有当确实没有下载在途、
        却依然拿不到图时, 才算一次真失败。慢速下载(单张 >8s)不该被误判成失败。
        """
        try:
            key = int(key)
        except (TypeError, ValueError):
            return False
        with self._lock:
            return key in self._downloading

    # v4.6.11 删除 has(): 全项目零调用。request() 内的"命中就别再提交下载"是
    # 直接 hit() 实现的, 这个"仅内存命中"的窄版本从未被用上。

    # ---------------- 写 ----------------

    def put(self, key: int, data: bytes, persist: bool = True) -> None:
        """写内存(LRU)。persist=True 时同时原子落盘。"""
        if not data or len(data) < MIN_BYTES:
            return
        key = int(key)
        with self._lock:
            if len(self._mem) >= self.max_mem and self._order:
                old = self._order.pop(0)
                self._mem.pop(old, None)
            self._mem[key] = data
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)
        if persist:
            # v4.4.0(P2-11): 忽略返回值即可 —— 内存已写入, 落盘失败不影响本进程
            # 显示; 失败细节通过 self.write_fail_count/_reason 查询。
            self._write_atomic(self._path(key), data)

    def mark_failed(self, key) -> None:
        """加入失败黑名单,防"表里查不到的 id"被轮询反复空打网络。

        表本身有上限(500),超了整表清空 —— 因为失败往往来自服务端临时抖动,
        永久记仇会误伤后来可用的 id。
        **blacklist=False 的缓存是空操作**(见构造函数说明)。
        """
        if not self.blacklist:
            return
        key = int(key)
        with self._lock:
            if len(self._failed) > 500:
                self._failed.clear()
            self._failed.add(key)

    # ---------------- 下载 ----------------

    def request(self, key, fetch: Callable[[int], Optional[bytes]],
                on_ready: Optional[Callable[[int, bytes], None]] = None,
                pool=None, log_errors: bool = True) -> bool:
        """主线程: 命中直接回调;未命中且未在下载 → 提交线程池。

        返回 True 表示"已有数据/已排队",False 表示"已拉黑或参数非法"。
        """
        try:
            key = int(key)
        except (TypeError, ValueError):
            return False
        if key <= 0 or key in self._failed:
            return False
        if self.hit(key) is not None:
            return True
        with self._lock:
            if key in self._mem or key in self._downloading:
                return True
            self._downloading.add(key)
        if pool is None:
            with self._lock:
                self._downloading.discard(key)
            return False
        try:
            pool.submit(self._fetch_worker, key, fetch, on_ready, log_errors)
        except Exception as e:
            with self._lock:
                self._downloading.discard(key)
            print(f'{self.log_tag} 下载任务提交失败 key={key}: '
                  f'{type(e).__name__}: {e}')
            return False
        return True

    def _fetch_worker(self, key, fetch, on_ready, log_errors) -> None:
        """线程池执行: fetch → 校验 → 缓存 → (可选)回调。失败则按需拉黑。"""
        ok = False
        try:
            data = fetch(key)
            if data and len(data) >= MIN_BYTES:
                if not self.check_image or is_valid_image(data):
                    self.put(key, data)
                    ok = True
                    if on_ready:
                        on_ready(key, data)
                else:
                    if log_errors:
                        tail = ('(已加入黑名单)' if self.blacklist
                                else '(本次丢弃, 下次仍会重试)')
                        print(f'{self.log_tag} 下载内容不是有效图片 key={key}{tail}')
            else:
                # blacklist=False 时这是"暂时取不到"(如客户端未启动)而非
                # "此 id 不存在" —— 只是这次没拿到, 下次还会再试, 不必刷屏。
                if log_errors and self.blacklist:
                    print(f'{self.log_tag} 无图源 key={key}(已加入黑名单)')
        except Exception as e:
            if log_errors:
                print(f'{self.log_tag} 下载失败 key={key}: '
                      f'{type(e).__name__}: {e}')
        finally:
            with self._lock:
                self._downloading.discard(key)
            if not ok:
                self.mark_failed(key)

    # ---------------- 预热与维护 ----------------

    def prewarm(self, limit: int = 0) -> int:
        """启动期把磁盘缓存读进内存,让首帧就有图。

        锁外读盘、锁内写内存,避免与下载 worker 竞态。
        """
        added = 0
        try:
            if not os.path.isdir(self._dir):
                return 0
            entries = []
            for f in os.listdir(self._dir):
                if not f.endswith('.png'):
                    continue
                try:
                    key = int(f[:-4])
                except (ValueError, TypeError):
                    continue
                if key <= 0:
                    continue
                path = os.path.join(self._dir, f)
                try:
                    with open(path, 'rb') as fh:
                        if self.check_image:
                            if not is_valid_image(fh.read(8)):
                                bad_path = path
                                data = None
                            else:
                                fh.seek(0)
                                data = fh.read()
                                bad_path = None
                        else:
                            data = fh.read()
                            bad_path = None
                    # ⚠️ v4.3.10: 删除必须放在 with **块外** —— 与 hit() 同理,
                    # Windows 上句柄未关时 os.remove 会抛 PermissionError,
                    # 而 _discard_bad_file 吞掉 OSError → 坏文件原地留着,
                    # 每次启动都白读一遍(prewarm 原来就踩了这个坑)。
                    if bad_path:
                        self._discard_bad_file(bad_path)
                        continue
                    if data and len(data) >= MIN_BYTES:
                        entries.append((key, data))
                except Exception:
                    continue
                if limit and len(entries) >= limit:
                    break
            for key, data in entries:
                with self._lock:
                    if key in self._mem:
                        continue
                    if len(self._mem) >= self.max_mem and self._order:
                        old = self._order.pop(0)
                        self._mem.pop(old, None)
                    self._mem[key] = data
                    self._order.append(key)
                    added += 1
        except Exception as e:
            print(f'{self.log_tag} 预热失败: {type(e).__name__}: {e}')
        return added

    # ---------------- v4.6.3: 资产版本 ----------------

    @property
    def _stamp_path(self) -> str:
        return os.path.join(self._dir, VERSION_STAMP)

    def assets_version(self) -> str:
        """读回"本目录里的图是哪个游戏版本下的"。没记录过返回 ''。"""
        try:
            with open(self._stamp_path, encoding='utf-8') as f:
                return (f.read() or '').strip()
        except Exception:
            return ''

    def apply_assets_version(self, version: str) -> int:
        """版本对齐: 与记录一致 → 什么都不做; 不一致 → 清空本目录并记新版本。

        返回被删掉的图片数(0 = 无需清理)。

        ⚠️ 三条安全约束(每条都对应一个真实踩过的坑):
        1. `version` 为空 → **直接返回 0, 不清任何东西**。
           客户端没启动时 get_game_version() 拿不到版本, 若把它当成"版本变了",
           每次离线启动都会把好端端的缓存清空, 反而制造故障。
        2. 首次运行(无标记) → **只记版本, 不清**。
           老用户升到本版本时目录里已有可用图标, 没有理由让他们重下。
           真正要防的是"记着 16.20 却来了 16.21"。
        3. 清空前先比对, 一致就短路 —— 这是常态路径(每次启动都会调),
           必须零开销。
        """
        v = (version or '').strip()
        if not v:
            return 0
        old = self.assets_version()
        if old == v:
            return 0
        removed = 0
        if old:
            removed = self.clear_images()
            print(f'{self.log_tag} 游戏版本 {old} → {v}, 已清理旧图标 {removed} 张'
                  f'({self.dir_name}), 将按需重新下载')
        if self._write_atomic(self._stamp_path, v.encode('utf-8')):
            pass
        else:
            # 标记写不进去(只读目录等): 不致命, 但下次启动还会再判一次"变了"。
            # 记一条即可 —— 这属于环境异常, 与图标功能本身无关。
            print(f'{self.log_tag} ⚠️ 资产版本标记写入失败({self.dir_name}), '
                  f'下次启动会重新判定')
        return removed

    def clear_images(self) -> int:
        """删除本目录里所有图片与残留 .tmp, 保留目录本身。返回删除数。

        v4.6.3: 供"版本变化自动清理"与设置页「清理缓存」共用 ——
        两处必须同一套实现, 否则将来一处改了另一处漏改, 表现是
        "手动清能清掉、自动清清不干净"这种极难复现的差异。

        只删本目录内的常规文件, 不递归、不跟随符号链接 —— 目录由
        app_data_dir 拼出, 永远不会是用户指定的任意路径。
        顺带把内存缓存一并清空, 否则"磁盘删了、内存还留着旧图",
        界面在本次会话里仍显示旧图标(与版本清理的初衷矛盾)。
        """
        removed = 0
        try:
            for f in os.listdir(self._dir):
                p = os.path.join(self._dir, f)
                try:
                    if not os.path.isfile(p):
                        continue
                    # v4.6.10 修: 临时文件判据独立 —— 实测 `{key}.png.tmp.{pid}.{tid}`
                    # 既不匹配图片后缀、也不 endswith('.tmp'), 原先这里根本删不掉它
                    # (文档却承诺"删除残留 .tmp")。现用 is_temp_file() 显式覆盖。
                    if is_temp_file(f) or f.lower().endswith(IMAGE_SUFFIXES):
                        os.remove(p)
                        removed += 1
                except OSError:
                    continue
        except Exception:
            pass
        with self._lock:
            self._mem.clear()
            self._order.clear()
            self._failed.clear()
        return removed

    def trim_disk(self) -> int:
        """按 mtime 保留最新 max_disk 张,返回删除数。启动后调一次即可。

        v4.4.2(P1-9): 过期 .tmp 一并清理 —— 进程被强杀(任务管理器/断电)时
        `_write_atomic` 的 except 分支来不及跑, 残留 `{key}.png.tmp` 会永久累积、
        无上限。tmp 名带 pid+线程 id, 属"不再可能被恢复"的孤儿文件, 按 mtime
        一并删除。

        v4.6.8: 全程持锁。原实现**无锁**(只有 put/hit/request 加锁), 而 main.py
        在提交预热任务时注释声称"IconCache 内部锁串行化" —— 注释与实现不符。
        真实窗口: 预热线程在这里 listdir/删文件, 同时下载线程在 put() 落盘,
        两者并发操作同一目录。危害有限(最坏是刚下好的图被当"最旧"删掉, 下次
        重新下载), 但既然对外承诺了串行化就该兑现 —— 而且本函数只做
        listdir/getmtime/remove, 持锁时间在毫秒级, 不会拖慢下载。
        锁内**没有**再获取同一把锁的调用, 无死锁风险。
        """
        if not self.max_disk:
            return 0
        removed = 0
        try:
            with self._lock:
                files = []
                for f in os.listdir(self._dir):
                    # v4.6.3: **必须排除资产版本标记** —— 它是最早写入的文件,
                    # 按 mtime 淘汰时必然排在最前, 不排除的话每轮清理都会先把它删掉,
                    # 于是下次启动又判成"首次运行/版本未知", 版本感知直接失效。
                    if f == VERSION_STAMP:
                        continue
                    p = os.path.join(self._dir, f)
                    try:
                        if not os.path.isfile(p):
                            continue
                        # v4.6.10 修死代码: 原先是
                        #     if f.endswith('.tmp'): files.append(...)
                        #     else:                  files.append(...)
                        # 两个分支体**完全相同**, 且 if 的条件**永不为真** ——
                        # _write_atomic 写的是 `{path}.tmp.{pid}.{tid}`, 不以 `.tmp`
                        # 结尾(实测确认)。也就是说注释声称的"tmp 与正常文件同按
                        # mtime 淘汰"结论没错, 但靠的是 else 兜底, if 是纯死代码。
                        # 现在正常文件与临时文件一视同仁地纳入 mtime 排序。
                        files.append((os.path.getmtime(p), p))
                    except OSError:
                        continue
                if len(files) <= self.max_disk:
                    return 0
                files.sort()
                for _, p in files[:len(files) - self.max_disk]:
                    try:
                        os.remove(p)
                        removed += 1
                    except OSError:
                        continue
        except Exception:
            pass
        return removed

    # ---------------- 内部 ----------------

    def _write_atomic(self, path: str, data: bytes) -> bool:
        """先写 .tmp 再 os.replace —— 杜绝"写一半被中断"留下坏文件。

        v4.4.0 修复(P2-11): 原先整条路径静默 —— 磁盘满/目录只读/杀软锁文件时
        图标写不进去, 用户只看到"图标一直刷不出来", 日志里一行都没有。
        现在:
          - 失败时记录可查询的原因与次数(self.write_fail_count / write_fail_reason),
            供 UI 或诊断脚本取用;
          - 打一次诊断(按异常类型去重), 不重复刷屏;
          - **返回 bool**, 调用方(put)可自行决定是否降级 —— 但不抛异常,
            保持"落盘失败绝不影响主流程"的既有契约(见文件头设计取舍)。
        成功路径的行为完全不变(仍 .tmp + fsync + os.replace, 仍在 except 里
        尽力清理残留 .tmp, 且删除动作在 with 块之外 —— Windows 句柄未释放时
        块内删除会失败)。
        v4.4.2(P1-9): tmp 名带 pid+线程 id —— 两个 exe 同时写同一 key 时不再
        互相覆盖对方的 .tmp 再 os.replace(可能落半截文件)。
        """
        tmp = f'{path}.tmp.{os.getpid()}.{threading.get_ident()}'
        try:
            with open(tmp, 'wb') as f:
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp, path)
            return True
        except Exception as e:
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass
            self.write_fail_count += 1
            self.write_fail_reason = f'{type(e).__name__}: {e}'
            # 同一种异常只报一次, 否则每种图标 × 每个 key 都会刷一行
            if self.write_fail_reason not in self._write_fail_reported:
                self._write_fail_reported.add(self.write_fail_reason)
                out = sys.__stdout__
                if out:
                    try:
                        out.write(f'{self.log_tag} ⚠️ 图标落盘失败'
                                  f'({self.dir_name}, 第{self.write_fail_count}次, '
                                  f'同类异常只报一次): {self.write_fail_reason}\n')
                    except Exception:
                        pass
            return False

    @staticmethod
    def _discard_bad_file(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass


def prewarm_all(caches, log_prefix: str = '[App]') -> int:
    """批量预热(启动期调用一次)。返回四类缓存预热成功的总数。

    v4.3.10 修复: 原实现没有 return → `added = prewarm_all(...)` 恒为 None,
    于是 main.py 里那句 `if added: print('预热图标缓存: 共 N 张')` **永不打印**
    (交付版日志里只有逐目录行, 可以印证)。调用方要拿它做判断, 这里必须返回。
    """
    total = 0
    for c in caches:
        n = c.prewarm()
        if n:
            print(f'{log_prefix} 预热缓存 {c.dir_name}: {n} 张 <- {c.dir}')
        total += n
    for c in caches:
        n = c.trim_disk()
        if n:
            print(f'{log_prefix} 清理超额缓存 {c.dir_name}: 删除 {n} 张')
    return total
