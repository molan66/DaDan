# -*- coding: utf-8 -*-
"""License Crypto - 纯 Python Ed25519 验签 + 授权码编解码

## 为什么自己实现 Ed25519 验签

签发侧（作者本机 tools/make_license.py）用 cryptography 的 Ed25519 签名；
**验证侧必须能进 exe**，而 cryptography 的 hazmat/bindings/_rust.pyd 单个就 9.48 MB
（整包 10.63 MB）—— 为了一个验签把 exe 从 ~23 MB 撑到 ~34 MB，
onefile 启动自解压时间与打包时长都会明显变差，不划算。

本文件是 RFC 8032 的**参考实现**（Bernstein 等原始 ed25519.py 的等价改写，
原实现属公有领域），只做**验签**，不做签名 —— 私钥永不进这个仓库。

## 正确性怎么保证（不能只靠"看着对"）

tests/test_licensing.py 里有交叉验证：用 cryptography 生成密钥并签名，
再用本文件的 ed25519_verify 验证，覆盖随机向量、篡改签名、
篡改消息、错误公钥、非规范点等。**任何一条不过就说明本实现有问题。**

## 两串码的分工（**别搞混，这是整套方案的核心**）

**机器码**（机器 → 卖家，52 字符）
    就是 4 个分量指纹原样拼起来（4 × 8 = 32 字节）的 base32。
    不带签名 —— 它只是"这台机器是谁"的摘要，谁都能造，不构成授权。

**授权码**（卖家 → 机器，164 字符）
    = base32( payload(38B) || signature(64B) )，共 102 字节。
    签名覆盖**全部 payload**，所以机器绑定信息、到期日都在签名保护下，
    买家改任何一个字节都会验签失败。私钥只在卖家手里，因此买家造不出码。

    payload（38B）:
        0       : 格式版本 (1B) = 1
        1  - 8  : 槽0 主板 UUID   分量指纹 (8B)
        9  - 16 : 槽1 CPU ID      分量指纹 (8B)
        17 - 24 : 槽2 主板序列号   分量指纹 (8B)
        25 - 32 : 槽3 硬盘序列号   分量指纹 (8B)
        33 - 36 : 到期日 (4B, big-endian) = 距 2020-01-01 的天数; 0 = 永久
        37      : 授权档位 (1B) —— 预留：1=正式, 2=试用, 255=人员内测

    分量指纹 = sha256(英文标签 || 归一化原始值)[:8]；取不到的槽写全 0。
    「取不到的槽」= 组装机常见的占位串（Default string / To be filled by
    O.E.M. 等）与全 0/全 F 哨兵值，licensing.py 的 _norm_component 负责识别。
"""
import base64
import binascii
import hashlib

# ---------------------------------------------------------------- Ed25519

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _recover_x(y, sign):
    """由 y 与 x 的奇偶位还原曲线点 x（RFC 8032 5.1.3）。"""
    xx = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P != 0:
        x = x * _I % _P
    if x % 2 != sign:
        x = _P - x
    return x


_BY = 4 * pow(5, _P - 2, _P) % _P
_BX = _recover_x(_BY, 0)
_B = (_BX, _BY)


def _edwards_add(p, q):
    """扭曲爱德华兹曲线点加法（仿射坐标，每次两遍求逆）。"""
    x1, y1 = p
    x2, y2 = q
    k = _D * x1 * x2 * y1 * y2 % _P
    x3 = (x1 * y2 + x2 * y1) * pow(1 + k, _P - 2, _P) % _P
    y3 = (y1 * y2 + x1 * x2) * pow(1 - k, _P - 2, _P) % _P
    return (x3, y3)


def _scalar_mult(p, e):
    """迭代式 double-and-add（不用递归，避免深栈；对调用方上下文无侵入）。"""
    q = (0, 1)                      # 单位元
    while e > 0:
        if e & 1:
            q = _edwards_add(q, p)
        p = _edwards_add(p, p)
        e >>= 1
    return q


def _decode_point(s):
    if len(s) != 32:
        raise ValueError('点长度应为 32 字节')
    y = int.from_bytes(s, 'little') & ((1 << 255) - 1)
    x = _recover_x(y, s[31] >> 7)
    if (-x * x + y * y - 1 - _D * x * x * y * y) % _P != 0:
        raise ValueError('点不在曲线上')
    return (x, y)


def ed25519_verify(public_key, signature, message):
    """验证 Ed25519 签名。任何异常一律返回 False（调用方不需要 try）。

    :param public_key: 32 字节原始公钥
    :param signature:  64 字节签名
    :param message:    被签名的原始字节
    """
    try:
        if len(public_key) != 32 or len(signature) != 64:
            return False
        a = _decode_point(public_key)
        r = _decode_point(signature[:32])
        s = int.from_bytes(signature[32:], 'little')
        # S 必须 < L，否则是可延展签名（RFC 8032 5.1.7 要求）
        if s >= _L:
            return False
        h = int.from_bytes(
            hashlib.sha512(signature[:32] + public_key + message).digest(),
            'little') % _L
        return _scalar_mult(_B, s) == _edwards_add(r, _scalar_mult(a, h))
    except Exception:
        return False


# ---------------------------------------------------------------- base32

# RFC 4648 字母表是 A-Z + 2-7，不含 0/1/8/9。
# 用户手抄时代入的常见字符做一次纠正（0→O, 1→I, 8→B, 9→G），
# 避免"复制粘贴本来没问题、手抄一个字符全盘失败"。
_CONFUSE = str.maketrans({'0': 'O', '1': 'I', '8': 'B', '9': 'G'})


def b32e(data):
    return base64.b32encode(data).decode('ascii').rstrip('=')


def b32d(text):
    t = (text or '').strip().upper().translate(_CONFUSE)
    t = ''.join(c for c in t if not c.isspace() and c != '-')
    if not t:
        raise ValueError('授权码为空')
    pad = (-len(t)) % 8
    try:
        return base64.b32decode(t + '=' * pad)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f'授权码含有非法字符或长度不对（{e}）')


# ---------------------------------------------------------------- 授权码

PAYLOAD_LEN = 38      # 4 槽指纹(32) + 到期日(4) + 档位(1) + 版本(1) = 38
SIG_LEN = 64
BLOB_LEN = PAYLOAD_LEN + SIG_LEN        # 102


def encode_license(payload, signature):
    if len(payload) != PAYLOAD_LEN:
        raise ValueError(f'payload 应为 {PAYLOAD_LEN} 字节，实为 {len(payload)}')
    if len(signature) != SIG_LEN:
        raise ValueError(f'签名应为 {SIG_LEN} 字节，实为 {len(signature)}')
    return b32e(payload + signature)


def decode_license(code):
    """拆出 (payload, signature)。只做结构校验，**不做签名校验**。

    签名校验由 verify_license 负责 —— 分开是为了让"格式错"和"签名假"
    能给出不同的用户提示（前者是抄错，后者是没授权/伪造）。
    """
    raw = b32d(code)
    if len(raw) != BLOB_LEN:
        raise ValueError(f'授权码长度不对：解出 {len(raw)} 字节，应为 {BLOB_LEN}')
    return raw[:PAYLOAD_LEN], raw[PAYLOAD_LEN:]


def group_code(code, size=5):
    """把长码按 size 分组成带连字符的形式，仅用于**显示**（粘贴时会被剔除）。"""
    return '-'.join(code[i:i + size] for i in range(0, len(code), size))
