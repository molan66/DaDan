# -*- coding: utf-8 -*-
r"""probe_conn_state_teeth.py —— 证明「小窗停琥珀」那组测试**真的会判红**。

为什么必须有这个探针（本项目的历史教训）:
    v4.6.15 那轮发现过「逻辑对但没接线」的事故 —— 测试全绿而防线其实不存在。
    本项目此后立了规矩: 凡新增缺陷护栏, 都要**故意把缺陷放回去, 确认测试判红**,
    跑完逐字节还原并核对 sha256。否则"测试通过"只说明测试能跑, 不说明它有牙。

本探针做五种破坏, 每一种都对应 v4.7.3 实际修掉的那个成因:
    ① 把 `_apply_ui_connected` 末尾的 `_sync_conn_state()` 调用删掉
       → 三态退回"只在跃迁时推一次", t2 那种非跃迁置绿又成 no-op。
    ② 把 `_try_connect` 的收口调用换回"无条件直推琥珀"
       → 已连接时仍会被重新盖琥珀(缺陷的起点)。
    ③ 把 `_sync_conn_state` 的判据改成"只看 _ui_connected"
       → WS 已 on_open 时会被误判成未连接。
    ④ 去掉 `_sync_conn_state` 的幂等比较
       → 每 3s 重推一次, 会把小窗的三点跳动动画周期性掐死。
    ⑤ 把"在途连接(琥珀)"的优先级提到"已连接"之前
       → 连接线程还在跑时, 已连接的绿会被降级成琥珀。

用法(离屏):
    <python> tools\probe_conn_state_teeth.py

退出码: 0 = 三种破坏全部被判红且还原成功; 1 = 有破坏没被判红(护栏无牙)或还原失败。
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
MAIN = os.path.join(_ROOT, 'main.py')
PY = sys.executable

# 只跑这条链上的两组用例，避免每次破坏都全量跑 160 个
TESTS = 'tests.test_regressions.TestOverlayConnStateNoStaleAmber '
TESTS += 'tests.test_regressions.TestOverlayConnState'


def sha(path):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()


def run_tests():
    env = dict(os.environ)
    env['QT_QPA_PLATFORM'] = 'offscreen'
    env['PYTHONIOENCODING'] = 'utf-8'
    # 清字节码缓存: "改 → 跑 → 还原"可能落在同一秒, Python 用 (mtime 秒 + 大小)
    # 判 .pyc 是否过期 → 会继续导入**被破坏的**字节码, 表现为"文件已还原、测试却
    # 仍失败"这种极具误导性的假红(v4.6.15 踩过)。
    for root, dirs, _ in os.walk(_ROOT):
        for d in list(dirs):
            if d == '__pycache__':
                shutil.rmtree(os.path.join(root, d), ignore_errors=True)
    r = subprocess.run(
        [PY, '-m', 'unittest', *TESTS.split()],
        cwd=_ROOT, env=env, capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=300)
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def main():
    original = open(MAIN, encoding='utf-8').read()
    orig_sha = sha(MAIN)
    print(f'[基线] main.py sha256={orig_sha[:16]}…')

    rc, out = run_tests()
    if rc != 0:
        print('[基线] 修复后的代码**测试就没过** —— 先修好再谈护栏有没有牙')
        print(out[-3000:])
        return 1
    print('[基线] 修复后测试通过 ✔ (下面开始故意破坏)')

    mutations = [
        # ⚠️ 锚点必须能唯一定位到**目标那一处**。首版这里踩过坑: 8 空格缩进的
        # `        self._sync_conn_state()` 是 12 空格那行的**子串**, count()=2,
        # replace(…,1) 于是改掉了 _try_connect 里那一处(不承重), 破坏"没被判红",
        # 一度被我误读成"护栏无牙"。现在锚点带上上下文并断言只匹配 1 处。
        ('① 删掉 _apply_ui_connected 末尾的 _sync_conn_state() 调用',
         '        # 原先把三态推送写在上面两个跃迁分支里, 是本次用户报障的根因, 详见本方法注释。\n'
         '        self._sync_conn_state()\n',
         '        pass  # MUTANT-1\n'),
        ('② 把 _try_connect 的收口调用换回"无条件直推琥珀"(还原旧缺陷)',
         '        # 入口(`_ws.connect()` 由 worker 调), 提前返回会让 WS 永不恢复。\n'
         '        self._sync_conn_state()\n',
         "        self._set_conn_state('connecting', '连接中')  # MUTANT-2\n"),
        ('③ _sync_conn_state 判据只认 _ui_connected(丢掉 WS 权威信号)',
         '            live = bool(self._ui_connected) or bool(self._ws.is_connected)\n',
         '            live = bool(self._ui_connected)  # MUTANT-3\n'),
        ('④ 去掉幂等比较(退化成"每次都重推")',
         "        if getattr(self, '_conn_state_shown', None) == target:\n"
         '            return                      # 屏上已经是对的 —— 别为了"重推一次"打断动画\n',
         '        pass  # MUTANT-4\n'),
        ('⑤ 把"在途连接(琥珀)"的优先级提到"已连接"之前',
         '        if live:\n'
         "            target = ('connected', '已连接')\n"
         "        elif getattr(self, '_connecting', False):\n"
         "            target = ('connecting', '连接中')\n",
         '        if getattr(self, \'_connecting\', False):\n'
         "            target = ('connecting', '连接中')  # MUTANT-5\n"
         '        elif live:\n'
         "            target = ('connected', '已连接')\n"),
    ]

    verdicts = []
    try:
        for label, old, new in mutations:
            n = original.count(old)
            if n != 1:
                print(f'[跳过] {label}: 锚点匹配 {n} 处（应为 1, 代码已变动, 请更新探针）')
                verdicts.append((label, None))
                continue
            with open(MAIN, 'w', encoding='utf-8', newline='') as f:
                f.write(original.replace(old, new, 1))
            rc, out = run_tests()
            caught = (rc != 0)
            verdicts.append((label, caught))
            print(f'[破坏] {label} → 测试{"判红 ✔" if caught else "**仍然通过 ✘ 护栏无牙**"}')
            if not caught:
                print(out[-1500:])
    finally:
        with open(MAIN, 'w', encoding='utf-8', newline='') as f:
            f.write(original)
        for root, dirs, _ in os.walk(_ROOT):
            for d in list(dirs):
                if d == '__pycache__':
                    shutil.rmtree(os.path.join(root, d), ignore_errors=True)

    now_sha = sha(MAIN)
    if now_sha != orig_sha:
        print(f'[还原] ✘ sha256 不一致! {now_sha[:16]}… != {orig_sha[:16]}…')
        return 1
    print(f'[还原] ✔ 逐字节一致 sha256={now_sha[:16]}…')

    rc, out = run_tests()
    if rc != 0:
        print('[还原后] 测试未通过 —— 还原有残留')
        print(out[-3000:])
        return 1
    print('[还原后] 测试重新通过 ✔')

    missing = [l for l, c in verdicts if c is not True]
    print()
    if missing:
        print(f'[FAIL] 下列破坏没被判红/未能验证: {missing}')
        return 1
    print('[OK] 五种破坏全部判红, 且已逐字节还原 —— 这组护栏有牙')
    return 0


if __name__ == '__main__':
    sys.exit(main())
