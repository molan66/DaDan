"""Logger - 捕获 print 输出到 GUI 日志面板 + 轮转落盘。

为什么落盘(v4.3.6)
------------------
此前日志只活在 GUI 面板的内存缓冲里(`_BUFFER_MAX = 200` 条)。用户报 bug 时
截图给你看, 你能看到的就 200 行、且被每 3 秒一轮的探活日志刷掉。
v4.3.5 排查"换号后战绩不刷新"时, 最终是靠**实连去查 LCU 原始返回**才定位的
—— 如果当时有落盘日志, 用户在那边就能直接确认"最新一局是什么时候的"。

设计取舍
--------
- 写到 `data/logs/app.log`(exe 同级), 不写 AppData —— 保持"绿色免安装、
  删目录即清空"。用户自己也能直接翻到这个文件。
- **轮转但静默**: 512KB × 2 个文件。不弹提示、不在界面显示路径, 避免打扰;
  只在用户主动找日志时它就在那儿。旧文件是 `app.log.1`。
- **只在捕获开启时才写**: `LogCapture.start()` 之后才落盘, 与 GUI 面板同源,
  不会多出一份"用户看不见但一直在长"的文件。
- 落盘失败**绝不能影响主流程**(磁盘满/目录只读都要静默降级), 所以整条写路径
  包在 try 里; 但降级要**打一次标记**, 免得你以为有日志其实没有。
"""

import os
import re
import sys
import threading
from collections import deque
from typing import Callable, Optional

# 单文件上限。512KB 约等于数千条探活日志 —— 足够覆盖一次
# "启动 → 打完一局 → 报 bug"的完整过程, 又不至于让目录失控。
# v4.4.2(P2-10): 原 LOG_BACKUPS 常量定义后零引用(轮转只保留一份备份,
# 见 _rotate_locked 的 app.log → app.log.1), 删掉避免误导。
LOG_MAX_BYTES = 512 * 1024

# v4.4.0 新增(P2-9): 落盘日志脱敏。
# ---------------------------------------------------------------
# 事故背景: main.py 的小窗日志面板对显示内容做了过滤(_HIDE_PATTERNS 里的
# 'token=' / 'port='), 但**落盘**这一路是逐字写的 —— 于是用户名/密码/令牌
# 会明文留在 data/logs/app.log 里, 而用户报 bug 时第一件事就是把这个文件发出来。
# 小窗看着"干净"反而让人以为日志里也没有敏感值, 更危险。
#
# 设计约束(与审查报告一致):
#   1) 只作用于**落盘**路径(_to_file)。self._buffer(供 GUI 读)和 _callbacks
#      (推给界面)拿到的仍是原文 —— 脱敏不该改变用户在界面看到的内容。
#   2) 做成**模块级纯函数**(无 self、无 IO、无全局状态), 可直接单测。
#   3) 不误伤正常文本: 特别地 `port=` 不是敏感值, **保持不动**。
# ---------------------------------------------------------------

# Authorization 头 —— LCU 的 Riot 客户端令牌就是 riot:<password> 的 base64。
# v4.4.2(P2-45) 补齐三种漏网形态:
#   - `Authorization: Basic <b64>`（原只认这个）
#   - `Authorization: Bearer <token>` / `Authorization=Basic <b64>`（等号形态）
#   - Python repr / JSON 形态 `{'Authorization': 'Basic <b64>'}`（键带引号 + 值带引号）
# 值分四支: 引号包裹(整串吃掉) / Basic|Bearer 后跟 token / 裸 token。
_RE_AUTH = re.compile(
    r'(?i)(?P<pre>["\']?Authorization["\']?\s*[=:]\s*)'
    r'(?:["\'](?P<q>[^"\']*)["\']'
    r'|(?P<scheme>Basic|Bearer)\s+(?P<rest>\S+)'
    r'|(?P<plain>\S+))')


def _redact_auth(match: 're.Match') -> str:
    """把 Authorization 值脱敏, 保留键头与(可选的)Basic/Bearer 方案名。"""
    pre = match.group('pre')
    if match.group('q') is not None:
        # 引号包裹的整段值(含 Basic xxx)整体抹掉, 引号一并去掉避免留半句
        return f'{pre}***'
    scheme = match.group('scheme')
    if scheme:
        return f'{pre}{scheme} ***'
    return f'{pre}***'


