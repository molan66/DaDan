"""验证 my-selection 端点能否在海克斯(3270)模式锁定英雄。

假设: 该模式同时选(hasSimultaneousPicks=True), 客户端点卡锁的端点
可能是 /session/my-selection 而非 actions/{id}。

方法: 进选人后, 对偏好英雄逐个 PATCH my-selection:
  - 若对非候选返回 4xx → 试错可行(跳过继续试下一个)
  - 若某 PATCH 后 action.completed=True → 锁定成功!
同时记录每个组合的 HTTP 码, 一次看清协议。

用法(双击 diag_myselect.bat): 进海克斯选人界面后自动跑, 别动鼠标键盘。
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lcu.connector import LCUConnector
from lcu.api import LCUAPI

_OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'diag_myselect.txt')
_SET_PATH = os.path.join(os.environ.get('APPDATA', ''), 'LOLScript',
                         'settings.json')


from _tee import _Tee


def get_my_action(s):
    lc = s.get('localPlayerCellId')
    for g in (s.get('actions') or []):
        if not isinstance(g, list):
            g = [g]
        for a in g:
            if a.get('actorCellId') == lc:
                return a
    return None


def read_pref():
    try:
        d = json.load(open(_SET_PATH, encoding='utf-8'))
        p = d.get('preferred_champions') or []
        print(f'偏好: {p}')
        return list(p)
    except Exception:
        return [76, 48, 21, 17, 35]


def main():
    try:
        f = open(_OUT_PATH, 'w', encoding='utf-8')
        sys.stdout = _Tee(sys.__stdout__, f)
    except Exception:
        pass
    print(f'记录文件: {_OUT_PATH}\n')

    prefs = read_pref()
    c = LCUConnector()
    if not c.find_client():
        print('❌ 找不到 LOL 客户端')
        return 1
    api = LCUAPI(c)
    print('✅ 已连接, 等待进入选人界面(BAN_PICK 且未锁定)...\n')

    # 等 BAN_PICK
    while True:
        try:
            s = api.get('/lol-champ-select/v1/session')
            a = get_my_action(s)
            ph = (s.get('timer') or {}).get('phase')
            if s and ph == 'BAN_PICK' and a and not a.get('completed'):
                break
        except Exception:
            pass
        time.sleep(0.4)
    print(f'[{time.strftime("%H:%M:%S")}] 进入选人, 等 4s 候选激活...')
    time.sleep(4)

    # 两个前缀都试
    pre = ['/lol-lobby-team-builder/champ-select/v1/session',
           '/lol-champ-select/v1/session']

    # 试错池: 偏好 + 几个兜底
    pool = list(prefs)
    for extra in (238, 888, 53, 79, 122, 6):
        if extra not in pool:
            pool.append(extra)

    for cid in pool:
        for p in pre:
            ep_sel = f'{p}/my-selection'
            # 1) PATCH my-selection
            try:
                r = api._req('PATCH', ep_sel,
                             data=json.dumps({'championId': cid}))
                code1 = r.status_code
            except Exception as e:
                print(f'[cid={cid}] PATCH {ep_sel} 异常: {e}')
                continue
            # 2) 看 my-selection 现在返回啥
            cur_sel = None
            try:
                r2 = api._req('GET', ep_sel)
                if r2.status_code == 200:
                    cur_sel = r2.text[:120]
            except Exception:
                pass
            # 3) POST complete
            try:
                r3 = api._req('POST', f'{p}/actions/0/complete')
                code3 = r3.status_code
            except Exception:
                code3 = 'x'
            # 4) 查 action 是否翻转
            try:
                s2 = api.get('/lol-champ-select/v1/session')
                a2 = get_my_action(s2)
                flipped = (a2 and a2.get('completed') is True
                           and a2.get('championId') == cid)
            except Exception:
                flipped = False
            print(f'[cid={cid:>3}] PATCH my-selection({p.split("/")[1]}) '
                  f'→ {code1}; my-sel当前={cur_sel}; complete→{code3}; '
                  f'锁定={"✅成功" if flipped else "否"}')
            if flipped:
                print(f'\n>>> ✅ 通过 my-selection 锁定了 #{cid}! 协议找到!')
                return 0
            # PATCH 成功(2xx) 但没翻转且目标非空 → 说明接受但没锁; 换下一个
            time.sleep(0.3)

    print('\n>>> 偏好+兜底全部试完, 未锁定。')
    print('若所有 PATCH 都 4xx → 端点不存在/不接受; 若 2xx 不锁 → 协议还差字段')
    print(f'把 tools\\diag_myselect.txt 发给 AI')
    return 0


if __name__ == '__main__':
    sys.exit(main())
