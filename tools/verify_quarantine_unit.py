# -*- coding: utf-8 -*-
r"""直接测 main.py 里**真实那份** _quarantine_own_runtime_dir 的行为。

为什么不用 GUI 自动化点托盘「退出」：
  托盘菜单要经 explorer 的 Shell_TrayWnd + Qt 自定义消息，脚本化点击既脆又慢，
  而且「点了退出」本身不是被验证的对象 —— 被验证的是"退出时这段代码跑不跑、
  跑完 bootloader 是否还弹框"。后者已由 verify_quarantine_exit.py 用真 onefile
  exe 验过（exit 0 + 留下 _QUAR），这里只补"main.py 里那份实现是对的"。

覆盖点：
  1) frozen 且 _MEIPASS 存在  → 目录内容被改名成 *_QUAR，**且原路径重建出空目录**
  2) 已经带 _QUAR 后缀        → 幂等，不重复加后缀
  3) 目标名已被占用           → 不覆盖、不报错
  4) 非 frozen（源码运行）    → 直接跳过，不碰任何目录

⚠️ 第 1 条里的"原路径重建空目录"是**必须**的：
   只改名不重建，bootloader 按原路径删不到，照样弹框（v4.9.1 实测踩过，
   窗口模式探针三种对照: none=exit1 / rename=exit1 / rename+empty=exit0）。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, r'D:\AI\大蛋小助手\dadan-build')
import main  # noqa: E402

FAILS = []


def case_rename():
    d = tempfile.mkdtemp(prefix='_MEIreal_')
    with open(os.path.join(d, 'VCRUNTIME140.dll'), 'wb') as f:
        f.write(b'x' * 1024)
    old_frozen = getattr(sys, 'frozen', None)
    old_mei = getattr(sys, '_MEIPASS', None)
    try:
        sys.frozen = True
        sys._MEIPASS = d
        main._quarantine_own_runtime_dir()
        new = d + main._RT_QUARANTINE_SUFFIX
        # ① 内容挪到 _QUAR
        moved = os.path.isdir(new) and os.path.isfile(os.path.join(new, 'VCRUNTIME140.dll'))
        # ② 原路径还在, 且是**空**目录（bootloader 才删得掉）
        recreated = os.path.isdir(d) and not os.listdir(d)
        ok = moved and recreated
        print(('PASS  ' if ok else 'FAIL  ') + '改名到 *_QUAR 且原路径重建为空目录')
        if not ok:
            FAILS.append('rename(%s/%s)' % (moved, recreated))
            print('        moved=%s recreated=%s' % (moved, recreated))
        # 幂等
        sys._MEIPASS = new
        main._quarantine_own_runtime_dir()
        ok2 = os.path.isdir(new) and not os.path.isdir(new + main._RT_QUARANTINE_SUFFIX)
        print(('PASS  ' if ok2 else 'FAIL  ') + '已带后缀时幂等(不再套一层)')
        if not ok2:
            FAILS.append('idempotent')
        # 目标已存在 -> 不覆盖
        d2 = tempfile.mkdtemp(prefix='_MEIconflict_')
        os.makedirs(d2 + main._RT_QUARANTINE_SUFFIX, exist_ok=True)
        sys._MEIPASS = d2
        main._quarantine_own_runtime_dir()
        ok3 = os.path.isdir(d2)
        print(('PASS  ' if ok3 else 'FAIL  ') + '目标名已占用时不覆盖(原目录保留)')
        if not ok3:
            FAILS.append('conflict')
        shutil.rmtree(d2, ignore_errors=True)
        shutil.rmtree(d2 + main._RT_QUARANTINE_SUFFIX, ignore_errors=True)
        shutil.rmtree(new, ignore_errors=True)
    finally:
        if old_frozen is None:
            del sys.frozen
        else:
            sys.frozen = old_frozen
        if old_mei is None:
            del sys._MEIPASS
        else:
            sys._MEIPASS = old_mei
        shutil.rmtree(d, ignore_errors=True)


def case_not_frozen():
    d = tempfile.mkdtemp(prefix='_MEInotfrozen_')
    old_frozen = getattr(sys, 'frozen', None)
    old_mei = getattr(sys, '_MEIPASS', None)
    try:
        if hasattr(sys, 'frozen'):
            del sys.frozen
        sys._MEIPASS = d
        main._quarantine_own_runtime_dir()
        ok = os.path.isdir(d) and not os.path.exists(d + main._RT_QUARANTINE_SUFFIX)
        print(('PASS  ' if ok else 'FAIL  ') + '非 frozen 时完全不碰目录')
        if not ok:
            FAILS.append('not_frozen')
    finally:
        if old_frozen is None:
            if hasattr(sys, 'frozen'):
                del sys.frozen
        else:
            sys.frozen = old_frozen
        if old_mei is None:
            if hasattr(sys, '_MEIPASS'):
                del sys._MEIPASS
        else:
            sys._MEIPASS = old_mei
        shutil.rmtree(d, ignore_errors=True)


def main_():
    print('=' * 70)
    case_rename()
    case_not_frozen()
    print('=' * 70)
    print('结论:', '全部通过' if not FAILS else '失败: %s' % FAILS)
    return 0 if not FAILS else 1


if __name__ == '__main__':
    sys.exit(main_())
