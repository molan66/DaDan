# -*- coding: utf-8 -*-
"""从活客户端重新生成「队列白名单」测试夹具。

为什么要有这个脚本：第一版夹具是我手工按名字过滤（"海克斯大乱斗|极地大乱斗"）
抓出来的，结果**漏了 451/452/720/721** —— 它们名字里没有这些词，但确实是
`kARAM + AllRandomPickStrategy`（3v3 ARAM / 1v1 ARAM / Clash ARAM）。
夹具漏了不算致命，但它让"全覆盖"这条测试**假通过**：测试只验证了夹具里有的东西。
所以夹具必须按**判据**抓，不能按名字抓。

夹具内容 = 判据会看到的全部队列：
  · 所有 kARAM 队列（判据正向）
  · 所有 AllRandomPickStrategy 队列（判据负向：确认没误纳 SR 全随机/无限乱斗）
  · EXTRA_ALLOWED 里备案的 id（确认它们还在客户端里）

用法（需要 LOL 客户端在运行）：
    <python> tools/refresh_queue_fixture.py
"""
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

OUT = os.path.join(_ROOT, 'tests', 'fixtures', 'aram_queues_16_21.json')


def main():
    from lcu.connector import LCUConnector
    from lcu.api import LCUAPI
    from tools.probe_aram_queue_coverage import EXTRA_ALLOWED

    c = LCUConnector()
    if not c.find_client(quick=False):
        print('SKIP 连不上客户端 —— 无法刷新夹具')
        return 2
    api = LCUAPI(c)
    ver = api.get_game_version()
    raw = api.get('/lol-game-data/assets/v1/queues.json') or []
    if not raw:
        print('FAIL 队列表为空')
        return 1

    keep = []
    for q in raw:
        try:
            qid = int(q.get('id') or 0)
        except (TypeError, ValueError):
            continue
        if q.get('gameSelectModeGroup') == 'kARAM' \
                or q.get('pickMode') == 'AllRandomPickStrategy' \
                or qid in EXTRA_ALLOWED:
            keep.append({
                'id': qid,
                'name': str(q.get('name') or '').strip(),
                'pickMode': q.get('pickMode') or '',
                'gameSelectModeGroup': q.get('gameSelectModeGroup') or '',
                'gameSelectCategory': q.get('gameSelectCategory') or '',
                'roster_size': len(q.get('viableChampionRoster') or []),
            })
    keep.sort(key=lambda d: d['id'])

    out = {
        '_note': ('大蛋小助手测试夹具：LOL %s 国服 queues.json 的「白名单判据相关切片」。'
                  '含所有 kARAM 队列、所有 AllRandomPickStrategy 队列、以及 '
                  'EXTRA_ALLOWED 备案的 id。**按判据抓，不按名字抓** —— '
                  '第一版按名字抓漏了 451/452/720/721，导致全覆盖测试假通过。'
                  '勿手改；游戏更新后用 tools/refresh_queue_fixture.py 重生成。'
                  % (ver or '?')),
        '_source': 'LCU /lol-game-data/assets/v1/queues.json  (build %s)' % (
            api.get('/lol-patch/v1/game-version') or ''),
        '_captured_at': '2026-10-22',
        'queues': keep,
    }
    with io.open(OUT, 'w', encoding='utf-8', newline='') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    pos = [d['id'] for d in keep
           if d['gameSelectModeGroup'] == 'kARAM'
           and d['pickMode'] == 'AllRandomPickStrategy']
    neg = [d['id'] for d in keep
           if d['gameSelectModeGroup'] != 'kARAM'
           and d['pickMode'] == 'AllRandomPickStrategy']
    print('已写入 %s' % OUT)
    print('切片条目: %d' % len(keep))
    print('正向(kARAM+AllRandom, %d): %s' % (len(pos), sorted(pos)))
    print('负向(非 kARAM 但 AllRandom, %d): %s' % (len(neg), sorted(neg)))
    print('备案 EXTRA_ALLOWED: %s' % sorted(EXTRA_ALLOWED))
    return 0


if __name__ == '__main__':
    sys.exit(main())
