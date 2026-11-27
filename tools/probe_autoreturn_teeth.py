# -*- coding: utf-8 -*-
"""probe_autoreturn_teeth.py —— 证明「自动返回阶段守卫」那组测试**真的会判红**。

为什么必须有这个探针（本项目的历史教训）:
    v4.6.15 那轮发现过「逻辑对但没接线」的事故 —— 测试全绿而防线其实不存在。
    本项目此后立了规矩: 凡新增缺陷护栏, 都要**故意把缺陷放回去, 确认测试判红**,
    跑完逐字节还原并核对 sha256。否则"测试通过"只说明测试能跑, 不说明它有牙。

本探针做四种破坏, 每一种都对应 v4.9.12 实际修掉的那个成因:
    ① 删掉 `_do_play_again` 的阶段守卫条件
       → 阶段已回大厅仍 playAgain, 把刚起的匹配流程倒带。
    ② 删掉「进入 Lobby 时作废返回倒计时」
       → 在途返回倒计时到点再 playAgain 一次。
    ③ 删掉「离开 Lobby 时作废匹配倒计时」
       → 状态栏两个倒计时交错刷。
    ④ 在 play_again 后重新插入 start_matchmaking
       → 与 _handle_lobby 的匹配倒计时抢开队列, 绕过用户的匹配延迟。

用法(离屏):
    <python> tools\\probe_autoreturn_teeth.py

退出码: 0 = 四种破坏全部被判红且还原成功; 1 = 有破坏没被判红(护栏无牙)或还原失败。
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
TESTS = 'tests.test_regressions.TestAutoReturnPhaseGuard'

# (破坏名, 原文锚点, 替换后) —— 每项都必须在文件里**唯一命中**
MUTATIONS = [
    ('删掉阶段守卫条件',
     '        if phase not in self._PLAY_AGAIN_ALLOWED_PHASES:',
     '        if False:'),
    ('删掉「回大厅作废返回倒计时」',
     "            self._bump_countdown('return')",
     '            pass  # MUTATED'),
    ('删掉「离开大厅作废匹配倒计时」',
     "            self._bump_countdown('queue')",
     '            pass  # MUTATED'),
    ('playAgain 后重新抢开匹配队列',
     "            self._api.play_again()\n"
     "            print('[Core] 返回房间')",
     "            self._api.play_again()\n"
     "            self._api.start_matchmaking()\n"
     "            print('[Core] 返回房间')"),
]


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(65536), b''):
            h.update(b)
    return h.hexdigest()


def clear_pycache():
    """必做: 「改 → 跑 → 还原」常在同一秒内完成, 过期判断只到秒级 + 文件大小,
    恢复后的源码 mtime 与被破坏版本的 .pyc 落在同一秒 → Python 认为缓存有效,
    实际导入的是被破坏的字节码(本项目已踩过, 见 fix-with-teeth 技能坑①)。"""
    for root, dirs, files in os.walk(_ROOT):
        if '__pycache__' in dirs:
            shutil.rmtree(os.path.join(root, '__pycache__'), ignore_errors=True)
            dirs.remove('__pycache__')


def run_tests():
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    clear_pycache()
    r = subprocess.run([PY, '-m', 'unittest', TESTS, '-v'], cwd=_ROOT,
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

            # 确认破坏真的落到了文件上（不靠"以为改了"）
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
    print('\nPASS 自动返回护栏有牙' if ok else '\nFAIL 见上')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
