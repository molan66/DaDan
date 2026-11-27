"""验证假设: 自动选失败是因为 PATCH 太早被服务器静默忽略,
候选卡要几秒后才"可锁"。同一英雄对比: 立即 PATCH vs 延迟 PATCH。

用法(双击 diag_probe.bat):
    python tools/diag_probe.py
进海克斯选人界面后脚本自动跑, 无需操作, 约 20 秒出结果。
"""
import os
import sys
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lcu.connector import LCUConnector
from lcu.api import LCUAPI

_OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         'diag_probe.txt')
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
        p = d.get('preferred_champions') or d.get('prefs') or []
        print(f'读取偏好设置: {p[:5]}{"..." if len(p) > 5 else ""}')
        return list(p)
    except Exception as e:
        print(f'读偏好失败({e}), 用默认测试英雄 [76, 888]')
        return [76, 888]


def try_lock(api, cid, prefix_endpoint, label):
    """PATCH 设英雄 + POST complete, 然后 1.5s 内轮询看是否真翻转。"""
    import requests
    a0 = None
    try:
        s = api.get('/lol-champ-select/v1/session')
        a0 = get_my_action(s)
    except Exception:
        pass
    before = f'action(cid={a0.get("championId") if a0 else "?"},' \
             f'done={a0.get("completed") if a0 else "?"})'
    try:
        r1 = api._req('PATCH', f'{prefix_endpoint}/actions/{a0["id"]}',
                      data=json.dumps({'championId': cid}))
        time.sleep(0.4)
        r2 = api._req('POST', f'{prefix_endpoint}/actions/{a0["id"]}/complete')
        print(f'[{label}] PATCH cid={cid} → HTTP {r1.status_code}, '
              f'complete → HTTP {r2.status_code}')
    except Exception as e:
        print(f'[{label}] 请求异常: {type(e).__name__}: {e}')
        return False
    # 轮询看翻转
    for i in range(5):
        time.sleep(0.4)
        try:
            s = api.get('/lol-champ-select/v1/session')
            a1 = get_my_action(s)
            if a1 and a1.get('completed') is True and \
               a1.get('championId') == cid:
                print(f'[{label}] ✅ 锁定成功! action.cid={a1.get("championId")} '
                      f'done=True ({before} → cid={cid},done=True)')
                return True
        except Exception:
            pass
    try:
        s = api.get('/lol-champ-select/v1/session')
        a2 = get_my_action(s)
        after = f'action(cid={a2.get("championId") if a2 else "?"},' \
                f'done={a2.get("completed") if a2 else "?"},' \
                f'inp={a2.get("isInProgress") if a2 else "?"})'
        print(f'[{label}] ❌ 未翻转, 当前 {after}')
    except Exception:
        pass
    return False


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

    # 等进入 BAN_PICK 且未锁
    t0 = None
    while True:
        try:
            s = api.get('/lol-champ-select/v1/session')
            a = get_my_action(s)
            ph = (s.get('timer') or {}).get('phase')
            if s and ph == 'BAN_PICK' and a and not a.get('completed'):
                t0 = time.time()
                print(f'[{time.strftime("%H:%M:%S")}] 进入选人 phase={ph}, '
                      f'开始实验\n')
                break
        except Exception:
            pass
        time.sleep(0.4)

    # session keys 里确认前缀(legacy vs new)
    try:
        s = api.get('/lol-champ-select/v1/session')
        is_legacy = s.get('isLegacyChampSelect', True) is not False
    except Exception:
        is_legacy = True
    prefix = ('/lol-champ-select/v1/session'
              if is_legacy else
              '/lol-lobby-team-builder/champ-select/v1/session')
    print(f'端点前缀: {prefix}  (legacy={is_legacy})')

    target = prefs[0] if prefs else 76

    # === 实验 1: 立即 PATCH(进选人 <0.5s) ===
    print('\n【实验1】立即 PATCH(候选可能未激活):')
    ok1 = try_lock(api, target, prefix, '立即')

    if ok1:
        print('\n>>> 立即 PATCH 就成功了! 说明不需要延迟, 之前失败另有原因')
    else:
        # === 实验 2: 等到 t0+7s 再 PATCH 同一英雄 ===
        wait = max(0, (t0 + 7) - time.time())
        print(f'\n等待 {wait:.1f}s 让候选卡激活, 再 PATCH 同一英雄 {target}...')
        time.sleep(wait)
        print('\n【实验2】延迟 PATCH(候选应已激活):')
        ok2 = try_lock(api, target, prefix, '延迟')
        if ok2:
            print('\n>>> 假设证实: 延迟后同一英雄锁上了! 修复方向=延迟 PATCH')
        else:
            print('\n>>> 延迟后也锁不上。再等 6s 最后一次(候选最终确认):')
            time.sleep(6)
            ok3 = try_lock(api, target, prefix, '最终')
            if ok3:
                print('\n>>> 更晚的第三次锁上了 → 需要更长的等待/轮询直到可锁')
            else:
                print('\n>>> 3 次都不锁 → 协议差异(手动锁用了别的请求),'
                      ' 需要抓 UI 实际请求')

    print(f'\n实验结束, 把 tools\\diag_probe.txt 发给 AI')
    return 0


if __name__ == '__main__':
    sys.exit(main())
