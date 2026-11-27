# -*- coding: utf-8 -*-
r"""probe_built_exe_preinput.py —— 产物侧核验（不是只 grep 源码名）。

理由同 probe_built_exe_autoreturn.py：「文件进包了」不等于「控制流是活的」。
本项目历史上有过符号在、逻辑被改死的事故，所以对关键功能要**反汇编打包产物**。

检查 dist\大蛋小助手.exe 里：
  ① core.script_core 有 `_do_preinput` 方法，且 `_do_auto_pick` 里**先**调它、
     再走偏好（顺序错了就等于没优先）。
  ② `_apply_phase_side_effects` 里写了 `_preinput_cid`（离开选人清空）。
  ③ ui.overlay_window 里有 `sig_preinput` / `set_preinput`，且 `_on_click` 里
     同时出现 `auto_preinput_champion` 与 `swap_signal`（开关分流两条路都在）。
  ④ ui.styles 里有 PREINPUT 令牌。
  ⑤ main 里有 `_on_preinput_click`，且 `_on_bench` 里调了 `set_preinput`。

用法:
    <python> tools\probe_built_exe_preinput.py [exe路径]
退出码: 0 = 全通过; 1 = 有缺失（产物没带上功能）。
"""
import dis
import io
import os
import sys
import tempfile

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
EXE = (sys.argv[1] if len(sys.argv) > 1
       else os.path.join(_ROOT, 'dist', '大蛋小助手.exe'))

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  [%s] %s%s' % ('PASS' if cond else 'FAIL', name,
                           ('  ' + detail) if detail else ''))


def walk_find(root, name):
    for c in root.co_consts:
        if hasattr(c, 'co_name'):
            if c.co_name == name:
                return c
            hit = walk_find(c, name)
            if hit:
                return hit
    return None


def collect_names(code, acc=None):
    """递归收集整棵树里出现过的所有名字。

    ⚠️ 必须递归 —— `sig_preinput` 在**类体**那段 code 的 co_names 里、
    `set_preinput` 是**方法**的 co_name、`PREINPUT` 在 styles 类体的 co_names 里。
    只查模块级 co_names / co_consts 会得到假失败（本次第一版探针就报了 3 个）。
    """
    if acc is None:
        acc = set()
    acc.update(code.co_names)
    acc.update(getattr(code, 'co_varnames', ()))
    for i in dis.get_instructions(code):
        if isinstance(i.argval, str):
            acc.add(i.argval)
    for c in code.co_consts:
        if hasattr(c, 'co_name'):
            collect_names(c, acc)
    return acc


def names_of(fn):
    """单个函数字节码里出现的所有名字(co_names + co_varnames + 指令 argval)。"""
    got = set(fn.co_names) | set(fn.co_varnames)
    for i in dis.get_instructions(fn):
        if isinstance(i.argval, str):
            got.add(i.argval)
    return got


def main():
    if not os.path.exists(EXE):
        print('[!!] 找不到产物: %s' % EXE)
        return 1
    print('=' * 72)
    print('产物侧核验(预输入选英雄): %s' % os.path.basename(EXE))
    print('=' * 72)

    ca = CArchiveReader(EXE)
    tmp = tempfile.mkdtemp(prefix='dadan_probe2_')
    pyz = os.path.join(tmp, 'PYZ.pyz')
    with open(pyz, 'wb') as f:
        f.write(ca.extract('PYZ.pyz'))
    za = ZlibArchiveReader(pyz)

    core = za.extract('core.script_core')
    pre = walk_find(core, '_do_preinput')
    check('① core 有 _do_preinput 方法', pre is not None)

    auto = walk_find(core, '_do_auto_pick')
    if auto is None:
        check('① _do_auto_pick 存在于包内', False)
    else:
        ins = list(dis.get_instructions(auto))
        # 语义判据：预输入若命中就 return True，**之后**才轮到偏好换人。
        # 所以"调用 _do_preinput"必须出现在"调用 bench_swap"之前，且中间有
        # 条件跳转(命中即返回) —— 只看 LOAD_ATTR preferred_champions 是错的：
        # 它在函数开头就赋给局部变量(指令 @4)，与顺序语义无关(第一版就栽在这)。
        idx_pre = idx_ret = idx_swap = idx_jump = -1
        for k, i in enumerate(ins):
            if i.argval == '_do_preinput' and idx_pre < 0:
                idx_pre = k
            if idx_swap < 0 and i.argval == 'bench_swap':
                idx_swap = k
        # 找 _do_preinput 之后最近的一次条件跳转(if self._do_preinput(...): return)
        for k in range(idx_pre + 1, len(ins)):
            if ins[k].opname.startswith(('POP_JUMP_IF', 'JUMP_IF')):
                idx_jump = k
                break
        for k in range(idx_jump + 1, len(ins)):
            if ins[k].opname == 'RETURN_VALUE':
                idx_ret = k
                break
        check('① 预输入先判、命中即返回，偏好换人排在其后',
              idx_pre >= 0 and idx_jump > idx_pre and idx_ret > idx_jump
              and idx_swap > idx_pre,
              '_do_preinput@%d 条件跳转@%d 返回@%d bench_swap@%d'
              % (idx_pre, idx_jump, idx_ret, idx_swap))

    side = walk_find(core, '_apply_phase_side_effects')
    check('② 离开选人阶段清空预输入',
          side is not None and '_preinput_cid' in names_of(side))

    ov = za.extract('ui.overlay_window')
    ov_all = collect_names(ov)
    # `sig_preinput` 在类体 code 的 co_names、`set_preinput` 是方法的 co_name ——
    # 都必须递归才能看到(第一版只查模块级, 报了假失败)。
    check('③ 小窗有 sig_preinput / set_preinput',
          'sig_preinput' in ov_all and 'set_preinput' in ov_all)
    click = walk_find(ov, '_on_click')
    click_names = names_of(click) if click else set()
    check('③ 单击按开关分流(预输入 vs 立即换)',
          'auto_preinput_champion' in click_names and 'swap_signal' in click_names,
          '有预输入门=%s 有立即换=%s'
          % ('auto_preinput_champion' in click_names, 'swap_signal' in click_names))

    st = za.extract('ui.styles')
    check('④ styles 有 PREINPUT 令牌', 'PREINPUT' in collect_names(st))

    # ⚠️ main 是**入口脚本**，在 CArchive 根而不是 PYZ 里(见 _verify_exe.py 同款注释)
    import marshal
    raw = ca.extract('main')
    mn = marshal.loads(raw[8:]) if raw[:4] == b'\xe3\x00\x00\x00' else marshal.loads(raw)
    check('⑤ main 有 _on_preinput_click', walk_find(mn, '_on_preinput_click') is not None)
    bench = walk_find(mn, '_on_bench')
    check('⑤ _on_bench 每轮对齐预输入状态',
          bench is not None and 'set_preinput' in names_of(bench))

    print()
    print('结果: %d 通过 / %d 失败' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  失败:', f)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