# riot:<令牌> —— LCU 命令行/日志里常见的明文形态(exe 参数、连接日志)。
# v4.4.2(P2-45): 原字符类 [A-Za-z0-9._\-]{4,} 不含 base64 的 + / / =,
# 导致 `riot:AbCd+Ef/Gh==` 只抹掉前 4 字符、`+Ef/Gh==` 残尾留在明文。补全 base64
# 字符集并靠 `{4,}` 最少长度避免把短串当令牌。
_RE_RIOT_AUTH = re.compile(r'(?i)\briot:([A-Za-z0-9+/=]{4,})')

# lockfile 四段形态 `LeagueClient:<pid>:<port>:<password>` —— 明文密码在末段。
_RE_LOCKFILE = re.compile(r'(?i)\b(LeagueClient:\d+:\d+:)([A-Za-z0-9+/=]+)')

# 空格分隔的命令行令牌 `--remoting-auth-token <t>` / `--riotclient-auth-token <t>`。
# 等号形态 `--remoting-auth-token=<t>` 由 _RE_KV_SECRET 的 `token` 键兜住, 这里只补
# 空格形态(键与值之间没有 [=:] 分隔符, KV 表吃不到)。
_RE_CLI_TOKEN = re.compile(
    r'(?i)(--remoting-auth-token|--riotclient-auth-token)\s+(\S+)')

# key=value / key: value 形态的凭据。注意这里**只列真正敏感的键**,
# port / hwid / pid 之类一律不列(port 是用户排查连接问题的关键信息)。
# v4.4.2(P2-45): 键两侧允许成对引号(JSON 的 `{"token": ...}` 与 repr 的
# `{'token': ...}`), 用命名组 + 反向引用 `(?P=q)` 保证前后引号成对。
# 值分三支: 双引号包裹 / 单引号包裹 / 裸值 —— 分开写才能正确吃掉成对的引号
# (若只写一个可选的引号前缀, `token="abc"` 会脱敏成 `token="***""`)。
# 用**命名组**: 值那一支是 (?:...) 非捕获组, 若靠编号取组极易数错(本文件
# v4.4.0 初版就写成 group(3) 而实际只有两个组, 直接 IndexError)。
_RE_KV_SECRET = re.compile(
    r'(?i)(?P<q>["\']?)\b(?P<key>remoting[-_]auth[-_]token|riotclient[-_]auth[-_]token|'
    r'token|access_token|accessToken|refresh_token|refreshToken|'
    r'password|passwd|pwd|client_secret|clientSecret|api_key|apikey)\b(?P=q)'
    r'(?P<sep>\s*[=:]\s*)'
    r'(?P<val>"[^"]*"|\'[^\']*\'|[^\s,;)\]}"\']+)')


def _redact_kv(match: 're.Match') -> str:
    """把 `token=abc` / `token="abc"` 还原成 `token=***` / `token="***"`,
    保留键名、分隔符与包裹引号, 便于阅读时仍知道"这里原本有个令牌"。"""
    q = match.group('q') or ''
    key, sep, value = (match.group('key'), match.group('sep'),
                       match.group('val'))
    if value[:1] in ('"', "'"):
        return f'{q}{key}{q}{sep}{value[0]}***{value[0]}'
    return f'{q}{key}{q}{sep}***'


def redact_sensitive(text: str) -> str:
    """抹掉一行日志里的凭据(纯函数, 可单测)。

    >>> redact_sensitive('token=abc123')
    'token=***'
    >>> redact_sensitive('Authorization: Basic eHg6')
    'Authorization: Basic ***'
    >>> redact_sensitive('port=54321')      # 端口不敏感, 原样保留
    'port=54321'

    幂等: 已脱敏的 `token=***` 再跑一次仍是 `token=***`(星号被当值再抹一遍)。

    v4.4.2(P2-45): 补齐四种漏网形态 —— Authorization 的引号/Bearer/等号变体、
    riot:<token> 的 base64 全字符集(+ / =)、lockfile 四段明文密码、
    空格分隔的命令行令牌。
    """
    if not text:
        return text
    out = _RE_AUTH.sub(_redact_auth, text)
    out = _RE_RIOT_AUTH.sub('riot:***', out)
    out = _RE_LOCKFILE.sub(r'\1***', out)
    out = _RE_CLI_TOKEN.sub(r'\1 ***', out)
    out = _RE_KV_SECRET.sub(_redact_kv, out)
    return out


def _default_log_dir() -> str:
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'data', 'logs')


