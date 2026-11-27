"""监听实验：记录"手动点卡锁定"前后 champ-select session 的变化。

目的：海克斯模式锁定协议与经典 ARAM 不同（API PATCH 204 但 UI 不锁）。
本脚本 0.4s 轮询 session，只在状态变化时打印一行带时间戳的 diff，
你在选人界面手动点一张卡 → 锁定，脚本会把整个过程的真实变化录下来。

用法（双击 diag_monitor.bat 或命令行）:
    python tools/diag_monitor.py
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lcu.connector import LCUConnector
from lcu.api import LCUAPI

_OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'diag_monitor.txt')


from _tee import _Tee


def _state_sig(s):
    """提取关键状态快照,便于 diff。"""
    if not s:
        return None
    lc = s.get('localPlayerCellId')
    a0 = None
    for g in (s.get('actions') or []):
        if not isinstance(g, list):
            g = [g]
        for a in g:
            if a.get('actorCellId') == lc:
                a0 = a
                break
        if a0 is not None:
            break
    me = next((p for p in s.get('myTeam', [])
               if p.get('cellId') == lc), None) or {}
    bench = sorted(b['championId'] for b in (s.get('benchChampions') or [])
                   if 'championId' in b)
    return {
        'act_cid': a0.get('championId') if a0 else None,
        'act_done': a0.get('completed') if a0 else None,
        'act_inp': a0.get('isInProgress') if a0 else None,
        'my_cid': me.get('championId'),
        'my_intent': me.get('championPickIntent'),
        'bench': bench,
        'phase': (s.get('timer') or {}).get('phase'),
        'timeleft': (s.get('timer') or {}).get('timeLeftInPhase'),
    }


def _diff(a, b):
    """a→b 的变化描述。"""
    if a is None or b is None:
        return 'session 出现/消失'
    out = []
    for k in ('act_cid', 'act_done', 'act_inp', 'my_cid', 'my_intent',
              'phase', 'timeleft'):
        if a.get(k) != b.get(k):
            out.append(f'{k}: {a.get(k)} → {b.get(k)}')
    if a.get('bench') != b.get('bench'):
        out.append(f'bench: {a.get("bench")} → {b.get("bench")}')
    return '; '.join(out) if out else None


def main():
    try:
        f = open(_OUT_PATH, 'w', encoding='utf-8')
        sys.stdout = _Tee(sys.__stdout__, f)
    except Exception:
        pass

    print(f'记录文件: {_OUT_PATH}\n')
    c = LCUConnector()
    if not c.find_client():
        print('❌ 找不到 LOL 客户端,先启动客户端再跑')
        return 1
    api = LCUAPI(c)
    print('✅ 已连接。请现在进入海克斯选人界面(2-3 张卡出现后):')
    print('  ① 点一张卡(不锁,看预选变化)')
    print('  ② 点锁定按钮 或 等锁定')
    print('  ③ 看到本脚本打印 ">>> 已锁定 完成" 即可关闭窗口\n')
    print('每秒轮询中... 状态一有变化就打印(最多 180 秒)\n')

    last = None
    end = time.time() + 180
    while time.time() < end:
        try:
            s = api.get('/lol-champ-select/v1/session')
        except Exception:
            s = None
        cur = _state_sig(s)
        if cur is not None and cur != last:
            ts = time.strftime('%H:%M:%S')
            d = _diff(last, cur)
            if last is not None and d:
                print(f'[{ts}] {d}')
            elif last is None:
                print(f'[{ts}] 进入选人: {cur}')
            last = cur
            # 检测锁定完成
            if (cur.get('act_done') is True and cur.get('my_cid') not in (0, None)):
                print(f'[{ts}] >>> 已锁定 英雄#{cur.get("my_cid")} / action.cid={cur.get("act_cid")}')
                # 保存完整 session 便于细看
                try:
                    full = json.dumps(s, ensure_ascii=False, indent=1)
                    fp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      'diag_locked_session.json')
                    open(fp, 'w', encoding='utf-8').write(full)
                    print(f'[{ts}] 完整 session 已存: {fp}')
                except Exception as e:
                    print(f'[存 session 失败] {e}')
                break
            # 预选变化提示(点了卡但还没锁)
            if cur.get('my_intent') not in (0, None) and cur.get('act_done') is not True:
                pass
        time.sleep(0.4)
    print('\n监听结束。把 tools\\diag_monitor.txt 发给 AI')
    return 0


if __name__ == '__main__':
    sys.exit(main())
