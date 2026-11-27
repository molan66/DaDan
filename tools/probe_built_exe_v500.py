# -*- coding: utf-8 -*-
r"""probe_built_exe_v500.py —— 产物侧核验 v5.0.0 的关键控制流(反汇编)。

理由同 probe_built_exe_preinput.py：「文件进包了」不等于「控制流是活的」。
检查 dist\大蛋小助手.exe:
  ① core.script_core 有 _stop_event / _spawn_worker / _swap_unconfirmed_cooling。
  ② _loop 里用了 stop_event.wait(而非 time.sleep) —— 停止协作退出的字节码证据。
  ③ _click_pick_card 里有 stop_event.is_set 早退。
  ④ main 里有 _schedule_recent_refresh_sig(补拉战绩回主线程的信号)。
  ⑤ lcu.websocket_listener.disconnect 里 _thread 置 None 前有 is_alive 判断
     (只有线程确实退出才置 None)。

用法:
    <python> tools\probe_built_exe_v500.py [exe路径]
退出码: 0 = 全通过; 1 = 有缺失。
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


def names_of(fn):
    got = set(fn.co_names) | set(fn.co_varnames)
    for i in dis.get_instructions(fn):
        if isinstance(i.argval, str):
            got.add(i.argval)
    return got


def collect_names(code, acc=None):
    """递归收集整棵树所有名字(类属性/嵌套函数)。"""
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


def main():
    if not os.path.exists(EXE):
        print('[!!] 找不到产物: %s' % EXE)
        return 1
    print('=' * 72)
    print('产物侧核验(v5.0.0): %s' % os.path.basename(EXE))
    print('=' * 72)

    ca = CArchiveReader(EXE)
    tmp = tempfile.mkdtemp(prefix='dadan_v500_')
    pyz = os.path.join(tmp, 'PYZ.pyz')
    with open(pyz, 'wb') as f:
        f.write(ca.extract('PYZ.pyz'))
    za = ZlibArchiveReader(pyz)

    core = za.extract('core.script_core')
    core_all = collect_names(core)
    check('① core 有 _spawn_worker / _stop_event / _swap_unconfirmed_cooling',
          walk_find(core, '_spawn_worker') is not None
          and '_stop_event' in core_all
          and walk_find(core, '_swap_unconfirmed_cooling') is not None)

    loop = walk_find(core, '_loop')
    ok_loop = False
    if loop is not None:
        ln = names_of(loop)
        ok_loop = '_stop_event' in ln and 'time' not in ln
    check('② _loop 用 stop_event.wait 协作退出(不再 time.sleep)',
          ok_loop,
          '_stop_event=%s time_sleep=%s' % ('_stop_event' in (names_of(loop) if loop else set()),
                                            'time' in (names_of(loop) if loop else set())))

    click = walk_find(core, '_click_pick_card')
    check('③ _click_pick_card 有 stop_event.is_set 早退',
          click is not None and '_stop_event' in names_of(click))

    ov = za.extract('ui.overlay_window')
    ov_all = collect_names(ov)
    ok_ov = 'sig_preinput_state' in ov_all and walk_find(ov, '_do_preinput_state') is not None
    check('④ overlay sig_preinput_state → _do_preinput_state 主线程槽', ok_ov)

    import marshal
    raw = ca.extract('main')
    mn = marshal.loads(raw[8:]) if raw[:4] == b'\xe3\x00\x00\x00' else marshal.loads(raw)
    main_all = set()
    def collect(code):
        main_all.update(code.co_names)
        for c in code.co_consts:
            if hasattr(c, 'co_name'):
                collect(c)
    collect(mn)
    check('⑤ main 有 _schedule_recent_refresh_sig(补拉战绩回主线程)',
          '_schedule_recent_refresh_sig' in main_all
          and walk_find(mn, '_schedule_recent_refresh_impl') is not None)

    wsl = za.extract('lcu.websocket_listener')
    disc = walk_find(wsl, '_disconnect_locked')
    ok_disc = False
    if disc is not None:
        ins = list(dis.get_instructions(disc))
        # 找 "STORE_ATTR _thread=None" 之前是否有条件(if t is None or not t.is_alive)
        store_idx = next((k for k, i in enumerate(ins)
                          if i.opname == 'STORE_ATTR' and i.argval == '_thread'), -1)
        cond_before = any(i.opname.startswith('POP_JUMP_IF')
                          for i in ins[:store_idx]) if store_idx > 0 else False
        ok_disc = store_idx > 0 and cond_before
    check('⑥ websocket_listener.disconnect 置 None 前有退出条件判断', ok_disc)

    print()
    print('结果: %d 通过 / %d 失败' % (len(PASS), len(FAIL)))
    for f in FAIL:
        print('  失败:', f)
    return 0 if not FAIL else 1


if __name__ == '__main__':
    sys.exit(main())
