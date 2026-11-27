"""专门 dump mastery 端点的真实字段,与游戏"英雄成就"页面对照。

游戏截图 Top3 显示:
  - 迅捷斥候 (#17) 37级英雄成就,最高评分 S+
  - 堕落天使 (#25) 72级英雄成就,最高评分 S+
  - 虚空先知 (#90) 16级英雄成就,最高评分 S+

目的: 找出 LCU mastery JSON 中哪个字段 = 截图的"X 级英雄成就"
      以及哪个字段 = 截图中"最高评分 S+"

注意: championLevel 之前我以为是"累计里程碑整数",需要重审。
       字段名可能因腾讯服而不同(grade/highestGrade/level 等)
"""
import os
import sys
import json
import time as _t

sys.path.insert(0, '.')
from lcu.connector import LCUConnector
from lcu.api import LCUAPI


def main():
    print('=' * 70)
    print('mastery 端点字段逐项 dump(对照游戏英雄成就页面)')
    print('=' * 70)
    conn = LCUConnector()
    print('[*] 等待 LCU 客户端连接...')
    for i in range(10):
        if conn.find_client():
            print(f'[+] 已连接: {conn.base_url}')
            break
        _t.sleep(1)
    else:
        print('[!] 找不到 LCU 客户端。请先打开 LOL 客户端并登录。')
        input('按回车退出...')
        return 1
    api = LCUAPI(conn)
    summoner = api.get_current_summoner() or {}
    puuid = summoner.get('puuid', '')
    sid = summoner.get('summonerId', '')
    print(f'[+] summonerId={sid} puuid={puuid[:24]}...')
    if not puuid:
        print('[!] 拿不到 puuid')
        input('按回车退出...')
        return 1

    data = api.get(
        f'/lol-champion-mastery/v1/{puuid}/champion-mastery') or []
    print(f'\n[+] mastery 总条数: {len(data)}')

    # 找 cid 17/25/90 (用户截图 Top3)
    target_cids = {17: '迅捷斥候(Teemo)', 25: '堕落天使(Morgana)',
                   90: '虚空先知(Malzahar)'}
    found = {}
    for m in data:
        cid = int(m.get('championId') or 0)
        if cid in target_cids:
            found[cid] = m

    print('\n[+] 目标三英雄 (cid 17/25/90) 完整 JSON:')
    for cid in (17, 25, 90):
        if cid not in found:
            print(f'  cid={cid} ({target_cids[cid]}): 不在列表中!')
            continue
        m = found[cid]
        print(f'\n--- cid={cid} {target_cids[cid]} ---')
        # 列出所有 key 和值的简短描述
        for k, v in m.items():
            vstr = json.dumps(v, ensure_ascii=False)
            if len(vstr) > 100:
                vstr = vstr[:97] + '...'
            print(f'  {k:30s} = {vstr}')

    # 横向排序对比
    print('\n' + '=' * 70)
    print('排序对比: 分别按 championLevel / championPoints / championSeasonMilestone')
    print('降序取 Top5,看与游戏截图 Top3 (cid 17/25/90) 是否吻合')
    print('=' * 70)
    for sort_key in ('championLevel', 'championPoints',
                     'championSeasonMilestone'):
        print(f'\n[按 {sort_key} 降序 Top5]')
        sorted_data = sorted(data, key=lambda x: int(
            x.get(sort_key) or 0), reverse=True)
        for i, m in enumerate(sorted_data[:5]):
            cid = int(m.get('championId') or 0)
            val = int(m.get(sort_key) or 0)
            tag = '  <-- 截图 Top3' if cid in target_cids else ''
            print(f'  #{i+1} cid={cid:>4d} {sort_key}={val}{tag}')

    print('\n' + '=' * 70)
    print('完成。')
    print('=' * 70)
    try:
        input('按回车退出...')
    except EOFError:
        pass
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        print(f'\n[异常] {type(e).__name__}: {e}')
        traceback.print_exc()
        try:
            input('按回车退出...')
        except EOFError:
            pass
        sys.exit(1)