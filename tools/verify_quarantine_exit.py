# -*- coding: utf-8 -*-
r"""验证退出隔离是否真能让 bootloader **不弹框** —— 用 exit code 判别。

🔴 为什么重写(2026-10-23 踩坑记录, 别再走一遍):
   上一版用 console 模式(默认)构建探针, 看到 "exit code 0 + 无窗口" 就判 PASS。
   **那是假阳性**: console 模式下 bootloader 的失败信息只打到 stderr,
   凡是 stderr 没被接管的场景都"看起来像成功"; 而且当时没造出
   "文件被外部进程锁住"这个**必要前提**, 对照组根本不复现故障。

   真机反证: v4.9.1(只改名)装上去, 关闭程序后 Warning 框**照样弹**,
   对话框里写的还是原路径。窗口模式(console=False)三模式对照实验:
       none          → exit 1（复现故障）
       rename        → exit 1（只改名无效）
       rename_empty  → exit 0（改名 + 原路径重建空目录 = 有效）

所以本探针的正确形态是：
  1) 用 `--noconsole` 构建(与真实产物 console=False 一致);
  2) **必须先自证前提**: 往 _MEIPASS 塞一个 dll, 让外部进程映射住,
     试删失败才算前提成立, 否则该用例作废(不许据此判 PASS);
  3) 判据只看 **exit code**: 非 0 = bootloader 清理失败(会弹框);
  4) 对照组(none)必须是非 0, 否则说明实验不通, 结果全部作废。

跑法：<venv python> tools/verify_quarantine_exit.py
"""
import ctypes
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

PY = sys.executable
WORK = os.path.join(os.environ.get('TEMP', '.'), '_quarprobe2')

u = ctypes.WinDLL('user32', use_last_error=True)

APP_SRC = r'''# -*- coding: utf-8 -*-
import ctypes, os, sys, time, subprocess

MODE = os.environ.get('QPROBE_MODE', 'none')
PY = os.environ['QPROBE_PY']
OUT = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'qres.txt')

def log(m):
    with open(OUT, 'a', encoding='utf-8') as f:
        f.write(str(m) + '\n')

k = ctypes.WinDLL('kernel32', use_last_error=True)
k.DeleteFileW.argtypes = [ctypes.c_wchar_p]
k.DeleteFileW.restype = ctypes.c_int

def main():
    mei = getattr(sys, '_MEIPASS', '')
    victim = os.path.join(mei, 'zz_victim_%d.dll' % os.getpid())
    with open(r'C:\Windows\System32\VCRUNTIME140.dll','rb') as fi:
        data = fi.read()
    with open(victim,'wb') as fo:
        fo.write(data)
    holder = os.path.join(os.environ['TEMP'], '_h_%d.py' % os.getpid())
    with open(holder,'w',encoding='utf-8') as f:
        f.write('import ctypes,sys,time\n'
                'ctypes.WinDLL("kernel32").LoadLibraryW(sys.argv[1])\n'
                'time.sleep(180)\n')
    p = subprocess.Popen([PY, holder, victim],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)
    alive = p.poll() is None
    removed = bool(k.DeleteFileW(victim))
    locked = alive and not removed
    log('locked=%s' % locked)
    if not locked:
        log('PRECONDITION_FAIL')
        return
    if MODE == 'rename':
        try:
            os.rename(mei, mei + '_QUAR')
            log('renamed')
        except Exception as e:
            log('rename_fail %r' % (e,))
    elif MODE == 'rename_empty':
        try:
            os.rename(mei, mei + '_QUAR')
            os.makedirs(mei, exist_ok=True)
            log('renamed_and_empty')
        except Exception as e:
            log('rename_empty_fail %r' % (e,))
    else:
        log('none')

main()
'''


def build():
    os.makedirs(WORK, exist_ok=True)
    src = os.path.join(WORK, 'app.py')
    with open(src, 'w', encoding='utf-8') as f:
        f.write(APP_SRC)
    env = dict(os.environ)
    env['TEMP'] = WORK
    env['TMP'] = WORK
    dist = os.path.join(WORK, 'dist')
    shutil.rmtree(dist, ignore_errors=True)
    print('构建窗口模式(console=False) onefile 探针 ...')
    r = subprocess.run([PY, '-m', 'PyInstaller', '--onefile', '--noconsole',
                        '--noconfirm', '--log-level', 'ERROR',
                        '--distpath', dist, '--workpath', os.path.join(WORK, 'build'),
                        '--specpath', WORK, src],
                       capture_output=True, text=True, encoding='utf-8', env=env)
    if r.returncode != 0:
        print((r.stdout or '')[-1500:])
        print((r.stderr or '')[-1500:])
        return None
    return os.path.join(dist, 'app.exe')


def run_case(exe, mode):
    res = os.path.join(os.path.dirname(exe), 'qres.txt')
    if os.path.exists(res):
        os.remove(res)
    env = dict(os.environ)
    env['QPROBE_MODE'] = mode
    env['QPROBE_PY'] = PY
    env['TEMP'] = WORK
    env['TMP'] = WORK
    p = subprocess.Popen([exe], env=env, cwd=WORK)
    try:
        code = p.wait(timeout=90)      # 窗口模式失败会卡模态框 -> 超时
    except subprocess.TimeoutExpired:
        p.kill()
        code = 'TIMEOUT(卡模态框)'
    log = ''
    try:
        with open(res, encoding='utf-8') as f:
            log = f.read().strip().replace('\n', ' | ')
    except OSError:
        pass
    return {'mode': mode, 'exit': code, 'log': log}


def main():
    exe = build()
    if not exe:
        return 1
    rows = [run_case(exe, m) for m in ('none', 'rename', 'rename_empty')]
    print('\n' + '=' * 78)
    for r in rows:
        print('  %-13s exit=%-16s %s' % (r['mode'], r['exit'], r['log']))
    print('=' * 78)

    control_ok = rows[0]['exit'] != 0
    if 'PRECONDITION_FAIL' in rows[0]['log']:
        print('结论: 作废 —— 对照组没造出"文件被锁"这个前提')
        return 1
    if not control_ok:
        print('结论: 作废 —— 对照组(none)没复现故障(exit=%s)，不能据此判断修复有效'
              % rows[0]['exit'])
        return 1

    fix_ok = rows[2]['exit'] == 0
    print('对照组 none         exit=%s  (非 0 = 故障已复现)' % rows[0]['exit'])
    print('v4.9.1 rename       exit=%s  (非 0 = 该方案无效)' % rows[1]['exit'])
    print('v4.9.2 rename+empty exit=%s  (0 = 修复有效)' % rows[2]['exit'])
    print('-' * 78)
    verdict = 'PASS —— 改名并重建空目录可让 bootloader 静默退出' if fix_ok else 'FAIL'
    print('判定:', verdict)
    return 0 if fix_ok else 1


if __name__ == '__main__':
    sys.exit(main())
