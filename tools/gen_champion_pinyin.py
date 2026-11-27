# -*- coding: utf-8 -*-
"""
一次性生成拼音搜索索引 → data/champion_pinyin.py
仅在开发/维护别名库时手动运行一次;打包不包含本脚本,运行时也不依赖 pypinyin。

用法: 在 dadan-build 目录下执行
    python tools/gen_champion_pinyin.py
产出: data/champion_pinyin.py
  CHAMPION_PINYIN = {cid: {'f': [全拼...], 'a': [缩写...]}}
  - f: 全拼(无音调小写) 匹配用"子串" (用户可输入 delaiwen / laiwen)
  - a: 拼音首字母缩写   匹配用"前缀" (逐字输入 dl / dlw)
"""
import json
import os
import re

from pypinyin import lazy_pinyin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALIAS_FILE = os.path.join(ROOT, 'data', 'champion_aliases.py')
CACHE_FILE = os.path.join(ROOT, 'data', 'champion_cache.json')
OUT_FILE = os.path.join(ROOT, 'data', 'champion_pinyin.py')

# 只提取纯汉字段(过滤 · 、空格、字母等)
_HAN = re.compile(r'[\u4e00-\u9fff]+')


def _full(word: str):
    """全拼(无音调,小写), 如 德莱文 -> delaiwen"""
    runs = _HAN.findall(word)
    if not runs:
        return None
    return ''.join(''.join(lazy_pinyin(run)) for run in runs)


def _abbr(word: str):
    """拼音首字母缩写, 如 德莱文 -> dlw"""
    runs = _HAN.findall(word)
    if not runs:
        return None
    return ''.join(''.join(p[0] for p in lazy_pinyin(run)) for run in runs)


def main():
    # 1) 读别名库(源码顶层常量)
    ns = {}
    with open(ALIAS_FILE, encoding='utf-8') as f:
        exec(compile(f.read(), ALIAS_FILE, 'exec'), ns)
    aliases = ns['CHAMPION_ALIASES']

    # 2) 读官方名缓存(含 title/name)
    with open(CACHE_FILE, encoding='utf-8') as f:
        cache = json.load(f)
    cache_d = {int(k): v for k, v in cache.get('d', {}).items() if 0 < int(k) <= 1000}

    # 3) 汇总每个 cid 的候选词: 官方缓存名 + 别名库所有词条
    cids = set(aliases) | set(cache_d)
    out = {}
    for cid in sorted(cids):
        words = set()
        cn = cache_d.get(cid)
        if cn:
            words.add(cn)
        for w in aliases.get(cid, []):
            words.add(w)
        fulls, abbrs = set(), set()
        for w in words:
            f = _full(w)
            a = _abbr(w)
            if f:
                fulls.add(f)
            if a:
                abbrs.add(a)
        out[cid] = {'f': sorted(fulls), 'a': sorted(abbrs)}

    # 4) 写出静态模块
    lines = ['# -*- coding: utf-8 -*-',
             '"""英雄拼音搜索索引(自动生成,勿手改)',
             '生成方式: python tools/gen_champion_pinyin.py',
             'f=全拼(子串匹配)  a=缩写(前缀匹配)',
             '"""',
             '',
             'CHAMPION_PINYIN = {']
    for cid in sorted(out):
        f = ', '.join(repr(s) for s in out[cid]['f'])
        a = ', '.join(repr(s) for s in out[cid]['a'])
        lines.append(f'    {cid}: {{"f": [{f}], "a": [{a}]}},')
    lines.append('}')
    lines.append('')

    with open(OUT_FILE, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    total = sum(len(v['f']) + len(v['a']) for v in out.values())
    print(f'OK 覆盖 {len(out)} 个英雄, 共 {total} 个拼音候选 → {OUT_FILE}')
    for cid in (21, 86, 119):
        print(f'  cid={cid}: {out[cid]}')


if __name__ == '__main__':
    main()
