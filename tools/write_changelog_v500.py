# -*- coding: utf-8 -*-
r"""v5.0.0 变更：写入 version.json 的 notes + 追加到 updater.py 的注释区。

为什么单独一个脚本（沿用 write_changelog_v49xx 的做法）:
    version.json 的 notes 是 4.4 万字符的大字符串，手改极易破坏转义 / 缩进；
    而且版本号两处必须同步（version.json 与 core/updater.py 的 APP_VERSION）。
    用脚本做，并把「写入后重读校验」打出来，避免"以为改了"。
"""
import json
import os
import re

PROJ = r'D:\AI\大蛋小助手\dadan-build'
VJSON = os.path.join(PROJ, 'version.json')
UPDATER = os.path.join(PROJ, 'core', 'updater.py')
NEW_VER = '5.0.0'

NOTE = """v5.0.0（大版本：停止全停 + 消除重复 POST + 全量审计修复）
- 用户要求四项：①停止脚本时全停、不留后台残留；②修"读回未确认时重复 POST"；
  ③对全部代码逐行审计优化；④版本号升 5.0.0。

【① 停止全停（ScriptCore 线程收口）】
- 新增 `_stop_event`(threading.Event) 取代"只置 _running 布尔"。所有长等待改
  `_stop_event.wait(短超时)`：主 _loop 的重连/轮询等待、倒计时 loop 的每秒 tick
  —— stop() 置位后立即醒来退出, 不再傻等整秒。
- 新增 `_spawn_worker(target, kwargs)` 统一派生并登记工作线程(点赞/返回/倒计时),
  线程结束自动摘除; stop() 统一 join 登记线程(上限 STOP_JOIN_S=3)。
- `_click_pick_card` 点卡内层循环加 `_stop_event.is_set()` 早退 —— 修掉"点停止后
  点卡流程又锁 3 张卡"的日志实锤事故。
- `_do_play_again` / `_do_auto_honor` 入口加停止检查, 停止后不再发起返回/点赞。
- `_ensure_stopped()` 供 start() 前置检查与 _quit() 退出收尾等待主 loop 线程。

【② 读回未确认时的重复 POST】
- 根因: `bench_swap` 返回 None(请求已发出但读回失败)后, 旧代码只写 `_last_swap`,
  但**没有任何消费方读取它**, 于是 1s 轮询每轮重新 POST 同一 swap(日志实测对同一
  英雄连打 8~10 次)。
- 修法: 新增 `_last_swap_unconfirmed_t/cid` + `_swap_unconfirmed_cooling()` +
  `_mark_swap_confirmed()`。同一英雄在 SWAP_UNCONFIRMED_RETRY_S(3s) 内不重复
  POST, 读回确认后清冷却; 预输入与偏好两条路径都接入。手动 swap_champion 的
  None 分支也同口径记冷却(三处语义一致)。

【③ 全量审计（两个只读审计子代理 + 本人复查）发现并修复】
- P1 core.stop() join 语义 × UI 线程调用点: _stop() 原在 UI 线程 _ensure_stopped,
  叠加 stop() 内 worker join 最坏冻结界面 6s。修: UI 线程只快速置位, 主 loop 的
  join 交给 start() 前置检查与 _quit() 退出路径。
- P1 websocket_listener.disconnect() join 超时后无条件置 None → 单飞守卫可被击穿
  (两条 WS 并存 → 事件双份分发 → 重复 accept/bench_swap)。修: 与 _stop_consumer
  对齐, 只有线程确实退出才置 None。
- P1 _schedule_recent_refresh 从 poll/WS 后台线程直接 QTimer.singleShot, 定时器
  绑定到无事件循环的后台线程 → 对局结束后 15/45/90s 补拉战绩是死代码。修:
  改信号 _schedule_recent_refresh_sig → 主线程槽排定时器。
- P2 停止与启动并发: _start_worker 在 core.start() 前复查 _starting, 防止用户点
  停止后, 启动流程把 _stop_event.clear() 掉又短暂拉起 loop。
- P2 settings 加载无类型规约(手改 "auto_queue":"false" 会当 True)。加载时按
  defaults 类型对 bool/int 键做规约。
- P3 _quit() 补 _ensure_stopped, 退出时让 _cleanup() 执行取消本脚本发起的队列。
- P3 overlay.update_settings(None) 判空提前(上一版只修了 core 侧)。
- P3 persist_setting 补发 settings_changed_signal, 修 UAC 取消回退后 core 侧设置
  陈旧。
- P3 手动 swap_champion None 分支记冷却(见②)。

【验证（都实跑过，不是推断）】
- 新增 TestStopAllThreads 4 条 + TestSwapUnconfirmedCooldown 4 条(共 8 条),
  全量 **227 用例全过**。
- tools/probe_v500_teeth.py: 把 5 个缺陷逐个放回去(loop 改回 sleep / 倒计时改回
  sleep / 两处删冷却守卫 / 冷却函数永不禁发), **5 项全部判红**, 源码 sha256 一致。
- 探针抓出并修正一处假绿: 首版 stop 测试用"耗时<5s"判, 破坏后 join 超时 3s 仍
  假绿; 改为断言"线程真正退出(_workers 清空 / _ensure_stopped 返回 True)"。
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
