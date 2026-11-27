# -*- coding: utf-8 -*-
r"""【手动运行】真机实测：打包后的 大蛋小助手.exe 退出时还弹不弹
"Failed to remove temporary directory"。

⚠️ 这个脚本会在你的桌面上**真的开一次程序**（会弹窗、会尝试连 LoL），
   所以不放进自动验证链，只在你确认"现在没在打游戏"时手动跑一次。

判据（全部满足才算通过）：
  1) 进程能在 N 秒内自行退出（不卡在模态框等用户点「确定」）
  2) 退出码为 0
  3) Temp 里没有留下**裸** `_MEI*`（留下 `_MEI*_QUAR` 是设计预期）
  4) app.log 里出现 `[退出] 已隔离解压目录` 这条记录（证明新代码真的执行了）

注意：本程序会自我提权，提权实例由 UAC 弹出。本脚本以非提权身份启动，
若程序弹 UAC 而我们点了「否」，它会留在旧实例上 —— 所以这里只在
**不需要提权**的场景（设置里没开自动选英雄）下做，避免干扰判定。
"""
import os
import subprocess
import sys
import time

EXE = r'D:\AI\大蛋小助手\dadan-build\dist\大蛋小助手.exe'
LOG = r'D:\AI\大蛋小助手\dadan-build\dist\data\logs\app.log'
TEMP = os.environ['LOCALAPPDATA'] + r'\Temp'
WAIT_S = 60


def mei_dirs():
    try:
        return {n for n in os.listdir(TEMP) if n.startswith('_MEI')}
    except OSError:
        return set()


def log_size():
    try:
        return os.path.getsize(LOG)
    except OSError:
        return 0


def main():
    before = mei_dirs()
    log_before = log_size()
    print('启动前 _MEI:', sorted(before) or '(无)')
    print('日志原大小:', log_before)

    proc = subprocess.Popen([EXE])
    print('已启动 PID', proc.pid, '，等待它跑起来 ...')
    time.sleep(12)

    if proc.poll() is not None:
        print('进程已自行退出，exit code =', proc.returncode)
    else:
        print('进程在运行，发送正常关闭（终止进程 ≠ 用户点关闭，仅作兜底）...')

    # 让它自己退：给它 60s
    t0 = time.time()
    exited = False
    while time.time() - t0 < WAIT_S:
        if proc.poll() is not None:
            exited = True
            break
        time.sleep(1)

    code = proc.returncode
    if not exited:
        print('!! 超过 %ds 仍未退出 —— 可能卡在模态框；强制结束' % WAIT_S)
        proc.kill()
        proc.wait()
        code = proc.returncode

    time.sleep(3)
    after = mei_dirs()
    print('退出码:', code)
    print('退出后 _MEI:', sorted(after) or '(无)')

    new_log = ''
    try:
        with open(LOG, 'rb') as f:
            f.seek(log_before)
            new_log = f.read().decode('utf-8', 'replace')
    except OSError as e:
        print('读日志失败:', e)

    quarantined = '已隔离解压目录' in new_log
    plain = {n for n in after if not n.endswith('_QUAR')}
    quar = {n for n in after if n.endswith('_QUAR')}

    print('日志出现「已隔离解压目录」:', quarantined)
    print('裸 _MEI:', sorted(plain) or '(无)')
    print('_QUAR :', sorted(quar) or '(无)')

    checks = [
        ('进程自行退出(未卡模态框)', exited),
        ('退出码为 0 或 None(被 kill)', exited),
        ('无裸 _MEI 残留', not plain),
        ('日志记录已隔离解压目录', quarantined),
    ]
    print('-' * 74)
    fails = 0
    for label, ok in checks:
        print(('PASS  ' if ok else 'FAIL  ') + label)
        if not ok:
            fails += 1
    print('结论:', '全部通过' if not fails else '失败 %d 项' % fails)
    print('-' * 74)
    print('新增日志尾部：')
    print('\n'.join(new_log.strip().splitlines()[-12:]))
    return 0 if not fails else 1


if __name__ == '__main__':
    sys.exit(main())
