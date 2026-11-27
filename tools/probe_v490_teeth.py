# -*- coding: utf-8 -*-
"""有牙验证：把 v4.9.0 修掉的缺陷逐个放回去，确认测试会判红。

这不是"再跑一遍测试"，而是**证明护栏存在**。绿了不等于防线在，删掉还会红才算。

每组做法：备份 → 注入缺陷 → 跑指定测试 → 断言失败 → 逐字节还原 → 校验 sha256。

⚠️ 每次跑测试前必须清 __pycache__：本机踩过这个坑（见 version.json v4.6.15）——
.pyc 过期判断用「mtime 秒级整数 + 文件大小」，而本脚本「改 → 跑 → 还原」全在
同一秒内完成，恢复后的源码 mtime 与被破坏版本的 .pyc 落在同一秒 →
Python 认为缓存有效 → 实际导入的是**被破坏的字节码**，表现是"文件完好、测试却红"。

用法：
    <python> tools/probe_v490_teeth.py
退出码 0 = 所有破坏都被判红（护栏有牙）；1 = 有破坏没被判红（护栏是假的）。
"""
import hashlib
import io
import os
import shutil
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
API = os.path.join(_ROOT, 'lcu', 'api.py')
SC = os.path.join(_ROOT, 'core', 'script_core.py')
TESTS = os.path.join(_ROOT, 'tests')


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(65536), b''):
            h.update(b)
    return h.hexdigest()


def clear_pycache():
    for base in (_ROOT, TESTS):
        d = os.path.join(base, '__pycache__')
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
        if os.path.isdir(base):
            for name in os.listdir(base):
                sub = os.path.join(base, name, '__pycache__')
                if os.path.isdir(sub):
                    shutil.rmtree(sub, ignore_errors=True)


def run_test(dotted):
    env = dict(os.environ)
    env['PYTHONIOENCODING'] = 'utf-8'
    r = subprocess.run([sys.executable, '-m', 'unittest', dotted],
                       cwd=_ROOT, capture_output=True, env=env)
    out = r.stdout.decode('utf-8', 'replace') + r.stderr.decode('utf-8', 'replace')
    return r.returncode, out


# (名称, 目标文件, 原文, 替换成, 应判红的测试)
BROKEN = [
    ('① 名字兜底整个删掉（未本地化名字直接放行）',
     API,
     "        nid = (name_id or '').strip()\n"
     "        if nid:\n"
     "            # 与 augmentNameId 完全相同, 或以它开头(BlankName / _V2 之类的后缀)\n"
     "            if s == nid or s.lower().startswith(nid.lower()):\n"
     "                return ''\n"
     "        # 纯 ASCII + 含下划线 = 内部标识符形态(合法英文名里没有下划线)\n"
     "        if '_' in s and re.fullmatch(r'[A-Za-z0-9_]+', s):\n"
     "            return ''\n"
     "        return s",
     "        return s",
     'tests.test_regressions.TestAugmentNameFallback',
     ),

    ('② 判据退化成"纯 ASCII 就清"（误伤 ADAPt 这类官方英文名）',
     API,
     "        if '_' in s and re.fullmatch(r'[A-Za-z0-9_]+', s):\n"
     "            return ''\n"
     "        return s",
     "        if re.fullmatch(r'[A-Za-z0-9_!]+', s):\n"
     "            return ''\n"
     "        return s",
     'tests.test_regressions.TestAugmentNameFallback.test_latin_name_without_underscore_kept',
     ),

    ('③ 清洗没接进真实建表链路（只在函数里正确，表里照样脏）',
     API,
     "                        name = self._clean_augment_name(\n"
     "                            raw_name, str(a.get('augmentNameId') or ''))",
     "                        name = raw_name",
     'tests.test_regressions.TestAugmentNameFallback.test_real_load_path_cleans_3176',
     ),

    ('④ 白名单退回 v4.1.57 那个原始手写集合（漏 2400 及其后所有变体）',
     SC,
     "ARAM_QUEUE_IDS = {450, 451, 452, 720, 721, 860, 861,\n"
     "                  2400, 2401, 2403, 2405, 2410, 2450, 3270}",
     "ARAM_QUEUE_IDS = {450, 3270}",
     'tests.test_regressions.TestAramQueueCoverage.test_declared_covers_all_karam_allrandom',
     ),

    ('④b 白名单只补 2400（v4.1.57 当时的修法：治标不治本）',
     SC,
     "ARAM_QUEUE_IDS = {450, 451, 452, 720, 721, 860, 861,\n"
     "                  2400, 2401, 2403, 2405, 2410, 2450, 3270}",
     "ARAM_QUEUE_IDS = {450, 3270, 2400}",
     'tests.test_regressions.TestAramQueueCoverage.test_declared_covers_all_karam_allrandom',
     ),

    ('⑤ 客户端推导不接（白名单只靠手写常量 —— 根因没修）',
     API,
     "                    if (q.get('gameSelectModeGroup') == 'kARAM'\n"
     "                            and q.get('pickMode') == 'AllRandomPickStrategy'):",
     "                    if False:",
     'tests.test_regressions.TestAramQueueCoverage.test_live_derivation_includes_new_variants',
     ),

    ('⑥ 判据放宽成"只看 AllRandom"（会把 SR 全随机/无限乱斗也拉进来）',
     API,
     "                    if (q.get('gameSelectModeGroup') == 'kARAM'\n"
     "                            and q.get('pickMode') == 'AllRandomPickStrategy'):",
     "                    if q.get('pickMode') == 'AllRandomPickStrategy':",
     'tests.test_regressions.TestAramQueueCoverage.test_live_derivation_includes_new_variants',
     ),

    ('⑦ 推导失败时返回空集（点卡兜底整个消失，比不修更糟）',
     API,
     "            print(f'[点卡] 队列白名单推导失败(用手写兜底): {type(e).__name__}: {e}')\n"
     "            return from_hand",
     "            print(f'[点卡] 队列白名单推导失败(用手写兜底): {type(e).__name__}: {e}')\n"
     "            return set()",
     'tests.test_regressions.TestAramQueueCoverage.test_falls_back_to_hand_list_on_error',
     ),

    ('⑧ 空结果被缓存（客户端稍后就绪也不再重试）',
     API,
     "        if live:\n"
     "            LCUAPI._ARAM_QUEUE_IDS_LIVE = live",
     "        if True:\n"
     "            LCUAPI._ARAM_QUEUE_IDS_LIVE = live",
     'tests.test_regressions.TestAramQueueCoverage.test_empty_table_not_cached',
     ),
]


