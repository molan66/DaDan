# -*- coding: utf-8 -*-
r"""verify_exe_conn_state.py —— 确认打进 exe 的小窗三态修复**控制流是活的**。

为什么要单独核这一条（本项目 v4.6.15 的教训）:
    只查"符号名在不在"是不够的 —— 符号在、控制流被改死, 照样坏。
    这里从 exe 的 CArchive 根取出入口脚本 main 的字节码, 反汇编
    `AppController._sync_conn_state`, 确认:
      ① 该方法真的存在(不是被静态分析漏掉 / 被什么改没了);
      ② 里面有 **三次** `_set_conn_state` 调用(已连接/连接中/未连接三个分支),
         说明"由事实重算"的三态分支被打进去了, 而不是只剩一个硬编码文案;
      ③ `_apply_ui_connected` 里仍调用 `_sync_conn_state`(收口点接上了);
      ④ 旧缺陷形态"跃迁分支里直接推 connected"不再作为唯一来源。

用法:
    <python> tools\verify_exe_conn_state.py [exe路径]
默认 exe: dist\大蛋小助手.exe
退出码: 0 全部通过 / 1 有失败。
"""
import dis
import io
import marshal
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
DEFAULT_EXE = os.path.join(_ROOT, 'dist', '大蛋小助手.exe')

EXE = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_EXE

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print(f'  [{"PASS" if cond else "FAIL"}] {name}' + (f'  {detail}' if detail else ''))


def entry_code():
    """从 exe 的 CArchive 根取出入口脚本(main)的 code 对象。

    onefile 结构: CArchive 根里 `main` 是入口脚本的 marshal 字节(不在 PYZ 内);
    PYZ 里则是各模块的字节码。
    """
    from PyInstaller.archive.readers import CArchiveReader
    ca = CArchiveReader(EXE)
    for nm in ca.toc:
        if os.path.basename(nm) == 'main':
            data = ca.extract(nm)
            return marshal.loads(data if isinstance(data, bytes) else data.read())
    raise SystemExit('exe 的 CArchive 根里找不到入口脚本 main')


def find_class(code, name):
    for c in code.co_consts:
        if hasattr(c, 'co_name') and c.co_name == name:
            return c
    return None


def find_func(code, name):
    """在 code(含其嵌套类)里递归找同名函数。"""
    for c in code.co_consts:
        if not hasattr(c, 'co_name'):
            continue
        if c.co_name == name:
            return c
        r = find_func(c, name)
        if r is not None:
            return r
    return None


def calls_in(code, target_name):
    """统计 code 指令流里对 target_name 的调用次数。

    ⚠️ 首版这里只认 "LOAD_ATTR 紧跟 CALL_FUNCTION" —— 而实际字节码是
    `LOAD_ATTR _set_conn_state / PUSH_NULL / … / CALL_FUNCTION_EX`(本项目跑在
    Python 3.13, 且收口后是 `self._set_conn_state(*target)` 这种**解包调用**),
    于是明明调了却统计成 0 处, 把已打好的修复误判成"没进包"。
    现在按"载入该方法名之后, 到下一个方法载入之前, 只要出现过任何 CALL* 指令"
    计数 —— 对 LOAD_ATTR/LOAD_METHOD 与各种 CALL 形态都稳。
    """
    instrs = list(dis.get_instructions(code))
    n = 0
    for i, ins in enumerate(instrs):
        if ins.opname in ('LOAD_METHOD', 'LOAD_ATTR') and ins.argval == target_name:
            for nxt in instrs[i + 1:]:
                if nxt.opname in ('LOAD_METHOD', 'LOAD_ATTR'):
                    break
                if nxt.opname.startswith('CALL'):
                    n += 1
                    break
    return n


def const_strings(code):
    """递归收集 code 里所有字符串常量(含**元组常量内**的)。

    `target = ('connected', '已连接')` 里的字符串落在 **tuple 常量**里,
    只扫顶层 co_consts 会漏 —— 首版就因此把三个状态分支全判成了缺失。
    """
    out = set()

    def walk(obj, depth=0):
        if depth > 4:
            return
        if isinstance(obj, str):
            out.add(obj)
        elif isinstance(obj, (tuple, frozenset, list)):
            for x in obj:
                walk(x, depth + 1)

    for c in code.co_consts:
        walk(c)
    return out


