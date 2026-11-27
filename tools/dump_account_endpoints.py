"""LCU 端点实测:wallet / mastery / matches 的真实 JSON 顶层结构。

输出三段:
1. wallet 全部顶层 key 及值类型
2. mastery 第一项的所有 key(如果不是 list 就打印类型)
3. matches 第一局的 participant 字段名(用于定位 win 字段)

用法(不要双击 .py!命令行执行):
    cd <项目根目录, 即 dadan-build>
    python tools\\dump_account_endpoints.py
按任意键退出。
"""
import sys
import json
import os

# 沿用主程序的代理清理
for _k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
           'all_proxy', 'ALL_PROXY'):
    os.environ.pop(_k, None)
os.environ['no_proxy'] = '*'
os.environ['NO_PROXY'] = '*'


def main():
    print('=' * 70)
    print('LCU 端点实测 - wallet / mastery / matches')
    print('=' * 70)
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from lcu.connector import LCUConnector
    from lcu.api import LCUAPI

    conn = LCUConnector()
    # 自动连接(轮询直到拿到为止)
    import time as _t
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

    # 1) wallet
    print('\n' + '=' * 70)
    print('[1] /lol-store/v1/wallet')
    print('=' * 70)
    try:
        w = api.get_wallet()
        if isinstance(w, dict):
            for k, v in w.items():
                t = type(v).__name__
                print(f'  {k!r:30} = {v!r:20}  ({t})')
        else:
            print(f'  返回类型: {type(w).__name__}')
            print(f'  内容: {w!r}')
    except Exception as e:
        print(f'  [异常] {type(e).__name__}: {e}')

    # 2) mastery
    print('\n' + '=' * 70)
    print('[2] /lol-collections/v1/inventories/{summonerId}/champion-mastery')
    print('=' * 70)
    try:
        s = api.get_current_summoner()
        sid = int(s.get('summonerId') or 0)
        puuid = (s.get('puuid') or '').strip()
        print(f'  summonerId = {sid}')
        print(f'  puuid      = {puuid[:16]}...{puuid[-8:] if puuid else ""}')
        m = api.get_champion_mastery(sid)
        print(f'  返回类型: {type(m).__name__}, 长度: {len(m) if hasattr(m, "__len__") else "N/A"}')
        if isinstance(m, list) and m:
            print(f'  第一项所有 key: {list(m[0].keys())}')
            print(f'  第一项内容: {m[0]}')
            print(f'  按 points 降序前 3 项:')
            sorted_m = sorted(m, key=lambda x: int(x.get('championPoints') or 0), reverse=True)
            for i, mm in enumerate(sorted_m[:3]):
                print(f'    [{i}] championId={mm.get("championId")}, '
                      f'level={mm.get("championLevel")}, '
                      f'points={mm.get("championPoints")}')
        elif isinstance(m, dict):
            print(f'  dict keys: {list(m.keys())[:20]}')
    except Exception as e:
        print(f'  [异常] {type(e).__name__}: {e}')

    # 3) matches
    print('\n' + '=' * 70)
    print('[3] /lol-match-history/v1/products/lol/{puuid}/matches')
    print('=' * 70)
    try:
        s = api.get_current_summoner()
        puuid = (s.get('puuid') or '').strip()
        ms = api.get_recent_matches(puuid, 5)
        if isinstance(ms, dict):
            print(f'  顶层 keys: {list(ms.keys())}')
            games = (ms.get('games') or {}).get('games') or []
            print(f'  games 长度: {len(games)}')
            if games:
                g0 = games[0]
                print(f'  games[0] keys: {list(g0.keys())}')
                parts = g0.get('participants') or []
                if parts:
                    p0 = parts[0]
                    print(f'  participants[0] keys: {list(p0.keys())}')
                    print(f'  participants[0].stats keys: {list((p0.get("stats") or {}).keys())[:15]}')
                    # 解析我方 stats
                    for pi in g0.get('participantIdentities') or []:
                        pl = pi.get('player') or {}
                        if pl.get('puuid') == puuid:
                            pid = pi.get('participantId')
                            for part in parts:
                                if part.get('participantId') == pid:
                                    print(f'  我的 stats.win = {part.get("stats", {}).get("win")}')
                                    break
                            break
        elif isinstance(ms, list):
            print(f'  返回 list, 长度: {len(ms)}')
    except Exception as e:
        print(f'  [异常] {type(e).__name__}: {e}')

    # 4) 探查腾讯 LCU 的 wallet 真实路径
    print('\n' + '=' * 70)
    print('[4] 探查 wallet 端点(腾讯 LCU)')
    print('=' * 70)
    wallet_candidates = [
        '/lol-store/v1/wallet',
        '/lol-inventory/v1/wallet',
        '/lol-store/v1/currencies',
        '/lol-store/v2/wallet',
        '/lol-store/v3/wallet',
        '/lol-catalog/v1/wallet',
        '/lol-loot/v1/player-loot',
    ]
    for ep in wallet_candidates:
        try:
            r = api.get(ep)
            print(f'  [200] {ep}')
            if isinstance(r, dict):
                print(f'        keys: {list(r.keys())[:8]}')
            elif isinstance(r, list):
                print(f'        list len={len(r)}, head: {r[:1] if r else "[]"}')
        except Exception as e:
            print(f'  [x]   {ep}  -> {type(e).__name__}: {str(e)[:60]}')

    # 5) 探查 mastery 端点
    print('\n' + '=' * 70)
    print('[5] 探查 mastery 端点(腾讯 LCU)')
    print('=' * 70)
    s = api.get_current_summoner()
    sid = int(s.get('summonerId') or 0)
    puuid = (s.get('puuid') or '').strip()
    mastery_candidates = [
        f'/lol-collections/v1/inventories/{sid}/champion-mastery',
        f'/lol-collections/v1/inventories/{sid}/champions',
        f'/lol-champion-mastery/v1/{puuid}/champion-mastery',
        f'/lol-collections/v3/inventories/{sid}/champion-mastery',
        f'/lol-collections/v1/inventories/{puuid}/champion-mastery',
        '/lol-champion-mastery/v1/local-player/champion-mastery',
        f'/lol-collections/v2/inventories/{sid}/champion-mastery',
    ]
    for ep in mastery_candidates:
        try:
            r = api.get(ep)
            tname = type(r).__name__
            ln = len(r) if hasattr(r, '__len__') else 'N/A'
            print(f'  [200] {ep}')
            print(f'        type={tname}, len={ln}')
            if isinstance(r, list) and r:
                print(f'        first keys: {list(r[0].keys())[:10]}')
                # 找出 championPoints 最高的几个
                sp = sorted(r, key=lambda x: int(x.get('championPoints') or 0), reverse=True)
                for i, mm in enumerate(sp[:3]):
                    print(f'        [{i}] cid={mm.get("championId")} '
                          f'lvl={mm.get("championLevel")} '
                          f'pts={mm.get("championPoints")}')
            elif isinstance(r, dict):
                print(f'        keys: {list(r.keys())[:10]}')
        except Exception as e:
            print(f'  [x]   {ep}  -> {type(e).__name__}: {str(e)[:60]}')

    # 6) mastery 字段语义：championLevel=72 是里程碑整数(非1-7英雄等级)
    print('\n' + '=' * 70)
    print('[6] mastery 字段语义')
    print('=' * 70)
    try:
        s = api.get_current_summoner()
        puuid = (s.get('puuid') or '').strip()
        m = api.get(f'/lol-champion-mastery/v1/{puuid}/champion-mastery')
        if isinstance(m, list) and m:
            sp = sorted(m, key=lambda x: int(x.get('championPoints') or 0), reverse=True)
            for i, mm in enumerate(sp[:3]):
                print(f'  [{i}] cid={mm.get("championId")}')
                print(f'      championLevel        = {mm.get("championLevel")}')
                print(f'      championSeasonMilestone = {mm.get("championSeasonMilestone")}')
                print(f'      highestGrade         = {mm.get("highestGrade")}')
                print(f'      championPoints       = {mm.get("championPoints")}')
                print()
    except Exception as e:
        print(f'  [异常] {e}')

    # 7) wallet 在 /lol-loot/v1/player-loot 里:CURRENCY 类型
    print('\n' + '=' * 70)
    print('[7] player-loot 里的 CURRENCY 类型')
    print('=' * 70)
    try:
        loot = api.get('/lol-loot/v1/player-loot') or []
        if isinstance(loot, list):
            for item in loot:
                t = item.get('type', '')
                if t == 'CURRENCY':
                    print(f'  lootName = {item.get("lootName"):40}  '
                          f'value = {item.get("value")}')
    except Exception as e:
        print(f'  [异常] {e}')

    # 8) 更广泛地探索腾讯 LOL 钱包端点(去重复已知 404)
    print('\n' + '=' * 70)
    print('[8] wallet 端点广泛探索')
    print('=' * 70)
    candidates2 = [
        '/lol-store/v1/wallet-with-cache',
        '/lol-store/v1/wallet/currencies',
        '/lol-store/v1/me',
        '/lol-store/v1/accounts/me',
        '/lol-store/v1/accounts/me/wallet',
        '/lol-payments/v1/wallet',
        '/lol-platform/v1/wallet',
        '/lol-account/v1/wallet',
        '/lol-account/v1/me',
        '/lol-summoner/v1/wallet',
        '/lol-summoner/v1/me',
        '/lol-currency/v1/wallet',
        '/lol-missions/v1/data',  # 已知是任务
        '/lol-inventory/v1/inventory',
        '/lol-loot/v1/currency',
        '/lol-loot/v1/currencies',
        '/lol-loot/v1/player-currency',
    ]
    for ep in candidates2:
        try:
            r = api.get(ep)
            print(f'  [200] {ep}')
            if isinstance(r, dict):
                ks = list(r.keys())[:8]
                print(f'        keys: {ks}')
                # 找含 ip/rp 字段的
                for k in r:
                    if any(s in k.lower() for s in ('ip', 'rp', 'point', 'currency', 'essence', 'coin')):
                        print(f'        -> {k!r} = {r[k]!r}')
            elif isinstance(r, list):
                print(f'        list len={len(r)}')
        except Exception as e:
            msg = str(e)[:55]
            print(f'  [x]   {ep:48}  {type(e).__name__}: {msg}')

    # 9) player-loot 完整 currency dump
    print('\n' + '=' * 70)
    print('[9] player-loot CURRENCY 完整内容')
    print('=' * 70)
    try:
        loot = api.get('/lol-loot/v1/player-loot') or []
        if isinstance(loot, list):
            for item in loot:
                if item.get('type') == 'CURRENCY':
                    print(f'  lootName = {item.get("lootName")}')
                    print(f'    value = {item.get("value")}')
                    print(f'    count = {item.get("count")}')
                    print(f'    itemDesc = {item.get("itemDesc")}')
                    print()
    except Exception as e:
        print(f'  [异常] {e}')

    print('\n' + '=' * 70)
    print('完成。')
    print('=' * 70)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f'\n[顶层异常] {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
    finally:
        try:
            input('\n按回车退出...')
        except EOFError:
            pass