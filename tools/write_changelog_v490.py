# -*- coding: utf-8 -*-
"""写入 v4.9.0 的 changelog 与版本号（version.json + core/updater.py）。

为什么用脚本而不是手改：
    · version.json 的 notes 是**一个巨大的字符串**（2.6 万字符），手改极易破坏转义；
    · 两处版本号必须同步（本机硬约束第 10 条），一处漏改会让"关于"页与文件不一致；
    · 控制台是 GBK，PowerShell 内联中文会坏，必须用 Python + 显式 utf-8。

幂等：重复跑不会重复插入（按 v4.9.0 前缀判断）。
"""
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DADAN = os.path.dirname(ROOT)
VERSION_JSON = os.path.join(DADAN, 'version.json')
UPDATER = os.path.join(DADAN, 'core', 'updater.py')

NEW_VER = '4.9.0'

NOTES = """v4.9.0（对齐 LOL 16.21：海克斯表 + 队列白名单，以及一处注释锈死）
- 你报的「LOL 更新了，海克斯有变动」，我实测对齐了一遍。**结论：海克斯数据侧
  不用改代码，能正确读新表。** 但顺手查出 3 个问题，都是这轮修掉的。

【先说好消息：数据侧是好的】
- 客户端现在是 16.21.8299102（上一版 16.20）。cherry-augments.json
  554 → **594 条，新增 40、删除 0**，稀有度仍是 kSilver/kGold/kPrismatic/kEventChoice。
- 拿**你真打的对局**验的：最近 21 局（全是 queueId=2400 海克斯大乱斗）、
  10 人 × 1~6 号位共 **756 次**海克斯引用、**175 个不同 id，全部命中新表，0 个查不到**。
  594 个图标也全部能取到。所以「更新后海克斯图标/名字丢一片」并没有发生。
- 版本对齐逻辑自己也生效了，日志里有：
  `[详情/海克斯] 游戏版本 16.20 → 16.21, 已清理旧图标 99 张`。
- 顺带核了其他项：召唤师技能 39 条不变；英雄 173 个、本地缓存 0 缺失；
  队列表 420 → 422（+4 条云顶"自然之力 恭喜发财 BETA"，-2 条空名条目）；
  战绩 playerAugment1..6 结构不变（实测每人最多 5 个）。

【修正一：一条海克斯名字是未本地化的内部标识符】
- 16.21 新增的 40 条里，id=3176 的 nameTRA 不是中文，而是
  `GoH_PentakillAug_Generic_BlankName`（它的 augmentNameId 是
  `GoH_PentakillAug_Generic`）—— 官方漏了这条的翻译。
- **先说实话：这条目前根本没显示到界面上。** 详情窗只画图标（只读
  augment_rarity），`augment_names` 算出来之后全仓无人读取。所以它不是线上可见缺陷。
- 那为什么还修：名字已经被 `_do_fetch_game_detail` 算出来了，只要以后有人在图标上
  加个 tooltip、或者把名字写进详情窗（v4.3.2 的注释本意就是这样），这串下划线
  就会当场怼到用户脸上。这类"实现了但暂时没接线"的东西，正是本机历史上反复复发的那一类。
- 做法：lcu/api 新增 `_clean_augment_name()`，在**读出口**清洗（表本身不动）。
  判据两条：与 augmentNameId 相同/以其开头，或"纯 ASCII 且含下划线"。
  返回**空串**而不是编个假名字 —— 我们不知道它中文叫什么，编一个比不显示更糟。
- 判据为什么要写这么细：官方英文名（ADAPt / escAPADe / BONK!）**没有下划线**，
  所以"纯 ASCII 就清"会误伤一大片。测试里专门钉了这条反面约束。

【修正二（根因级）：ARAM 队列白名单是手写常量，游戏每加个变体就漏一次】
- 点卡兜底（自动选英雄）只在 ARAM 队列启用，判据是 script_core.ARAM_QUEUE_IDS。
  v4.1.57 就因为白名单只有 {450, 3270}、漏了最常玩的 2400 出过事（"没自动选"）。
  当时的修法是**补上 2400 这一个 id** —— 根因没动。16.21 把海克斯大乱斗扩到
  2400/2401/2403/2405/2410/2450，于是又漏 5 个。
- 现在权威来源换成**客户端自己**的 queues.json，判据是两条可核对的结构特征：
      gameSelectModeGroup == 'kARAM'  且  pickMode == 'AllRandomPickStrategy'
  （kARAM = 嚎哭深渊那条线；AllRandom = 全随机发牌，所以才有"2-3 张卡"给你点。）
  手写常量降级为**离线兜底**，两者取并集。以后游戏再加变体，自动纳入。
- 三个容易写坏的地方都做了防护并有测试：
  · 推导失败 → 回落到手写集合，**绝不能返回空集**（否则点卡兜底整个消失，
    表现和没修一样，还更难查）；
  · 空表**不缓存**（客户端未就绪时表可能为空，缓存住就整个会话都用旧集合）；
  · 判据**不能只看 AllRandom** —— 那会把召唤师峡谷全随机(3120)、无限乱斗(900~)
    也拉进来，而那些界面不是 2-3 张卡布局，点上去会**在别的界面上乱点**。
- 明确**没做**的一件事：3240（海克斯大乱斗，但分组是 kAlternativeLeagueGameModes）
  形状像大乱斗，但**没有实测证据**证明它的选人界面真是 2-3 张卡布局。把没验证过的
  队列塞进白名单，风险是乱点 —— 比"不自动选"严重得多。所以探针把它报成**待实测**，
  由有实机的人决定。同理 3280/62~65 也没加。
- 3270 保留（已在白名单里用很久、无报障），并在 EXTRA_ALLOWED 里写明了保留理由，
  免得下一个人不知道它为什么在、出问题也不敢删。

【修正三：注释里的死数字】
- lcu/api、ui/icons、ui/styles 里散着 "374 个里有 344 个是纯白蒙版" 这类**具体数字**。
  数字当时是对的，但 16.21 已经是 594 条 / 527 白蒙版。过期注释不会报错，却会让
  下一个改代码的人**按错的分布去推断**。
- 做法：能改成不依赖版本的表述就改（"绝大多数"+"按版本变，别猜"），并在旁边
  留下当前实测值和"判据只认图本身、不认 rarity"这个真正的结论。
- **发现并纠正了一处旧说法**：v4.6.1 起代码注释一直写"自带颜色的 30 个(含
  kEventChoice 全部)"。实测 16.21：kEventChoice 共 25 条，其中 **5 条也是白蒙版**
  （340/341/342/355/364）。也就是说"kEventChoice 全部自带颜色"这个前提**已经不成立**，
  幸好判据一直是逐图判断的，所以没有实际影响 —— 但注释必须改，否则下一个人会照着它写错逻辑。
- core/updater.py 里那段是**历史 changelog**，记的是"当时那一刻"的实测值。改它等于
  篡改历史（下一版的人就看不出这数字是哪个版本说的），所以只加"历史值，勿外推"标注，
  并让探针**显式豁免并打印**——不是偷偷跳过。

【测试与护栏（这轮的重点：证明护栏真的有牙）】
- 新增 9 个测试（tests/test_regressions.py 的 TestAugmentNameFallback /
  TestAramQueueCoverage），175 → **184 passed**。
- 新增 5 个探针：
  · tools/probe_aram_queue_coverage.py —— 用客户端 queues.json 校验白名单
  · tools/probe_augment_name_fallback.py —— 校验没有内部标识符漏出
  · tools/probe_stale_comment_counts.py —— 校验注释里的条数还成立
  · tools/probe_v490_teeth.py —— **故意把 8 种缺陷放回去**，确认测试会判红
  · tools/probe_stale_comment_teeth.py —— 同上，针对注释探针
- `probe_v490_teeth.py` 实测 **8 组破坏全部判红**，跑完逐字节还原、两个源文件
  sha256 与实验前一致。破坏项包括："名字兜底整个删掉""判据退化成纯 ASCII 就清
  （会误伤 ADAPt）""清洗没接进真实建表链路""白名单退回手写三件套""客户端推导不接"
  "判据放宽成只看 AllRandom""推导失败返回空集""空结果被缓存"。

【过程中我自己犯的错，都记下来免得下次再犯】
1. 第一版探针把被测函数整个替换掉（`api._load_augment_table = lambda: ...`），
   于是测的是探针自己 —— 修复前判红、修复后**照样判红**（假红）。后来改成只替换
   网络层 get()，让真实的建表+清洗链路跑起来。
2. 第一版注释探针按「海克斯/augment/剪影/纯白」过滤行，结果 ui/icons.py:308 那行
   （写的是"白蒙版"，一个关键词都不含）被整行跳过 → 把旧数字放回去探针**照样通过**。
   这是"护栏变装饰"的典型。现在判据改成"这一行在声称白蒙版的数量"，不再靠碰词。
3. 第一版数字正则用 `{2,4}` 位，把注释里的队列 id 链 `2400/2401/2403/...`
   当成了"子集/总数"，误报一堆。现在限 3 位并加边界，宁可少报也不报假。
4. 用 PowerShell 内联命令做"改文件再还原"的破坏实验时，中文被 GBK 控制台编码坏掉
   （文件里出现 3F 问号），替换静默不生效。破坏性实验必须用 Python + 显式 utf-8。

【没改的东西】
- 海克斯图标染色逻辑一行没动 —— 它本来就是逐图判断，16.21 的白蒙版比例从约九成
  涨到 88.7%，这套逻辑反而更重要了。
- 战绩详情窗没动（那条未本地化名字既然不显示，就不为它加界面改动）。
"""


