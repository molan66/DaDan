"""LCU 未接入端点可用性测试 + 数据结构展示。

目的: 找出哪些官方端点能用于扩展账号卡(玩家标签/赛季胜场/拥有英雄数/符文等),
用 input() 兜底防止闪退, 用户跑完看输出决定接入哪些。

用法(命令行, 不要双击 .py!):
    cd <项目根目录, 即 dadan-build>
    python tools\\dump_unused_endpoints.py
按任意键退出。
"""
import sys
import os

# 沿用主程序的代理清理(LCU 走 127.0.0.1, 绝不走系统代理)
for _k in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY',
           'all_proxy', 'ALL_PROXY'):
    os.environ.pop(_k, None)
os.environ['no_proxy'] = '*'
os.environ['NO_PROXY'] = '*'


def _trunc(s, n=200):
    """长字符串截断, 避免输出爆炸."""
    s = str(s)
    if len(s) <= n:
        return s
    return s[:n] + f'... (truncated, total {len(s)} chars)'


def _try(api, ep, label):
    """测试一个端点, 打印状态码 + 顶层结构. 返回 (ok, body)."""
    print(f'\n[{label}] GET {ep}')
    try:
        r = api._req('GET', ep, timeout=(2.0, 5.0))
        sc = r.status_code
        print(f'  状态 = {sc}')
        if 200 <= sc < 300:
            try:
                data = r.json()
            except Exception as je:
                return True, f'<json 解压失败: {type(je).__name__}>'
            if isinstance(data, dict):
                print(f'  顶层 keys ({len(data)}): {list(data.keys())}')
                for k in list(data.keys())[:12]:
                    v = data[k]
                    t = type(v).__name__
                    if isinstance(v, (str, int, float, bool)):
                        print(f'    {k!r:35} = {_trunc(v, 60)!r:30}  ({t})')
                    elif isinstance(v, list):
                        print(f'    {k!r:35} = list[{len(v)}]  (前 1 项: {type(v[0]).__name__ if v else "?"})')
                    elif isinstance(v, dict):
                        print(f'    {k!r:35} = dict({len(v)} keys): {list(v.keys())[:6]}')
                    else:
                        print(f'    {k!r:35} = {t}')
                if len(data) > 12:
                    print(f'  ... 还有 {len(data) - 12} 个顶层 key')
                return True, data
            elif isinstance(data, list):
                print(f'  list[{len(data)}]')
                if data:
                    print(f'  第一项类型: {type(data[0]).__name__}')
                    if isinstance(data[0], dict):
                        print(f'  第一项 keys: {list(data[0].keys())[:12]}')
                return True, data
            else:
                print(f'  返回值: {_trunc(data, 60)!r}')
                return True, data
        else:
            try:
                print(f'  body: {_trunc(r.text, 200)}')
            except Exception:
                pass
            return False, None
    except Exception as e:
        print(f'  [异常] {type(e).__name__}: {_trunc(e, 120)}')
        return False, None


def main():
    print('=' * 70)
    print('LCU 未接入端点可用性 + 数据结构探测')
    print('=' * 70)
    import time as _t
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from lcu.connector import LCUConnector
    from lcu.api import LCUAPI

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

    s = api.get_current_summoner() or {}
    sid = int(s.get('summonerId') or 0)
    puuid = (s.get('puuid') or '').strip()
    print(f'\n  summonerId = {sid}')
    print(f'  puuid      = {puuid[:16]}...{puuid[-8:] if puuid else ""}')

    # === 1. 玩家标签(在 get_current_summoner 里, 我们没提取) ===
    print('\n' + '=' * 70)
    print('[1] 玩家标签(在 get_current_summoner 字段里, 账号卡未展示)')
    print('=' * 70)
    for k in ('hotStreak', 'veteran', 'inactive', 'freshBlood', 'leagueTier',
              'rankedLeagueDivision', 'rankedLeagueQueue', 'rankedLosses', 'rankedWins'):
        if k in s:
            print(f'  {k!r:30} = {s[k]!r}')

    # === 2. 赛季胜场(在 get_current_ranked_stats 里, 账号卡未展示) ===
    print('\n' + '=' * 70)
    print('[2] 赛季胜场(在 queueMap 里, 账号卡未展示)')
    print('=' * 70)
    try:
        rs = api.get_current_ranked_stats() or {}
        qm = rs.get('queueMap') or {}
        for q, info in qm.items():
            print(f'  {q}:')
            for k, v in (info or {}).items():
                print(f'    {k!r:25} = {v!r}')
    except Exception as e:
        print(f'  [异常] {type(e).__name__}: {e}')

    # === 3. 拥有英雄数 ===
    _try(api, '/lol-champions/v1/owned-champions-minimal', '3 拥有英雄数(简版)')

    # === 4. 符文 ===
    _try(api, '/lol-perks/v1/current-page', '4 当前符文页')
    _try(api, '/lol-perks/v1/perks/loadout', '4b 符文装载')

    # === 5. 单场对局详情(取最近一场 gameId) ===
    print('\n' + '=' * 70)
    print('[5] 单场对局详情(每场 KDA, 现有战绩行可扩展)')
    print('=' * 70)
    try:
        matches = api.get_recent_matches(puuid, 5) or {}
        games = (matches.get('games') or {}).get('games') or []
        if games:
            g0 = games[0]
            gid = g0.get('gameId')
            print(f'  最近一场 gameId = {gid}')
            for k, v in g0.items():
                if isinstance(v, (str, int, float, bool)):
                    print(f'    {k!r:25} = {v!r}')
                elif isinstance(v, list):
                    print(f'    {k!r:25} = list[{len(v)}]')
                else:
                    print(f'    {k!r:25} = {type(v).__name__}')
            if gid:
                _try(api, f'/lol-match-history/v1/products/lol/{puuid}/games/{gid}',
                     '5b 单场详细(含 participants.stats 全字段)')
        else:
            print('  [无] games.games 为空')
    except Exception as e:
        print(f'  [异常] {type(e).__name__}: {e}')

    # === 6. 收藏品总览 ===
    _try(api, f'/lol-collections/v1/inventories/{puuid}/champion',
         '6 收藏品(英雄)')
    _try(api, f'/lol-collections/v1/inventories/{puuid}/skin',
         '6b 收藏品(皮肤)')

    # === 7. 战旗/英雄皮肤卡 ===
    _try(api, '/lol-loot/v1/player-loot', '7 战旗/物品(已用, 验证 fields)')

    # === 8. 好友 ===
    _try(api, '/lol-summoner/v1/summoner-friends', '8 好友列表')

    print('\n' + '=' * 70)
    print('探测结束')
    print('=' * 70)
    input('按回车退出...')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        import traceback
        traceback.print_exc()
        input('按回车退出...')
