# -*- coding: utf-8 -*-
"""gen_keys.py —— 生成 Ed25519 密钥对（**只在作者本机跑一次**）

## 安全铁律（改这个文件前先读）

    · 私钥**绝不进项目目录、绝不进 git、绝不进 exe**。
      默认落点：%USERPROFILE%\\.dadan-license\\private_key.txt（项目外）。
      本文件被 git 跟踪，但**生成出来的私钥不在仓库里**。
    · 公钥是公开信息，写进 core/licensing.py 的 PUBLIC_KEY_HEX。

## 一旦泄露 / 丢失怎么办

    · 丢失 → 无法再签发授权码（但已发的码在 2020 年后仍有效，因为公钥没变）。
      故生成后**立刻备份私钥**到至少两处离线位置。
    · 泄露 → 别人能造码。必须重新生成密钥对、更新 PUBLIC_KEY_HEX、
      重新打包发版；**已售出的用户需要重新激活**（旧码会失效）。
      这就是为什么私钥要单独放。

## 用法

    python tools/gen_keys.py                    # 生成新密钥对
    python tools/gen_keys.py --force            # 覆盖已存在的私钥（危险，会换密钥）
    python tools/gen_keys.py --show-public      # 只打印当前公钥的 Python 字面量
"""
import argparse
import os
import re
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.asymmetric import ed25519      # noqa: E402

KEY_DIR = os.path.join(os.path.expanduser('~'), '.dadan-license')
KEY_PATH = os.path.join(KEY_DIR, 'private_key.txt')
PROJECT_LICENSING = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'core', 'licensing.py')


def _fmt_hex(h):
    """格式化成 C 源码风格的 32 字符一行，便于粘进 licensing.py。"""
    return '\n'.join("    '%s'" % h[i:i + 32] for i in range(0, len(h), 32))


def show_public():
    if not os.path.exists(KEY_PATH):
        print(f'[公钥] 还没有私钥文件：{KEY_PATH}')
        print('       先跑 `python tools/gen_keys.py` 生成。')
        return 1
    with open(KEY_PATH, encoding='utf-8') as f:
        txt = f.read()
    m = re.search(r'^\s*public\s*=\s*([0-9a-fA-F]{64})\s*$', txt, re.M)
    if not m:
        print('[公钥] 私钥文件里没找到 public= 行，文件可能被改坏了。')
        return 1
    h = m.group(1).lower()
    print('把下面这段填进 core/licensing.py 的 PUBLIC_KEY_HEX：\n')
    print('PUBLIC_KEY_HEX = (')
    print(_fmt_hex(h))
    print(')')
    return 0


def _write_public_into_licensing(pub_hex):
    """把公钥写进 core/licensing.py（只在用户显式要求时做）。"""
    with open(PROJECT_LICENSING, encoding='utf-8') as f:
        src = f.read()
    new_body = '(' + '\n'.join("    '%s'" % pub_hex[i:i + 32]
                               for i in range(0, len(pub_hex), 32)) + '\n)'
    pat = re.compile(r'PUBLIC_KEY_HEX = \(.*?\n\)', re.S)
    if not pat.search(src):
        print(f'[自动写入] 没在 {PROJECT_LICENSING} 里找到 PUBLIC_KEY_HEX = (...) 结构，请手工填。')
        return False
    src2 = pat.sub('PUBLIC_KEY_HEX = ' + new_body, src, count=1)
    with open(PROJECT_LICENSING, 'w', encoding='utf-8', newline='') as f:
        f.write(src2)
    print(f'[自动写入] 公钥已写入 {PROJECT_LICENSING}')
    return True


