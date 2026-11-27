# -*- coding: utf-8 -*-
"""v4.1.41 段位最高差异诊断: 打印 ranked stats 完整 JSON。

用法:
  1. 先打开 LOL 客户端到主界面(主界面状态,不需要进选人)
  2. cmd / 终端: cd dadan-build && python tools/dump_ranked.py
  3. 复制输出贴回对话 → 我能看到 highestTier 实际值, 判断是 LCU 限制还是 bug

输出三块:
  - 完整 ranked-stats JSON
  - 关键字段(只看非空)
  - 与脚本 _highest 函数逻辑对照, 看它会选哪一对
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 强制 UTF-8 输出(Windows cmd 默认 GBK 会乱码)
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

from lcu.connector import LCUConnector


def _tier_value(tier, division):
    """复制脚本里 _TIER_CN 段的段位数值化, 用于诊断输出比对"""
    t_rank = {'IRON': 0, 'BRONZE': 1, 'SILVER': 2, 'GOLD': 3,
              'PLATINUM': 4, 'EMERALD': 5, 'DIAMOND': 6,
              'MASTER': 7, 'GRANDMASTER': 8, 'CHALLENGER': 9}
    d_rank = {'IV': 0, 'III': 1, 'II': 2, 'I': 3}
    t = t_rank.get((tier or '').upper(), -1)
    d = d_rank.get((division or '').upper(), -1)
    if t < 0 or d < 0:
        return -1
    # 段位数值: tier*4 + (3-d), 越大段位越高(PLATINUM IV=19, GOLD I=12)
    return t * 4 + (3 - d)


def main():
    print('=' * 60)
    print('  段位最高差异诊断 v4.1.41')
    print('  (确认 highestTier / previousSeasonEndTier 实际返回)')
    print('=' * 60)

    c = LCUConnector()
    if not c.find_client(quick=False):
        print('\n[失败] 找不到 LOL 客户端(leagueclientux.exe)')
        print('  请确认: 1) LOL 客户端已打开到主界面  2) 进程未结束')
        return 1

    print(f'\n[连接] port={c._port} pid={c._pid}')

    import requests
    import urllib3
    urllib3.disable_warnings()
    sess = requests.Session()
    sess.verify = False
    sess.trust_env = False

    # 1) ranked-stats 完整 JSON
    url = f'{c.base_url}/lol-ranked/v1/current-ranked-stats'
    r = sess.get(url, auth=c.auth, timeout=5)
    if r.status_code != 200:
        print(f'\n[失败] ranked-stats HTTP {r.status_code}: {r.text[:300]}')
        return 1
    data = r.json()
    print(f'\n--- 完整 ranked-stats 返回 ({len(json.dumps(data, ensure_ascii=False))} 字节) ---')
    print(json.dumps(data, ensure_ascii=False, indent=2))

    # 2) 关键字段抽取
    print('\n' + '=' * 60)
    print('  关键字段(只看非空):')
    print('=' * 60)
    qm = (data or {}).get('queueMap') or {}
    for qk, label in [('RANKED_SOLO_5x5', '单双排'), ('RANKED_FLEX_SR', '灵活')]:
        e = qm.get(qk) or {}
        if not e:
            print(f'\n[{label}] 未打段位(无 entry)')
            continue
        print(f'\n[{label}] entry:')
        for k in ['queueType', 'tier', 'division', 'leaguePoints',
                  'highestTier', 'highestDivision',
                  'previousSeasonEndTier', 'previousSeasonEndDivision',
                  'wins', 'losses', 'isProvisional', 'provisionalGamesRemaining']:
            v = e.get(k)
            if v is not None and v != '':
                print(f'  {k:32s} = {v!r}')

    # 3) 与脚本 _highest 函数逻辑对照
    print('\n' + '=' * 60)
    print('  脚本 _highest() 判定对照:')
    print('  (逻辑: 优先 highestTier,回退 previousSeasonEndTier)')
    print('=' * 60)
    for qk, label in [('RANKED_SOLO_5x5', '单双排'), ('RANKED_FLEX_SR', '灵活')]:
        e = qm.get(qk) or {}
        if not e:
            print(f'\n[{label}] _highest → None(无 entry)')
            continue
        cur_h = ((e.get('highestTier') or '').upper(),
                 (e.get('highestDivision') or ''))
        ps_end = ((e.get('previousSeasonEndTier') or '').upper(),
                  (e.get('previousSeasonEndDivision') or ''))
        cv = _tier_value(cur_h[0], cur_h[1])
        pv = _tier_value(ps_end[0], ps_end[1])
        print(f'\n[{label}] highestTier={cur_h[0]} {cur_h[1]} (数值={cv})')
        print(f'[{label}] previousSeasonEnd={ps_end[0]} {ps_end[1]} (数值={pv})')
        if cv >= 0 and (cv >= pv or pv < 0):
            chosen = f'highestTier → {cur_h[0]} {cur_h[1]}'
        elif pv >= 0:
            chosen = f'previousSeasonEnd → {ps_end[0]} {ps_end[1]}'
        else:
            chosen = 'None(两端都空)'
        print(f'[{label}] _highest 取: {chosen}')

    print('\n' + '=' * 60)
    print('  请复制上方完整输出贴回对话')
    print('=' * 60)
    return 0


if __name__ == '__main__':
    code = 1
    try:
        code = main()
    except Exception as e:
        print(f'\n[异常] {type(e).__name__}: {e}')
        import traceback
        traceback.print_exc()
        code = 1
    finally:
        # 双击运行时控制台会秒关, 这里停住让用户能看清输出
        try:
            input('\n\n按回车键退出...')
        except EOFError:
            pass
    sys.exit(code)