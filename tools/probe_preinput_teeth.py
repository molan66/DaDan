# -*- coding: utf-8 -*-
"""probe_preinput_teeth.py —— 证明「预输入选英雄」那组测试**真的会判红**。

为什么必须有这个探针（本项目的历史教训）:
    v4.6.15 那轮发现过「逻辑对但没接线」的事故 —— 测试全绿而防线其实不存在。
    本项目此后立了规矩: 凡新增缺陷护栏, 都要**故意把缺陷放回去, 确认测试判红**,
    跑完逐字节还原并核对 sha256。

本探针做六种破坏, 每一种都对应 v4.9.13 这个功能真实可能踩的坑:
    ① 把预输入判断从"偏好之前"挪到"偏好之后"（或直接删掉调用）
       → 预输入不再优先, 用户标了也没用。
    ② 命中后清掉 _preinput_cid
       → 下一轮退回偏好逻辑, 把刚换好的英雄又换走。
    ③ 池里没有它时**不**清标记
       → 永远等一个不会出现的英雄, 蓝光一直亮着。
    ④ 池为空时也清标记
       → 备选池刷新间隙把用户刚点的标记误清。
    ⑤ 离开选人阶段不清
       → 跨局沿用, 下一局先亮蓝光再被"已不在池"清掉。
    ⑥ toggle 不做"同 id 取消"
       → 再单击无法取消, 与用户"单击选中、再单击取消"的要求不符。

用法(离屏):
    <python> tools\\probe_preinput_teeth.py

退出码: 0 = 六种破坏全部被判红且还原成功; 1 = 有破坏没被判红(护栏无牙)或还原失败。
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
TESTS = 'tests.test_regressions.TestPreinputChampion'

# (破坏名, 原文锚点, 替换后) —— 每项都必须在文件里**唯一命中**
MUTATIONS = [
    ('删掉"预输入优先"的调用',
     '        if self._do_preinput(bench_set, current_id, bench_ids, now):\n'
     '            return\n',
     '        if False:\n            return\n'),
    ('命中后清掉标记(下轮被偏好换走)',
     "        if current_id == cid:\n"
     "            with self._preinput_lock:\n"
     "                first_hit = not self._preinput_hit",
     "        if current_id == cid:\n"
     "            self._clear_preinput()\n"
     "            with self._preinput_lock:\n"
     "                first_hit = not self._preinput_hit"),
    ('池里没有它时不清标记',
     "        if cid not in bench_set:\n"
     "            name = self._champ_data.get(cid, str(cid))\n"
     "            self._clear_preinput()",
     "        if False:\n"
     "            name = self._champ_data.get(cid, str(cid))\n"
     "            self._clear_preinput()"),
    ('池为空时也清标记',
     "        if not bench_ids:\n"
     "            # v4.9.13: 池为空时**不许**清预输入 —— 备选池在下发/刷新中会短暂为空,\n"
     "            # 那时清掉标记, 用户刚点的那一下就白做了(实测日志里池子逐秒长出来)。\n"
     "            return",
     "        if not bench_ids:\n"
     "            self._clear_preinput()\n"
     "            return"),
    ('离开选人阶段不清空',
     "            if self._preinput_cid or self._preinput_hit:\n"
     "                print('[Core] 离开选人阶段, 已清空预输入英雄')\n"
     "            self._clear_preinput()",
     "            pass  # MUTATED"),
    ('toggle 不做"同 id 取消"',
     "            # 同一英雄再点一次 = 取消\n"
     "            self._preinput_cid = 0 if cid == self._preinput_cid else cid",
     "            # 同一英雄再点一次 = 取消\n"
     "            self._preinput_cid = cid"),
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
    print('\nPASS 预输入护栏有牙' if ok else '\nFAIL 见上')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