def _read_existing_key():
    """返回 (是否存在合法私钥, 文件内容)。0 字节 / 无 private= 行的残文件不算数。

    ⚠️ 这个判断是必需的：本脚本**先建文件再写入**，若写入过程失败（例如编码
    错误）会留下一个 0 字节文件；若不识别，下次运行就会以"私钥已存在"为由
    拒绝生成，用户被一个空壳文件永久卡住（本机实测踩到过）。
    """
    if not os.path.exists(KEY_PATH):
        return False, ''
    try:
        with open(KEY_PATH, encoding='utf-8', errors='replace') as f:
            txt = f.read()
    except OSError:
        return False, ''
    if re.search(r'^\s*private\s*=\s*[0-9a-fA-F]{64}\s*$', txt, re.M):
        return True, txt
    return False, txt


def generate(force=False, auto_write=False):
    os.makedirs(KEY_DIR, exist_ok=True)
    valid, old_txt = _read_existing_key()
    if valid and not force:
        print(f'[中止] 私钥已存在：{KEY_PATH}')
        print('       换个密钥对会让**所有已售出的授权码立即失效**，所以不默认覆盖。')
        print('       确实要换：--force（并准备好重新给老用户发码）')
        return 1
    if not valid and old_txt.strip():
        print(f'[提示] {KEY_PATH} 存在但不是合法私钥（无 private= 行），将重新生成。')

    priv = ed25519.Ed25519PrivateKey.generate()
    priv_raw = priv.private_bytes_raw()
    pub_raw = priv.public_key().public_bytes_raw()
    pub_hex = pub_raw.hex()

    # 用一次自检签名确认 key 可用，避免写出一个坏 key。
    # ⚠️ verify() 成功时返回 None（失败才抛 InvalidSignature），
    #    所以这里必须 try/except，不能写成 `assert verify(...)` —— 那恒为假。
    probe = secrets.token_bytes(16)
    try:
        priv.public_key().verify(priv.sign(probe), probe)
    except Exception as e:
        print(f'[中止] 密钥自检失败：{type(e).__name__}: {e}')
        return 1

    body = f"""# 大蛋小助手 授权私钥 —— 严禁泄露 / 严禁入 git / 严禁进 exe
#
# 生成时间: {__import__('time').strftime('%Y-%m-%d %H:%M:%S')}
# 用途: tools/make_license.py 读取本文件的 private= 来签发授权码
#
# ⚠️ 备份提醒: 本文件丢失后你将**无法再签发新授权码**。
#    请立刻复制到至少两处离线位置（U盘 / 密码管理器 / 加密压缩包）。
# ⚠️ 泄露提醒: 任何人拿到本文件都能批量造授权码。
#    公钥（可公开）已在下方，它是要填进 core/licensing.py 的那份。

public  = {pub_hex}
private = {priv_raw.hex()}
"""
    # 原子写：先写临时文件再替换 —— 中途失败不会留下半截文件把用户卡住
    tmp = KEY_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(body)
    os.replace(tmp, KEY_PATH)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass

    print('=' * 66)
    print('  密钥对已生成')
    print('=' * 66)
    print(f'  私钥: {KEY_PATH}')
    print(f'  公钥: {pub_hex}')
    print()
    print('  下一步：')
    print('    1) 立刻把私钥备份到两处离线位置（丢了就再也发不了码）')
    print('    2) 把公钥填进 core/licensing.py：')
    print('         python tools/gen_keys.py --show-public')
    print('      或让它自动写：加 --auto-write')
    print()
    if auto_write:
        _write_public_into_licensing(pub_hex)
    return 0


def main():
    ap = argparse.ArgumentParser(description='大蛋小助手 Ed25519 密钥生成（作者本机，跑一次）')
    ap.add_argument('--force', action='store_true', help='覆盖已有私钥（会让已发出的码失效）')
    ap.add_argument('--show-public', action='store_true', help='只打印当前公钥字面量')
    ap.add_argument('--auto-write', action='store_true', help='生成后自动把公钥写进 core/licensing.py')
    a = ap.parse_args()
    if a.show_public:
        sys.exit(show_public())
    sys.exit(generate(force=a.force, auto_write=a.auto_write))


if __name__ == '__main__':
    main()
