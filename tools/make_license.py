# -*- coding: utf-8 -*-
"""make_license.py —— 用私钥给买家的机器码签发授权码（**作者本机专用**）

## 这是你每卖一单要跑的脚本

流程：
    1. 买家打开软件 → 「激活」页显示机器码 → 发给你
    2. 你：python tools/make_license.py --machine <机器码> --days 365
    3. 把打印出来的授权码发给买家 → 他粘进软件 → 完成

## 用法

    # 年卡（推荐默认）
    python tools/make_license.py --machine ABCD-EFGH-... --days 365

    # 永久
    python tools/make_license.py --machine ABCD-EFGH-... --days 0

    # 指定具体到期日
    python tools/make_license.py --machine ABCD-... --until 2027-01-01

    # 批量（每行一个机器码，便于一次给多个买家出码）
    python tools/make_license.py --file buyers.txt --days 365

    # 只校验一个机器码格式对不对（不出码）
    python tools/make_license.py --machine ABCD-... --check

    # 查自己的密钥状态
    python tools/make_license.py --info

## 安全

读取 %USERPROFILE%\\.dadan-license\\private_key.txt。
私钥**不会被写进任何输出**；脚本只打印授权码。
"""
import argparse
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import license_crypto as LC                            # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ed25519    # noqa: E402

KEY_PATH = os.path.join(os.path.expanduser('~'), '.dadan-license', 'private_key.txt')
SLOT_TAGS = ('board_uuid', 'cpu_id', 'board_serial', 'disk_serial')


def load_private():
    """读私钥。找不到就给出**可操作**的报错，而不是一句 FileNotFoundError。"""
    if not os.path.exists(KEY_PATH):
        print(f'[错误] 找不到私钥：{KEY_PATH}')
        print('       先跑一次：python tools/gen_keys.py')
        sys.exit(2)
    with open(KEY_PATH, encoding='utf-8') as f:
        txt = f.read()
    m = re.search(r'^\s*private\s*=\s*([0-9a-fA-F]{64})\s*$', txt, re.M)
    if not m:
        print(f'[错误] 私钥文件里没有合法的 private= 行：{KEY_PATH}')
        print('       文件可能被改动或截断。重新生成会让你已发出的码全部失效，')
        print('       所以请先找备份恢复。')
        sys.exit(2)
    try:
        return ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(m.group(1))), txt
    except Exception as e:
        print(f'[错误] 私钥无法解析：{type(e).__name__}: {e}')
        sys.exit(2)


def parse_machine_id(text):
    """机器码 → 4 个 8 字节槽指纹。格式错直接抛 ValueError（带可读原因）。"""
    raw = LC.b32d(text)
    if len(raw) != 32:
        raise ValueError(f'机器码解出 {len(raw)} 字节，应为 32（4 个槽 × 8 字节）—— 请核对是否抄全')
    return [raw[i * 8:(i + 1) * 8] for i in range(4)]


def build_payload(slots, abs_days, tier):
    """payload = 版本(1) + 4×槽指纹(32) + 到期日(4) + 档位(1) = 38 字节。

    ⚠️ abs_days 是**绝对天数**（距 2020-01-01 的天数，0=永久），
       **不是"有效天数"**。把 --days 365 直接塞进来会得到 2020-12-31 这种
       已经过期的到期日 —— 见 issue() 里先换算成具体日期再转绝对天数。
    """
    if len(slots) != 4:
        raise ValueError('槽数量应为 4')
    if abs_days < 0:
        raise ValueError('到期天数不能为负')
    return (bytes([1]) + b''.join(slots)
            + int(abs_days).to_bytes(4, 'big') + bytes([tier]))


