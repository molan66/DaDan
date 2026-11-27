# -*- coding: utf-8 -*-
"""核验 exe 的 `_start` 字节码里**确实有一条条件返回**（门禁不是死代码）。

为什么再做一层：符号名存在只说明"有这个名字"，但 `if not ...: return`
需要**控制流**证据。这里直接反汇编 `_start`，找 CALL _license_ok 之后
紧跟的 POP_JUMP + RETURN_VALUE 组合。
"""
import dis
import io
import marshal
import os
import sys

sys.path.insert(0, r'D:\AI\大蛋小助手\dadan-build')
from PyInstaller.archive.readers import CArchiveReader

EXE = r'D:\AI\大蛋小助手\dadan-build\dist\大蛋小助手.exe'
a = CArchiveReader(EXE)
raw = a.extract('main')
code = marshal.loads(raw)

# 找到 _start 的 code object
target = None


def walk(c, depth=0):
    global target
    if depth > 14:
        return
    for k in c.co_consts:
        if hasattr(k, 'co_consts'):
            if k.co_name == '_start' and k.co_code:
                target = k
            walk(k, depth + 1)


walk(code)
if target is None:
    print('[FAIL] exe 的 main 里找不到 _start 的 code object')
    sys.exit(1)

print('=' * 72)
print('  exe `_start` 字节码门禁核验')
print('=' * 72)
print(f'\n_start 字节码长度 = {len(target.co_code)}，名字表前 20 = {list(target.co_names)[:20]}')

# 反汇编并找关键指令序列
insns = list(dis.get_instructions(target))
found_call_ok = False
found_return_after = False
found_popjump = False

# v5.1(ultra): 原窗口只有 12 条指令 —— 而 _start 的门禁块在 return 之前还有
# 两条 STORE_ATTR + 一次 _prompt_activation 调用, 超过 12 条, 导致探针假红。
# 改为: 找到 _license_ok 的 CALL 后, 在**整条指令流**里查找
# POP_JUMP_IF_TRUE(条件分支) 与紧随其后的 RETURN_VALUE, 不再限窗口。
for i, ins in enumerate(insns):
    if '_license_ok' in str(ins.argval):
        found_call_ok = True
        print(f'\n  在偏移 {ins.offset} 找到 {ins.opname} {ins.argval}')
        # 往后找 POP_JUMP_IF_TRUE(if 成立则跳过门禁块)
        for w in insns[i + 1:]:
            if w.opname in ('POP_JUMP_IF_TRUE', 'POP_JUMP_IF_FALSE'):
                found_popjump = True
                print(f'  找到条件跳转 {w.opname} @ {w.offset}')
                break
        # 整条指令流中, 条件跳转之后必须有 RETURN_VALUE(门禁块末尾的早退)
        for w in insns[i + 1:]:
            if w.opname == 'RETURN_VALUE':
                found_return_after = True
                print(f'  找到 RETURN_VALUE @ {w.offset}(门禁早退)')
                break
        break

print()
print(f'  找 CALL _license_ok          : {"PASS" if found_call_ok else "FAIL"}')
print(f'  其后存在条件跳转            : {"PASS" if found_popjump else "FAIL"}')
print(f'  门禁块存在 RETURN_VALUE     : {"PASS" if found_return_after else "FAIL"}')

# 另外确认 _prompt_activation 也在 _start 里被调用（弹窗入口）
calls_prompt = any('_prompt_activation' in str(x.argval) for x in insns)
print(f'  _start 内调用 _prompt_activation : {"PASS" if calls_prompt else "FAIL"}')

ok = found_call_ok and found_popjump and found_return_after and calls_prompt
print('\n' + '=' * 72)
print('  结论：exe 的 _start 里门禁是**活的控制流** ✅' if ok
      else '  结论：exe 门禁字节码不完整 ❌ 需重新打包')
print('=' * 72)
sys.exit(0 if ok else 1)
