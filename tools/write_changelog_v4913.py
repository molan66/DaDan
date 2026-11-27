# -*- coding: utf-8 -*-
r"""v4.9.13 变更：写入 version.json 的 notes + 追加到 updater.py 的注释区。

为什么单独一个脚本（沿用 write_changelog_v490/491/492/4912 的做法）:
    version.json 的 notes 是 4 万字符的大字符串，手改极易破坏转义 / 缩进；
    而且版本号两处必须同步（version.json 与 core/updater.py 的 APP_VERSION）。
    用脚本做，并把「写入后重读校验」打出来，避免"以为改了"。
"""
import json
import os
import re

PROJ = r'D:\AI\大蛋小助手\dadan-build'
VJSON = os.path.join(PROJ, 'version.json')
UPDATER = os.path.join(PROJ, 'core', 'updater.py')
NEW_VER = '4.9.13'

NOTE = """v4.9.13（新增「预输入选英雄」——小窗提前指定本局想玩的英雄）
- 用户要求（2026-11-22 原话）：「小窗选英雄时能显示备选栏英雄，有偏好时绿光；
  没偏好时只能等倒计时结束后自己点。想提前点一个、蓝光显示，脚本自动帮我切到它，
  优先级排在偏好之上，单击选中、再单击取消，主界面加开关。」
- 补充澄清（关键，决定了实现方式）：「和偏好一个逻辑」。

【为什么不做成"等倒计时结束再换"—— 实测数据说话】
- ARAM 选人 session 的 `timer.timeLeftInPhase` 在 v4.9.7 实跑日志里
  **12/12 次都是 None**（`[选人] v30 ... timer.phase=BAN_PICK timeLeft=None`）。
  任何"等倒计时剩 N 秒才动手"的实现，在这台机器上永远不会触发。
- 而偏好换英雄本来就不看倒计时：`_do_auto_pick` 由 1s 轮询驱动，备选池里
  有就换。所以「预输入」照抄同一条链路，只是把判断排在偏好**之前** ——
  用户看到的效果正是"我提前点好，池里一出现脚本就切过去"。

【这轮新增了什么】
- 【core】`_preinput_cid` / `_preinput_hit` 两个标记 + `toggle_preinput(cid)`。
  后者是唯一写入点（除阶段迁移清零）：单击标记、再单击取消、点别的直接改标记。
- 【core】`_do_preinput()` 排在 `_do_auto_pick` 的偏好判断之前，四条分支：
  ① 无预输入/开关关 → 返回 False 交回偏好（绝不拦着偏好）
  ② 当前已是它 → 记命中并 return True（本局不再被偏好换走）
  ③ 池非空但没有它 → 清标记 + 状态栏提示（否则永远等一个不会来的英雄）
  ④ 在池里且当前不是它 → `bench_swap`，成功则记命中
- 【core】`_auto_select_pass` 放行条件改为
  `auto_pick_champion or auto_preinput_champion` —— 预输入是用户的显式指令，
  即使"自动换英雄"关着也要生效。
- 【core】离开 ChampSelect 阶段清空两个标记（用户要求每局清空，不跨局沿用）。
- 【ui】`OverlayWindow.sig_preinput` 信号 + `set_preinput(cid)` 接口；
  `ChampionIcon.set_selected` 增加第三态，视觉优先级
  **预输入(蓝) > 当前选中(靛) > 偏好(绿) > 普通**。
- 【ui】`_on_click` 分流：开关开 → 只 emit 请求（不自己改视觉，等 main 回推，
  保证界面与 core 永不漂移）；开关关 → **完全退回 v4.9.12 的"单击立即换"**。
- 【ui】`_do_bench` 里"预输入的英雄已不在池中"时同帧收起蓝光（与 core 自愈口径一致）。
- 【ui/styles】`Glass.PREINPUT` 一套蓝色令牌（sky-400 #38bdf8）。刻意不用更深的蓝：
  靛 #6366f1 本身就在蓝区，两枚 52px 头像挨在一起时同色相不同明度**肉眼分不出来**。
- 【ui/main_window】设置页「自动换英雄」下方新增「预输入选英雄」开关卡片（默认开）。
- 【main】`_on_preinput_click` 接线 + `_on_bench` 每轮把 core 的预输入状态对齐到小窗
  （`set_preinput` 幂等，值没变不重绘）。

【验证（都实跑过，不是推断）】
- 新增 `tests/test_regressions.py::TestPreinputChampion` **11 条**，
  覆盖：单击标记/取消/换目标、池里有就换、预输入压过偏好、无预输入时偏好照常、
  命中后连续三轮不被偏好换走、消失即清、池空不清、离开阶段清空、开关关闭不生效、
  **不依赖 timer 字段**（防有人日后改成等倒计时）。
- `tools/probe_preinput_teeth.py`：把六个缺陷逐个放回去（删掉优先调用、命中后清标记、
  池里没有不清理、池空也清理、离开阶段不清空、toggle 不取消），**6 项全部判红**，
  源码 sha256 与实验前一致。
- 离屏 UI 冒烟：`sig_preinput` 只在开关开时发出、`swap_signal` 只在开关关时发出；
  蓝光在"同时是当前英雄"时仍优先显示；取消后复原。
- 全量回归 **216 个用例全过**。

【试过但放弃 / 踩到的坑（留给下一个人）】
- ⚠️ 探针抓到一处**假绿**：最初写"命中后清掉 `_preinput_cid`"，测试居然全绿 ——
  因为用例只跑两轮，第二轮被"当前已是它"那条分支接住（它只 return True，
  不看标记还在不在）。标记被清要到**第三轮**才暴露：cid=0 → 返回 False →
  偏好接管 → 换回偏好英雄。补第三轮后该缺陷立刻判红。
  **教训：状态机类修复，"跑几轮"本身就是测试设计的一部分。**
- ❌ 曾考虑"标记加 3 秒反悔窗口再换"：与用户"和偏好一个逻辑"的要求相悖
  （偏好没有延迟），而且会让用户觉得点了没反应。放弃。
- ❌ 曾考虑"单击立即换 + 右键预输入"并存：右键是隐藏操作，用户记不住。
  改为用开关分流（关掉开关即恢复旧的立即换）。
- 📌 命中后**故意不清** `_preinput_cid`：清了下一轮就会退回偏好逻辑把英雄换走。
  标记留着还让蓝光停在你指定的英雄上，反馈更准确。
"""


