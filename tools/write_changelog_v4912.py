# -*- coding: utf-8 -*-
r"""v4.9.12 变更：写入 version.json 的 notes + 追加到 updater.py 的注释区。

为什么单独一个脚本（沿用 write_changelog_v490/v491/v492 的做法）:
    version.json 的 notes 是 3.7 万字符的大字符串，手改极易破坏转义 / 缩进；
    而且版本号两处必须同步（version.json 与 core/updater.py 的 APP_VERSION）。
    用脚本做，并把「写入后重读校验」打出来，避免"以为改了"。

本次修的内容：自动返回/自动匹配两条流程互相打断（详见 NOTE）。
"""
import json
import os
import re

PROJ = r'D:\AI\大蛋小助手\dadan-build'
VJSON = os.path.join(PROJ, 'version.json')
UPDATER = os.path.join(PROJ, 'core', 'updater.py')
NEW_VER = '4.9.12'

NOTE = """v4.9.12（修「返回房间」把刚起的匹配打回大厅 + 两个倒计时交错刷）
- 现象（用户贴的日志，对应 dist/data/logs/app.log 尾部）：
      [Core] 进入房间，5秒后开始匹配      ← 大厅流程启动「匹配倒计时」
      [Core] 返回房间                    ← 旧的「返回倒计时」到点，又 playAgain 一次
      ⏳ 匹配倒计时 5/4 → 阶段变化: 匹配中 → [Core] 匹配启动（第1次）
      ⏳ 匹配倒计时 3/2/1 → 阶段变化: 确认对局 → ⏳ 接受倒计时 1...
  两个计时器交替刷，中间还夹一次多余的 playAgain。

【取证（8115 行实跑日志统计，不是推断）】
- 89 次进入大厅里，**30 次**在进"匹配中"之前又打了一行 `[Core] 返回房间`
  → 返回倒计时迟到，把刚起的大厅流程倒带。
- 进入大厅后仍继续刷 `返回倒计时` tick：**149 行**。
- 阶段已离开大厅仍在刷 `匹配倒计时` tick：**25 行**（多在"确认对局"阶段）。
- `[Core] 开始匹配` 与 `[Core] 匹配启动（第1次）` 紧邻（±5 行）出现 **3 次**
  → 两条通道同时开队列；日志里另有 **54 次**「非房主，等待房主匹配」噪音。

【根因（三处，互相独立）】
① `_do_play_again` 没有阶段判断，只看倒计时有没有到点。回到大厅时
   `_apply_phase_side_effects` 只复位了 `_return_pending`（挡"下一次进入结算"），
   **挡不住已经在跑的那个倒计时线程** → 阶段已回 Lobby，倒计时到点照样 playAgain。
② 阶段迁移点没有作废在途倒计时：进大厅不作废 return、离开大厅不作废 queue。
   `_do_queue` 里虽有阶段判断兜住了"动作"，但状态栏照样一路刷到 1。
③ `_do_play_again` 自己重试 15 次 start_matchmaking，而回到大厅后
   `_handle_lobby` 又按 `auto_queue_delay` 再开一次 → 抢开队列，且**绕过用户
   设置的匹配延迟**（用户设 5 秒，实际生效的是重试循环里那个 1 秒）。

【这轮改了什么】
- 【A 阶段守卫】`_do_play_again` 开头判当前阶段，只有
  `EndOfGame / WaitingForStats / PreEndOfGame` 才 playAgain；否则打印
  `跳过返回房间(阶段已变为 X, 返回流程已完成)` 并**复位 `_return_pending`**
  （不复位的话，这次被挡掉会让本局后续自动返回永久失效 —— 与 v4.3.11 的
  playAgain 失败分支同一个道理）。
- 【B 倒计时收口】`_apply_phase_side_effects` 里：
  进入 `Lobby/None` → `_bump_countdown('return')`（返回流程已完成）；
  离开 `Lobby` → `_bump_countdown('queue')`（匹配倒计时已无意义）。
  两处都走既有的「每功能独立取消计数器」机制，不新增并行状态。
- 【C 单一写入点】删掉 `_do_play_again` 里那段 15 次 start_matchmaking 重试。
  重排现在**只由** `_handle_lobby → _do_queue` 发起，队列归属登记
  (`_queue_started_by_us`) 也在那里完成，用户的匹配延迟真正生效。
  连带删除已无人使用的常量 `PLAY_AGAIN_RETRY_MAX / PLAY_AGAIN_RETRY_INTERVAL_S`。

【验证（都实跑过，不是推断）】
- 新增 `tests/test_regressions.py::TestAutoReturnPhaseGuard` 6 条：
  大厅阶段不许 playAgain / 结算三阶段必须照常返回 / 返回后不许抢开队列 /
  回大厅作废 return 倒计时 / 离开大厅作废 queue 倒计时 /
  **阶段未变时倒计时必须照常到点**（防"收口把功能改死"）。
- `tools/probe_autoreturn_teeth.py`：把四个缺陷逐个放回去（删守卫条件、删两处
  `_bump_countdown`、playAgain 后重新抢开队列），**4 项全部判红**，
  源码 sha256 与实验前一致。
- 全量回归 **205 个用例全过**。

【试过但放弃 / 踩到的坑（留给下一个人）】
- ❌ 只在 `_do_play_again` 里加 `if phase != 'Lobby': return`：挡不住
  `Matchmaking/ReadyCheck` 阶段的迟到 playAgain，且没解决倒计时乱刷。
- ⚠️ 探针第一版**假绿**：新测试里起了 30 秒倒计时却只等 0.2 秒（tick 压到 20ms
  时 30 秒仍需 0.6 秒），"没触发"是因为还没到点，不是因为被取消 ——
  删掉 `_bump_countdown` 的两项破坏当时仍全绿。后把倒计时改成 2 秒 / 等 0.3 秒，
  四项破坏才真正判红。**这类"等待时间不足导致的假通过"值得单独记一笔。**
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