class LogCapture:
    def __init__(self, log_dir: Optional[str] = None):
        self._callbacks: list = []
        self._orig_out = None
        self._orig_err = None
        self._active = False
        # v4.4.0 修复(P2-10): 原先用 list + `if len >= MAX: pop(0)` 手动淘汰。
        # list.pop(0) 是 O(n) —— 每来一条日志就要把 199 个元素整体前移,
        # 而 print 在探活/WS/下载池多线程里高频调用, 等于把一次内存搬运
        # 摊在每个日志点上。deque(maxlen=200) 满员后自动丢弃最旧的, O(1)。
        self._buffer = deque(maxlen=200)
        self._BUFFER_MAX = 200  # 最多保留200条日志(与上面 maxlen 保持一致)
        # --- 落盘(v4.3.6) ---
        self._log_dir = log_dir
        self._log_path: Optional[str] = None
        self._fh = None
        self._file_lock = threading.Lock()
        self._file_size = 0
        self._file_failed = False   # 落盘不可用(只读目录/磁盘满) → 静默降级

    # ---------------- GUI 面板 ----------------

    def add_callback(self, cb: Callable[[str], None]):
        self._callbacks.append(cb)

    def start(self):
        if self._active:
            return
        self._active = True
        # v4.4.2(P2-11): 重入(如 stop() 后又 start())时复位落盘失败标记 —— 否则
        # 上一轮因磁盘满/目录只读置的 _file_failed=True 会一直黏着, 本轮即使
        # 目录已恢复可写, _to_file 也会被 `if self._file_failed: return` 永久挡下。
        self._file_failed = False
        self._open_log_file()
        self._orig_out = sys.stdout
        self._orig_err = sys.stderr
        sys.stdout = self._Writer(self._orig_out, self._write)
        sys.stderr = self._Writer(self._orig_err, self._write)

    def stop(self):
        """停止捕获: 还原 sys.stdout/stderr 并关闭日志文件。

        v4.4.0(P2-10) 契约说明(管理员交接路径依赖它):
        - 还原**幂等**: 无论调用几次, 最终 sys.stdout/stderr 都指回真实原始流;
        - 原始流缺失时兜底到 sys.__stdout__/__stderr__, 绝不把 _Writer 留在
          sys.stdout 上 —— 否则交接后新进程/后续代码写出的东西会进到一个
          已关闭文件的捕获器里(或被静默丢弃);
        - 关闭文件句柄, 让 Windows 能立刻删除/重命名 app.log。
        注意: main.py 的 _quit() 目前**没有**调本方法(见交付报告的协调事项),
        所以这里不依赖"一定有人调"来保证落盘完整 —— _to_file 每行都 flush。
        """
        if not self._active:
            return
        self._active = False
        # 只在"当前 stdout 确实是我们装的 _Writer"时才还原, 避免把别人
        # (如 pytest 的 capsys、logging 的 redirect)替换过的流覆盖掉。
        if isinstance(getattr(sys, 'stdout', None), LogCapture._Writer):
            sys.stdout = self._orig_out or sys.__stdout__
        if isinstance(getattr(sys, 'stderr', None), LogCapture._Writer):
            sys.stderr = self._orig_err or sys.__stderr__
        self._close_log_file()

    def _write(self, text: str):
        if not text or text == '\n':
            return
        msg = text.rstrip()
        if not msg:
            return
        # v4.4.0(P2-10): deque(maxlen) 自动淘汰最旧, 不需要手动 pop(0)。
        # (仍然保留 _BUFFER_MAX 兜底: 万一有人把 _buffer 换回 list, 这里也不会无限涨。)
        if self._buffer.maxlen is None and len(self._buffer) >= self._BUFFER_MAX:
            self._buffer.popleft()
        self._buffer.append(msg)
        # v4.4.0 修复(P2-9): 只有**落盘**这一路做脱敏; 上面的 _buffer 与下面的
        # 回调拿到的都是原文, 保证 GUI 显示内容与改动前完全一致。
        self._to_file(redact_sensitive(msg))
        for cb in self._callbacks:
            try:
                cb(msg)
            except Exception:
                pass

    # ---------------- 轮转落盘 ----------------

    def _open_log_file(self) -> None:
        """打开日志文件。失败只打一次提示, 之后静默(不影响主流程)。"""
        try:
            d = self._log_dir or _default_log_dir()
            os.makedirs(d, exist_ok=True)
            self._log_path = os.path.join(d, 'app.log')
            self._file_size = (os.path.getsize(self._log_path)
                               if os.path.exists(self._log_path) else 0)
            self._fh = open(self._log_path, 'a', encoding='utf-8')
        except Exception as e:
            self._fh = None
            self._file_failed = True
            # 用原始 stdout 打印, 避免递归进 _write
            out = self._orig_out or sys.__stdout__
            if out:
                out.write(f'[日志] 落盘不可用(仅界面显示): '
                          f'{type(e).__name__}: {e}\n')

    def _close_log_file(self) -> None:
        with self._file_lock:
            if self._fh:
                try:
                    self._fh.flush()
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None

    def _to_file(self, msg: str) -> None:
        if self._file_failed:
            return
        if self._fh is None:
            # v4.4.2(P0-1): 轮转失败后 _fh 可能为 None 但非永久失败 —— 尝试重开自愈
            # (磁盘满/杀软锁是暂时性的, 恢复后应能继续写)。失败则下次写入再试。
            self._try_reopen()
            if self._fh is None:
                return
        line = msg + '\n'
        try:
            data = line.encode('utf-8')
        except Exception:
            return
        with self._file_lock:
            try:
                self._fh.write(line)
                self._fh.flush()
                self._file_size += len(data)
                if self._file_size >= LOG_MAX_BYTES:
                    self._rotate_locked()
            except Exception as e:
                # 写失败(磁盘满/文件被删) → 静默降级, 不死循环报错
                self._file_failed = True
                # v4.4.0 修复(P2-9 同族): 原先这里是**完全静默**的 —— windowed
                # 打包(console=False)后用户看不到任何提示, 会以为"日志出问题了",
                # 实际是磁盘满了。打一次诊断(直写原始流, 不走 _write, 避免递归),
                # 且 _file_failed 已置位, 后续不会再重复刷屏。
                out = self._orig_out or sys.__stdout__
                if out:
                    try:
                        out.write(f'[日志] 落盘中断(仅界面显示): '
                                  f'{type(e).__name__}: {e}\n')
                    except Exception:
                        pass

    def _rotate_locked(self) -> None:
        """调用方必须已持有 _file_lock。app.log → app.log.1(覆盖旧备份)。"""
        try:
            if self._fh:
                self._fh.close()
                self._fh = None
            if not self._log_path:
                return
            backup = self._log_path + '.1'
            if os.path.exists(backup):
                os.remove(backup)
            if os.path.exists(self._log_path):
                os.replace(self._log_path, backup)
            self._fh = open(self._log_path, 'a', encoding='utf-8')
            self._file_size = 0
        except Exception as e:
            # v4.4.2(P0-1): 轮转失败**不再**把 _file_failed 置 True —— 磁盘满/杀软锁
            # 是暂时性的, 置永久失败会让 app.log 从此停写且再无任何诊断。改为:
            # 只关掉句柄、置 _fh=None, 下次 _to_file 会走 _try_reopen() 重试自愈,
            # 并直写一次原始流诊断(不走 _write, 避免递归)。
            self._fh = None
            out = self._orig_out or sys.__stdout__
            if out:
                try:
                    out.write(f'[日志] 轮转失败(将自动重试): '
                              f'{type(e).__name__}: {e}\n')
                except Exception:
                    pass

    def _try_reopen(self) -> None:
        """P0-1: 轮转失败/句柄丢失后尝试重开 app.log, 让暂时性失败能自愈。

        不置 _file_failed(那会让永久停写); 每次写入前由 _to_file 调用,
        成功即恢复落盘, 失败则下次写入再试。调用方已持有 _file_lock 时
        传入的锁状态不变 —— 本方法自行加锁, 避免与 _to_file 的锁嵌套死锁:
        这里用非阻塞 acquire, 抢不到锁就放弃本次重开(下次再试)。
        """
        if not self._log_path:
            return
        if not self._file_lock.acquire(blocking=False):
            return
        try:
            if self._fh is not None:
                return
            try:
                self._fh = open(self._log_path, 'a', encoding='utf-8')
                # 重开成功后按文件实际大小重算, 避免轮转阈值判断失真
                self._file_size = os.path.getsize(self._log_path)
            except Exception:
                self._fh = None
        finally:
            self._file_lock.release()

    @property
    def log_path(self) -> Optional[str]:
        """当前日志文件路径(None = 落盘不可用)。供"导出日志"之类的功能用。"""
        return self._log_path

    class _Writer:
        def __init__(self, orig, callback):
            self._orig = orig
            self._cb = callback

        def write(self, text):
            if self._orig:
                self._orig.write(text)
                self._orig.flush()
            self._cb(text)

        def flush(self):
            if self._orig:
                self._orig.flush()

        def isatty(self):
            # v4.4.2(P2-10): 有意保留(协议方法) —— _Writer 替换 sys.stdout/stderr
            # 后, 第三方库可能调用 sys.stdout.isatty() 决定是否输出 ANSI 颜色;
            # 项目内零引用, 但删掉会让这类调用 AttributeError。
            return False


log_capture = LogCapture()
