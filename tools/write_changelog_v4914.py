# -*- coding: utf-8 -*-
r"""v4.9.14 变更：写入 version.json 的 notes + 追加到 updater.py 的注释区。

为什么单独一个脚本（沿用 write_changelog_v490/491/492/4912/4913 的做法）:
    version.json 的 notes 是 4.3 万字符的大字符串，手改极易破坏转义 / 缩进；
    而且版本号两处必须同步（version.json 与 core/updater.py 的 APP_VERSION）。
    用脚本做，并把「写入后重读校验」打出来，避免"以为改了"。
"""
import json
import os
import re

PROJ = r'D:\AI\大蛋小助手\dadan-build'
VJSON = os.path.join(PROJ, 'version.json')
UPDATER = os.path.join(PROJ, 'core', 'updater.py')
NEW_VER = '4.9.14'

NOTE = """v4.9.14（审计修复：预输入选英雄的线程安全 + 开关清理 + 状态收口）
- 起因：对 v4.9.13「预输入选英雄」逐行审计，修复 6 项缺陷（P1×1 / P2×2 / P3×3）。

【P1 跨线程直触 Qt 控件（最重）】
- 位置：main.py `_on_bench` 直调 `overlay.set_preinput()`。
- 根因：`_on_bench` 是 core 的 poll/WS **后台线程**回调，而旧 `set_preinput`
  直接遍历 `self._icons` 并 `setStyleSheet` —— 违反 Qt 线程模型，且与主线程
  `_do_bench` 的 `icons.pop()` 并发会抛 "dictionary changed size during
  iteration"（被 `_on_bench` 的 except 吞掉后每轮刷屏 + 蓝光更新失败）。
- 修法：`set_preinput` 改为只 emit `sig_preinput_state(int)`，新增
  `@ui_slot _do_preinput_state` 在主线程执行控件更新。与
  `update_bench_champions` 等既有调用点完全同构（QueuedConnection 自动回主线程）。
- 实测：离屏 QApplication 下从后台线程调 set_preinput，无异常、蓝光正确落在
  主线程槽里。

【P2 关闭开关不清标记】
- 位置：core `update_settings` 只 `self._settings.update(s)`；overlay 同。
- 现象：蓝光亮着时去设置里关「预输入选英雄」，标记不清，蓝光一直挂到下一局
  阶段迁移 —— 用户以为"关了没用"。
- 修法：core `update_settings` 检测 `auto_preinput_champion is False` → 清标记
  （权威源收口）；overlay `update_settings` 同帧清本地蓝光（即时兜底）。
- 顺带修：`update_settings(None)` 原先会 `dict.update(None)` 抛 TypeError，
  现在先判空。

【P2 预输入标记无锁读-比较-写】
- 位置：`toggle_preinput`（主线程）与 `_do_preinput`（poll/WS 线程）并发读写
  `_preinput_cid/_preinput_hit`，三步序列非原子。
- 修法：新增 `_preinput_lock`，`preinput_champion`/`toggle_preinput`/
  `_do_preinput` 分支②③④ 全部收口；新增 `_clear_preinput()` 作唯一清理入口
  （开关关闭 / 阶段迁移 / 自愈三处共用）。

【P3 其它】
- `toggle_preinput` 增加 cid<=0 校验（0/负数拒绝，不再亮蓝光后被自愈清掉刷噪音）；
- 删除 overlay `_do_bench` 里的本地双写入点（"已不在池中清蓝光"），统一以
  core 经信号回推为唯一视觉写入点；
- 修正 `toggle_preinput` docstring（"唯一写入点"与事实不符）。

【验证（都实跑过，不是推断）】
- 新增 3 条回归用例（test_regressions.py::TestPreinputChampion）：
  ①先标记后关开关必须清（审计 P2 护栏）
  ②toggle 拒绝 0/负数/非数字/None
  ③update_settings(None) 不崩
- `tools/probe_preinput_teeth.py` 锚点随代码重构更新，**6 项破坏全部判红**，
  源码 sha256 与实验前一致。
- 离屏 UI 冒烟：关开关 → 本地蓝光同帧收起；再开回 → 回推恢复；后台线程调
  set_preinput 无异常、蓝光落在主线程。
- 全量回归 **219 个用例全过**。
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
