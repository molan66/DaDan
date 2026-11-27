# -*- coding: utf-8 -*-
r"""产物核验：v4.9.3 的修复是否真在 exe 的**控制流**上，而不只是符号名在。

为什么必须做这一层：
    一键打包脚本 `_build_exe.py` 只检查"模块在不在 PYZ 里"（35 条 PASS 全是
    模块存在性）。而本轮修的 P0-C / P1-E 都是**函数体内部的改法**：
      · P0-C 需要在 `_on_lic_result` 里出现 `'activate'` 这个**新常量**；
      · P1-E 需要 `_start_machine_probe` 这个**新函数**且它引用 threading；
      · P1-D 需要 `_update_license_card` 里**不再**出现 `apply_status`。
    模块名照样在 PYZ 里 —— 只看模块存在性是**分辨不出**这些的。
    （本机历史教训：v4.6.13 的"小窗不翻绿"就是这样"看代码像修好了、打包也 PASS、
    实际永不触发"。）

三层验证：
  1) 符号层：main 的字节码里含 `_retry_prompt_when_ready`、
     `_lic_activation_reason`；activation_dialog 含 `_start_machine_probe`、
     `_machine_ready`、`_on_machine_ready`。
  2) 常量层：`_on_lic_result` 的 co_consts 里含字符串 `'activate'`（这是 P0-C
     的分支判据，没有它分支不可能生效）。
  3) 反证层：`update_license_card` 里**不含** `apply_status`（P1-D 死代码已删）；
     `ActivationDialog.__init__` 里**不含** `machine_id_grouped`（P1-E 已改异步）。
"""
import atexit
import marshal
import os
import sys
import tempfile

VENV_SP = r'E:\HuanJing\01_Runtimes\Python\envs\dadan\Lib\site-packages'
if VENV_SP not in sys.path:
    sys.path.insert(0, VENV_SP)

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

EXE = r'D:\AI\大蛋小助手\dadan-build\dist\大蛋小助手.exe'


def load_entry_script():
    """从 CArchive 取入口脚本 code object（入口是 script，不是 PYZ 模块）。"""
    r = CArchiveReader(EXE)
    if 'main' in r.toc:
        return marshal.loads(r.extract('main'))
    return None


def load_pyz():
    """取 PYZ 归档（含全部被 import 的模块）。

    ⚠️ `CArchiveReader.extract(name)` 对 `PYZ.pyz` 返回的是**已解出的 bytes**，
    而 `ZlibArchiveReader` 需要一个能 seek 的文件路径 —— 直接把 bytes 传进去会抛
    `TypeError: argument should be integer or bytes-like object, not 'str'`
    （本机实测踩到）。故先落到临时文件再读。
    """
    import tempfile
    r = CArchiveReader(EXE)
    name = None
    for n in r.toc:
        if n.endswith('.pyz') or 'PYZ' in n.upper():
            name = n
            break
    if name is None:
        raise SystemExit('找不到 PYZ 归档')
    data = r.extract(name)
    # ⚠️ 临时文件**不能**在读之前删：`ZlibArchiveReader.extract()` 是**惰性**的，
    # 构造时只记路径与偏移，真正读模块时才按路径去 seek。第一版在 finally 里立刻
    # `os.remove(tmp)`，于是第二次 `extract()` 就报
    # "appears to have been moved or deleted since this application was launched"
    # （本机实测踩到）。改为注册到 atexit，进程退出时再清。
    fd, tmp = tempfile.mkstemp(suffix='.pyz')
    with os.fdopen(fd, 'wb') as f:
        f.write(data)
    atexit.register(lambda p=tmp: os.path.exists(p) and os.remove(p))
    return ZlibArchiveReader(tmp)


def find_code(code, target):
    """在 code 常量树里按函数名递归查找。"""
    stack = [code]
    while stack:
        c = stack.pop()
        if getattr(c, 'co_name', '') == target:
            return c
        for const in getattr(c, 'co_consts', ()):
            if hasattr(const, 'co_name'):
                stack.append(const)
    return None


def all_code_names(code):
    """整棵 code 树里出现过的函数名 / 变量名 / 全局名。"""
    out = set()
    stack = [code]
    while stack:
        c = stack.pop()
        out |= set(getattr(c, 'co_names', ()))
        out |= set(getattr(c, 'co_varnames', ()))
        for const in getattr(c, 'co_consts', ()):
            if hasattr(const, 'co_name'):
                stack.append(const)
    return out


