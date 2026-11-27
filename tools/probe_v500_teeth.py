# -*- coding: utf-8 -*-
"""probe_v500_teeth.py —— 证明 v5.0.0(#1 全停 / #2 冷却)的护栏**真的会判红**。

为什么必须有这个探针（本项目的历史教训）:
    v4.6.15 那轮发现过「逻辑对但没接线」的事故 —— 测试全绿而防线其实不存在。
    本项目此后立了规矩: 凡新增缺陷护栏, 都要**故意把缺陷放回去, 确认测试判红**,
    跑完逐字节还原并核对 sha256。

本探针做五种破坏, 对应 v5.0.0 两处修复:
    #1 全停:
      ① 把 _loop 的 `if self._stop_event.wait(C.RECONNECT_INTERVAL_S)` 改回
         `time.sleep(...)` → 停止后主循环不能立即醒来。
      ② 把 _start_countdown 里的 `if self._stop_event.wait(...)` 改回
         `time.sleep(C.COUNTDOWN_TICK_S)` → 倒计时线程 stop 后仍睡满整秒。
    #2 冷却:
      ③ 删掉 _do_auto_pick 里 `if self._swap_unconfirmed_cooling(...)` 前置守卫
         → 读回未确认后每轮重复 POST。
      ④ 删掉 _do_preinput 里同款前置守卫 → 预输入路径重复 POST。
      ⑤ 把 _swap_unconfirmed_cooling 的冷却条件改成 `return False`(永不禁发)
         → 冷却形同虚设。

用法(离屏):
    <python> tools\\probe_v500_teeth.py
退出码: 0 = 全部判红且还原成功; 1 = 有破坏没被判红(护栏无牙)或还原失败。
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
TARGET = os.path.join(_ROOT, 'core', 'script_core.py')
PY = sys.executable
TESTS = ('tests.test_regressions.TestStopAllThreads '
         'tests.test_regressions.TestSwapUnconfirmedCooldown')

MUTATIONS = [
    ('#1 _loop 改回 time.sleep(停止不能立即醒来)',
     '                    if self._stop_event.wait(C.RECONNECT_INTERVAL_S):\n'
     '                        break',
     '                    time.sleep(C.RECONNECT_INTERVAL_S)'),
    ('#1 倒计时改回 time.sleep(stop 后睡满整秒)',
     '                if self._stop_event.wait(C.COUNTDOWN_TICK_S):\n'
     '                    _cancel()\n'
     '                    return',
     '                time.sleep(C.COUNTDOWN_TICK_S)'),
    ('#2 偏好路径删掉冷却前置守卫',
     '        if self._swap_unconfirmed_cooling(best_cid, now):\n'
     '            return',
     '        if False:\n            return'),
    ('#2 预输入路径删掉冷却前置守卫',
     '        if self._swap_unconfirmed_cooling(cid, now):\n'
     '            return True',
     '        if False:\n            return True'),
    ('#2 冷却函数永不禁发',
     '        if (self._last_swap_unconfirmed_cid == cid\n'
     '                and now - self._last_swap_unconfirmed_t < C.SWAP_UNCONFIRMED_RETRY_S):\n'
     '            return True\n'
     '        return False',
     '        if False:\n'
     '            return True\n'
     '        return False'),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(65536), b''):
            h.update(b)
    return h.hexdigest()


def clear_pycache():
    for root, dirs, files in os.walk(_ROOT):
        if '__pycache__' in dirs:
            shutil.rmtree(os.path.join(root, '__pycache__'), ignore_errors=True)
            dirs.remove('__pycache__')


def run_tests():
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    clear_pycache()
    r = subprocess.run([PY, '-m', 'unittest'] + TESTS.split(), cwd=_ROOT,
                       capture_output=True, env=env)
    out = r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')
    return r.returncode, out


def main():
    before = sha256(TARGET)
    bak = TARGET + '.teeth.bak'
    shutil.copy2(TARGET, bak)
    ok = True
    try:
        with io.open(TARGET, encoding='utf-8') as f:
            original = f.read()

        for name, old, new in MUTATIONS:
            if original.count(old) != 1:
                print('[SKIP] 锚点不唯一/不存在(count=%d): %s'
                      % (original.count(old), name))
                ok = False
                continue
            with io.open(TARGET, 'w', encoding='utf-8', newline='') as f:
                f.write(original.replace(old, new))
            with io.open(TARGET, encoding='utf-8') as f:
                now = f.read()
            if new not in now:
                print('[!!] 破坏没有生效: %s' % name)
                ok = False
                continue
            code, out = run_tests()
            if code == 0:
                print('[FAIL] 放回缺陷后测试仍然全绿 —— 护栏是假的: %s' % name)
                print(out[-900:])
                ok = False
            else:
                red = [ln for ln in out.splitlines() if ln.startswith(('FAIL:', 'ERROR:'))]
                print('[RED ] 判红: %s' % name)
                for ln in red[:3]:
                    print('        %s' % ln.strip())
    finally:
        shutil.copy2(bak, TARGET)
        os.remove(bak)

    after = sha256(TARGET)
    print()
    if after != before:
        print('[!!] 还原失败  sha256 %s -> %s' % (before[:16], after[:16]))
        return 1
    print('还原校验通过：core/script_core.py sha256 与实验前一致')

    code, out = run_tests()
    if code != 0:
        print('[!!] 还原后测试仍然失败：\n%s' % out[-900:])
        return 1
    print('还原后测试通过')
    print('\nPASS v5.0.0 护栏有牙' if ok else '\nFAIL 见上')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
