# -*- coding: utf-8 -*-
"""探针：注释里引用的「官方数据条数」是否还成立。

问题
----
代码注释里散落着 `374 个里有 344 个是纯白蒙版` 这类**具体数字**，用来解释
"为什么要按稀有度染色"。这些数字本身是对的（在写注释那一刻），但游戏一更新
就过期 —— LOL 16.21 已经是 594 条 / 527 个白蒙版。过期注释不会让程序报错，
但会让下一个改代码的人**按错的分布去推断**（例如以为九成是白蒙版、或以为
kEventChoice 全部自带颜色）。

判据
----
从注释里抽出形如 `<A> 个里有 <B> 个` / `<B>/<A>` 的"总数/子集数"对，与真实
数据算出的同一个量对比：
  · 总数 必须等于客户端表里的条目数
  · 子集数 必须等于真实白蒙版数

用法
----
    <python> tools/probe_stale_comment_counts.py [cherry-augments.json]

需要先有图标缓存目录（用 --icons 指定，或用环境变量 DADAN_AUG_ICONS）。
没有图标目录时**跳过子集校验**并明确说明（不是通过，是"无法验证"）。

退出码 0 = 数字全部对得上（或全部显式跳过）；1 = 有过期数字。
"""
import io
import json
import os
import re
import struct
import sys
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

FIXTURE = os.path.join(_ROOT, 'tests', 'fixtures', 'cherry_augments_16_21.json')

# 要检查的文件（注释在这四个里）
TARGETS = [
    os.path.join(_ROOT, 'lcu', 'api.py'),
    os.path.join(_ROOT, 'ui', 'icons.py'),
    os.path.join(_ROOT, 'ui', 'styles.py'),
    os.path.join(_ROOT, 'core', 'updater.py'),
]

# `374 个里有 344 个` / `344/374` / `全部 374 个中有 **344 个`
# ⚠️ 数字位数上限刻意取 3 位：条数最大也就几百，而 **queueId 是 4 位**
#    （注释里常写 `2400/2401/2403/2405/2410/2450` 这样的队列 id 链）。
#    第一版用 {2,4} 把队列 id 链当成了"子集/总数"，误报一堆 —— 判据精度就是
#    这么被一点点磨掉的：宁可少报，也不能报假。
PAT_PAIR = re.compile(r'(\d{2,3})\s*个?里有\s*\*{0,2}(\d{2,3})\s*个')
PAT_SLASH = re.compile(r'(?<![\d/])(\d{2,3})\s*/\s*(\d{2,3})(?![\d/])')
PAT_TOTAL = re.compile(r'全部\s*(\d{2,3})\s*个')
PAT_SUB = re.compile(r'\*{0,2}(\d{2,3})\s*个是纯白')


def _png_is_white_mask(path):
    """解码 PNG，判断是否「所有可见像素都接近纯白」。纯标准库实现。"""
    try:
        with open(path, 'rb') as f:
            data = f.read()
        if data[:8] != b'\x89PNG\r\n\x1a\n':
            return None
        pos, w, h, bitd, color = 8, 0, 0, 0, 0
        idat = b''
        while pos < len(data):
            ln = struct.unpack('>I', data[pos:pos + 4])[0]
            typ = data[pos + 4:pos + 8]
            body = data[pos + 8:pos + 8 + ln]
            if typ == b'IHDR':
                w, h, bitd, color = struct.unpack('>IIBB', body[:10])
            elif typ == b'IDAT':
                idat += body
            elif typ == b'IEND':
                break
            pos += 12 + ln
        if bitd != 8 or color not in (6, 4):
            return None  # 只处理 RGBA / 灰度+alpha 的 8 位图
        ch = 4 if color == 6 else 2
        raw = zlib.decompress(idat)
        stride = w * ch
        prev = bytearray(stride)
        seen = False
        p = 0
        for _y in range(h):
            ft = raw[p]
            p += 1
            line = bytearray(raw[p:p + stride])
            p += stride
            if ft == 1:
                for i in range(ch, stride):
                    line[i] = (line[i] + line[i - ch]) & 0xFF
            elif ft == 2:
                for i in range(stride):
                    line[i] = (line[i] + prev[i]) & 0xFF
            elif ft == 3:
                for i in range(stride):
                    a = line[i - ch] if i >= ch else 0
                    line[i] = (line[i] + ((a + prev[i]) >> 1)) & 0xFF
            elif ft == 4:
                for i in range(stride):
                    a = line[i - ch] if i >= ch else 0
                    b = prev[i]
                    c = prev[i - ch] if i >= ch else 0
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    line[i] = (line[i] + pr) & 0xFF
            for x in range(w):
                al = line[x * ch + ch - 1]
                if al <= 8:
                    continue
                seen = True
                for k in range(ch - 1):
                    if abs(line[x * ch + k] - 255) > 24:
                        return False
            prev = line
        return True if seen else False
    except Exception:
        return None


