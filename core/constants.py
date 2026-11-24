"""constants.py —— 脚本核心的时序/节流常量(v4.4.0)。

为什么单独一个文件
------------------
这些数字原本散落在 core/script_core.py 各处的 `time.sleep(...)` 与比较式里。
调一个(比如把换英雄节流从 0.5s 放宽到 1s)得在几种"魔法数字"里翻找, 而且
"日志去重间隔 8s"和"重试上限 15 次"这类语义完全不同的数字长得一样, 极易改串。
集中在这里, 每个常量带一句"它控制什么、改了会怎样"。

⚠️ 只放 core/script_core.py 用得到的常量。main.py / lcu/ / ui/ 的时序常量
   不在本文件范围(各自的负责人自行集中)。
⚠️ 本模块是纯常量, 零 import —— 不引入任何第三方依赖, 也不引入 Qt。
"""

# ---- 主循环节奏 ----
# 未连接时的重连间隔。"未连接"包含"客户端没启动/LCU 端口还没写出来",
# 3s 兼顾"客户端刚起来时尽快接上"与"没开客户端时别把日志刷满"。
RECONNECT_INTERVAL_S = 3
# 轮询异常后的退避(LCU 假死/请求超时会走到这里, 避免异常时热循环)。
POLL_ERROR_SLEEP_S = 2
# 正常轮询间隔。选人倒计时、接受状态都靠它兜底, 不宜再放大。
POLL_INTERVAL_S = 1

# ---- 选人节流 ----
# 自动选英雄的全局节流。点卡兜底一轮最坏约 5s(12 个坐标 × 0.25s 读回),
# 3s 保证不会打爆 LCU, 又能在选人那几十秒里试足够多轮。
PICK_THROTTLE_S = 3.0
# 手动换英雄节流(与 _do_auto_pick 成功后写的 _last_swap 共用同一语义)。
SWAP_THROTTLE_S = 0.5
# 点击卡片后, 等客户端把 championId 写回 session 的读回等待。
CLICK_READBACK_SLEEP_S = 0.25
# 预选命中后补 POST complete, 再读回确认"是否已 locked"前的等待。
COMPLETE_READBACK_SLEEP_S = 0.2
# v4.4.2(P1-2): 点卡内层扫描的总时限。12 个候选点最坏分钟级, 全程持 _swap_lock
# 且阻塞 poll 线程 —— 超过即放弃本轮, 交给下个 PICK_THROTTLE_S 周期重试。
CLICK_PICK_DEADLINE_S = 8.0
# "点卡未锁定 / 偏好未中"这类提示的日志去重间隔(否则 1s 轮询会刷屏)。
NO_ACCEPT_LOG_INTERVAL_S = 8.0

# ---- 自动接受 ----
# 接受延迟上限(sona 模式保护): 再大就可能错过 ReadyCheck 的确认窗口。
ACCEPT_DELAY_MAX_S = 15
# _random_delay 的抖动下限: 至少 1 秒, 避免退化成"几乎立即接受"。
RANDOM_DELAY_MIN_S = 1
# 抖动幅度下限(base 的一半, 但不小于 0.5s), 防 base 很小时抖动为 0。
RANDOM_DELAY_MIN_HALF_S = 0.5

# ---- 停止/全停 ----
# v5.0.0(#1): stop() 等待工作线程退出的最长时长(秒)。这些线程都是 daemon,
# 即便超时主进程退出也会带走它们, 这里只是给一个"尽量同步"的上限, 不让 UI 卡死。
STOP_JOIN_S = 3.0
# v5.0.0(#2): bench_swap 读回未确认(返回 None)后的冷却。此前每 1s 轮询都会
# 重新 POST 同一个 swap(日志实测连打 8~10 次), 这里在冷却期内不再重复提交。
SWAP_UNCONFIRMED_RETRY_S = 3.0

# ---- 自动返回 ----
# v4.9.12: 原先这里的 PLAY_AGAIN_RETRY_MAX(15) / PLAY_AGAIN_RETRY_INTERVAL_S(1)
# 已**删除**。它们只为 `_do_play_again` 里那段"playAgain 后循环抢开队列"服务,
# 而那段与 `_handle_lobby` 的匹配倒计时重复抢开队列(日志实测: `匹配启动（第1次）`
# 与 `开始匹配` 紧邻 3 次, 夹 54 次"非房主"噪音), 还绕过用户设置的匹配延迟。
# 重排现在只由 `_handle_lobby` → `_do_queue` 一个写入点发起, 不再需要重试上限。

# ---- 倒计时线程 ----
# 倒计时每秒一跳(状态栏 "⏳ xx 倒计时 N..." 的刷新节奏)。
COUNTDOWN_TICK_S = 1
