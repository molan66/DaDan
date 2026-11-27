# -*- coding: utf-8 -*-
"""把 v4.9.0 changelog 里"实机验证补记"一段插进去。

为什么单独一个脚本：notes 是 3 万字符的大字符串，手改易破坏转义；
且控制台 GBK，PowerShell 内联中文会坏。

幂等：按标记串判断，重复跑不会重复插入。
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DADAN = os.path.dirname(ROOT)
VERSION_JSON = os.path.join(DADAN, 'version.json')

MARK = '【打包前实机复查（追加）'

ADD = """
【打包前实机复查（追加）：连真客户端跑了一遍，又抓出 3 个问题】
- 打包前我用真实 LOL 客户端（16.21，port 56703）把三处改动全验了一遍
  （tools/verify_v490_live.py，只读：不点击、不改设置、不启脚本）。结果：三处改动
- 本身都对，但**发现我自己的验证体系有 3 个洞**，都补了。

① 我的测试夹具漏了 4 个队列，导致"全覆盖"那条测试**假通过**
- 第一版夹具是我手写 PowerShell 按名字过滤（`海克斯大乱斗|极地大乱斗`）抓的，
  于是漏掉 451(3v3 ARAM) / 452(1v1 ARAM) / 720 / 721(Clash ARAM) ——
  它们名字里没有那些词，但确实是 `kARAM + AllRandomPickStrategy`。
- 后果很典型：测试只验证"夹具里有的东西"，夹具漏了的东西**永远不会被测到**，
  于是测试绿着、缺陷还在。这正是"夹具按名字抓"的原罪。
- 修法：新增 tools/refresh_queue_fixture.py，夹具改为**按判据抓**（所有 kARAM
  队列 + 所有 AllRandom 队列 + 备案 id，共 28 条），不再按名字抓。
- 补好后探针立刻判红，暴露出 4 个漏项 —— 说明这个洞是真的。

② 手写兜底集合跟着补齐（补成 14 个 id）
- 450, 451, 452, 720, 721, 860, 861, 2400, 2401, 2403, 2405, 2410, 2450, 3270。
- 实机结论：客户端应有 13 个（kARAM+AllRandom），程序实际启用 14 个
  （多的是备案保留的 3270），**覆盖完整**。
- 顺带把这段注释的定位改清楚了：**手写集合不是要维护的清单**，只是"客户端没就绪时
  别让点卡兜底整个消失"的兜底。真正跟版本走的是在线推导。这个区分很重要，
  否则下一版的人还会把它当成清单去手工补。

③ 我的实机验证脚本自己误报了一次
- 它把 3270 报成"误纳非 kARAM 队列 → 可能乱点"。但 3270 是 EXTRA_ALLOWED 里
  **已备案保留**的（分组是 kAlternativeLeagueGameModes，推导抓不到，但用很久无报障）。
- 也就是说：**判据没写错，是验证脚本忘了扣掉备案项**。这类"验证工具自己错"
  最容易骗人 —— 它一红，人第一反应是去改被测代码。
- 已修：判定误纳时先减掉 EXTRA_ALLOWED。

④ 有牙脚本的一组破坏锚点失效了（改了常量后没同步）
- 改 ARAM_QUEUE_IDS 后，probe_v490_teeth.py 第④组的锚点对不上（count=0），
  脚本报 SKIP。**SKIP 不是通过** —— 那组护栏等于没验。已同步锚点，并**新增一组
  ④b**：把白名单退回 v4.1.57 当时的"只补 2400"改法，专门钉住"治标不治本"这条。
- 现在 9 组破坏全部判红，还原后两文件 sha256 一致。

【实机验证的正面结论】
- 海克斯表 594 条真读到；3176 名字已清空、稀有度(kGold)/图标完好；
  官方英文名与中文名均未被误伤。
- 真实 21 局战绩里 50 个不同海克斯 id，**查不到名字 0 个、查不到稀有度 0 个**。
- 队列表 28 条切片：正向 13 全纳入，负向 11 个（SR 全随机/峡谷大乱斗/无限乱斗/
  3240）全部正确排除。

【新增文件】
- tools/verify_v490_live.py —— 连真客户端的三项只读验证（客户端没开时明确 SKIP，不假通过）
- tools/refresh_queue_fixture.py —— 按判据重生成队列夹具
"""


def main():
    with io.open(VERSION_JSON, encoding='utf-8') as f:
        data = json.load(f)
    notes = data.get('notes') or ''
    if MARK in notes:
        print('已含追加段，跳过')
        return 0
    # 追加段插在 v4.9.0 那段的**末尾**（即下一个版本标题之前）
    idx = notes.find('\nv4.8.1')
    if idx < 0:
        print('FAIL 找不到插入点（v4.8.1 标题）')
        return 1
    data['notes'] = notes[:idx] + '\n' + ADD + notes[idx:]
    with io.open(VERSION_JSON, 'w', encoding='utf-8', newline='') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    with io.open(VERSION_JSON, encoding='utf-8') as f:
        back = json.load(f)
    print('version = %s' % back['version'])
    print('notes 总长 = %d' % len(back['notes']))
    print('含追加段 = %s' % (MARK in back['notes']))
    print('追加段在 v4.8.1 之前 = %s'
          % (back['notes'].find(MARK) < back['notes'].find('v4.8.1')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
