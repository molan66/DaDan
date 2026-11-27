# -*- coding: utf-8 -*-
r"""有牙验证：把缺陷一个个放回去，确认探针真的判红。

只改字节、不改语义，每次改动后跑探针，期望 FAIL；最后还原并核对 sha256。

跑法：<venv python> tools/probe_runtime_dir_in_use_teeth.py
"""
import hashlib
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
MAIN = os.path.join(ROOT, 'main.py')
PROBE = os.path.join(HERE, 'probe_runtime_dir_in_use.py')
PY = sys.executable

NEW_BLOCK = """            ok, winerror = _open_probe_error(fp)
            if ok:
                continue                 # 能写开 -> 肯定没人独占
            if winerror is None:
                return True              # 探测本身失败 -> 保守当作在用
            if winerror == _ERROR_SHARING_VIOLATION:
                return True              # 判据①: 真被映射/独占 -> 在用
            if winerror == _ERROR_ACCESS_DENIED:
                # v4.9.1: 只是没写权限(DACL 不给), **不代表在用** ——
                # 这类文件删除只需父目录的 FILE_DELETE_CHILD, 实测可删。
                # 继续探测下一个文件, 别因为一个 RX-only 文件就整体放弃。
                continue
            return True                  # 其它未知错误 -> 保守当作在用"""

OLD_BLOCK = """            try:
                fd = os.open(fp, os.O_RDWR)
                os.close(fd)
            except OSError:
                return True
            except Exception:
                return True"""

# (名字, 原文, 改成)
BROKEN = [
    ('把判据退回 os.open(O_RDWR)（缺陷本体：权限不足被当成占用）',
     NEW_BLOCK, OLD_BLOCK),
    ('把 ERROR_ACCESS_DENIED 当成占用（只差一个分支）',
     "            if winerror == _ERROR_ACCESS_DENIED:\n                # v4.9.1: 只是没写权限(DACL 不给), **不代表在用** ——\n                # 这类文件删除只需父目录的 FILE_DELETE_CHILD, 实测可删。\n                # 继续探测下一个文件, 别因为一个 RX-only 文件就整体放弃。\n                continue",
     "            if winerror == _ERROR_ACCESS_DENIED:\n                return True"),
    ('去掉空目录短路（空目录会被 os.walk 判成可删，但这里改成一律在用）',
     "    if not names:\n        return False                     # 空目录(v4.6.6 残留的多为此类, 且已过年龄检查)",
     "    if not names:\n        return True"),
    ('清理逻辑不再识别 _MEI 前缀（_QUAR 目录永远扫不到）',
     "            if not name.startswith('_MEI'):",
     "            if not name.startswith('_MEIzzz'):"),
    # ⚠️ 这一条是 v4.9.2 的关键教训：只改名、不重建空目录 -> bootloader 照样弹框
    #    （窗口模式对照: none=卡住 / rename=卡住 / rename+empty=exit0）。
    #    必须由 verify_quarantine_unit.py 的"原路径重建为空目录"断言来抓。
    ('退出隔离漏掉"原路径重建空目录"这一步（只改名，bootloader 照样报错）',
     "        os.rename(mei, new)\n        # ② 关键: 原路径必须重新存在且为空, 否则 bootloader 会报\"删不掉\"。\n        try:\n            os.makedirs(mei, exist_ok=True)\n        except OSError:\n            pass",
     "        os.rename(mei, new)"),
]


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def read_bytes(path):
    with open(path, 'rb') as f:
        return f.read()


def write_bytes(path, data):
    # ⚠️ 必须用二进制写回。用 text 模式(`open(..., 'w')`)会把原先的 LF
    #    统一改成 CRLF，导致"还原后 sha256 不一致"，误以为源码被改坏
    #    （本机 2026-10-23 踩过：sha 从 3a3315… 变成 30c8d5…，内容其实一致）。
    with open(path, 'wb') as f:
        f.write(data)


def run_probe():
    """跑两个验证脚本：占用判定探针 + 退出隔离单元验证。

    两者缺一不可 —— 前 4 条破坏对应探针，第 5 条（漏掉重建空目录）
    只有 verify_quarantine_unit.py 抓得住。
    任一脚本非 0 即算"判红"。
    """
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    scripts = [
        os.path.join(HERE, 'probe_runtime_dir_in_use.py'),
        os.path.join(HERE, 'verify_quarantine_unit.py'),
    ]
    out_all, rc_all = [], 0
    for s in scripts:
        r = subprocess.run([PY, s], capture_output=True, text=True,
                           encoding='utf-8', env=env)
        out_all.append((r.stdout or '') + (r.stderr or ''))
        if r.returncode != 0:
            rc_all = r.returncode
    return rc_all, '\n'.join(out_all)


def main():
    raw = read_bytes(MAIN)
    orig = hashlib.sha256(raw).hexdigest()
    print('main.py 原始 sha256 =', orig)
    source = raw.decode('utf-8')

    rc0, out0 = run_probe()
    print('基线：探针 exit=%d（应为 0）' % rc0)
    if rc0 != 0:
        print('基线就是红的，先修，不要做有牙验证')
        print(out0)
        return 1

    failures = 0
    for idx, (name, old, new) in enumerate(BROKEN, 1):
        if old not in source:
            print('SKIP  %d) %s —— 找不到要替换的原文' % (idx, name))
            failures += 1
            continue
        broken = source.replace(old, new, 1)
        write_bytes(MAIN, broken.encode('utf-8'))
        # 清 __pycache__，避免踩「同一秒 mtime + 同大小 = 缓存有效」的坑
        for dirpath, dirnames, _fn in os.walk(ROOT):
            if '__pycache__' in dirnames:
                import shutil
                shutil.rmtree(os.path.join(dirpath, '__pycache__'), ignore_errors=True)
                dirnames.remove('__pycache__')
        rc, out = run_probe()
        ok = (rc != 0)
        print('%s  %d) %s  → 探针 exit=%d' % ('GOOD' if ok else 'BAD ', idx, name, rc))
        if not ok:
            failures += 1
        write_bytes(MAIN, raw)          # 二进制还原

    now = sha(MAIN)
    print('main.py 复原 sha256 =', now, '(一致)' if now == orig else '(不一致!!)')
    if now != orig:
        failures += 1
    print('-' * 90)
    print('有牙检查：%d 项中 %d 项未判红' % (len(BROKEN), failures))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
