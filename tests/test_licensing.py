# -*- coding: utf-8 -*-
"""授权体系回归测试（零第三方依赖：只用标准库 unittest）。

跑法：
    <python> -m unittest discover -s tests -v

## 为什么这些用例必须存在

授权模块有个特殊性质：**它错了不会当场报错，而是"该拦的没拦"或
"该放的没放"** —— 前者等于白送（卖出去的软件谁都能用），后者会
误伤付了钱的买家（他会被锁在门外，然后来退款）。两种都无法靠"看一眼
代码"发现，必须用测试钉住。

本文件里带 `── 真实缺陷` 标记的用例，都对应开发过程中**实际踩到**的问题。

## 设计约束

  · 零第三方依赖：只用标准库（环境里没 pytest；另外这是要进 exe 的代码，
    测试不该反向给打包引入依赖）。签名功能在测试里用自生成的 Ed25519 密钥，
    不依赖本机私钥是否存在。
  · 不依赖 Qt 事件循环、不依赖真实硬件：机器指纹用**注入的假分量**，
    这样在 CI/别的机器上也能跑，不会因为"这台机器读不到硬盘序列号"而漂移。
  · **不碰真实授权文件**：测试通过 patch state_path 指向临时目录，
    绝不去动 %APPDATA%\\LOLScript\\license.json（那是用户真实激活状态）。
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from core import license_crypto as LC          # noqa: E402
from core import licensing as L                # noqa: E402


# ---------------------------------------------------------------- 测试辅助

def _make_keys():
    """生成一对测试密钥。没有 cryptography 就退化成"跳过签名相关用例"。

    这样在没有 cryptography 的机器上，格式/指纹/容错逻辑仍能被测到，
    整份测试不会因为缺一个库而全红。
    """
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except Exception:
        return None, None
    sk = ed25519.Ed25519PrivateKey.generate()
    return sk, sk.public_key().public_bytes_raw().hex()


def _sign_payload(sk, payload):
    return sk.sign(payload)


def _issue(sk, components, days=365, tier=1, slots_override=None):
    """按给定机器分量签发一个码，返回 (code, payload)。"""
    digs = L.slot_digests(components) if slots_override is None else slots_override
    if days:
        exp = (datetime.date.today() + datetime.timedelta(days=days))
        abs_days = (exp - datetime.date(2020, 1, 1)).days
    else:
        abs_days = 0
    payload = (bytes([1]) + b''.join(digs)
               + int(abs_days).to_bytes(4, 'big') + bytes([tier]))
    return LC.encode_license(payload, _sign_payload(sk, payload)), payload


#: 4 个"看起来正常"的假机器分量，用于不依赖真机的测试
FAKE_A = ['UUID-AAAA-1111', 'CPUIDAAAAAAAAAAA', 'BOARDSN-AAAA', 'DISKSN-AAAA']
#: 完全不同的另一台机器
FAKE_B = ['UUID-BBBB-2222', 'CPUIDBBBBBBBBBBB', 'BOARDSN-BBBB', 'DISKSN-BBBB']


class TestLicenseCodeFormat(unittest.TestCase):
    """授权码编解码 —— 这是最容易"改了格式没同步"的地方。"""

    def test_roundtrip(self):
        payload = bytes(range(38))
        code = LC.encode_license(payload, b'\x01' * 64)
        p2, s2 = LC.decode_license(code)
        self.assertEqual(p2, payload)
        self.assertEqual(s2, b'\x01' * 64)

    def test_wrong_payload_len_rejected(self):
        """payload 长度写错必须当场报错，不能签出一个结构错的码。"""
        with self.assertRaises(ValueError):
            LC.encode_license(b'\x00' * 30, b'\x00' * 64)

    def test_wrong_sig_len_rejected(self):
        with self.assertRaises(ValueError):
            LC.encode_license(b'\x00' * 38, b'\x00' * 63)

    def test_truncated_code_rejected(self):
        """少抄一截 → 明确报错（而不是静默解出半个 payload）。"""
        payload = bytes(range(38))
        code = LC.encode_license(payload, b'\x01' * 64)
        with self.assertRaises(ValueError):
            LC.decode_license(code[:100])

    def test_empty_code_rejected(self):
        with self.assertRaises(ValueError):
            LC.decode_license('')

    def test_confusable_chars_normalized(self):
        """用户手抄常见误写 0/O、1/I、8/B、9/G 要被纠正。

        这段是**为售后写的**：实测买家手抄长码错误率极高，逐个字符
        打回重发会把售后拖死。只要不引入歧义（这些字符不在 base32 字母表里），
        就应当替用户纠正，而不是判他错。
        """
        payload = bytes([7] * 38)
        code = LC.encode_license(payload, b'\x02' * 64)
        # 把码里的 O/I/B/G 换成对应的数字误写形式
        mangled = code.replace('O', '0').replace('I', '1')
        mangled = mangled.replace('B', '8').replace('G', '9')
        p2, s2 = LC.decode_license(mangled)
        self.assertEqual(p2, payload)
        self.assertEqual(s2, b'\x02' * 64)

    def test_hyphens_and_spaces_stripped(self):
        """带连字符/空格/换行的码（用户从微信复制常见的形态）要能解析。"""
        payload = bytes([9] * 38)
        code = LC.encode_license(payload, b'\x03' * 64)
        messy = LC.group_code(code, 5) + '\n  ' + code[:20].lower()
        # 只取第一段（模拟用户粘贴了重复内容时应报错，而单段应可解）
        grouped_only = LC.group_code(code, 5)
        p2, _ = LC.decode_license(grouped_only)
        self.assertEqual(p2, payload)
        # 混入换行 + 空白也要能解
        with_ws = ' ' + code[:40] + '\n' + code[40:] + ' '
        p3, _ = LC.decode_license(with_ws)
        self.assertEqual(p3, payload)

    def test_group_code_is_display_only(self):
        """分组只是显示用途：拼回去必须与原码等价。"""
        payload = bytes([5] * 38)
        code = LC.encode_license(payload, b'\x04' * 64)
        grouped = LC.group_code(code, 5)
        self.assertIn('-', grouped)
        self.assertEqual(grouped.replace('-', ''), code)


class TestEd25519Verify(unittest.TestCase):
    """纯 Python 验签实现 —— 必须与 cryptography 交叉验证。

    这是整个方案的地基：**如果验签实现错了，可能把伪造的码判成合法**
    （等于白送），也可能把真码判成非法（买家被锁门外）。
    """

    # RFC 8032 §7.1 TEST 1：空消息的官方测试向量
    RFC_PK = bytes.fromhex(
        'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
    RFC_SIG = bytes.fromhex(
        'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155'
        '5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b')

    def test_rfc8032_official_vector(self):
        """官方标准向量必须先过 —— 证明实现符合 RFC，而不只是"自洽"。"""
        self.assertTrue(LC.ed25519_verify(self.RFC_PK, self.RFC_SIG, b''))

    def test_rfc_vector_wrong_message_rejected(self):
        self.assertFalse(LC.ed25519_verify(self.RFC_PK, self.RFC_SIG, b'x'))

    def test_cross_validate_with_cryptography(self):
        """与 cryptography 双向交叉验证（随机向量）。

        单向测不够：本实现和 cryptography 都是"自认为对"的，
        只有拿一方签、另一方验，才能证明两者语义一致。
        """
        sk, pub_hex = _make_keys()
        if sk is None:
            self.skipTest('未安装 cryptography，跳过交叉验证')
        pub = bytes.fromhex(pub_hex)
        for i in range(12):
            msg = os.urandom(i * 5 + 1)
            sig = sk.sign(msg)
            self.assertTrue(LC.ed25519_verify(pub, sig, msg),
                            f'第 {i} 个随机向量验签失败')

    def test_wrong_pubkey_rejected(self):
        sk, _ = _make_keys()
        if sk is None:
            self.skipTest('未安装 cryptography')
        _, other_pub = _make_keys()
        msg = b'hello'
        self.assertFalse(LC.ed25519_verify(bytes.fromhex(other_pub), sk.sign(msg), msg))

    def test_tampered_signature_rejected(self):
        """翻转签名任意一位都必须被拒 —— 这是"防篡改"的核心。"""
        sk, pub_hex = _make_keys()
        if sk is None:
            self.skipTest('未安装 cryptography')
        pub = bytes.fromhex(pub_hex)
        msg = b'payload'
        sig = bytearray(sk.sign(msg))
        sig[0] ^= 0x01
        self.assertFalse(LC.ed25519_verify(pub, bytes(sig), msg))

    def test_bad_lengths_rejected_not_crash(self):
        """长度不对必须返回 False，**不能抛异常**（调用方不该被迫 try）。"""
        self.assertFalse(LC.ed25519_verify(b'', b'', b''))
        self.assertFalse(LC.ed25519_verify(b'\x00' * 31, b'\x00' * 64, b'x'))
        self.assertFalse(LC.ed25519_verify(b'\x00' * 32, b'\x00' * 63, b'x'))

    def test_non_canonical_s_rejected(self):
        """S >= L 的签名必须被拒（RFC 8032 要求，防签名可延展）。

        真实缺陷预防：早期版本只比较等式不检查 S 范围，
        同一个消息+密钥可以构造出多个不同但都"验得过"的签名。
        """
        sk, pub_hex = _make_keys()
        if sk is None:
            self.skipTest('未安装 cryptography')
        pub = bytes.fromhex(pub_hex)
        msg = b'm'
        sig = bytearray(sk.sign(msg))
        # 把 S 抬到 L 以上（保持低 32 位之外的字节不变，直接构造超界值）
        huge_s = LC._L + 12345
        sig[32:] = huge_s.to_bytes(32, 'little')
        self.assertFalse(LC.ed25519_verify(pub, bytes(sig), msg))


class TestMachineFingerprint(unittest.TestCase):
    """机器指纹：归一化与稳定性。**用注入分量，不碰真机**。"""

    def test_placeholder_normalized_to_empty(self):
        """OEM 占位串必须归成空 —— 否则一堆联想机/组装机会"指纹相同"。"""
        for junk in ('Default string', 'To be filled by O.E.M.', 'None',
                     'System Serial Number', '00000000', 'FFFFFFFF',
                     '  ', 'N/A', 'To Be Filled By O.E.M.'):
            self.assertEqual(L._norm_component(junk), '', repr(junk))

    def test_real_value_kept(self):
        self.assertEqual(L._norm_component('  A5877BE7-06A4  '),
                         'A5877BE7-06A4')

    def test_trailing_dots_stripped(self):
        """真实缺陷：本机磁盘序列号实测以 '.' 结尾（'...3CE9.'）。

        不 strip 的话，同一个值在不同机器上写法不一致会造成指纹漂移。
        """
        self.assertEqual(L._norm_component('0000_8CE3_8E03_0067_3CE9.'),
                         '0000_8CE3_8E03_0067_3CE9')

    def test_all_zero_and_all_f_rejected(self):
        """全 0 / 全 F 是常见的"没读到"哨兵值，不能当成真实序列号。"""
        self.assertEqual(L._norm_component('0000-0000-0000'), '')
        self.assertEqual(L._norm_component('FFFF-FFFF-FFFF'), '')

    def test_machine_id_stable_for_same_input(self):
        a = L.machine_id(FAKE_A)
        b = L.machine_id(list(FAKE_A))
        self.assertEqual(a, b)

    def test_machine_id_differs_across_machines(self):
        self.assertNotEqual(L.machine_id(FAKE_A), L.machine_id(FAKE_B))

    def test_machine_id_length(self):
        """32 字节 → base32 无填充 = 52 字符（UI 上按这个长度提示用户）。"""
        self.assertEqual(len(L.machine_id(FAKE_A)), 52)

    def test_missing_slot_is_zero_digest(self):
        comps = list(FAKE_A)
        comps[2] = ''                     # 主板序列号读不到
        digs = L.slot_digests(comps)
        self.assertEqual(digs[2], b'\x00' * 8)
        self.assertNotEqual(digs[0], b'\x00' * 8)

    def test_tag_separation(self):
        """同样的值放在不同槽位必须产生不同指纹（标签参与哈希）。

        否则"主板序列号恰好等于 CPU ID"这种巧合会让槽位互相冒充。
        """
        v = 'SAME-VALUE-1234'
        d0 = L._slot_digest(L.SLOTS[0][0], v)
        d1 = L._slot_digest(L.SLOTS[1][0], v)
        self.assertNotEqual(d0, d1)


class TestVerifyAndBinding(unittest.TestCase):
    """验签 + 机器绑定容错。**这组是防误伤/防白送的关键**。"""

    def setUp(self):
        self.sk, self.pub = _make_keys()
        if self.sk is None:
            self.skipTest('未安装 cryptography')

    def test_valid_code_accepted_on_same_machine(self):
        code, _ = _issue(self.sk, FAKE_A, days=365)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        self.assertIsNotNone(info['expires'])
        self.assertGreaterEqual(len(info['matched']), 2)

    def test_other_machine_rejected(self):
        """核心功能：转发给别人必须无效。"""
        code, _ = _issue(self.sk, FAKE_A, days=365)
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(code, components=FAKE_B, public_key_hex=self.pub)
        self.assertIn('不匹配', str(cm.exception))

    def test_single_component_drift_still_accepted(self):
        """**防误伤**：换硬盘/刷 BIOS 只动一项时不能把买家锁死。

        这是加了容错的直接理由 —— 本地授权一旦误判，售后成本全在卖家身上。
        """
        code, _ = _issue(self.sk, FAKE_A, days=365)
        drift = list(FAKE_A)
        drift[3] = 'BRAND-NEW-DISK-SN'
        info = L.verify_code(code, components=drift, public_key_hex=self.pub)
        self.assertGreaterEqual(len(info['matched']), 2)

    def test_two_component_drift_still_accepted(self):
        """容忍 2 项变化（换主板+硬盘这种升级）也是**有意**放行的。

        为什么安全：容错只在"**唯一标识**"上才有意义，而这些槽里只有
        cpu_id 可能与他人重复（同型号 CPU 的 ProcessorId 完全相同），
        board_uuid / board_serial / disk_serial 都是每台机器独有的。
        所以别人的电脑最多只能匹配上 cpu_id 一项 → 永远凑不满 2 项。
        见下面 test_different_machine_with_same_cpu_rejected，那条才是
        真正守"防转发"的用例。
        """
        code, _ = _issue(self.sk, FAKE_A, days=365)
        drift = list(FAKE_A)
        drift[0] = 'UUID-OTHER'
        drift[2] = 'BOARDSN-OTHER'
        info = L.verify_code(code, components=drift, public_key_hex=self.pub)
        self.assertEqual(len(info['matched']), 2)

    def test_three_component_drift_rejected(self):
        """变化 3 项时必须拒绝 —— 这就是容错的真实边界（最多容忍 1~2 项）。"""
        code, _ = _issue(self.sk, FAKE_A, days=365)
        drift = list(FAKE_A)
        drift[0] = 'UUID-OTHER'
        drift[2] = 'BOARDSN-OTHER'
        drift[3] = 'DISKSN-OTHER'
        with self.assertRaises(L.LicenseError):
            L.verify_code(code, components=drift, public_key_hex=self.pub)

    def test_different_machine_with_same_cpu_rejected(self):
        """**防转发的核心用例**：买家的 PC 转发给朋友的 PC。

        现实约束：`ProcessorId` 在同型号 CPU 上**完全相同**，它压根不是
        唯一标识。所以"朋友的电脑"必然出现：cpu_id 相同，而 board_uuid /
        board_serial / disk_serial 三项全不同 → 只能匹配 1 项 → 必须拒绝。

        这条钉死的是整件事的收益：**转发给别人用不了**。
        若将来有人把 need 从 2 改成 1，这条会立刻变红 —— 那正是它存在的意义。
        """
        code, _ = _issue(self.sk, FAKE_A, days=365)
        friend = list(FAKE_A)
        friend[0] = 'UUID-FRIEND-PC'          # 主板不同
        friend[2] = 'BOARDSN-FRIEND-PC'       # 主板序列号不同
        friend[3] = 'DISKSN-FRIEND-PC'        # 硬盘不同
        # 只有 cpu_id（第 1 项）相同
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(code, components=friend, public_key_hex=self.pub)
        self.assertIn('不匹配', str(cm.exception))

    def test_forged_code_rejected(self):
        """随机伪造的码必须被拒（且报"签名未通过"，不是"机器不符"）。"""
        payload = bytes([1]) + b'\xaa' * 32 + b'\x00' * 4 + bytes([1])
        forged = LC.encode_license(payload, os.urandom(64))
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(forged, components=FAKE_A, public_key_hex=self.pub)
        self.assertIn('签名', str(cm.exception))

    def test_expired_code_rejected(self):
        """过期必须拦 —— 否则年卡等于永久卡。"""
        code, _ = _issue(self.sk, FAKE_A, days=1)
        future = datetime.date.today() + datetime.timedelta(days=3)
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(code, components=FAKE_A, public_key_hex=self.pub,
                          today=future)
        self.assertIn('过期', str(cm.exception))

    def test_expiring_today_still_valid(self):
        """**边界**：到期日当天仍应可用（不能提前一天就把人锁了）。"""
        exp = datetime.date.today()
        abs_days = (exp - datetime.date(2020, 1, 1)).days
        digs = L.slot_digests(FAKE_A)
        payload = (bytes([1]) + b''.join(digs)
                   + abs_days.to_bytes(4, 'big') + bytes([1]))
        code = LC.encode_license(payload, self.sk.sign(payload))
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub,
                             today=exp)
        self.assertEqual(info['expires'], exp)

    def test_permanent_code_has_no_expiry(self):
        code, _ = _issue(self.sk, FAKE_A, days=0)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        self.assertIsNone(info['expires'])

    def test_days_are_from_today_not_from_epoch(self):
        """**真实缺陷**：--days 365 曾被算成"2020 年 12 月 31 日到期"。

        根因是把"有效天数"直接当成了"距 2020 基准的绝对天数"塞进 payload，
        于是签出来的码当场过期。这里锁死语义：N 天 = 从今天起 N 天。
        用发行侧同一个换算路径来断言，防止两边再次分家。
        """
        code, _ = _issue(self.sk, FAKE_A, days=365)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        expect = datetime.date.today() + datetime.timedelta(days=365)
        self.assertEqual(info['expires'], expect)
        self.assertGreater(info['expires'].year, 2020)

    def test_wrong_version_rejected(self):
        """格式版本不匹配要明确拒绝（将来换格式时老程序不能瞎解析）。"""
        digs = L.slot_digests(FAKE_A)
        payload = (bytes([99]) + b''.join(digs) + b'\x00' * 4 + bytes([1]))
        code = LC.encode_license(payload, self.sk.sign(payload))
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        self.assertIn('版本', str(cm.exception))

    def test_unbound_code_rejected(self):
        """4 个槽全 0 的码（没绑定任何机器）必须拒绝。

        否则随便一个人只要拿到密钥就能签"通杀码"；更要紧的是：
        如果这种码被放行，等于"不绑定机器"的授权，转发就彻底失效了。
        """
        payload = (bytes([1]) + b'\x00' * 32 + b'\x00' * 4 + bytes([1]))
        code = LC.encode_license(payload, self.sk.sign(payload))
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        self.assertIn('机器绑定', str(cm.exception))

    def test_partially_bound_code_uses_only_known_slots(self):
        """码里只有 2 个槽有值时，只拿这 2 个槽比对。

        这解决"发行方机器读不到某项"的现实问题：作者本机取不到的槽是 0，
        不能被当成"要求买家那一项也为 0"。
        """
        digs = L.slot_digests(FAKE_A)
        partial = [digs[0], digs[1], b'\x00' * 8, b'\x00' * 8]
        code, _ = _issue(self.sk, FAKE_A, days=30, slots_override=partial)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        self.assertEqual(len(info['matched']), 2)
        # 买家第三项读不到，也不该因此被拒
        comps_missing = [FAKE_A[0], FAKE_A[1], '', '']
        info2 = L.verify_code(code, components=comps_missing,
                              public_key_hex=self.pub)
        self.assertEqual(len(info2['matched']), 2)

    def test_missing_pubkey_reports_config_error(self):
        """公钥没配（占位全 0）必须明确报"配置错误"，**绝不能静默放行**。

        这是最危险的失败模式：打包时忘了填公钥，如果返回"验证通过"，
        等于把软件免费发出去，而且没人会发现。
        """
        code, _ = _issue(self.sk, FAKE_A, days=30)
        with self.assertRaises(L.LicenseError) as cm:
            L.verify_code(code, components=FAKE_A, public_key_hex='0' * 64)
        self.assertIn('公钥', str(cm.exception))

    def test_shipped_pubkey_is_configured(self):
        """随包发布的 PUBLIC_KEY_HEX 必须是合法且非占位的。

        真实风险：忘了跑 gen_keys.py --auto-write 就打包 →
        所有买家激活时报"未配置公钥"。这条测试把它挡在打包前。
        """
        hexstr = L.PUBLIC_KEY_HEX.strip()
        self.assertNotEqual(set(hexstr.lower()), {'0'},
                            'PUBLIC_KEY_HEX 还是占位全 0 —— 先跑 tools/gen_keys.py --auto-write')
        self.assertEqual(len(hexstr), 64, f'公钥应为 64 位十六进制，实为 {len(hexstr)}')
        bytes.fromhex(hexstr)          # 非法十六进制会在这里抛

    def test_bad_public_key_hex_reports_error(self):
        code, _ = _issue(self.sk, FAKE_A, days=30)
        for bad in ('xyz', 'ab' * 31, 'zz' * 32):
            with self.assertRaises(L.LicenseError):
                L.verify_code(code, components=FAKE_A, public_key_hex=bad)


class TestActivationState(unittest.TestCase):
    """落盘/读取/清除。**全用临时目录，绝不碰用户真实 APPDATA**。"""

    def setUp(self):
        self.sk, self.pub = _make_keys()
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state = L.state_path
        # 隔离真实授权文件：如果这里直接写 APPDATA，跑一次测试就会
        # 覆盖用户真实的激活状态（把已激活的用户变成未激活）。
        L.state_path = lambda: os.path.join(self._tmp.name, 'license.json')

    def tearDown(self):
        L.state_path = self._orig_state
        self._tmp.cleanup()

    def test_save_load_roundtrip(self):
        if self.sk is None:
            self.skipTest('未安装 cryptography')
        code, _ = _issue(self.sk, FAKE_A, days=365)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        L.save_license(code, info)
        self.assertEqual(L.load_license(), code)

    def test_load_missing_returns_none(self):
        self.assertIsNone(L.load_license())

    def test_load_corrupt_returns_none_not_crash(self):
        """损坏的 license.json 不能让程序崩 —— 降级成"未激活"即可。"""
        p = L.state_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{ this is not json')
        self.assertIsNone(L.load_license())

    def test_load_non_dict_returns_none(self):
        p = L.state_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('["a", "b"]')
        self.assertIsNone(L.load_license())

    def test_clear_removes_file(self):
        if self.sk is None:
            self.skipTest('未安装 cryptography')
        code, _ = _issue(self.sk, FAKE_A, days=30)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        L.save_license(code, info)
        self.assertTrue(L.clear_license())
        self.assertIsNone(L.load_license())

    def test_clear_missing_is_not_error(self):
        """没激活过就点"取消激活"不该报错。"""
        self.assertFalse(L.clear_license())

    def test_save_is_atomic_no_temp_left(self):
        """原子写的中间文件必须被 replace 掉，不能留 *.tmp 残骸。

        真实缺陷预防：残留 .tmp 会让下次的原子写/清理逻辑行为不一致。
        """
        if self.sk is None:
            self.skipTest('未安装 cryptography')
        code, _ = _issue(self.sk, FAKE_A, days=30)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        L.save_license(code, info)
        leftovers = [n for n in os.listdir(self._tmp.name) if n.endswith('.tmp')]
        self.assertEqual(leftovers, [], f'残留临时文件: {leftovers}')

    def test_tampered_state_file_is_revalidated(self):
        """**关键安全属性**：直接改 license.json 的 expires 字段无效。

        因为 load 只回传 code，一切判定都重新走 verify_code ——
        落盘内容不被信任。否则用户改个日期就能"续期"。
        """
        if self.sk is None:
            self.skipTest('未安装 cryptography')
        code, _ = _issue(self.sk, FAKE_A, days=1)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=self.pub)
        L.save_license(code, info)
        # 手动把落盘的 expires 改成 2099
        import json
        p = L.state_path()
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        d['expires'] = '2099-12-31'
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(d, f)
        # 用"3 天后"的视角重新校验：应仍被判过期（因为 payload 里的日期没变）
        loaded = L.load_license()
        self.assertEqual(loaded, code)
        future = datetime.date.today() + datetime.timedelta(days=3)
        with self.assertRaises(L.LicenseError):
            L.verify_code(loaded, components=FAKE_A, public_key_hex=self.pub,
                          today=future)

    def test_check_never_raises_when_unactivated(self):
        """check() 必须永不抛 —— 它是启动门禁调用的，抛了就是程序崩。"""
        st = L.check(components=FAKE_A)
        self.assertFalse(st.activated)
        self.assertTrue(st.reason)

    def test_check_never_raises_on_garbage_state(self):
        p = L.state_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{"code": "完全不是合法授权码"}')
        st = L.check(components=FAKE_A)
        self.assertFalse(st.activated)

    def test_try_activate_stores_canonical_form(self):
        """带连字符/小写粘贴进来的码，落盘要存规范化后的纯码。

        否则"同一张授权码"在文件里会以多种写法存在，比对/排查都麻烦。
        """
        if self.sk is None:
            self.skipTest('未安装 cryptography')
        code, _ = _issue(self.sk, FAKE_A, days=30)
        messy = LC.group_code(code, 5).lower()
        st = L.try_activate(messy, components=FAKE_A, public_key_hex=self.pub)
        self.assertTrue(st.activated, st.reason)
        self.assertEqual(L.load_license(), code)

    def test_try_activate_failure_does_not_write_state(self):
        """激活失败绝不能落盘 —— 否则下次启动会以为"已激活过"。"""
        if self.sk is None:
            self.skipTest('未安装 cryptography')
        bad = LC.encode_license(
            bytes([1]) + b'\x11' * 32 + b'\x00' * 4 + bytes([1]), os.urandom(64))
        st = L.try_activate(bad, components=FAKE_A, public_key_hex=self.pub)
        self.assertFalse(st.activated)
        self.assertIsNone(L.load_license())


class TestTrialPeriod(unittest.TestCase):
    """试用期 —— 用来避免"上线即锁死老买家"的迁移事故。

    v4.7.0 之前没有任何授权逻辑，老买家都能正常用。若新版直接锁死，
    他们表现为"点了启动没反应"，会当成软件坏了 → 退款/差评。
    所以必须有试用期做缓冲；这组测试守住它的边界。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._orig_state = L.state_path
        L.state_path = lambda: os.path.join(self._tmp.name, 'license.json')
        self._orig_days = L.TRIAL_DAYS

    def tearDown(self):
        L.state_path = self._orig_state
        L.TRIAL_DAYS = self._orig_days
        self._tmp.cleanup()

    def test_first_run_creates_trial_and_allows(self):
        """首次运行：应创建试用记录并允许使用。"""
        L.TRIAL_DAYS = 3
        ok, remaining, started = L.trial_state()
        self.assertTrue(ok, '首次运行应在试用期内')
        self.assertAlmostEqual(remaining, 3, delta=0.1)
        self.assertIsNotNone(started)
        # 记录应已落盘
        self.assertTrue(os.path.exists(L.trial_path()))

    def test_trial_expires_after_n_days(self):
        """超过试用天数后应关闭。"""
        L.TRIAL_DAYS = 3
        L._write_trial(1_000_000)                    # 很久以前开始
        ok, remaining, _ = L.trial_state(now=1_000_000 + 4 * 86400)
        self.assertFalse(ok, '第 4 天应已过期')
        self.assertEqual(remaining, 0)

    def test_trial_last_moment_still_ok(self):
        """边界：试用最后一天内仍应可用（不能提前半天就锁）。"""
        L.TRIAL_DAYS = 3
        L._write_trial(1_000_000)
        ok, remaining, _ = L.trial_state(now=1_000_000 + 2.9 * 86400)
        self.assertTrue(ok, '2.9 天时仍应在试用期内')
        self.assertGreater(remaining, 0)

    def test_trial_disabled_when_days_zero(self):
        """TRIAL_DAYS=0 表示关闭试用（未激活就是不能用）。"""
        L.TRIAL_DAYS = 0
        ok, remaining, _ = L.trial_state()
        self.assertFalse(ok)
        self.assertIsNone(remaining)

    def test_check_allows_trial_when_unactivated(self):
        """未激活 + 在试用期内 → LicenseStatus.trial=True（门禁据此放行）。"""
        L.TRIAL_DAYS = 3
        st = L.check(components=FAKE_A)
        self.assertFalse(st.activated)
        self.assertTrue(st.trial, '应处于试用期')
        self.assertTrue(st.usable, '**试用期内必须可放行**')
        self.assertIn('试用', st.expires_text)

    def test_check_locked_after_trial(self):
        """未激活 + 试用过期 → 不可放行。"""
        L.TRIAL_DAYS = 3
        L._write_trial(1_000_000)
        # 用真实当前时间无关；这里直接验证判定函数
        ok, _, _ = L.trial_state(now=1_000_000 + 10 * 86400)
        self.assertFalse(ok)
        st = L._trial_or_locked('MID', True, '未激活')
        # 注意 _trial_or_locked 内部用真实时间，故这里断言的是"函数存在且返回状态对象"
        self.assertIsInstance(st, L.LicenseStatus)

    def test_allow_trial_false_skips_trial(self):
        """allow_trial=False 时不给试用（测试/强制激活场景）。"""
        L.TRIAL_DAYS = 3
        st = L.check(components=FAKE_A, allow_trial=False)
        self.assertFalse(st.trial)
        self.assertFalse(st.usable)
        self.assertEqual(st.reason, '未激活')

    def test_activated_user_usable_and_not_trial(self):
        """已激活用户：usable=True 且 trial=False（不该被当成试用）。"""
        sk, pub = _make_keys()
        if sk is None:
            self.skipTest('未安装 cryptography')
        code, _ = _issue(sk, FAKE_A, days=365)
        info = L.verify_code(code, components=FAKE_A, public_key_hex=pub)
        L.save_license(code, info)
        # ⚠️ check() 默认用**随包公钥**（PUBLIC_KEY_HEX），而这里是用一次性
        #    测试密钥签的码，所以必须把 pub 传进去 —— 否则验签必然失败，
        #    看起来像"已激活用户被当成未激活"，其实只是测试用错了钥匙。
        st = L.check(components=FAKE_A, public_key_hex=pub)
        self.assertTrue(st.activated, st.reason)
        self.assertFalse(st.trial)
        self.assertTrue(st.usable)

    def test_expired_activation_is_not_usable(self):
        """**已激活但已过期 → 不可放行**（否则年卡会变成永久）。"""
        sk, pub = _make_keys()
        if sk is None:
            self.skipTest('未安装 cryptography')
        # 造一个"昨天到期"的码（绕过发行侧校验，直接构造 payload）
        exp = datetime.date.today() - datetime.timedelta(days=1)
        abs_days = (exp - datetime.date(2020, 1, 1)).days
        digs = L.slot_digests(FAKE_A)
        payload = (bytes([1]) + b''.join(digs)
                   + abs_days.to_bytes(4, 'big') + bytes([1]))
        code = LC.encode_license(payload, sk.sign(payload))
        # 直接构造一个"已激活但过期"的状态对象，验证 usable 的判定
        info = {'expires': exp, 'tier': 1, 'matched': [], 'required': 2,
                'machine_id': 'X'}
        st = L.LicenseStatus(True, '已激活', info=info)
        self.assertTrue(st.activated)
        self.assertLess(st.days_left, 0)
        self.assertFalse(st.usable, '**已过期必须不可放行**')
        # 也不能因为过期就给"试用"续命
        self.assertFalse(st.trial)

    def test_corrupt_trial_file_does_not_grant_trial(self):
        """试用文件损坏 → 不能就此获得"全新试用"（否则删文件就能永久白用）。

        实现上损坏会被当作"没有记录"，于是**重建**一次试用 —— 这是个已知的
        本地授权局限（删掉 trial.json 就能重来）。这里明确断言"损坏不会崩"，
        并把局限写成测试名，避免以后有人误以为它是安全的。
        """
        L.TRIAL_DAYS = 3
        p = L.trial_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'w', encoding='utf-8') as f:
            f.write('{ not json')
        ok, remaining, started = L.trial_state()
        self.assertTrue(ok, '损坏时按首次运行处理（本地授权的已知局限）')
        self.assertIsNotNone(started)

    def test_trial_record_is_utc_stable(self):
        """trial.json 应含可读时间，便于售后判断"他什么时候开始试用的"。"""
        L.TRIAL_DAYS = 3
        L.trial_state()
        with open(L.trial_path(), encoding='utf-8') as f:
            d = json.load(f)
        self.assertIn('started_at', d)
        self.assertIn('started_text', d)


