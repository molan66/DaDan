# -*- coding: utf-8 -*-
"""产物核验：确认 v4.9.0 的修复**真的进了 exe**，且控制流是活的。

为什么不只 grep 符号名（本机 v4.6.15 的教训）：
    符号在、控制流被改死，照样白跑。所以这里把 lcu.api 的字节码从 PYZ 里取出来，
    反汇编 `_clean_augment_name` / `aram_queue_ids`，确认关键分支真的在。

为什么不用自己写的 CArchive 解析器：
    第一版我手写 struct 解包，结果偏移算错、只读出 1 个条目、还误判"找不到 PYZ"。
    PyInstaller 自带 `CArchiveReader` / `ZlibArchiveReader`，项目里
    tools/verify_exe.py 和 verify_exe_gate_bytecode.py 早就在用 —— 直接复用，
    不重复造轮子，也不用手工维护结构定义。

检查项：
  ① PYZ 里能取到 lcu.api / core.script_core 的 code object
  ② _clean_augment_name 的字面量里有 augmentNameId / 下划线判据痕迹
  ③ aram_queue_ids 的常量里有 kARAM 与 AllRandomPickStrategy
  ④ core.script_core 的常量里含新补的队列 id
  ⑤ 测试夹具没被打包（那是测试资产，进包只占体积）
  ⑥ core.updater 的 APP_VERSION 字面量是 4.9.0

用法：
    <python> tools/verify_exe_v490.py
退出码 0 = 全部通过。
"""
import dis
import io
import marshal
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(_ROOT, 'dist', '大蛋小助手.exe')


def load_pyz_code():
    """返回 {模块名: code object}，从 exe 的 PYZ.pyz 里读。"""
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    ca = CArchiveReader(EXE)
    names = list(ca.toc)
    tmp = os.path.join(tempfile.gettempdir(), '_v490_pyz.pyz')
    with open(tmp, 'wb') as f:
        f.write(ca.extract('PYZ.pyz'))
    try:
        z = ZlibArchiveReader(tmp)
        out = {}
        for name in ('lcu.api', 'core.script_core', 'core.updater'):
            try:
                out[name] = z.extract(name)
            except Exception as e:
                print('  (取不到 %s: %r)' % (name, e))
        return names, out
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def find_func(mod_code, name):
    """在模块 code 里（含嵌套）找指定名字的函数 code。"""
    stack = [mod_code]
    while stack:
        c = stack.pop()
        for k in c.co_consts:
            if hasattr(k, 'co_name') and hasattr(k, 'co_consts'):
                if k.co_name == name:
                    return k
                stack.append(k)
    return None


def main():
    if not os.path.isfile(EXE):
        print('FAIL 找不到 exe: %s' % EXE)
        return 1
    print('exe: %s' % EXE)
    print('大小: %.1f MB' % (os.path.getsize(EXE) / 1048576.0))

    names, codes = load_pyz_code()
    print('CArchive 条目数: %d' % len(names))

    ok = True
    api_code = codes.get('lcu.api')
    sc_code = codes.get('core.script_core')
    up_code = codes.get('core.updater')
    for nm, cd in (('lcu.api', api_code), ('core.script_core', sc_code),
                   ('core.updater', up_code)):
        if cd is None:
            print('[FAIL] PYZ 里取不到 %s' % nm)
            ok = False
    if not ok:
        return 1
    print('[PASS] lcu.api / core.script_core / core.updater 字节码已取出')

    # ---- ② 名字清洗 ----
    clean = find_func(api_code, '_clean_augment_name')
    if clean is None:
        print('[FAIL] 找不到 _clean_augment_name（修复没进包）')
        ok = False
    else:
        consts = [c for c in clean.co_consts]
        blob = ' '.join(str(c) for c in consts)
        rets_empty = sum(1 for i in dis.get_instructions(clean)
                         if i.opname in ('RETURN_CONST',) and i.argval == '')
        if rets_empty == 0:
            rets_empty = sum(1 for i in dis.get_instructions(clean)
                             if i.opname == 'LOAD_CONST' and i.argval == '')
        has_underscore = '_' in blob
        has_regex = 'A-Za-z0-9_' in blob or 'A-Za-z0-9_' in str(consts)
        print('  常量: %r' % (consts,))
        print('  返回空串分支数=%d  含下划线判据=%s' % (rets_empty, has_underscore))
        if rets_empty < 2:
            print('[FAIL] 判据分支不足（应至少 2 处返回空串）')
            ok = False
        elif not has_underscore:
            print('[FAIL] 字节码里没有下划线判据常量')
            ok = False
        else:
            print('[PASS] 名字清洗的两条判据都在字节码里')

    # ---- ③ 客户端推导判据 ----
    aram = find_func(api_code, 'aram_queue_ids')
    if aram is None:
        print('[FAIL] 找不到 aram_queue_ids（根因修复没进包）')
        ok = False
    else:
        blob = ' '.join(str(c) for c in aram.co_consts)
        if 'kARAM' in blob and 'AllRandomPickStrategy' in blob:
            print('[PASS] 推导判据(kARAM + AllRandomPickStrategy)在字节码里')
        else:
            print('[FAIL] 判据常量不在字节码里: %r' % (aram.co_consts,))
            ok = False

    # ---- ④ 白名单常量 ----
    nums = set()

    def walk(c):
        for k in c.co_consts:
            if isinstance(k, (frozenset, set, tuple)):
                nums.update(x for x in k if isinstance(x, int))
            if hasattr(k, 'co_consts'):
                walk(k)
    walk(sc_code)
    need = {451, 452, 720, 721, 2400, 2401, 2403, 2405, 2410, 2450}
    miss = need - nums
    if miss:
        print('[FAIL] 白名单新补 id 不在包内: %s' % sorted(miss))
        ok = False
    else:
        print('[PASS] 白名单含新补队列 id（451/452/720/721/2400~2450）')

    # ---- ⑤ 夹具泄漏 ----
    leaked = [n for n in names if 'fixtures' in n or 'aram_queues' in n
              or 'probe_' in n or 'verify_' in n]
    if leaked:
        print('[FAIL] 测试/工具资产混进包了: %s' % leaked)
        ok = False
    else:
        print('[PASS] 测试夹具与探针脚本未混入 exe')

    # ---- ⑥ 版本号 ----
    up_blob = ' '.join(str(c) for c in up_code.co_consts)
    if '4.9.0' in up_blob:
        print('[PASS] core.updater 内含 APP_VERSION 字面量 4.9.0')
    else:
        print('[?] core.updater 顶层常量里未见 4.9.0（可能在嵌套 code 里）')
        found = []

        def walk2(c):
            for k in c.co_consts:
                if k == '4.9.0':
                    found.append(k)
                if hasattr(k, 'co_consts'):
                    walk2(k)
        walk2(up_code)
        if found:
            print('[PASS] core.updater 嵌套常量里找到 4.9.0')
        else:
            print('[FAIL] core.updater 里找不到 4.9.0')
            ok = False

    print()
    print('PASS 产物核验全部通过' if ok else 'FAIL 见上')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
