# -*- coding: utf-8 -*-
"""探针：海克斯名兜底 —— 未本地化的内部标识符不许漏到界面。

背景（LOL 16.21 实测）
--------------------
`cherry-augments.json` 594 条里有一条（id=3176）的 `nameTRA` 不是中文名，而是
内部标识符 `GoH_PentakillAug_Generic_BlankName`（它的 `augmentNameId` 是
`GoH_PentakillAug_Generic`）。官方没给这条做本地化。

这一条目前**没有显示到界面上** —— 详情窗只画图标（只读 `augment_rarity`），
`augment_names` 算出来之后全仓无人读取。所以它不是线上可见缺陷，但它是
一个**随时会变成可见缺陷的定时炸弹**：只要以后有人在图标上加个 tooltip、
或者把名字写进详情窗（v4.3.2 的注释本意就是这样），这串下划线标识符就会
直接怼到用户脸上。

判据
----
不许出现「像内部标识符」的名字：
  · 与 augmentNameId 相同，或以 augmentNameId 开头；
  · 或整串只由 [A-Za-z0-9_] 组成且含下划线。

反面约束（同样要测，否则修法会误伤正常名字）：
  · `ADAPt` / `escAPADe` / `APADe` 这类**没有下划线**的官方英文名必须原样保留；
  · 中文名、带空格名（如 `404 强化符文未找到`）必须原样保留。

用法
----
    <python> tools/probe_augment_name_fallback.py [cherry-augments.json]

退出码 0 = 全部干净；1 = 有内部标识符漏出。
本探针只读，不联网。
"""
import io
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FIXTURE = os.path.join(_ROOT, 'tests', 'fixtures', 'cherry_augments_16_21.json')

# 真实里见过的合法英文名（全部**不含下划线**）—— 修法不许把它们一起灭掉
LEGIT_LATIN_NAMES = ['ADAPt', 'escAPADe', 'APADe', 'BONK!', 'Stats!',
                     '404 Augment Not Found', 'En Passant']


def main(argv):
    path = argv[1] if len(argv) > 1 else FIXTURE
    if not os.path.isfile(path):
        print('FAIL 找不到海克斯表: %s' % path)
        return 1
    with io.open(path, encoding='utf-8-sig') as f:
        table = json.load(f)
    if not isinstance(table, list) or not table:
        print('FAIL 海克斯表为空或格式不符: %s' % path)
        return 1

    from lcu.api import LCUAPI

    # ⚠️ 必须驱动**真实的** _load_augment_table，不能在这里另写一份建表逻辑 ——
    # 第一版探针就是这么错的：它把被测函数整个替换掉，于是测的是探针自己，
    # 修复前判红、修复后照样判红（假红）。这里只替换网络层 get()，让真实的
    # 建表 + 清洗链路跑起来。
    LCUAPI._AUGMENT_TABLE = None      # 清类级缓存，否则拿到别的用例留下的表
    api = LCUAPI.__new__(LCUAPI)       # 只测纯逻辑，不建连接
    api.get = lambda ep, **kw: (table if 'cherry-augments' in ep else [])

    try:
        built = api._load_augment_table()
    finally:
        LCUAPI._AUGMENT_TABLE = None
    if not built:
        print('FAIL 真实加载函数返回空表')
        return 1

    bad = []
    for a in table:
        raw = str(a.get('nameTRA') or '')
        name_id = str(a.get('augmentNameId') or '')
        v = built.get(int(a.get('id') or 0))
        shown = v[0] if v else ''
        if _looks_internal(shown, name_id):
            bad.append((a.get('id'), name_id, raw, shown))

    print('数据源: %s' % path)
    print('条目: %d  已建表: %d' % (len(table), len(built)))

    ok = True
    if bad:
        ok = False
        print('\n[漏] %d 条名字仍是内部标识符(会显示成下划线字符串):' % len(bad))
        for aid, name_id, raw, shown in bad:
            print('    id=%-6s nameId=%-28s nameTRA=%-34s 显示=%r'
                  % (aid, name_id, raw, shown))

    # 反面约束：合法名字不许被误伤
    hurt = []
    for nm in LEGIT_LATIN_NAMES:
        if _looks_internal(nm, 'ARAM_Something'):
            hurt.append(nm)
    if hurt:
        ok = False
        print('\n[误伤] %d 个合法名字被判成了内部标识符: %s' % (len(hurt), hurt))

    if ok:
        print('\nPASS 无内部标识符漏出，且合法名字未被误伤')
        return 0
    print('\nFAIL 见上')
    return 1


def _looks_internal(name, name_id):
    """与 lcu.api 的判据保持同一语义（这里独立实现，避免自证）。"""
    s = (name or '').strip()
    if not s:
        return False           # 空名 = 已兜底，不算漏
    nid = (name_id or '').strip()
    if nid:
        if s == nid:
            return True
        if s.lower().startswith(nid.lower()):
            return True
    if '_' in s and re.fullmatch(r'[A-Za-z0-9_]+', s):
        return True
    return False


def _build(table):
    out = {}
    for a in table:
        try:
            aid = int(a.get('id') or 0)
        except (TypeError, ValueError):
            continue
        icon = str(a.get('augmentSmallIconPath') or '')
        if aid > 0 and icon:
            out[aid] = (str(a.get('nameTRA') or ''), icon,
                        str(a.get('rarity') or ''))
    return out


if __name__ == '__main__':
    sys.exit(main(sys.argv))
