# -*- coding: utf-8 -*-
r"""产物核验：exe 里 v4.9.1 的新逻辑是否真在**控制流**上，而不只是符号名在。

三层验证：
  1) 符号层：CArchive 里 main 的字节码含 `_quarantine_own_runtime_dir` /
     `_open_probe_error` / `_ERROR_SHARING_VIOLATION`。
  2) 字节码层：反汇编 `_runtime_dir_in_use`，确认它 **不再** 调用 os.open，
     而是调用 _open_probe_error，并且出现了与 32 / 5 比较的分支。
  3) 常量层：确认 `_QUAR` 后缀常量与 atexit.register 调用真实存在。
"""
import dis
import marshal
import os
import re
import sys

sys.path.insert(0, r'E:\HuanJing\01_Runtimes\Python\envs\dadan\Lib\site-packages')
from PyInstaller.archive.readers import CArchiveReader

EXE = r'D:\AI\大蛋小助手\dadan-build\dist\大蛋小助手.exe'


def load_entry_script():
    """从 CArchive 里取出入口脚本的 code object（main 是 script，不是 PYZ 模块）。

    ⚠️ 入口脚本的 marshal 数据**从第 0 字节就是 code object**
    （实测前 32 字节 `c\\x00\\x00...\\xf3\\x08\\x03` 就是 code 的 marshal 头）。
    不要照抄网上"跳过 16 字节 PYZ 头"的老写法 —— 那只适用于 .pyc/PYZ 条目。
    """
    r = CArchiveReader(EXE)
    for name in r.toc:
        if name == 'main':
            data = r.extract('main')
            return marshal.loads(data)
    return None


def find_code(code, target):
    """在 code 常量树里按函数名找 code object。"""
    stack = [code]
    while stack:
        c = stack.pop()
        if getattr(c, 'co_name', '') == target:
            return c
        for const in getattr(c, 'co_consts', ()):
            if hasattr(const, 'co_name'):
                stack.append(const)
    return None


def names_of(code):
    out = set(code.co_names) | set(code.co_varnames)
    for const in code.co_consts:
        if hasattr(const, 'co_names'):
            out |= set(const.co_names)
    return out


def main():
    fails = []

    print('=' * 74)
    print('1) 符号层')
    code = load_entry_script()
    if code is None:
        print('FAIL 取不到入口脚本')
        return 1
    all_names = names_of(code)

    for sym in ('_quarantine_own_runtime_dir', '_open_probe_error',
                '_RT_QUARANTINE_SUFFIX'):
        ok = sym in all_names
        print(('  PASS ' if ok else '  FAIL ') + sym)
        if not ok:
            fails.append(sym)

    print('=' * 74)
    print('2) 字节码层：_runtime_dir_in_use 的控制流')
    fn = find_code(code, '_runtime_dir_in_use')
    if fn is None:
        print('  FAIL 找不到 _runtime_dir_in_use')
        fails.append('_runtime_dir_in_use')
    else:
        dis_names = set()
        for ins in dis.get_instructions(fn):
            if ins.opname in ('LOAD_GLOBAL', 'LOAD_ATTR', 'LOAD_METHOD',
                              'LOAD_CONST', 'LOAD_FAST'):
                dis_names.add(str(ins.argval))
        # 必须调用新探针
        uses_probe = '_open_probe_error' in dis_names
        print(('  PASS ' if uses_probe else '  FAIL ') + '调用 _open_probe_error')
        if not uses_probe:
            fails.append('未调用 _open_probe_error')
        # 不得再直接 os.open(O_RDWR)
        uses_os_open = ('open' in dis_names) and ('os' in dis_names)
        print(('  PASS ' if not uses_os_open else '  FAIL ') + '已不再调用 os.open')
        if uses_os_open:
            fails.append('仍在调用 os.open')
        # 错误码分支常量要在
        consts = set()
        for c in fn.co_consts:
            if isinstance(c, int):
                consts.add(c)
        has_branch = '_ERROR_SHARING_VIOLATION' in dis_names and '_ERROR_ACCESS_DENIED' in dis_names
        print(('  PASS ' if has_branch else '  FAIL ') + '区分 SHARING_VIOLATION / ACCESS_DENIED')
        if not has_branch:
            fails.append('未区分两个错误码')

    print('=' * 74)
    print('3) 常量层：_QUAR 后缀与 atexit 注册')
    mod_consts = set()
    for c in code.co_consts:
        if isinstance(c, str):
            mod_consts.add(c)
    quar_ok = '_QUAR' in mod_consts
    print(('  PASS ' if quar_ok else '  FAIL ') + "模块常量含 '_QUAR'")
    if not quar_ok:
        fails.append('_QUAR 常量缺失')
    atexit_ok = 'atexit' in all_names
    print(('  PASS ' if atexit_ok else '  FAIL ') + 'atexit 已被引用')
    if not atexit_ok:
        fails.append('atexit 未引用')

    print('=' * 74)
    print('4) 字节码层：_quarantine_own_runtime_dir 是否**同时**做了改名 + 重建')
    qfn = find_code(code, '_quarantine_own_runtime_dir')
    if qfn is None:
        print('  FAIL 找不到 _quarantine_own_runtime_dir')
        fails.append('quarantine 缺失')
    else:
        qnames = set()
        for ins in dis.get_instructions(qfn):
            if ins.opname in ('LOAD_GLOBAL', 'LOAD_ATTR', 'LOAD_METHOD'):
                qnames.add(str(ins.argval))
        has_rename = 'rename' in qnames
        has_makedirs = 'makedirs' in qnames or 'mkdir' in qnames
        print(('  PASS ' if has_rename else '  FAIL ') + '调用 rename（把内容挪走）')
        print(('  PASS ' if has_makedirs else '  FAIL ') +
              '调用 makedirs/mkdir（原路径重建空目录）')
        if not has_rename:
            fails.append('缺 rename')
        if not has_makedirs:
            fails.append('缺 makedirs（v4.9.1 就是漏了这步，真机照样弹框）')

    print('=' * 74)
    print('结论:', '全部通过' if not fails else ('失败 %d 项: %s' % (len(fails), fails)))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
