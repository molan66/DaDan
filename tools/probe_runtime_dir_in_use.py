# -*- coding: utf-8 -*-
r"""探针：_runtime_dir_in_use / _cleanup_stale_runtime_dirs 的占用判定是否可靠。

背景（2026-10-23 实测）：用户退出程序时 PyInstaller 弹
"Failed to remove temporary directory: ...\_MEI000052182"，
残留目录里只剩 VCRUNTIME140*.dll。Restart Manager 查明是
icreateLol.exe 把这两个 DLL 映射了（LoL 客户端 DLL 搜索路径串进了
_MEI 目录）。

现实现用 `os.open(fp, O_RDWR)` 判"在用"，但：
  · 被映射时 → GENERIC_WRITE 报 ERROR_SHARING_VIOLATION(32)
  · 只是 DACL 不给写 → 报 ERROR_ACCESS_DENIED(5)，文件其实随时可删
两者在 os.open 层面都塌缩成 PermissionError，无法区分 → 误判。

本探针逐条判红，全部 PASS 才算修复到位。

跑法：<venv python> tools/probe_runtime_dir_in_use.py
"""
import ctypes
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_STALE_AGE = 600.0          # 造出来的目录一律改成"10 分钟前"，绕开年龄保护


def _age_dir(path):
    """把目录 mtime 推到过去，避免被"太新"判据直接拦下。"""
    import time
    old = time.time() - _STALE_AGE
    for dirpath, dirnames, filenames in os.walk(path):
        for n in filenames:
            try:
                os.utime(os.path.join(dirpath, n), (old, old))
            except OSError:
                pass
    os.utime(path, (old, old))


def _lock_me(path):
    """让**本进程**映射该 DLL，模拟"活实例正在用"。返回句柄（勿释放）。"""
    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.LoadLibraryW.restype = ctypes.c_void_p
    return k.LoadLibraryW(path)


def _make_rx_only(path):
    """把文件 DACL 改成"只有读+执行"（无写、无删除），模拟提权实例留下的 DLL。

    ⚠️ 四个坑（全部实测踩过，别再改回去）：
      1. 必须用 **SID**（`*S-1-5-...`）授权，不能用 `%USERNAME%` ——
         同名形式下 icacls 认不出账户，`/grant:r` 静默失败，文件只剩
         SYSTEM/Administrators，用例完全失真。
      2. 必须 `icacls /inheritance:r` 断掉继承，只留我们写的那条。
      3. **必须再 `/remove:g *S-1-3-4` 删掉 OWNER RIGHTS** ——
         这是关键：OWNER RIGHTS(S-1-3-4) 是"所有者隐式全权"的 ACE，
         只要当前用户是文件所有者，它就自动生效，文件永远可写、可删。
         不去掉它，"只有 RX 权限"这个场景**根本造不出来**
         （实测：留着它时 os.open(O_RDWR) 返回 OK，用例永远是绿的假象）。
      4. 授权里不能含 DELETE/DELETE_CHILD —— 否则测不出"没写权但可删"。
    """
    import subprocess
    sid = subprocess.run(
        ['powershell', '-NoProfile', '-Command',
         '[Security.Principal.WindowsIdentity]::GetCurrent().User.Value'],
        capture_output=True, text=True).stdout.strip()
    grant = ('*%s:(RX)' % sid) if sid else 'Everyone:(RX)'
    subprocess.run(['icacls', path, '/inheritance:r', '/grant:r', grant],
                   capture_output=True, check=False)
    # 去掉所有者的隐式全权（见上面第 3 条）
    subprocess.run(['icacls', path, '/remove:g', '*S-1-3-4'],
                   capture_output=True, check=False)


