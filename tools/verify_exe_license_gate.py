# -*- coding: utf-8 -*-
"""核验打包产物里**门禁真的接线了** —— 不只是"文件进包了"。

为什么单独验这个：
  "core.licensing 在 PYZ 里"只能证明模块被打包，**不能证明有人在用它**。
  真正的防线是 main 里的 `_start()` 调用 `_license_ok()` 并在失败时 return。
  PyInstaller 会把 main 的字节码放进 CArchive 根（不在 PYZ），所以要从
  那里取出来反编译检查。
"""
import marshal
import os
import sys

sys.path.insert(0, r'D:\AI\大蛋小助手\dadan-build')
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

EXE = r'D:\AI\大蛋小助手\dadan-build\dist\大蛋小助手.exe'
print('=' * 72)
print('  exe 门禁接线核验')
print('=' * 72)

a = CArchiveReader(EXE)

# ---- main 在 CArchive 根（PyInstaller 的入口脚本）
# ⚠️ 这里**不是** .pyc（没有 16 字节的 magic+mtime+size 头），而是直接
#    marshal 序列化的 code object —— 实测偏移 0 就能解析。
#    早先按 .pyc 处理（raw[16:]）会报 `bad marshal data`。
#    为稳妥起见逐个偏移试一次，避免将来 PyInstaller 改格式时静默失效。
raw = a.extract('main')
code = None
used_off = None
for _off in range(0, 33):
    try:
        _c = marshal.loads(raw[_off:])
        if hasattr(_c, 'co_names'):
            code, used_off = _c, _off
            break
    except Exception:
        continue
if code is None:
    print('[FAIL] 无法从 main 条目解析出 code object（PyInstaller 格式可能变了）')
    sys.exit(1)
print(f'\n[1] main 字节码取出，{len(raw)} 字节（marshal 偏移 {used_off}）')
print(f'[2] marshal 解析成功，模块 = {code.co_name}')

_names = set()
_strings = set()


def walk(c, depth=0):
    if depth > 14:
        return
    for n in c.co_names:
        _names.add(n)
    for n in getattr(c, 'co_varnames', ()):
        _names.add(n)
    for k in c.co_consts:
        if isinstance(k, str):
            _strings.add(k)
        elif hasattr(k, 'co_consts'):
            walk(k, depth + 1)


walk(code)
print(f'[3] 收集到 {len(_names)} 个名字 / {len(_strings)} 个字符串常量')

# ---- 关键断言：门禁相关符号必须在
need_names = ['_license_ok', '_prompt_activation', '_start']
print('\n[4] 门禁符号是否在 exe 的 main 里')
missing = []
for n in need_names:
    hit = n in _names
    print(f'    {"PASS" if hit else "FAIL"}  {n}')
    if not hit:
        missing.append(n)

# ---- 关键断言：编译期常量里应含门禁的日志文案（证明那段代码真在）
need_str = ['[授权]', '授权']
print('\n[5] 授权相关字符串是否在 main（证明该代码段被编译进去了）')
for s in need_str:
    hit = any(s in x for x in _strings)
    print(f'    {"PASS" if hit else "FAIL"}  含 {s!r}')
    if not hit:
        missing.append(s)

# ---- core.licensing 的 usable 属性必须存在（门禁读的就是它）
pyz_tmp = os.path.join(os.environ.get('TEMP', '.'), '_gate_pyz.pyz')
with open(pyz_tmp, 'wb') as f:
    f.write(a.extract('PYZ.pyz'))
z = ZlibArchiveReader(pyz_tmp)
print('\n[6] core.licensing 里的关键成员')
lic = z.extract('core.licensing')
lic_names = set()


def walk2(c, depth=0):
    if depth > 14:
        return
    for n in c.co_names:
        lic_names.add(n)
    for k in c.co_consts:
        if hasattr(k, 'co_consts'):
            walk2(k, depth + 1)


walk2(lic)
# v4.9.3: 把 'online_activate' 从必查清单移除 —— 在线激活已整体删除
# （按用户要求只保留本地激活），继续查它必然 FAIL。
# 换成 v4.9.3 授权保护的核心符号：effective_today（时钟水位）。
for n in ['usable', 'TRIAL_DAYS', 'check', 'effective_today']:
    hit = n in lic_names
    print(f'    {"PASS" if hit else "FAIL"}  {n}')
    if not hit:
        missing.append(n)

try:
    os.remove(pyz_tmp)
except OSError:
    pass

print('\n' + '=' * 72)
if missing:
    print(f'  结论：exe 里缺少 {missing} —— 授权可能没生效，需重新打包！')
    sys.exit(1)
print('  结论：exe 门禁已接线 ✅（符号与代码段都在产物内）')
print('=' * 72)