def issue(machine_id, days=365, tier=1, until=None):
    """返回 (授权码, 人类可读说明)。

    --days N 的语义是「**从今天起** N 天」；--until 直接指定到期日；
    days=0 且无 until 表示永久。
    """
    priv, _ = load_private()
    slots = parse_machine_id(machine_id)

    today = datetime.date.today()
    if until:
        try:
            exp_date = datetime.date.fromisoformat(until)
        except ValueError:
            raise ValueError(f'--until 需要 YYYY-MM-DD 格式，收到 {until!r}')
        if exp_date < today:
            raise ValueError(f'--until {until} 已经过期了（今天是 {today.isoformat()}）')
    elif days:
        exp_date = today + datetime.timedelta(days=int(days))
    else:
        exp_date = None                      # 永久

    abs_days = 0 if exp_date is None else LC_days_from_date(exp_date)
    payload = build_payload(slots, abs_days, tier)
    sig = priv.sign(payload)
    code = LC.encode_license(payload, sig)

    exp_text = '永久' if exp_date is None else exp_date.isoformat()

    # 自检：签完立刻用**公钥**验一遍。宁可现在发现，也别把坏码发给买家。
    pub = priv.public_key().public_bytes_raw().hex()
    if not LC.ed25519_verify(bytes.fromhex(pub), sig, payload):
        raise RuntimeError('自检失败：刚签出的码验不过 —— 不要发出，先查问题')

    bound = [SLOT_TAGS[i] for i in range(4) if slots[i] != b'\x00' * 8]
    return code, {
        'expires': exp_text,
        'tier': tier,
        'bound_slots': bound,
        'payload_len': len(payload),
        'machine_id': LC.b32e(LC.b32d(machine_id)),
    }


def LC_days_from_date(d):
    delta = (d - datetime.date(2020, 1, 1)).days
    if delta <= 0:
        raise ValueError('到期日必须晚于 2020-01-01')
    return delta


def _print_one(machine_id, code, info):
    print()
    print('─' * 66)
    print(f'  机器码   : {LC.group_code(LC.b32e(LC.b32d(machine_id)), 4)}')
    print(f'  有效期至 : {info["expires"]}')
    print(f'  绑定槽位 : {", ".join(info["bound_slots"]) or "（无！该机器码不含绑定信息，别发）"}')
    print('─' * 66)
    print('  授权码（整段复制发给买家，可带连字符）：')
    print()
    grouped = LC.group_code(code, 5)
    for i in range(0, len(grouped), 60):
        print('    ' + grouped[i:i + 60])
    print()
    print('  纯码（无连字符）：')
    print('    ' + code)
    print('─' * 66)