def _deletable(path):
    """真删一次看看 —— 这是唯一可信的"是否在用"地面真值。"""
    try:
        os.remove(path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- 用例

def case_empty_dir():
    """空目录应判"不在用"（可删）。"""
    d = tempfile.mkdtemp(prefix='_MEIprobe_empty_')
    _age_dir(d)                     # 必须调年龄：否则"太新"保护会把它判成在用
    try:
        from main import _runtime_dir_in_use
        verdict = _runtime_dir_in_use(d)
        return ('空目录', verdict, False, '空目录不该被当成"在用"')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def case_rx_only_file():
    """文件只有 RX 权限、无人持有 → 应判"不在用"（它真的能被删掉）。"""
    d = tempfile.mkdtemp(prefix='_MEIprobe_rx_')
    f = os.path.join(d, 'VCRUNTIME140.dll')
    shutil.copyfile(r'C:\Windows\System32\VCRUNTIME140.dll', f)
    _make_rx_only(f)
    # 地面真值：删一次。能删 = 没人在用。
    really_deletable = _deletable(f)
    if really_deletable:
        shutil.copyfile(r'C:\Windows\System32\VCRUNTIME140.dll', f)
        _make_rx_only(f)
    else:
        # 连删都删不掉，说明 RX 权限设错了 —— 这个用例没意义，直接判红。
        shutil.rmtree(d, ignore_errors=True)
        return ('RX 用例自身的权限设置失败(文件不可删)', True, False,
                '用例没造出"没写权但可删"的场景，护栏是假的')

    # ⚠️ 年龄必须在**所有会碰目录的操作之后**再调 —— os.remove / copy
    #    都会把父目录 mtime 刷成"现在", 早调等于白调(踩过)。
    _age_dir(d)

    from main import _runtime_dir_in_use
    verdict = _runtime_dir_in_use(d)
    try:
        shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass
    # 文件真的可删，却判成"在用" → 失败
    return ('只有 RX 权限、无人持有(实测可删=是)', verdict, False,
            '可删的文件被误判成"在用" → 永远清不掉')


def case_mapped_by_self():
    """文件被**另一个进程**映射 → 应判"在用"。

    ⚠️ 必须用子进程来做映射，不能用本进程 LoadLibrary：
       本进程映射后无法卸载，这个目录就永远删不掉，探针每跑一次
       Temp 里就多一个 `_MEIprobe_mapped_*` 垃圾（本机踩过，堆了两个）。
       用子进程则可以 kill 掉它，然后干净收场。
    """
    d = tempfile.mkdtemp(prefix='_MEIprobe_mapped_')
    f = os.path.join(d, 'VCRUNTIME140_1.dll')
    shutil.copyfile(r'C:\Windows\System32\VCRUNTIME140_1.dll', f)
    _age_dir(d)

    holder = os.path.join(d, '_hold.py')
    with open(holder, 'w', encoding='utf-8') as fp:
        fp.write('import ctypes, sys, time\n'
                 'ctypes.WinDLL("kernel32").LoadLibraryW(sys.argv[1])\n'
                 'print("held", flush=True)\n'
                 'time.sleep(120)\n')
    proc = subprocess.Popen([sys.executable, holder, f],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        # 等它真的把 DLL 映射上（读到 held 为止）
        proc.stdout.readline()
        from main import _runtime_dir_in_use
        verdict = _runtime_dir_in_use(d)
    finally:
        proc.kill()
        proc.wait(timeout=10)
        # ⚠️ 子进程刚死时内核可能还在回收映射，立即 rmtree 会失败并留下垃圾目录
        #    （实测每跑一次就在 Temp 堆一个 `_MEIprobe_mapped_*`）。
        #    重试几轮即可删净。
        for _ in range(10):
            shutil.rmtree(d, ignore_errors=True)
            if not os.path.isdir(d):
                break
            time.sleep(0.5)
    return ('文件被另一进程映射', verdict, True,
            '真正被占用时必须判"在用"（安全边界）')


def case_quarantine_dir():
    """退出时自改名隔离出的 _MEI*_QUAR 目录，应被清理逻辑识别。"""
    d = tempfile.mkdtemp(prefix='_MEIprobe_quar_')
    f = os.path.join(d, 'dummy.dll')
    with open(f, 'wb') as fp:
        fp.write(b'x' * 1024)
    _age_dir(d)
    # 模拟真实命名
    quar = d + '_QUAR'
    os.rename(d, quar)
    try:
        from main import _cleanup_stale_runtime_dirs
        # 接管 Temp 扫描范围：把 quarantine 放进系统 Temp 才会被扫到
        tmp = tempfile.gettempdir()
        target = os.path.join(tmp, '_MEIprobeQUAR_QUAR')
        shutil.rmtree(target, ignore_errors=True)
        os.rename(quar, target)
        _age_dir(target)
        _cleanup_stale_runtime_dirs()
        gone = not os.path.isdir(target)
        return ('_MEI*_QUAR 隔离目录被清理', gone, True, '自改名留下的隔离目录必须能被下次启动清掉')
    finally:
        shutil.rmtree(quar, ignore_errors=True)


def main():
    cases = [case_empty_dir, case_rx_only_file, case_mapped_by_self, case_quarantine_dir]
    failed = 0
    for fn in cases:
        try:
            label, actual, expect, why = fn()
        except Exception as e:
            print('ERROR %-52s %s: %s' % (fn.__name__, type(e).__name__, e))
            failed += 1
            continue
        ok = (actual == expect)
        mark = 'PASS' if ok else 'FAIL'
        if not ok:
            failed += 1
        print('%s  %-52s 实际=%-5s 期望=%-5s  %s' % (mark, label, actual, expect, '' if ok else why))
    print('-' * 100)
    print('失败项: %d / %d' % (failed, len(cases)))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
