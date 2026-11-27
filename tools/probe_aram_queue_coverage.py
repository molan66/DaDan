# -*- coding: utf-8 -*-
"""探针：ARAM_QUEUE_IDS 是否覆盖客户端当前所有「2-3 张卡」队列。

为什么需要它
------------
`core/script_core.py` 的 `ARAM_QUEUE_IDS` 决定**屏幕点卡兜底**是否启用。它不是
随便一个集合：点卡坐标是按 ARAM「2-3 张卡」布局标定的，坐标落到别处会乱点。
所以这个集合漏一个队列 → 那个队列自动选英雄整局不动；多一个队列 → 在别的
选人界面上乱点。两种都是静默故障。

历史
----
v4.1.57 修过一次同类缺陷：白名单只有 {450, 3270}，而实际最常玩的
queueId=2400（海克斯大乱斗）不在里面，导致点卡兜底被跳过、真实测试「没自动选」。
当时的修法是加 2400。**但根因没修**：白名单是一个手写的、与游戏版本无关的
常量，游戏每加一个变体就再漏一次。LOL 16.21 就把海克斯大乱斗扩到了
2400/2401/2403/2405/2410/2450/3240/3270/3280 九个 id。

判据（取权威源，不取近似值）
--------------------------
权威事实 = 客户端自己的 `queues.json`。判据不是「名字里带某个字」这种猜测，而是
两条可核对的、来自同一份数据的结构特征：

    gameSelectModeGroup == 'kARAM'   且   pickMode == 'AllRandomPickStrategy'

`kARAM` = 这局是嚎哭深渊那条线（大乱斗家族）；`AllRandom` = 全随机发牌（所以
才有「2-3 张卡」给你点）。两者同时成立才是点卡坐标成立的前提。

**严格判据之外的 id 必须逐条给出理由**（见 EXTRA_ALLOWED）。为什么不能放宽判据：
客户端里另有一批 `kAlternativeLeagueGameModes + AllRandom` 的队列（3240/3280、
62~65 等）名字也叫大乱斗，但它们**没有实测证据**证明选人界面真是「2-3 张卡」布局。
把没验证过的队列塞进白名单，风险是**在别的选人界面上乱点** —— 比「不自动选」
严重得多。所以宁可不加，让探针把这类队列**报成待实测**，由有实机的人决定。

用法
----
    <python> tools/probe_aram_queue_coverage.py            # 用内置夹具
    <python> tools/probe_aram_queue_coverage.py <queues.json>   # 用活客户端全量快照

退出码 0 = 严格判据全覆盖、且无来历不明的 id；1 = 有问题（见输出）。

本探针**只读**：不联网、不改代码、不需要 LOL 在运行。
"""
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FIXTURE = os.path.join(_ROOT, 'tests', 'fixtures', 'aram_queues_16_21.json')

# 严格判据之外、但**有实测依据**保留在白名单里的 id。每条都要写清理由，
# 否则下一个人不知道它为什么在这儿，出问题也不敢删。
EXTRA_ALLOWED = {
    3270: 'v4.1.57 时期就在白名单里并长期使用；客户端 queues.json 里名字是'
          '「海克斯大乱斗」，但分组为 kAlternativeLeagueGameModes（与 2400 的 kARAM '
          '不同），所以严格判据抓不到它。保留：已在用、无报障。',
}


def load_queues(path):
    """读 queues.json（活客户端全量）或夹具（{queues:[...]}）。"""
    with io.open(path, encoding='utf-8-sig') as f:
        data = json.load(f)
    if isinstance(data, dict):
        return data.get('queues') or []
    return data or []


def authoritative_aram_ids(queues):
    """按客户端数据算出「应该」进白名单的 id 集合。

    这就是判据本身。改这里 = 改语义，必须同时改测试与注释。
    """
    out = set()
    for q in queues:
        try:
            if (q.get('gameSelectModeGroup') == 'kARAM'
                    and q.get('pickMode') == 'AllRandomPickStrategy'):
                qid = int(q.get('id') or 0)
                if qid > 0:
                    out.add(qid)
        except (TypeError, ValueError):
            continue
    return out


def declared_ids():
    """读当前代码里声明的白名单。"""
    from core.script_core import ARAM_QUEUE_IDS
    return set(int(x) for x in ARAM_QUEUE_IDS)


def main(argv):
    path = argv[1] if len(argv) > 1 else FIXTURE
    if not os.path.isfile(path):
        print('FAIL 找不到队列数据: %s' % path)
        return 1
    queues = load_queues(path)
    if not queues:
        print('FAIL 队列数据为空: %s' % path)
        return 1

    want = authoritative_aram_ids(queues)
    have = declared_ids()

    all_by_id = {}
    for q in queues:
        try:
            all_by_id[int(q.get('id') or 0)] = q
        except (TypeError, ValueError):
            pass

    # 白名单的**期望**值 = 严格判据 + 已备案的额外 id（后者必须仍在客户端里存在）
    want |= {k for k in EXTRA_ALLOWED if k in all_by_id}

    missing = sorted(want - have)
    extra = sorted(have - want)
    orphan_extra = sorted(k for k in EXTRA_ALLOWED if k not in all_by_id)

    print('数据源: %s' % path)
    print('队列条目: %d' % len(queues))
    print('应有(严格判据 %d + 备案 %d): %d 个'
          % (len(authoritative_aram_ids(queues)),
             len([k for k in EXTRA_ALLOWED if k in all_by_id]),
             len(want)))
    print('代码声明(ARAM_QUEUE_IDS): %d 个' % len(have))
    print('  声明: %s' % sorted(have))
    print('  应有: %s' % sorted(want))

    # 参考信息：结构像大乱斗、但没有实测证据的队列（**不进白名单**）
    suspects = sorted(
        int(q.get('id') or 0) for q in queues
        if q.get('pickMode') == 'AllRandomPickStrategy'
        and q.get('gameSelectModeGroup') != 'kARAM'
        and int(q.get('id') or 0) > 0
        and int(q.get('id') or 0) not in want)

    ok = True
    if missing:
        ok = False
        print('\n[漏] %d 个队列该进白名单却没进 —— 这些队列的点卡兜底会被跳过:'
              % len(missing))
        for qid in missing:
            q = all_by_id.get(qid) or {}
            print('    %-6s %s' % (qid, str(q.get('name') or '').strip()))
    if extra:
        ok = False
        print('\n[多] %d 个 id 在白名单里但既不符合判据、也没备案 —— 可能在别的界面误点:'
              % len(extra))
        for qid in extra:
            q = all_by_id.get(qid) or {}
            print('    %-6s %s' % (qid, str(q.get('name') or '').strip() or '(客户端查不到)'))
            print('            要么删掉它，要么在 EXTRA_ALLOWED 里写明保留理由。')
    if orphan_extra:
        ok = False
        print('\n[失效备案] EXTRA_ALLOWED 里 %d 个 id 客户端已无此队列: %s'
              % (len(orphan_extra), orphan_extra))
        print('            队列没了就把它从 EXTRA_ALLOWED 一起删掉。')
    if suspects:
        print('\n[待实测] %d 个队列形状像大乱斗、但分组不是 kARAM，故**未**加入白名单:'
              % len(suspects))
        for qid in suspects:
            q = all_by_id.get(qid) or {}
            print('    %-6s %s' % (qid, str(q.get('name') or '').strip()))

    if ok:
        print('\nPASS 白名单与客户端数据一致')
        return 0
    print('\nFAIL 见上')
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