def main():
    with open(VJSON, encoding='utf-8') as f:
        data = json.load(f)
    old_version = data.get('version')
    if (data.get('notes') or '').startswith('v%s' % NEW_VER):
        print('version.json: notes 已含 v%s（跳过）' % NEW_VER)
    else:
        data['notes'] = NOTE + '\n' + (data.get('notes') or '')
    data['version'] = NEW_VER
    with open(VJSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('version.json: %s -> %s' % (old_version, NEW_VER))

    with open(UPDATER, encoding='utf-8') as f:
        src = f.read()
    m = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
    print('updater.py 原 APP_VERSION =', m.group(1) if m else '(未找到)')
    if not m:
        raise SystemExit('updater.py 里找不到 APP_VERSION，已中止')
    src = src[:m.start(1)] + NEW_VER + src[m.end(1):]
    with open(UPDATER, 'w', encoding='utf-8', newline='') as f:
        f.write(src)

    # 重读校验（不靠"以为改了"）
    with open(VJSON, encoding='utf-8') as f:
        back = json.load(f)
    with open(UPDATER, encoding='utf-8') as f:
        src2 = f.read()
    m2 = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", src2, re.M)
    print('校验 version.json version  =', back.get('version'))
    print('校验 updater.py APP_VERSION =', m2.group(1) if m2 else '(未找到)')
    print('校验 notes 开头             =', (back.get('notes') or '')[:28].replace('\n', ' '))
    print('校验 notes 总长             = %d' % len(back.get('notes') or ''))
    assert back.get('version') == NEW_VER
    assert m2 and m2.group(1) == NEW_VER


if __name__ == '__main__':
    main()
