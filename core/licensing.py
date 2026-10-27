# -*- coding: utf-8 -*-
"""Licensing - 离线授权（机器绑定 + 授权码验签），**无服务器**

## 为什么是这套设计（结论先行）

打包出来的 exe 里，PYZ 可被 PyInstaller 自带读取器整块提取、
`marshal.loads` 就能还原 code object（已在本项目产物上实测）。因此：

  · **程序里不能有"能用来自制授权码"的秘密**。任何对称密钥 / HMAC 密钥
    写死在 exe 里 = 等于公开，买家掏出密钥就能批量造码。

故采用**非对称签名**：私钥只在作者本机（tools/make_license.py），
exe 里只有公钥。公钥泄露无害 —— 没有私钥就签不出有效码。

## 边界（诚实说明，不要当成"绝对防破解"）

本地校验一定能被 patch 掉（改掉那个 `if` 就行）。本方案的目标是把
「随手转发文件」变成「必须动手逆向」，**防的是转发，不是专业破解**。
真正防破解需要把核心逻辑加密、密钥由授权码派生，那是另一个量级的改造。

## 机器指纹：为什么不用 MAC 地址

作者本机实测有 **2 块**物理网卡。USB 网卡、虚拟机、VPN 都会让 MAC 漂移，
一旦列为强绑定项就会大量误判、直接砸售后。故只用主板/CPU/硬盘三项，
并且采用「**非零槽位中匹配 ≥ 2 项即通过**」的容错策略 —— 允许单项变化
（换硬盘、刷 BIOS），同时换一台机器几乎不可能凑够 2 项。
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import license_crypto as LC

# ------------------------------------------------------------------ 常量

#: 公钥（32 字节）。由 tools/gen_keys.py 生成，仅**公钥**进仓库，
#: 私钥落在项目外的 %USERPROFILE%\.dadan-license\private_key.txt（见 tools/gen_keys.py）。
#: 占位全 0 时视为"未配置" —— 程序会明确报错而不是静默放行（见 verify_code）。
PUBLIC_KEY_HEX = (    '529a4d7c3f1ab7a4d48620aba382ad9a'
    'ad3e3c33e9da287e6d594fa9afdb5d33'
)

#: 授权状态文件（与 settings.json 同目录，便于一起备份/清理）
STATE_FILENAME = 'license.json'

#: 机器指纹槽位定义：(英文标签, 是否强校验)
#: 标签进 sha256 的输入，**一旦发布就不能改** —— 改了所有已发出的码全部失效。
SLOTS = (
    ('board_uuid', 'Win32_ComputerSystemProduct.UUID'),
    ('cpu_id', 'Win32_Processor.ProcessorId'),
    ('board_serial', 'Win32_BaseBoard.SerialNumber'),
    ('disk_serial', 'Win32_DiskDrive.SerialNumber'),
)

#: 组装机 / OEM 常见的无意义占位串。
#: 比对键已按「去点 + 去空格 + 小写」归一化（见 _norm_component），
#: 所以 'To be filled by O.E.M.' 与 'tobefilledbyoem' 是同一个键。
_PLACEHOLDER_KEYS = {
    'none', 'null', 'unknown', 'defaultstring', 'default',
    'tobefilledbyoem', 'tobefilledbyeom', 'filledbyoem',
    'systemserialnumber', 'systemmanufacturer', 'systemproductname',
    'notspecified', 'notapplicable', 'na', 'oem', 'oemid',
    'baseboardserialnumber', 'chassisserialnumber', 'serialnumber',
    'invalid', 'notavailable',
}

_ALL_ZERO_OR_F = re.compile(r'^(?:0+|f+)$')

#: **首次运行的试用天数**。0 = 关闭试用（未激活就是不能用）。
#:
#: ⚠️ 为什么默认给试用期，而不是"没激活就锁死" —— 这是**迁移风险**，不是功能：
#:     v4.7.0 之前的版本没有任何授权逻辑，**老买家全都能正常用**。
#:     一旦推送带门禁的新版，老买家机器上没有 license.json，
#:     直接锁死会让他们表现为"点了启动没反应" → 以为软件坏了 → 退款 / 差评。
#:     给一段试用期，老买家有缓冲、新买家也能"先试后买"，
#:     而且从"能用"到"要付费"的过渡不会砸在售后上。
#:
#: ⚠️ 为什么是 7 天而不是更短 —— 这是**离线授权模式的必需缓冲**：
#:     卖家用的是"买家发机器码 → 卖家回授权码"的手工流程，全程靠人。
#:     买家半夜下单、卖家在睡觉/上班/出差，都可能几小时甚至一两天回不上。
#:     试用期若太短（例如 3 天），就会出现"买家付了钱、却在等码期间被锁在门外"
#:     —— 这类体验直接换来退款和差评，而损失的远不止一单。
#:     7 天足够覆盖"卖家一两天没看消息"的常见情况，且不显著削弱付费转化
#:     （想长期用的买家终究会激活；只想白用的，7 天和 3 天一样会流失）。
TRIAL_DAYS = 7

#: trial 起点允许比"当前时间"超前多少秒仍视为合法（容忍时钟/时区小误差）。
#: 超过这个量说明 trial 记录被改到了未来 —— 判**已失效**而不是"还剩 3650 天"。
_TRIAL_FUTURE_TOLERANCE_S = 300.0

_TRIAL_LOCK = threading.Lock()

_CACHE_LOCK = threading.Lock()
# 进程内缓存：指纹采集要 spawn PowerShell，**冷启动实测 1.5 s**（不是毫秒级）。
# v4.9.3 更正：原注释写"~190ms"，比实测小 8 倍。这个错误数字直接掩盖了
# "在 GUI 线程采集会让激活窗白屏"这个真缺陷（见 ui/activation_dialog.py 的 P1-E）。
# 凡"实测 XXms"这类结论性注释，都应标明**测量日期与机器**，否则它只会变成
# 一句无法证伪、却会误导后来人的断言。
_CACHE = {}


# ------------------------------------------------------------------ 机器指纹

def _norm_component(raw):
    """把 OEM 占位值归一化成空串 —— 空串代表"这台机器取不到该项"。

    ⚠️ 占位比对必须**先去掉点号和空格再比**：真实值形如
       'To be filled by O.E.M.'，而本函数开头已 strip 掉结尾的点 →
       拿到 'To be filled by O.E.M'，与集合里的带点写法对不上，
       占位值就会漏网（实测被测试逮到）。故两边都做「去点+去空格+小写」。
    """
    s = (raw or '').strip().strip('.').strip()
    s = re.sub(r'\s+', ' ', s)
    # 比对键：去掉 点/空格/下划线/连字符/斜杠 后转小写。
    # ⚠️ 斜杠必须在列 —— 占位串 'N/A' 靠它才能归成 'na' 命中集合；
    #    漏掉斜杠会让 'N/A' 原样留下，成为一堆机器的"共同指纹"（实测踩到）。
    key = re.sub(r'[.\s_\-/\\]', '', s).lower()
    if not key:
        return ''
    if key in _PLACEHOLDER_KEYS or _ALL_ZERO_OR_F.match(key):
        return ''
    # 磁盘序列号实测形如 "0000_..._3CE9." —— 结尾杂点已在上方 strip
    return s


# v4.9.3: 删除 `_ps_json()` —— 全项目 grep 只有定义行、零调用点（指纹采集走的是
# 下面的 `_PS_COLLECT` + `|` 分隔，不用 JSON 通道）。留一个"看起来是通用工具"
# 的死函数，只会让后来人以为 PowerShell 那条路还有第二套实现。


#: 一次性把 4 项取全（**实测 2026-10-23 本机冷启动 1.509 s**；分 4 次调用只会更慢，
#: 故合并成一次）。
#: v4.9.3 更正：原注释写"~190ms"，比实测小 8 倍 —— 报错的量级会让"这不该放在
#: GUI 线程"这个判断失去依据，是 P1-E 被长期忽略的直接原因。
#: 输出用 '|' 分隔而不是 JSON —— 免去 PowerShell 侧引号转义与中文编码坑。
_PS_COLLECT = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$a=(Get-CimInstance Win32_ComputerSystemProduct).UUID;"
    "$b=(Get-CimInstance Win32_Processor).ProcessorId;"
    "$c=(Get-CimInstance Win32_BaseBoard).SerialNumber;"
    "$d=(Get-CimInstance Win32_DiskDrive | Sort-Object Index | "
    "Select-Object -First 1).SerialNumber;"
    "Write-Output ($a + '|' + $b + '|' + $c + '|' + $d)"
)


def collect_components(refresh=False):
    """返回 4 个槽位的归一化原始值 [board_uuid, cpu_id, board_serial, disk_serial]。

    取不到的槽为空串。**单次 PowerShell 调用**（用 output 分隔符解析）。
    进程内缓存：verify 会被 UI 多次调用，不必重复起进程。
    """
    with _CACHE_LOCK:
        if not refresh and 'components' in _CACHE:
            return list(_CACHE['components'])

        vals = ['', '', '', '']
        if os.name == 'nt':
            txt = None
            try:
                flags = 0x08000000
                r = subprocess.run(
                    ['powershell.exe', '-NoProfile', '-NonInteractive',
                     '-ExecutionPolicy', 'Bypass', '-Command', _PS_COLLECT],
                    capture_output=True, text=True, encoding='utf-8',
                    errors='replace', timeout=30, creationflags=flags)
                txt = (r.stdout or '').strip()
            except Exception:
                txt = None
            if txt:
                # 取最后一行含 '|' 的输出（隔离可能的警告噪音）
                for line in reversed(txt.splitlines()):
                    if '|' in line:
                        parts = line.split('|')
                        for i in range(4):
                            if i < len(parts):
                                vals[i] = parts[i].strip()
                        break
        comps = [_norm_component(v) for v in vals]
        _CACHE['components'] = comps
        return list(comps)


def _slot_digest(tag, value):
    """一个槽位的 8 字节指纹。value 为空串时返回全 0（= 该槽缺失）。"""
    if not value:
        return b'\x00' * 8
    return hashlib.sha256((tag + '|' + value).encode('utf-8')).digest()[:8]


def machine_id(components=None):
    """机器码：4 槽指纹拼成 32 字节后 base32（52 字符，无连字符）。

    这是给**买家念给卖家/发给卖家**的那串，不带签名、可以公开。
    """
    comps = components if components is not None else collect_components()
    return LC.b32e(b''.join(_slot_digest(SLOTS[i][0], comps[i]) for i in range(4)))


def slot_digests(components=None):
    """4 个槽位指纹（各 8 字节），顺序与 SLOTS 一致。"""
    comps = components if components is not None else collect_components()
    return [_slot_digest(SLOTS[i][0], comps[i]) for i in range(4)]


def machine_id_grouped(components=None):
    """带连字符的机器码（仅用于显示，便于用户核对/抄写）。"""
    return LC.group_code(machine_id(components), 4)


# ------------------------------------------------------------------ 到期日

def days_to_expiry_from_date(d):
    """datetime.date → payload 用的天数（0 = 永久）。"""
    if d is None:
        return 0
    delta = (d - datetime.date(2020, 1, 1)).days
    if delta <= 0:
        raise ValueError('到期日早于 2020-01-01')
    return delta


def expiry_date_from_days(days):
    """payload 天数 → datetime.date；0 返回 None（永久）。"""
    if not days:
        return None
    return datetime.date(2020, 1, 1) + datetime.timedelta(days=int(days))


# ------------------------------------------------------------------ 授权码校验

class LicenseError(Exception):
    """授权码不合法（格式错 / 签名假 / 绑定不符 / 已过期）。message 可直接给用户看。"""


def _clock_watermark_path():
    """时钟水位文件路径（与授权文件同目录）。

    为什么需要：到期判断原先只有 `datetime.date.today()` 一个输入，实测把系统
    时间调到 2020 年就能让一张 `expires=2026-10-20` 的过期年卡"复活"通过验签。
    这里持久化"本机见过的最大日期"，每次校验取 `max(系统日期, 水位)`，
    于是时间往前调不再有任何收益（水位已经记着真实进度）。
    """
    return os.path.join(os.path.dirname(state_path()), 'clock_watermark.json')


def _read_clock_watermark():
    """读历史最大日期（datetime.date）；没有/损坏返回 None。"""
    try:
        with open(_clock_watermark_path(), encoding='utf-8') as f:
            d = json.load(f)
        iso = (d.get('max_date') or '').strip()
        if not iso:
            return None
        return datetime.date.fromisoformat(iso)
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _write_clock_watermark(d):
    """原子写历史最大日期。失败静默（不抛，不影响主流程）。"""
    p = _clock_watermark_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump({'max_date': d.isoformat(),
                       'written_at': time.strftime('%Y-%m-%d %H:%M:%S')},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
        return True
    except OSError:
        return False


def effective_today(now_date=None):
    """返回"应当用于到期判断的日期" = max(系统日期, 历史水位)。

    水位在返回前会被**抬升并回写**，所以调用方不需要自己维护。
    """
    today = now_date or datetime.date.today()
    wm = _read_clock_watermark()
    if wm is not None and wm > today:
        return wm
    if wm is None or today > wm:
        _write_clock_watermark(today)
    return today


def verify_code(code, components=None, public_key_hex=None, today=None):
    """校验授权码。合法返回 dict，不合法抛 LicenseError（message 面向用户）。

    返回：
        {'payload': bytes, 'expires': date|None, 'tier': int,
         'matched': [槽位标签...], 'required': int, 'machine_id': str}

    校验顺序（**先验签名，再看绑定**）：
      1. 结构 / base32                 → 抄错
      2. Ed25519 验签（覆盖整个 payload）→ 伪造或改过内容
      3. 版本号、到期日
      4. 机器绑定容错匹配
    先验签很关键：否则一个乱码的码会在"绑定不符"上报错，误导用户以为抄错了机器码。
    """
    pk_hex = (public_key_hex or PUBLIC_KEY_HEX).strip()
    if not pk_hex or set(pk_hex.lower()) == {'0'}:
        raise LicenseError('本程序未配置授权公钥，无法校验授权码（此为打包配置错误，请联系作者）。')
    try:
        pk = bytes.fromhex(pk_hex)
    except ValueError:
        raise LicenseError('授权公钥配置非法（非十六进制），请联系作者。')
    if len(pk) != 32:
        raise LicenseError('授权公钥长度非法，请联系作者。')

    try:
        payload, sig = LC.decode_license(code)
    except ValueError as e:
        raise LicenseError(f'授权码格式不对：{e}')

    if not LC.ed25519_verify(pk, sig, payload):
        raise LicenseError('授权码无效：签名校验未通过（可能是抄错、或该码不是本软件签发的）。')

    ver = payload[0]
    if ver != 1:
        raise LicenseError(f'授权码版本不支持（v{ver}），请更新软件或联系作者。')

    # 签名已通过，以下内容可信
    expires_days = int.from_bytes(payload[33:37], 'big')
    tier = payload[37]
    exp = expiry_date_from_days(expires_days)
    if exp is not None:
        # ⚠️ today 显式传入时（测试/调用方指定）**不掺水位** —— 否则测试无法
        #    用固定日期验证边界。生产路径（不传 today）走 effective_today()，
        #    它会把"历史见过的最大日期"一起考虑，堵住"把系统时间调回过去
        #    让过期年卡复活"这条路（实测旧实现可绕过）。
        now = today if today is not None else effective_today()
        if now > exp:
            raise LicenseError(f'授权已过期（有效期至 {exp.isoformat()}）。')

    comps = components if components is not None else collect_components()
    mine = slot_digests(comps)

    # 只对**码里非零的槽**做比对；码里为 0 的槽代表发行方当时没取到该项，
    # 不能拿来否决用户。这样"作者机器上取不到硬盘序列号"也不会卡死发码。
    bound = [i for i in range(4) if payload[1 + i * 8:9 + i * 8] != b'\x00' * 8]
    if not bound:
        raise LicenseError('授权码不含机器绑定信息，不是本软件的正式授权码。')

    matched = [i for i in bound if payload[1 + i * 8:9 + i * 8] == mine[i]]
    available = [i for i in bound if mine[i] != b'\x00' * 8]
    need = 1 if len(available) < 2 else 2          # 只有一项可比时降到 1 项
    if len(matched) < need:
        raise LicenseError(
            '授权码与当前电脑不匹配：该码是为另一台电脑签发的。\n'
            '换电脑 / 更换主板硬盘后需要重新获取授权码。\n'
            f'（本机机器码：{LC.group_code(LC.b32e(b"".join(mine)), 4)}）')

    return {
        'payload': payload,
        'expires': exp,
        'tier': tier,
        'matched': [SLOTS[i][0] for i in matched],
        'required': need,
        'machine_id': LC.b32e(b''.join(mine)),
    }


# ------------------------------------------------------------------ 状态持久化

def state_path():
    """授权状态文件路径：%APPDATA%\\LOLScript\\license.json。

    与 settings.json 同目录同策略（含 APPDATA 缺失时的 expanduser 兜底）。
    """
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'LOLScript', STATE_FILENAME)


def save_license(code, info):
    """把授权码与解析结果落盘。原子写（临时文件 + replace）避免半截文件。"""
    path = state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = {
        'code': code,
        'machine_id': info.get('machine_id', ''),
        'expires': info['expires'].isoformat() if info.get('expires') else None,
        'tier': int(info.get('tier', 0)),
        'activated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_license():
    """读回已保存的授权码字符串；无 / 损坏返回 None（不抛）。

    只回传 code 字符串，**校验一律重新走 verify_code** —— 不信任落盘内容，
    否则改一下 license.json 里的 expires 就能"续期"。
    """
    try:
        with open(state_path(), encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            c = d.get('code')
            return c if isinstance(c, str) and c.strip() else None
        return None
    except (OSError, ValueError):
        return None


def clear_license():
    """删除授权文件（用于"重新激活"/排查）。返回是否确实删掉了。"""
    try:
        os.remove(state_path())
        return True
    except OSError:
        return False


# ------------------------------------------------------------------ 试用期
#
# ## 为什么一个文件不够（v4.9.3 修）
#
# 旧实现把试用起点**只**存在 `trial.json` 里，而且那个字段既没有签名、也没有
# 任何第二处校验。实测（本机 Python 3.14.7，隔离 APPDATA）：
#     试用已过期             -> usable=False
#     os.remove(trial.json)  -> usable=True，剩余 7.0 天
# 也就是说"删一个明文的 JSON"就能无限续试用 —— 这不是"离线授权的固有局限"
# （那指的是要逆向改字节码），而是**改一个字段/删一个文件的成本**，两者差
# 好几个量级，与文件头承诺的"把随手转发变成必须动手逆向"完全不符。
#
# 现在：试用记录**同时写三份**（其中两份是隐藏文件名），读的时候取三者里
# **最早**的起点。任意删掉一份都还有另外两份兜底。
#
# ⚠️ 诚实边界：三份都删掉仍然能重来。本地授权做不到绝对，但门槛从"删一个
#    看得见的文件"抬到了"知道有三个、其中一个还是隐藏文件"。要真正堵死必须
#    引入服务器或注册表（后者需要管理员/会被安全软件拦，本机规范也不允许
#    未经许可动注册表），故不在此做。

#: 试用记录的三个落点（同目录、不同文件名）。第一个是给人看的，其余是兜底。
#: 顺序无所谓 —— 读取时取"最早的有效起点"，不是取第一个命中的。
_TRIAL_FILENAMES = ('trial.json', '.trial_state', '.trial_watermark')


def trial_path():
    """试用标记文件路径（与授权文件同目录）。这是**给人看的那一份**。"""
    return os.path.join(os.path.dirname(state_path()), _TRIAL_FILENAMES[0])


def _trial_paths():
    """三份试用记录的完整路径列表。"""
    d = os.path.dirname(state_path())
    return [os.path.join(d, n) for n in _TRIAL_FILENAMES]


def _read_trial_record():
    """读三份试用记录并合并。

    返回 dict：
        started_at : float|None  —— 三份里**最早**的有效起点（越早 = 试用越少）
        max_seen_at: float|None  —— 三份里**最大**的"见过的当前时间"水位

    任何一份缺失/损坏都只是不计入，不会让整体失败（单份被删仍能兜住）。
    """
    starts, seen = [], []
    for p in _trial_paths():
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(d, dict):
            continue
        try:
            v = d.get('started_at')
            if v is not None:
                starts.append(float(v))
        except (TypeError, ValueError):
            pass
        try:
            w = d.get('max_seen_at')
            if w is not None:
                seen.append(float(w))
        except (TypeError, ValueError):
            pass
    return {'started_at': min(starts) if starts else None,
            'max_seen_at': max(seen) if seen else None}


# v4.9.3: 删除 `_read_trial()` —— 引入三份记录后它退化成 `_read_trial_record()`
# 的单键取值薄壳，全项目 grep 零调用点（含 tools/ 与 tests/）。
# 留着会让后来人以为"读试用"有两条路径（一条读一份、一条读三份）。


def _write_trial(ts, max_seen=None):
    """把试用起点写进**全部三份**记录。原子写。失败返回 False（不抛）。

    :param ts: 试用起始时间（epoch 秒）
    :param max_seen: 见过的最晚时间（时钟回拨水位）；None 时取 ts 与当前时间的较大者
    """
    if max_seen is None:
        max_seen = max(float(ts), time.time())
    ok_any = False
    for p in _trial_paths():
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump({'started_at': ts,
                           'max_seen_at': max_seen,
                           'started_text': time.strftime('%Y-%m-%d %H:%M:%S',
                                                         time.localtime(ts))},
                          f, ensure_ascii=False, indent=2)
            os.replace(tmp, p)
            ok_any = True
        except OSError:
            continue
    return ok_any


def trial_state(now=None):
    """返回 (是否可用, 剩余天数, 起始时间)。首次调用会**创建**试用记录。

    三条判据（任一命中即判"不可用"，绝不放行）：

      ① **起点在未来** —— `started_at` 比现在超前超过 _TRIAL_FUTURE_TOLERANCE_S。
         旧实现直接 `remaining = TRIAL_DAYS - (now - started)/86400`，于是把
         started_at 写成 10 年后就能得到"剩余 3650 天"且 usable=True（实测）。
      ② **时钟回拨** —— 本次 `now` 比历史见过的最大时间还早超过容差。水位
         每次都回写，把系统时间往前调不再能延长试用。
      ③ 正常超期。

    `remaining` 一律夹到 `[0, TRIAL_DAYS]` —— 任何异常记录都不会显示出
    "剩余 3000 天"这种明显不可能的数值。

    ⚠️ 显式传入 `now` 时（测试/诊断注入时钟）**跳过判据②**：调用方既然
       接管了"现在几点"，就不该再被本机真实水位否决 —— 否则任何用固定时间戳
       的测试都会假红（本机实测：注入 1970 基准时间戳会被判成"回拨"）。

    返回 remaining 为 None 表示试用已关闭（TRIAL_DAYS<=0）或已过期。
    """
    if TRIAL_DAYS <= 0:
        return (False, None, None)
    injected = now is not None
    now = float(now if injected else time.time())

    with _TRIAL_LOCK:
        rec = _read_trial_record()
        started = rec['started_at']
        max_seen = rec['max_seen_at']

        if started is None:
            # 首次运行：用"当前时间"与"程序文件时间"里较晚者作为起点。
            # 用较晚者是为了防"把系统时间调到过去再首次运行"。
            started = now
            try:
                exe = sys.executable if getattr(sys, 'frozen', False) else __file__
                fts = os.path.getmtime(exe)
                # 只在差异明显时采用（避免时钟/时区小误差导致误判）
                if fts > started + 60:
                    started = fts
            except OSError:
                pass
            _write_trial(started, max_seen=max(now, started))

        # ① 起点在未来 → 记录被篡改，判失效（不重建、不给新试用）
        if started > now + _TRIAL_FUTURE_TOLERANCE_S:
            print(f'[授权] 试用记录起点在未来({started:.0f} > {now:.0f})，'
                  f'判定为被篡改，试用作废')
            return (False, 0, started)

        # ② 时钟回拨：本次时间早于历史水位（超出容差）→ 判失效。
        #    仅在"没注入时钟"时生效 —— 见 docstring 末尾的说明。
        if (not injected and max_seen is not None
                and now < max_seen - _TRIAL_FUTURE_TOLERANCE_S):
            print(f'[授权] 检测到系统时间回拨({now:.0f} < 水位{max_seen:.0f})，'
                  f'试用作废')
            return (False, 0, started)

        # 水位只升不降，每次回写（三份都写，避免删一份就丢水位）
        if not injected and (max_seen is None or now > max_seen + 1.0):
            _write_trial(started, max_seen=now)

        elapsed_days = (now - started) / 86400.0
        remaining = TRIAL_DAYS - elapsed_days
        # ③ 夹到 [0, TRIAL_DAYS]：异常记录不该显示出"剩余 3000 天"
        if remaining > TRIAL_DAYS:
            remaining = float(TRIAL_DAYS)
        if remaining <= 0:
            return (False, 0, started)
        return (True, remaining, started)


# ------------------------------------------------------------------ 本地签发

# v4.9.3: **整段在线激活代码已删除**（按用户要求只保留本地激活）。
#
# 删掉的东西：
#   ACTIVATION_URL      在线激活服务地址（原为 ""，即从未启用）
#   HTTP_TIMEOUT        只服务在线激活的 15s 超时
#   NetworkError        "网络/服务不可用" 与 LicenseError 的区分（本地不需要）
#   activation_available() / online_activate() / online_status()
#
# 为什么整段删而不是留着备查：
#   ACTIVATION_URL 一直是空串 → activation_available() 恒为 False →
#   这三个函数在生产路径上**永远不会执行**。留着它们会让后来人以为
#   "这软件支持自助激活，只是没配地址"，从而去找一个不存在的服务端
#   （对应的 license-server/src/worker.js 等文件也已一并删除）。
#
# 现在的授权流程只有一条：卖家在本机用 tools/make_license.py（或
# license-server/dist/授权码工具.html）用私钥签发授权码 → 买家粘贴 →
# verify_code() 离线验签。全程不联网。

# ------------------------------------------------------------------ 统一入口

class LicenseStatus:
    """一次完整判定：激活与否 + 面向用户的说明。UI 只读这个对象，不自己判逻辑。

    `trial` / `trial_days_left` 用来表达"还没激活，但仍在试用期内" ——
    这是一种**可放行**的状态，与"未激活且试用已过"必须区分开，
    否则界面没法告诉用户"你还能用 2 天"。
    """

    __slots__ = ('activated', 'reason', 'info', 'machine_id', 'code',
                 'trial', 'trial_days_left')

    def __init__(self, activated, reason='', info=None, machine_id='', code='',
                 trial=False, trial_days_left=None):
        self.activated = activated
        self.reason = reason
        self.info = info or {}
        self.machine_id = machine_id
        self.code = code
        self.trial = trial
        self.trial_days_left = trial_days_left

    @property
    def usable(self):
        """**能否放行** —— 门禁应当只读这个属性，不要自己拼 activated/trial。

        三种放行情况：正式授权、试用期内、授权码本身的到期日尚未到。
        注意"已激活但已过期"必须返回 False：那通常会被 verify_code 提前拦成
        LicenseError，但**长期运行**的进程里状态是缓存的，跨过到期时刻后
        这条路径就会命中 —— 少了它，年卡会变成"开到第 366 天还能一直用"。
        """
        if self.trial and not self.activated:
            return True
        if not self.activated:
            return False
        if self.days_left is not None and self.days_left < 0:
            return False
        return True

    @property
    def expires_text(self):
        if self.trial and not self.activated:
            d = self.trial_days_left
            return f'试用中（剩余 {d:.1f} 天）' if d is not None else '试用中'
        e = self.info.get('expires')
        return '永久' if not e else e.isoformat()

    @property
    def days_left(self):
        # 试用期的"剩余天数"是浮点（可能不足 1 天），与授权码的整数天区分开，
        # 由调用方通过 trial 标志决定怎么展示。
        if self.trial and not self.activated:
            return self.trial_days_left
        e = self.info.get('expires')
        if not e:
            return None
        # v4.9.3: 与 verify_code 用同一个"今天"口径（含时钟水位），否则会出现
        # "验签说已过期、界面显示还剩 20 天"这种自相矛盾。
        return (e - effective_today()).days

    def __repr__(self):
        return (f'LicenseStatus(activated={self.activated}, trial={self.trial}, '
                f'reason={self.reason!r}, expires={self.expires_text})')


def _trial_or_locked(machine_id, allow_trial, reason_if_locked):
    """没激活时决定"还能试用"还是"锁死"。

    这是**唯一**决定未激活用户能否使用的地方 —— 集中在这里是为了让
    "放行条件"只有一处可读可审，而不是散在门禁/UI/配置里各判一次。
    """
    if not allow_trial:
        return LicenseStatus(False, reason_if_locked, machine_id=machine_id)
    try:
        ok, remaining, _started = trial_state()
    except Exception as e:
        # 试用判定出问题：**不能因此放行**（否则删掉文件就能永久白用），
        # 但也不能让程序崩 —— 当作"试用已结束"。
        return LicenseStatus(False, f'试用状态读取异常，请激活后使用。({type(e).__name__})',
                             machine_id=machine_id)
    if ok:
        return LicenseStatus(False, '试用中', machine_id=machine_id,
                             trial=True, trial_days_left=remaining)
    return LicenseStatus(False, reason_if_locked, machine_id=machine_id)


def check(components=None, public_key_hex=None, allow_trial=True):
    """读取已保存的授权并校验；没保存 / 不合法都返回状态对象。

    **永不抛异常** —— 启动门禁调用它，任何意外都应降级为"未激活"而不是崩溃。

    allow_trial=False 用于测试与"强制激活"场景（跳过试用）。

    v4.9.3: 删除 `recheck_online` 参数 —— 它唯一的作用是查"本机是否被卖家撤销"，
    而那个查询依赖的在线服务（`online_status` / `ACTIVATION_URL`）已随
    Cloudflare Worker 一起移除。纯离线模式**做不到远程撤销**，这是移除服务端的
    必然代价，已写进 license-server/README.md 的"设计边界"表。
    """
    try:
        comps = components if components is not None else collect_components()
        mid = LC.b32e(b''.join(slot_digests(comps)))
        code = load_license()
        if not code:
            return _trial_or_locked(mid, allow_trial, '未激活')
        info = verify_code(code, components=comps, public_key_hex=public_key_hex)
        return LicenseStatus(True, '已激活', info=info, machine_id=mid, code=code)
    except LicenseError as e:
        try:
            mid = LC.b32e(b''.join(slot_digests(components)))
        except Exception:
            mid = ''
        # 有授权码但无效（过期/换机/伪造）：**不给试用**。
        # 理由：他曾经激活成功过，现在失效是"到期/换机"这种确定性原因，
        # 再开试用等于把年卡变成永久的。试用只针对"从未激活过"的新装。
        return LicenseStatus(False, str(e), machine_id=mid)
    except Exception as e:
        return LicenseStatus(False, f'授权检查出错：{type(e).__name__}: {e}')


def try_activate(code, components=None, public_key_hex=None):
    """激活：校验通过才落盘。返回 LicenseStatus（activated 表示是否成功）。"""
    comps = components if components is not None else collect_components()
    try:
        info = verify_code(code, components=comps, public_key_hex=public_key_hex)
    except LicenseError as e:
        mid = LC.b32e(b''.join(slot_digests(comps)))
        return LicenseStatus(False, str(e), machine_id=mid)
    except Exception as e:
        return LicenseStatus(False, f'激活失败：{type(e).__name__}: {e}')
    # 存规范化后的纯码（用户可能带连字符/小写/空格，统一后再存，便于比对）
    canonical = LC.b32e(LC.b32d(code))
    try:
        save_license(canonical, info)
    except OSError as e:
        return LicenseStatus(False, f'激活信息保存失败（{e}）。请检查是否有磁盘/权限问题。',
                             info=info, machine_id=info.get('machine_id', ''))
    return LicenseStatus(True, '激活成功', info=info,
                         machine_id=info.get('machine_id', ''), code=canonical)