def main():
    # ---------- updater.py：版本号 ----------
    with io.open(UPDATER, encoding='utf-8') as f:
        src = f.read()
    m = re.search(r"^APP_VERSION = '([0-9.]+)'", src, re.M)
    if not m:
        print('FAIL 在 updater.py 里找不到 APP_VERSION')
        return 1
    old_ver = m.group(1)
    if old_ver != NEW_VER:
        src = src[:m.start()] + "APP_VERSION = '%s'" % NEW_VER + src[m.end():]
        with io.open(UPDATER, 'w', encoding='utf-8', newline='') as f:
            f.write(src)
        print('updater.py: APP_VERSION %s -> %s' % (old_ver, NEW_VER))
    else:
        print('updater.py: APP_VERSION 已是 %s（跳过）' % NEW_VER)

    # ---------- version.json：版本号 + notes ----------
    with io.open(VERSION_JSON, encoding='utf-8') as f:
        data = json.load(f)
    data['version'] = NEW_VER
    notes = data.get('notes') or ''
    if notes.startswith('v%s' % NEW_VER):
        print('version.json: notes 已含 v%s（跳过）' % NEW_VER)
    else:
        data['notes'] = NOTES + notes
        print('version.json: notes 已插入 v%s（+%d 字符）'
              % (NEW_VER, len(NOTES)))
    with io.open(VERSION_JSON, 'w', encoding='utf-8', newline='') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # ---------- 回读校验 ----------
    with io.open(VERSION_JSON, encoding='utf-8') as f:
        back = json.load(f)
    with io.open(UPDATER, encoding='utf-8') as f:
        usrc = f.read()
    uv = re.search(r"^APP_VERSION = '([0-9.]+)'", usrc, re.M).group(1)
    print()
    print('version.json version = %s' % back['version'])
    print('updater APP_VERSION  = %s' % uv)
    print('notes 开头           = %s' % back['notes'][:28].replace('\n', ' '))
    print('notes 总长           = %d' % len(back['notes']))
    if back['version'] != NEW_VER or uv != NEW_VER:
        print('\nFAIL 两处版本号不一致')
        return 1
    print('\nPASS 两处版本号一致（%s）' % NEW_VER)
    return 0


if __name__ == '__main__':
    sys.exit(main())