def getattr_names(code):
    """收集 `getattr(self, '名', 默认)` 形式的属性名。

    这些都是**字符串常量**而非属性名指令(co_names), 用 co_names 找不到。
    """
    instrs = list(dis.get_instructions(code))
    names = set()
    for i, ins in enumerate(instrs):
        if ins.opname == 'LOAD_GLOBAL' and ins.argval == 'getattr':
            # 期望形状: getattr / self / '<name>' / <default>
            window = instrs[i + 1:i + 4]
            strs = [w.argval for w in window
                    if w.opname == 'LOAD_CONST' and isinstance(w.argval, str)]
            if strs:
                names.add(strs[0])
    return names


def main():
    if not os.path.isfile(EXE):
        print(f'找不到 exe: {EXE}')
        return 1
    print('=' * 72)
    print(f'exe 小窗三态修复核对: {os.path.basename(EXE)}')
    print('=' * 72)

    top = entry_code()
    app = find_class(top, 'AppController')
    check('入口脚本里有 AppController', app is not None)
    if app is None:
        return 1

    sync = find_func(app, '_sync_conn_state')
    check('_sync_conn_state(三态收口点)已打进 exe', sync is not None)
    if sync is not None:
        # 收口后的形状: 一个调用点 `self._set_conn_state(*target)`, 三个状态作为
        # **元组常量**由上面三条分支算出来。所以"接线活着"= 有 1 处调用 + 三个
        # 状态的元组都在。首版这里既要 3 处调用又要顶层字符串常量, 两条都错。
        n = calls_in(sync, '_set_conn_state')
        check('_sync_conn_state 调用 _set_conn_state(推送接线活着)',
              n >= 1, f'实得 {n} 处')
        consts = const_strings(sync)
        for st, txt in (('connected', '已连接'),
                        ('connecting', '连接中'),
                        ('disconnected', '未连接')):
            check(f"三态目标含 {st!r}/{txt}",
                  st in consts and txt in consts)
        check('_sync_conn_state 读 _conn_state_shown(幂等记账)',
              '_conn_state_shown' in getattr_names(sync)
              or '_conn_state_shown' in sync.co_names)
        check('_sync_conn_state 读 _connecting(在途连接态)',
              '_connecting' in getattr_names(sync)
              or '_connecting' in sync.co_names)
        check('_sync_conn_state 读 _ui_connected(二元状态)',
              '_ui_connected' in sync.co_names)
        check('_sync_conn_state 读 _ws.is_connected(WS 权威信号)',
              '_ws' in sync.co_names and 'is_connected' in sync.co_names)

    auc = find_func(app, '_apply_ui_connected')
    check('_apply_ui_connected 已打进 exe', auc is not None)
    if auc is not None:
        check('_apply_ui_connected 调用 _sync_conn_state(收口已接线)',
              calls_in(auc, '_sync_conn_state') >= 1,
              f'实得 {calls_in(auc, "_sync_conn_state")} 处')

    # 收口是否真的收干净了: _try_connect / _settle_connecting 也应只调收口点,
    # 而不是各自再直接 _set_conn_state(那正是缺陷的土壤: 两份判据)。
    for fn in ('_try_connect', '_settle_connecting'):
        c = find_func(app, fn)
        if c is None:
            check(f'{fn} 已打进 exe', False)
            continue
        check(f'{fn} 调用收口点 _sync_conn_state',
              calls_in(c, '_sync_conn_state') >= 1,
              f'实得 {calls_in(c, "_sync_conn_state")} 处')
        check(f'{fn} 不再直接 _set_conn_state(收口已收干净)',
              calls_in(c, '_set_conn_state') == 0,
              f'实得 {calls_in(c, "_set_conn_state")} 处')

    print()
    print(f'结果: {len(PASS)} 通过 / {len(FAIL)} 失败')
    print('=' * 72)
    if FAIL:
        for f in FAIL:
            print(f'  FAIL: {f}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