def main():
    originals = {API: sha256(API), SC: sha256(SC)}
    backups = {p: p + '.teeth.bak' for p in originals}
    for p in originals:
        shutil.copy2(p, backups[p])

    failures = []
    try:
        for name, target, old, new, dotted in BROKEN:
            with io.open(target, encoding='utf-8') as f:
                src = f.read()
            if src.count(old) != 1:
                failures.append((name, '锚点不唯一/不存在（count=%d），没法做破坏实验'
                                 % src.count(old)))
                print('[SKIP] %s\n        锚点 count=%d' % (name, src.count(old)))
                continue
            with io.open(target, 'w', encoding='utf-8', newline='') as f:
                f.write(src.replace(old, new))
            try:
                clear_pycache()
                code, out = run_test(dotted)
                if code == 0:
                    failures.append((name, '测试仍绿 —— 护栏是假的'))
                    print('[FAIL] %s\n        破坏后测试仍通过（护栏没牙）' % name)
                else:
                    first = ''
                    for ln in out.splitlines():
                        if ln.startswith(('FAIL:', 'ERROR:')):
                            first = ln.strip()
                            break
                    print('[RED ] %s\n        %s' % (name, first or '(测试判红)'))
            finally:
                shutil.copy2(backups[target], target)
                clear_pycache()
    finally:
        for p, bak in backups.items():
            shutil.copy2(bak, p)
            os.remove(bak)
        clear_pycache()

    # 还原校验：逐字节
    bad_restore = []
    for p, h in originals.items():
        now = sha256(p)
        if now != h:
            bad_restore.append((os.path.relpath(p, _ROOT), h, now))
    print()
    if bad_restore:
        for rel, want, got in bad_restore:
            print('[!!] 还原失败 %s  sha256 期望 %s 实得 %s' % (rel, want[:16], got[:16]))
        return 1
    print('还原校验通过：%d 个源文件 sha256 与实验前一致'
          % len(originals))

    if failures:
        print('\n有 %d 组破坏没被判红 —— 护栏无效：' % len(failures))
        for name, why in failures:
            print('    %s -> %s' % (name, why))
        return 1
    print('\nPASS %d 组破坏全部被判红，护栏有牙' % len(BROKEN))
    return 0


if __name__ == '__main__':
    sys.exit(main())