def main():
    ap = argparse.ArgumentParser(
        description='大蛋小助手 授权码签发（作者本机，用私钥）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--machine', '-m', help='买家提供的机器码')
    ap.add_argument('--file', '-f', help='批量：每行一个机器码的文件')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--days', '-d', type=int, default=None,
                   help='有效期天数，从**今天**起算；0 = 永久（默认 365）')
    g.add_argument('--until', help='到期日 YYYY-MM-DD')
    ap.add_argument('--tier', type=int, default=1, help='档位（1=正式 2=试用，默认 1）')
    ap.add_argument('--check', action='store_true', help='只校验机器码格式，不出码')
    ap.add_argument('--json', action='store_true', help='以 JSON 输出（便于脚本/表格记录）')
    ap.add_argument('--info', action='store_true', help='显示密钥状态后退出')
    a = ap.parse_args()

    if a.info:
        priv, txt = load_private()
        pub = priv.public_key().public_bytes_raw().hex()
        print(f'私钥文件 : {KEY_PATH}')
        print(f'公钥     : {pub}')
        m = re.search(r'^# 生成时间: (.+)$', txt, re.M)
        print(f'生成时间 : {m.group(1) if m else "未知"}')
        print()
        print('提示：这个公钥必须与 core/licensing.py 里的 PUBLIC_KEY_HEX 一致，')
        print('      否则签出来的码在用户机器上验不过。核对：')
        print('        python tools/gen_keys.py --show-public')
        return 0

    if not a.machine and not a.file:
        ap.print_help()
        print('\n[错误] 至少要给 --machine 或 --file。')
        return 2

    days = 0 if (a.days == 0) else (365 if a.days is None and not a.until else a.days)

    targets = []
    if a.machine:
        targets.append(a.machine.strip())
    if a.file:
        if not os.path.exists(a.file):
            print(f'[错误] 找不到文件：{a.file}')
            return 2
        with open(a.file, encoding='utf-8-sig') as f:
            for ln in f:
                ln = ln.strip()
                if ln and not ln.startswith('#'):
                    targets.append(ln)
    if not targets:
        print('[错误] 没有可处理的机器码。')
        return 2

    if a.check:
        ok = True
        for t in targets:
            try:
                slots = parse_machine_id(t)
                nonzero = sum(1 for s in slots if s != b'\x00' * 8)
                print(f'[OK]   {LC.group_code(LC.b32e(LC.b32d(t)), 4)}  有效槽位 {nonzero}/4')
                if nonzero < 2:
                    print('       ⚠️ 有效槽位少于 2 个：该码容错不足，激活容易误判。'
                          '请让买家确认软件能读到主板/CPU 信息。')
            except ValueError as e:
                ok = False
                print(f'[FAIL] {t[:40]}...  {e}')
        print()
        print('全部通过' if ok else '有机器码不合法（见上）')
        return 0 if ok else 1

    results = []
    fails = 0
    for t in targets:
        try:
            code, info = issue(t, days=days, tier=a.tier, until=a.until)
        except ValueError as e:
            fails += 1
            if a.json:
                print(json.dumps({'machine': t, 'error': str(e)}, ensure_ascii=False),
                      file=sys.stderr)
            else:
                print(f'\n[失败] {t}\n       {e}')
            continue
        except SystemExit:
            raise
        except Exception as e:
            fails += 1
            if a.json:
                print(json.dumps({'machine': t, 'error': f'{type(e).__name__}: {e}'},
                                 ensure_ascii=False), file=sys.stderr)
            else:
                print(f'\n[失败] {t}\n       {type(e).__name__}: {e}')
            continue
        results.append({'machine': info.get('machine_id', t), 'code': code,
                        'expires': info['expires'], 'tier': info['tier'],
                        'bound_slots': info['bound_slots']})
        if not a.json:
            _print_one(t, code, info)

    # --json 模式：**整个 stdout 必须是纯 JSON**（便于脚本直接 json.loads）。
    # 汇总/失败信息一律走 stderr —— 早先混在 stdout 尾部会让解析直接报
    # "Extra data"，等于这个选项不可用。
    # ⚠️ 有效期文案不能用 `days` 变量拼：走 --until 时 days 是 None，
    #    会印出"有效期：永久"，而那个码其实是有具体到期日的（实测踩到）。
    #
    # 🔴 v4.9.3 修（真缺陷）：这里原先写的是 `if until:` —— 而 `until`
    #    **在本函数里从未定义**（只有 `a.until`）。后果是**每一次**调用都会在
    #    打完授权码之后抛 `NameError: name 'until' is not defined`：
    #      · 终端里码已经打印出来了，看着像成功；
    #      · 但 exit code = 1、汇总行缺失；--json 模式下 stdout 已有半截输出，
    #        调用方 json.loads 直接炸。
    #    这是"每卖一单都要跑"的工具，不该坏在这条路上。
    if a.until:
        span_text = f'至 {a.until}'
    elif days:
        span_text = f'{days} 天'
    else:
        span_text = '永久'
    summary = (f'共签发 {len(results)} 个授权码'
               + (f'，失败 {fails} 个' if fails else '')
               + f'。有效期：{span_text}')
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        print(summary, file=sys.stderr)
    elif results:
        print('\n' + summary)
    return 1 if fails and not results else 0


if __name__ == '__main__':
    sys.exit(main())
