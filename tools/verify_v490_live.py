# -*- coding: utf-8 -*-
"""实机验证：用**真实 LOL 客户端**跑一遍 v4.9.0 的三处改动。

为什么需要它：单元测试都是假数据。这里连真客户端，验证
  ① 海克斯表 594 条能真读、3176 那条脏名字被清掉、其余名字没被误伤；
  ② ARAM 队列白名单能从真 queues.json 推导出来，且覆盖所有 kARAM+AllRandom；
  ③ 真实对局详情里每个海克斯 id 都能查到名字与稀有度。

只读：不点击、不改设置、不启动脚本。客户端没开就明确报"无法验证"，不假装通过。

用法：
    <python> tools/verify_v490_live.py
"""
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from lcu.connector import LCUConnector       # noqa: E402
from lcu.api import LCUAPI                   # noqa: E402
from core.script_core import ARAM_QUEUE_IDS  # noqa: E402


def main():
    c = LCUConnector()
    if not c.find_client(quick=False):
        print('SKIP 连不上 LOL 客户端 —— 无法实机验证（不假装通过）')
        return 2
    print('已连上客户端 port=%s' % c.port)
    api = LCUAPI(c)

    ok = True

    # ---------- ① 海克斯表 ----------
    print('\n=== ① 海克斯表 ===')
    ver = api.get_game_version()
    print('游戏版本: %s' % (ver or '(取不到)'))
    table = api._load_augment_table()
    print('条目数: %d' % len(table))
    if len(table) < 500:
        ok = False
        print('[FAIL] 条目数异常少，可能表没读到')

    # 「内部标识符形态」判据与 lcu.api._clean_augment_name 同语义：
    # 含下划线 且 全部是 ASCII 字母数字下划线。**不能只判 isascii** ——
    # 官方英文名（ADAPt / BONK!）也是 ASCII，那样会误报一片。
    def _looks_internal(name):
        s = (name or '').strip()
        if not s or '_' not in s:
            return False
        return all(ch.isalnum() or ch == '_' for ch in s) and s.isascii()

    dirty = [(aid, v[0]) for aid, v in table.items() if _looks_internal(v[0])]
    if dirty:
        ok = False
        print('[FAIL] 仍有内部标识符形态的名字: %s' % dirty[:5])
    else:
        print('无内部标识符形态的名字')

    # 3176 这条：名字必须被清空，且图标/稀有度不受影响
    v = table.get(3176)
    if v is None:
        print('注: 3176 不在表里（版本差异，可接受）')
    else:
        print('3176 名字=%r 稀有度=%s 图标有=%s'
              % (v[0], v[2], bool(v[1])))
        if v[0]:
            ok = False
            print('[FAIL] 3176 的未本地化名字没被清掉')
        if not v[2] or not v[1]:
            ok = False
            print('[FAIL] 清洗把稀有度/图标也弄丢了')

    # 反面约束：官方英文名不许被误伤
    legit = [aid for aid, vv in table.items()
             if (vv[0] or '') in ('物理转魔法', '面包和奶酪')]
    print('抽样中文名保留: %d 条' % len(legit))
    if not legit:
        ok = False
        print('[FAIL] 中文名被误清了')

    # ---------- ② 队列白名单 ----------
    print('\n=== ② ARAM 队列白名单 ===')
    raw = api.get('/lol-game-data/assets/v1/queues.json') or []
    want = {int(q['id']) for q in raw
            if q.get('gameSelectModeGroup') == 'kARAM'
            and q.get('pickMode') == 'AllRandomPickStrategy'
            and int(q.get('id') or 0) > 0}
    got = api.aram_queue_ids(refresh=True)
    print('客户端应有(kARAM+AllRandom): %s' % sorted(want))
    print('程序实际启用:                %s' % sorted(got))
    miss = want - got
    if miss:
        ok = False
        print('[FAIL] 漏了: %s' % sorted(miss))
    else:
        print('覆盖完整')
    sr = {int(q['id']) for q in raw
          if q.get('pickMode') == 'AllRandomPickStrategy'
          and q.get('gameSelectModeGroup') != 'kARAM'
          and int(q.get('id') or 0) > 0}
    # 判定"误纳"时必须扣掉 EXTRA_ALLOWED 里**已备案**的 id —— 3270 就是这种：
    # 客户端分组是 kAlternativeLeagueGameModes，但它在白名单里已用很久且无报障。
    # 第一版验证脚本忘了这层，把 3270 报成"误纳 → 可能乱点"，属于**假警报**。
    from tools.probe_aram_queue_coverage import EXTRA_ALLOWED
    bad = (sr - set(EXTRA_ALLOWED)) & got
    if bad:
        ok = False
        print('[FAIL] 误纳了未备案的非 kARAM 队列（可能乱点）: %s' % sorted(bad))
    else:
        print('未误纳非 kARAM 全随机队列（排除 %d 个，其中 %d 个已备案保留）'
              % (len(sr), len(sr & set(EXTRA_ALLOWED))))

    # ---------- ③ 真实战绩里的海克斯 ----------
    print('\n=== ③ 真实对局里的海克斯 ===')
    me = api.get('/lol-summoner/v1/current-summoner') or {}
    puuid = me.get('puuid') or ''
    if not puuid:
        print('取不到 puuid，跳过')
    else:
        m = api.get('/lol-match-history/v1/products/lol/%s/matches'
                    '?begIndex=0&endIndex=20' % puuid) or {}
        games = ((m.get('games') or {}).get('games') or [])
        print('取到 %d 局' % len(games))
        ids = set()
        for g in games:
            for p in (g.get('participants') or []):
                st = p.get('stats') or {}
                for i in range(1, 7):
                    v2 = st.get('playerAugment%d' % i)
                    if v2:
                        ids.add(int(v2))
        print('涉及海克斯 id: %d 个' % len(ids))
        no_name = [i for i in ids if not api.augment_info(i)[0]]
        no_rar = [i for i in ids if not api.augment_rarity(i)]
        print('查不到名字: %s' % (no_name or '无'))
        print('查不到稀有度: %s' % (no_rar or '无'))
        # 3176 若出现在战绩里，名字为空是**预期**（我们主动清的）
        if no_name:
            ok = False
            print('[FAIL] 有海克斯查不到名字')
        if no_rar:
            ok = False
            print('[FAIL] 有海克斯查不到稀有度')

    print()
    if ok:
        print('PASS 实机验证全部通过')
        return 0
    print('FAIL 见上')
    return 1


if __name__ == '__main__':
    sys.exit(main())