def _claims_white_mask(line):
    """这一行是否在说"白蒙版/剪影蒙版"的数量。

    列全官方数据里出现过的说法（'白蒙版' 是代码里最常用的那个词，第一版漏了它）。
    """
    line = line or ''
    low = line.lower()
    return any(k in line for k in ('白蒙版', '剪影', '纯白')) \
        or 'white mask' in low or 'whitemask' in low


def real_counts(table, icons_dir):
    total = len(table)
    if not icons_dir or not os.path.isdir(icons_dir):
        return total, None
    white = 0
    checked = 0
    for a in table:
        p = os.path.join(icons_dir, '%s.png' % a.get('id'))
        if not os.path.isfile(p):
            continue
        r = _png_is_white_mask(p)
        if r is None:
            continue
        checked += 1
        if r:
            white += 1
    if checked < total * 0.9:
        return total, None   # 图标不全，不给结论
    return total, white


def main(argv):
    args = [a for a in argv[1:]]
    icons = os.environ.get('DADAN_AUG_ICONS', '')
    table_path = FIXTURE
    for i, a in enumerate(args):
        if a == '--icons' and i + 1 < len(args):
            icons = args[i + 1]
        elif not a.startswith('--'):
            table_path = a

    if not os.path.isfile(table_path):
        print('FAIL 找不到海克斯表: %s' % table_path)
        return 1
    with io.open(table_path, encoding='utf-8-sig') as f:
        table = json.load(f)

    total, white = real_counts(table, icons)
    print('数据源: %s' % table_path)
    print('真实条目数: %d' % total)
    if white is None:
        print('真实白蒙版数: 无法验证（图标目录缺失或不全: %r）' % (icons or ''))
    else:
        print('真实白蒙版数: %d' % white)

    findings = []
    for path in TARGETS:
        if not os.path.isfile(path):
            continue
        with io.open(path, encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        rel = os.path.relpath(path, _ROOT)
        for no, line in enumerate(lines, 1):
            # 过滤条件：**这一行在声称白蒙版的数量**（而不是"行里恰好有某个词"）。
            # 第一版只认 '剪影'/'纯白'，漏了最常用的说法「白蒙版」——
            # ui/icons.py:308 写的是"594 条里 527 条是白蒙版"，一个关键词都不含，
            # 于是整行被跳过；把旧数字放回去探针照样通过，护栏等于装饰。
            if not _claims_white_mask(line):
                continue
            for m in PAT_PAIR.finditer(line):
                a, b = int(m.group(1)), int(m.group(2))
                findings.append((rel, no, m.group(0), a, b))
            for m in PAT_SLASH.finditer(line):
                a, b = int(m.group(1)), int(m.group(2))
                # `344/374` 里前一个是子集、后一个是总数
                if a > b:
                    a, b = b, a
                findings.append((rel, no, m.group(0), b, a))
            for m in PAT_TOTAL.finditer(line):
                findings.append((rel, no, m.group(0), int(m.group(1)), None))
            if PAT_TOTAL.search(line):
                continue
            for m in PAT_SUB.finditer(line):
                if not PAT_PAIR.search(line) and not PAT_SLASH.search(line):
                    findings.append((rel, no, m.group(0), None, int(m.group(1))))

    stale = []
    exempt = []
    for rel, no, text, said_total, said_sub in findings:
        # core/updater.py 是**历史 changelog**：里面记的是"当时那一刻"的实测值，
        # 改了等于篡改历史，下一版的人就看不出这个数字是哪个版本说的。
        # 所以这里**显式豁免并打印出来**，而不是悄悄跳过 —— 否则探针会假装绿。
        if rel.replace('\\', '/').endswith('core/updater.py'):
            exempt.append((rel, no, text))
            continue
        if said_total is not None and said_total != total:
            stale.append((rel, no, text, '总数应为 %d，注释写 %d' % (total, said_total)))
            continue
        if said_sub is not None:
            if white is None:
                stale.append((rel, no, text,
                              '子集数 %d 无法验证（缺图标目录），请勿留不可验证的数字'
                              % said_sub))
            elif said_sub != white:
                stale.append((rel, no, text,
                              '白蒙版应为 %d，注释写 %d' % (white, said_sub)))

    if not findings:
        print('\nPASS 未在注释里找到具体条数引用（无需检查）')
        return 0
    print('\n共检查 %d 处数字引用' % len(findings))
    if exempt:
        print('\n[豁免] %d 处位于历史 changelog（保留当时实测值，只在旁边标注了"历史值"）:'
              % len(exempt))
        for rel, no, text in exempt:
            print('    %s:%s  %r' % (rel, no, text))
    if not stale:
        print('\nPASS 全部与真实数据一致（豁免项如上）')
        return 0
    print('\n[过期] %d 处注释数字与真实数据不符:' % len(stale))
    for rel, no, text, why in stale:
        print('    %s:%s  %r' % (rel, no, text))
        print('        %s' % why)
    print('\nFAIL 见上')
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv))
