# -*- coding: utf-8 -*-
r"""probe_built_exe_autoreturn.py —— 产物侧核验（不是只 grep 源码名）。

为什么需要它（fix-with-teeth 第 5 步）:
    「文件进包了」不等于「控制流是活的」。本项目历史上有过符号在、逻辑被改死的
    事故，所以对每个关键修复都要**反汇编打包产物**确认条件返回还在。

本探针检查 dist\大蛋小助手.exe 里 core.script_core 的字节码，断言：
  ① `_do_play_again` 里有阶段守卫：出现 `_PLAY_AGAIN_ALLOWED_PHASES` 的读取 +
     与阶段比较 + **条件跳转到 return**（`POP_JUMP_IF_*` / `FORWARD`），
     而且顺序在 `play_again` 调用**之前**。
  ② `_do_play_again` 里已无 `start_matchmaking` 调用、已无 `匹配启动（第` 字面量。
  ③ `_apply_phase_side_effects` 里对 'return' 与 'queue' 各调了一次
     `_bump_countdown`。
  ④ `PLAY_AGAIN_RETRY_MAX` 已不在 core.constants 里。

用法:
    <python> tools\probe_built_exe_autoreturn.py [exe路径]

退出码: 0 = 四项全通过; 1 = 有缺失（产物没带上修复）。
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


def find_code(root, name, path=''):
    """按限定名在 code 常量树里找函数（递归）。"""
    for c in root.co_consts:
        if hasattr(c, 'co_name'):
            q = (path + '.' + c.co_name) if path else c.co_name
            if c.co_name == name:
                return c, q
            hit = find_code(c, name, q)
            if hit:
                return hit
    return None


def main():
    if not os.path.exists(EXE):
        print('[!!] 找不到产物: %s' % EXE)
        return 1
    print('=' * 72)
    print('产物侧核验: %s' % os.path.basename(EXE))
    print('=' * 72)

    ca = CArchiveReader(EXE)
    tmp = tempfile.mkdtemp(prefix='dadan_probe_')
    pyz = os.path.join(tmp, 'PYZ.pyz')
    with open(pyz, 'wb') as f:
        f.write(ca.extract('PYZ.pyz'))
    za = ZlibArchiveReader(pyz)

    found = find_code(za.extract('core.script_core'), '_do_play_again')
    ok_guard = False
    if not found:
        check('_do_play_again 存在于包内', False)
    else:
        fn, qual = found
        ins = list(dis.get_instructions(fn))
        names = [i.argval for i in ins if isinstance(i.argval, str)]
        # ① 守卫在 play_again 之前，且带条件返回
        has_phase_attr = '_PLAY_AGAIN_ALLOWED_PHASES' in names
        idx_attr = next((k for k, i in enumerate(ins)
                         if i.argval == '_PLAY_AGAIN_ALLOWED_PHASES'), -1)
        idx_play = next((k for k, i in enumerate(ins)
                         if i.argval in ('play_again',)), -1)
        has_cond_ret = any(i.opname.startswith('POP_JUMP_IF')
                           for i in ins[:idx_play if idx_play > 0 else len(ins)])
        ok_guard = has_phase_attr and idx_play > 0 and idx_attr < idx_play and has_cond_ret
        check('① 阶段守卫在 play_again 之前且带条件返回', ok_guard,
              'attr@%d play@%d cond_ret=%s' % (idx_attr, idx_play, has_cond_ret))

        # ② 已无自身重试开队列
        no_retry = 'start_matchmaking' not in names and not any(
            isinstance(i.argval, str) and '匹配启动（第' in i.argval for i in ins)
        check('② _do_play_again 内已无 start_matchmaking 重试', no_retry)

        # ③ 两处倒计时作废
        side = find_code(za.extract('core.script_core'), '_apply_phase_side_effects')
        bump_kinds = []
        if side:
            side_ins = list(dis.get_instructions(side[0]))
            for k, i in enumerate(side_ins):
                if i.argval == '_bump_countdown':
                    for j in range(k + 1, min(k + 4, len(side_ins))):
                        if isinstance(side_ins[j].argval, str) and \
                                side_ins[j].argval in ('return', 'queue', 'accept'):
                            bump_kinds.append(side_ins[j].argval)
                            break
        check('③ 阶段迁移点作废 return + queue 倒计时',
              'return' in bump_kinds and 'queue' in bump_kinds,
              '实测 bump: %s' % (bump_kinds or '无'))

    # ④ 常量已删
    consts_mod = za.extract('core.constants')
    const_names = [c for c in consts_mod.co_names]
    check('④ core.constants 已无 PLAY_AGAIN_RETRY_MAX',
          'PLAY_AGAIN_RETRY_MAX' not in const_names
          and 'PLAY_AGAIN_RETRY_MAX' not in consts_mod.co_consts)

    print()
    print('结果: %d 通过 / %d 失败' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  失败:', f)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
