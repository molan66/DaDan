# -*- coding: utf-8 -*-
"""验证门禁测试"真的有牙"：故意破坏门禁，确认测试会判红。

思路（本项目 probes/p17 同款做法）：
  把 main.py 的**门禁那三行临时删掉**，跑测试，必须**失败**；
  恢复后必须**通过**。如果删掉门禁测试还全绿，那这些测试就是摆设。

⚠️ 用备份+finally 恢复，绝不让 main.py 留在被破坏的状态。

⚠️⚠️ **必须清 __pycache__（踩过的坑，别删这段）**：
   Python 判断 .pyc 是否过期用的是 **mtime 秒级整数 + 文件大小**。
   本脚本「改 main.py → 跑测试 → 恢复 main.py」全在同一秒内完成，
   于是恢复后的源码 mtime 与被破坏版本生成的那个 .pyc **落在同一秒**，
   Python 认为缓存仍有效 → 测试实际导入的是**被破坏的字节码**。
   表现极具误导性：main.py 已逐字节还原（sha256 一致），
   但"恢复后应重新通过"这一项**假红** —— 让人以为恢复失败或测试有问题。
   所以每次跑测试前都清掉 __pycache__，保证跑的是磁盘上的真实源码。
"""
import io
import os
import re
import shutil
import subprocess
import sys

PROJ = r'D:\AI\大蛋小助手\dadan-build'
MAIN = os.path.join(PROJ, 'main.py')
PY = sys.executable

GATE = """        if not self._license_ok():
            self._prompt_activation()
            return
"""


def purge_pycache():
    """清掉项目里的 __pycache__（跳过 node_modules/.wrangler）。

    不清就会踩上面 docstring 里写的"同秒 mtime → 陈旧字节码被复用"的坑。
    """
    n = 0
    for root, dirs, files in os.walk(PROJ):
        base = os.path.basename(root)
        if base in ('node_modules', '.wrangler', 'build', 'dist'):
            dirs[:] = []
            continue
        if base == '__pycache__':
            for f in files:
                if f.endswith(('.pyc', '.pyo')):
                    try:
                        os.remove(os.path.join(root, f))
                        n += 1
                    except OSError:
                        pass
    return n


def run_tests():
    purged = purge_pycache()
    r = subprocess.run([PY, '-X', 'utf8', '-m', 'unittest', 'tests.test_license_gate'],
                       cwd=PROJ, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return r.returncode, (r.stdout or '') + (r.stderr or ''), purged


def main():
    with io.open(MAIN, encoding='utf-8') as f:
        original = f.read()
    # 记录原始时间戳：探针结束时要连 mtime 一起还原，
    # 否则每跑一次都会让 main.py"看起来比 exe 新"（见 finally 里的注释）。
    st = os.stat(MAIN)
    orig_atime, orig_mtime = st.st_atime, st.st_mtime

    if GATE not in original:
        print('[前提失败] main.py 里找不到门禁代码块，无法做"拔牙"验证。')
        print('          期望片段：\n' + GATE)
        return 2

    results = []
    try:
        # ---- 对照组：正常状态应通过
        code, out, npurged = run_tests()
        ok_normal = (code == 0)
        results.append(('基线（未破坏）应通过', ok_normal))
        print(f'[1] 基线运行 → 退出码 {code}  {"PASS" if ok_normal else "FAIL"}'
              f'  （清掉 {npurged} 个陈旧 .pyc）')

        # ---- 实验组 A：删掉整个门禁块（模拟"有人不小心删了"）
        broken = original.replace(GATE, '', 1)
        with io.open(MAIN, 'w', encoding='utf-8', newline='') as f:
            f.write(broken)
        code_a, out_a, _ = run_tests()
        failed_a = (code_a != 0)
        results.append(('删掉门禁块后测试应判红', failed_a))
        print(f'[2] 删掉门禁块 → 退出码 {code_a}  '
              f'{"PASS（测试有牙）" if failed_a else "FAIL（测试是摆设！）"}')
        if failed_a:
            names = re.findall(r'^(?:FAIL|ERROR): (\S+)', out_a, re.M)
            print(f'    判红的用例（前 6 个）：{names[:6]}')

        # ---- 实验组 B：只把 return 删掉（校验了却照样放行）
        no_return = GATE.replace('            return\n', '', 1)
        with io.open(MAIN, 'w', encoding='utf-8', newline='') as f:
            f.write(original.replace(GATE, no_return, 1))
        code_b, out_b, _ = run_tests()
        failed_b = (code_b != 0)
        results.append(('删掉 return 后测试应判红', failed_b))
        print(f'[3] 只删 return → 退出码 {code_b}  '
              f'{"PASS（测试有牙）" if failed_b else "FAIL（测试是摆设！）"}')

        # ---- 实验组 C：把门禁挪到线程派发之后（顺序反了）
        m = re.search(r'(\n        self\._starting = True\n)', original)
        if m:
            anchor = m.group(1)
            moved = original.replace(GATE, '', 1).replace(
                anchor, anchor + GATE.replace('        ', '        '), 1)
            with io.open(MAIN, 'w', encoding='utf-8', newline='') as f:
                f.write(moved)
            code_c, out_c, _ = run_tests()
            failed_c = (code_c != 0)
            results.append(('门禁后移后测试应判红', failed_c))
            print(f'[4] 门禁移到派发之后 → 退出码 {code_c}  '
                  f'{"PASS（测试有牙）" if failed_c else "FAIL（测试是摆设！）"}')
    finally:
        # 无论如何都恢复原文，并逐字节核对。
        # ⚠️ **连 mtime 一起恢复**（踩过的坑，别删）：
        #    本探针会重写 main.py，若不还原 mtime，则**每跑一次 run_all.mjs**
        #    都会把 main.py 的修改时间推到最新 → 之后任何"源码比产物新"的
        #    检查都会误报「需要重新打包」，而内容其实一字未变。
        #    这是"工具留下误导性痕迹"，比功能 bug 更烦人：它会让人反复重打包。
        with io.open(MAIN, 'w', encoding='utf-8', newline='') as f:
            f.write(original)
        try:
            os.utime(MAIN, (orig_atime, orig_mtime))
        except OSError as e:
            print(f'[警告] main.py mtime 还原失败（不影响内容）：{e}')

    with io.open(MAIN, encoding='utf-8') as f:
        restored = f.read()
    same = (restored == original)
    print(f'\n[恢复] main.py 与原文逐字节一致 = {same}')
    print(f'[恢复] mtime 已还原 = {os.path.getmtime(MAIN) == orig_mtime}')
    if not same:
        print('        ❌ 恢复失败！请立刻从 git 恢复 main.py')
        return 3

    # 恢复后再跑一遍，确认干净（run_tests 会先清 pycache，故不会复用脏字节码）
    code_final, _, _ = run_tests()
    print(f'[复原核对] 再次运行 → 退出码 {code_final}  '
          f'{"PASS" if code_final == 0 else "FAIL"}')
    results.append(('恢复后应重新通过', code_final == 0))

    print('\n' + '=' * 62)
    allok = all(v for _, v in results)
    for label, v in results:
        print(f'  {"✓" if v else "✗"} {label}')
    print('=' * 62)
    print('结论：' + ('门禁测试真的有牙 ✅' if allok else '有实验组没判红，测试需加强 ❌'))
    return 0 if allok else 1


if __name__ == '__main__':
    sys.exit(main())