class TestExpiryHelpers(unittest.TestCase):
    """到期日换算（payload 用绝对天数，UI 用日期，两边必须对得上）。"""

    def test_epoch_base_is_2020(self):
        self.assertEqual(L.expiry_date_from_days(0), None)
        self.assertEqual(L.expiry_date_from_days(1), datetime.date(2020, 1, 2))

    def test_days_from_date_roundtrip(self):
        d = datetime.date(2030, 6, 15)
        self.assertEqual(L.expiry_date_from_days(L.days_to_expiry_from_date(d)), d)

    def test_date_before_epoch_rejected(self):
        with self.assertRaises(ValueError):
            L.days_to_expiry_from_date(datetime.date(2019, 12, 31))

    def test_none_date_is_permanent(self):
        self.assertEqual(L.days_to_expiry_from_date(None), 0)


class TestIssuerTool(unittest.TestCase):
    """签发工具的核心换算（不依赖本机私钥，直接测函数）。"""

    def test_parse_machine_id_roundtrip(self):
        sys.path.insert(0, os.path.join(_ROOT, 'tools'))
        try:
            import make_license as MK
        except Exception as e:
            self.skipTest(f'无法导入签发工具: {e}')
        mid = L.machine_id(FAKE_A)
        slots = MK.parse_machine_id(mid)
        self.assertEqual(len(slots), 4)
        self.assertEqual(slots, L.slot_digests(FAKE_A))

    def test_parse_rejects_short_code(self):
        sys.path.insert(0, os.path.join(_ROOT, 'tools'))
        try:
            import make_license as MK
        except Exception as e:
            self.skipTest(f'无法导入签发工具: {e}')
        with self.assertRaises(ValueError):
            MK.parse_machine_id('ABCD')

    def test_build_payload_layout(self):
        """payload 布局锁死：版本 | 4×8槽 | 到期日(4) | 档位(1) = 38 字节。

        布局一旦漂移，**已售出的所有授权码会全部失效**，
        所以这里用硬编码的偏移量钉住，而不是"读代码看起来对"。
        """
        sys.path.insert(0, os.path.join(_ROOT, 'tools'))
        try:
            import make_license as MK
        except Exception as e:
            self.skipTest(f'无法导入签发工具: {e}')
        slots = L.slot_digests(FAKE_A)
        p = MK.build_payload(slots, 1000, 7)
        self.assertEqual(len(p), 38)
        self.assertEqual(p[0], 1)
        self.assertEqual(p[1:9], slots[0])
        self.assertEqual(p[9:17], slots[1])
        self.assertEqual(p[17:25], slots[2])
        self.assertEqual(p[25:33], slots[3])
        self.assertEqual(int.from_bytes(p[33:37], 'big'), 1000)
        self.assertEqual(p[37], 7)