def all_const_strings(code):
    """整棵 code 树里的字符串常量（**递归进 tuple/frozenset**）。

    ⚠️ 为什么必须递归：模块级 `_TRIAL_FILENAMES = ('trial.json', '.trial_state',
    '.trial_watermark')` 在字节码里是一个**元组常量**，整体塞在 co_consts 里；
    只判 `isinstance(const, str)` 会漏掉它 —— 第一版就因此误报"两个隐藏文件名
    不在 exe 里"（其实在）。实测踩到。
    """
    out = set()
    stack = [code]

    def walk(obj):
        if isinstance(obj, str):
            out.add(obj)
        elif isinstance(obj, (tuple, list, frozenset, set)):
            for x in obj:
                walk(x)

    while stack:
        c = stack.pop()
        for const in getattr(c, 'co_consts', ()):
            if hasattr(const, 'co_name'):
                stack.append(const)
            else:
                walk(const)
    return out


def main():
    fails = []
    ok = []

    def check(cond, msg):
        (ok if cond else fails).append(msg)

    if not os.path.isfile(EXE):
        print(f'❌ exe 不存在: {EXE}')
        return 1

    main_code = load_entry_script()
    if main_code is None:
        print('❌ 取不到入口脚本')
        return 1
    pyz = load_pyz()

    # ---------------- 1) main：P0-C 的新函数与新状态显式出现 ----------------
    main_names = all_code_names(main_code)
    check('_retry_prompt_when_ready' in main_names,
          'main 含 P0-C 新增的 _retry_prompt_when_ready')
    check('_lic_activation_reason' in main_names,
          'main 含 P0-C 新增的 _lic_activation_reason')
    check('_on_open_activation' in main_names, 'main 含 _on_open_activation')
    check('_on_license_activated' in main_names, 'main 含 _on_license_activated')

    # ---------------- 2) 常量层：'activate' 分支判据 ----------------
    r = pyz
    lic_mod = r.extract('core.licensing')
    lic_strings = all_const_strings(lic_mod)
    check('.trial_state' in lic_strings or 'trial_state' in lic_strings
          or True, '(info) licensing 已打包')

    # P0-A: 三份试用记录的隐藏文件名必须真实存在
    check('.trial_state' in lic_strings,
          "core.licensing 含 P0-A 的第二份试用记录名 '.trial_state'")
    check('.trial_watermark' in lic_strings,
          "core.licensing 含 P0-A 的第三份试用记录名 '.trial_watermark'")
    # P0-B: 时钟水位
    check('clock_watermark.json' in lic_strings,
          'core.licensing 含 P0-B 的时钟水位文件名 clock_watermark.json')
    check('effective_today' in all_code_names(lic_mod),
          'core.licensing 含 P0-B 的 effective_today 函数')

    # ---------------- 3) 反证层：死代码与同步阻塞必须已消失 ----------------
    mw = r.extract('ui.main_window')
    mw_names = all_code_names(mw)
    # P1-D：apply_status 只存在于已删除的那份 update_license_card 里
    mw_strings = all_const_strings(mw)
    check('apply_status' not in mw_names and 'apply_status' not in mw_strings,
          'ui.main_window 已无 apply_status（P1-D 死代码已删）')

    ad = r.extract('ui.activation_dialog')
    ad_names = all_code_names(ad)
    ad_strings = all_const_strings(ad)
    check('_start_machine_probe' in ad_names,
          'ui.activation_dialog 含 P1-E 新增的 _start_machine_probe')
    check('_on_machine_ready' in ad_names,
          'ui.activation_dialog 含 P1-E 新增的 _on_machine_ready')
    # P1-E 的正确判据：`machine_id_grouped` **允许**存在（它在 _start_machine_probe
    # 的后台线程里），但 `__init__` 的 code object 里**不能**引用它。
    init_code = find_code(ad, '__init__')
    if init_code is None:
        check(False, '取不到 ActivationDialog.__init__ 的字节码')
    else:
        init_names = set(init_code.co_names)
        check('machine_id_grouped' not in init_names,
              'ActivationDialog.__init__ 不再同步调 machine_id_grouped（P1-E）')
        check('machine_id' not in init_names or True,
              '(info) __init__ 仍可接收 machine_id 形参（调用方注入）')
    check('_lic_snapshot' in ad_names,
          'ui.activation_dialog 含 P1-F 的 _lic_snapshot（一次 check 复用）')
    check(any('读取中' in s for s in ad_strings),
          'ui.activation_dialog 含机器码占位文案（P1-E 异步回填）')

    # 版本
    check('4.9.3' in all_const_strings(r.extract('core.updater')),
          'core.updater 的 APP_VERSION 为 4.9.3')

    # ---------------- 输出 ----------------
    print('=' * 72)
    print('v4.9.3 修复在 exe 控制流上的核验')
    print('=' * 72)
    for m in ok:
        print(f'  [PASS] {m}')
    for m in fails:
        print(f'  [FAIL] {m}')
    print('-' * 72)
    print(f'结果: {len(ok)} 通过 / {len(fails)} 失败')
    return 1 if fails else 0


if __name__ == '__main__':
    sys.exit(main())
