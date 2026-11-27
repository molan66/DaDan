"""一次性诊断工具：定位"本局屏幕那 2-3 张候选卡"在 LCU 哪个端点。

不需要打包，直接在 champ-select 阶段跑:
    python tools/diag_pickable.py

结果会同时显示在窗口 + 写入 diag_out.txt(脚本同目录, utf-8)。
跑完把 diag_out.txt 发给 AI 即可。
"""
import os
import sys
import json
import io

# 复用项目里的 LCU connector
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lcu.connector import LCUConnector
from lcu.api import LCUAPI


# ---- tee: print 同时输出到窗口和文件(diag_out.txt, utf-8) ----
from _tee import _Tee


_OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'diag_out.txt')


def _start_tee():
    try:
        _f = open(_OUT_PATH, 'w', encoding='utf-8')
        sys.stdout = _Tee(sys.__stdout__, _f)
    except Exception:
        pass


def short(x, n=8):
    if x is None:
        return 'None'
    if isinstance(x, list):
        return f'[{len(x)}]={x[:n]}{"..." if len(x) > n else ""}'
    if isinstance(x, dict):
        return f'{{{len(x)} keys}}'
    return str(x)[:80]


def main():
    _start_tee()
    print(f'诊断结果文件: {_OUT_PATH}')
    c = LCUConnector()
    if not c.find_client():   # 自动找 LOL 客户端(进程/lockfile/日志,完整方法链)
        print('❌ 找不到 LOL 客户端,先启动客户端再跑')
        return 1
    api = LCUAPI(c)
    print('✅ 已连接到客户端,开始诊断 champ-select 端点\n')
    print(f'{"=" * 60}')

    # 0. session 是最全的,先看
    print('\n[0] GET /lol-champ-select/v1/session (基础结构)')
    s = api.get('/lol-champ-select/v1/session')
    if s:
        print(f'  session keys = {list(s.keys())}')
        print(f'  isCustomGame = {s.get("isCustomGame")}')
        print(f'  allowSubset  = {s.get("allowSubsetChampionPicks")}')
        print(f'  queueId      = {s.get("queueId")}')
        print(f'  bans.myTeamBans     = {short(s.get("bans", {}).get("myTeamBans", []))}')
        print(f'  bans.theirTeamBans  = {short(s.get("bans", {}).get("theirTeamBans", []))}')
        bans_all = []
        for b in (s.get('bans', {}).get('myTeamBans', []) or []):
            bans_all.append(b.get('championId'))
        for b in (s.get('bans', {}).get('theirTeamBans', []) or []):
            bans_all.append(b.get('championId'))
        print(f'  所有 bans       = {bans_all}')
        # myTeam 详细
        print(f'  myTeam[0] keys  = {list(s["myTeam"][0].keys()) if s.get("myTeam") else "空"}')
        me = next((p for p in s.get('myTeam', [])
                   if p.get('cellId') == s.get('localPlayerCellId')), None)
        if me:
            print(f'  我(我自己的 myTeam 项) = {me}')

    # 1. 试各种可能端点
    candidates = [
        ('/lol-champ-select/v1/pickable-champion-ids',     'pickable 端点(v29 用过,200+ 错)'),
        ('/lol-champ-select/v1/bannable-champion-ids',     'bannable 端点'),
        ('/lol-champ-select/v1/grid-champions',            'grid 端点'),
        ('/lol-champ-select/v1/disabled-champion-ids',     'disabled 端点'),
        ('/lol-champ-select/v1/ongoing-pickable-champion-ids', 'ongoing-pickable 端点'),
        ('/lol-champ-select/v1/team-boost/pickable-champions', 'team-boost 端点'),
        ('/lol-champ-select/v1/pickable-skins',            'pickable-skins 端点'),
        ('/lol-champ-select/v1/skin-carousel',             'skin-carousel 端点'),
    ]
    for ep, desc in candidates:
        try:
            r = api._req('GET', ep)
            data = r.json() if r.status_code == 200 else None
            print(f'\n[{ep}]')
            print(f'  状态={r.status_code}, 描述={desc}')
            if data is None:
                print(f'  响应: 空/204')
            elif isinstance(data, list):
                print(f'  长度: {len(data)}, 前几项: {data[:10]}')
            elif isinstance(data, dict):
                print(f'  keys: {list(data.keys())}')
                for k, v in list(data.items())[:6]:
                    print(f'    {k} = {short(v, 4)}')
        except Exception as e:
            print(f'\n[{ep}] 异常: {type(e).__name__}: {str(e)[:60]}')

    # 2. 计算: pickable - bans 是不是子集?
    print(f'\n{"=" * 60}')
    print('[计算] pickable - bans 看是不是本局子集')
    try:
        pick = api.get('/lol-champ-select/v1/pickable-champion-ids') or []
        if s:
            ba = []
            for b in (s.get('bans', {}).get('myTeamBans', []) or []):
                ba.append(b.get('championId'))
            for b in (s.get('bans', {}).get('theirTeamBans', []) or []):
                ba.append(b.get('championId'))
            sub = [c for c in pick if c not in ba]
            print(f'  pickable 数量: {len(pick)}')
            print(f'  bans 数量:    {len(ba)}')
            print(f'  pickable - bans = {len(sub)} 个')
            print(f'  是否包含 #6 影流之主: {6 in sub}')
            print(f'  是否包含 #53 酒桶:   {53 in sub}')
    except Exception as e:
        print(f'  失败: {e}')

    # 3. 客户端 LCU 公开 schema 里有提到 "currentChampion" 之类的
    # 试一下 current-champion
    try:
        r = api._req('GET', '/lol-champ-select/v1/current-champion')
        print(f'\n[/lol-champ-select/v1/current-champion] 状态={r.status_code} 响应={r.text[:200]}')
    except Exception as e:
        print(f'\n[/lol-champ-select/v1/current-champion] 异常: {e}')

    # 4. 试玩家自己的 "champion-pick-intent"
    try:
        if s and me:
            puuid = me.get('puuid')
            r = api._req('GET', f'/lol-champ-select/v1/summoners/{puuid}/champion-pick-intent')
            print(f'\n[/lol-champ-select/v1/summoners/{{me}}/champion-pick-intent]')
            print(f'  状态={r.status_code} 响应={r.text[:200]}')
    except Exception as e:
        print(f'\n[champion-pick-intent] 异常: {e}')

    print(f'\n{"=" * 60}')
    print('诊断完成, 把上面全部输出复制给我')

    return 0


if __name__ == '__main__':
    sys.exit(main())