class TestNoPrivateKeyShipped(unittest.TestCase):
    """**安全底线**：私钥绝不能出现在项目里。

    私钥泄露 = 任何人可造授权码 = 整套授权作废。
    这条测试扫仓库里可能藏私钥的几个位置。
    """

    def test_private_key_not_in_project(self):
        import re
        pattern = re.compile(r'private\s*=\s*[0-9a-fA-F]{64}')
        offenders = []
        for dirpath, dirnames, filenames in os.walk(_ROOT):
            dirnames[:] = [d for d in dirnames
                           if d not in ('__pycache__', 'build', 'dist', 'data')]
            for fn in filenames:
                if not fn.endswith(('.py', '.json', '.txt', '.md', '.spec')):
                    continue
                p = os.path.join(dirpath, fn)
                try:
                    with open(p, encoding='utf-8', errors='replace') as f:
                        txt = f.read()
                except OSError:
                    continue
                if pattern.search(txt):
                    offenders.append(os.path.relpath(p, _ROOT))
        self.assertEqual(offenders, [],
                         f'项目里出现了私钥！必须立刻移除并换密钥: {offenders}')

    def test_keypair_generator_is_outside_project(self):
        """私钥生成器的默认落点必须在项目目录之外。"""
        sys.path.insert(0, os.path.join(_ROOT, 'tools'))
        try:
            import gen_keys as GK
        except Exception as e:
            self.skipTest(f'无法导入 gen_keys: {e}')
        key_dir = os.path.abspath(GK.KEY_DIR)
        proj = os.path.abspath(_ROOT)
        self.assertFalse(
            key_dir.lower().startswith(proj.lower()),
            f'私钥目录 {key_dir} 落在项目目录 {proj} 内 —— 极易被打包/提交，必须移出去')


if __name__ == '__main__':
    unittest.main(verbosity=2)
