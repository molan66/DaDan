# -*- coding: utf-8 -*-
"""有牙验证：注释数字探针是否真的会判红。

做法：把 ui/icons.py 里现行的「594 条 / 527 条」临时改回旧的「374 个里有 344 个」，
跑 tools/probe_stale_comment_counts.py，确认它判红；再逐字节还原、校验 sha256。

为什么要单独一个脚本而不是在命令行里 -replace：
    本机控制台是 GBK，PowerShell 内联命令里的中文会被编码坏掉（实测替换静默不生效，
    文件里连旧串都没变）。用 Python + 显式 utf-8 读写才可靠。

用法：
    <python> tools/probe_stale_comment_teeth.py [图标目录]
退出码 0 = 探针有牙；1 = 探针没判红（护栏是假的）。
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
TARGET = os.path.join(_ROOT, 'ui', 'icons.py')
PROBE = os.path.join(_HERE, 'probe_stale_comment_counts.py')

NEW_TEXT = '16.21 实测 594 条里 527 条是白蒙版'
OLD_TEXT = '374 个里有 344 个是白蒙版'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(65536), b''):
            h.update(b)
    return h.hexdigest()


def run_probe(icons):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    if icons:
        env['DADAN_AUG_ICONS'] = icons
    r = subprocess.run([sys.executable, PROBE], cwd=_ROOT,
                       capture_output=True, env=env)
    out = r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')
    return r.returncode, out


def main(argv):
    icons = argv[1] if len(argv) > 1 else os.environ.get('DADAN_AUG_ICONS', '')
    before = sha256(TARGET)
    bak = TARGET + '.teeth.bak'
    shutil.copy2(TARGET, bak)
    ok = True
    try:
        with io.open(TARGET, encoding='utf-8') as f:
            src = f.read()
        if src.count(NEW_TEXT) != 1:
            print('[SKIP] 锚点不唯一/不存在（count=%d），无法做破坏实验'
                  % src.count(NEW_TEXT))
            return 1
        with io.open(TARGET, 'w', encoding='utf-8', newline='') as f:
            f.write(src.replace(NEW_TEXT, OLD_TEXT))

        # 确认破坏真的落到了文件上（不靠"以为改了"）
        with io.open(TARGET, encoding='utf-8') as f:
            now = f.read()
        if OLD_TEXT not in now:
            print('[!!] 破坏没有生效 —— 文件里找不到旧串，实验无效')
            ok = False
        else:
            code, out = run_probe(icons)
            if code == 0:
                print('[FAIL] 改回旧数字后探针仍然通过 —— 护栏是假的')
                print(out[-800:])
                ok = False
            else:
                hit = [ln for ln in out.splitlines() if '过期' in ln or '应为' in ln]
                print('[RED ] 改回旧数字后探针判红')
                for ln in hit[:4]:
                    print('        %s' % ln.strip())
    finally:
        shutil.copy2(bak, TARGET)
        os.remove(bak)

    after = sha256(TARGET)
    print()
    if after != before:
        print('[!!] 还原失败  sha256 %s -> %s' % (before[:16], after[:16]))
        return 1
    print('还原校验通过：ui/icons.py sha256 与实验前一致')

    code, out = run_probe(icons)
    if code != 0:
        print('[!!] 还原后探针仍然失败：\n%s' % out[-800:])
        return 1
    print('还原后探针通过')
    print('\nPASS 注释数字探针有牙' if ok else '\nFAIL 见上')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
