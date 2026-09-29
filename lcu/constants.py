"""LCU 层常量集中定义

v4.4.0 修复：原先超时、BFS 预算、窗口阈值等魔数散落在 connector.py / api.py /
clicker.py 三个文件里（且多处重复），改一处容易漏改另一处。本模块只做"常量归位"，
不引入任何第三方依赖，可被 lcu/ 下任意模块安全 import（无循环依赖）。
"""

# ==================== HTTP 超时 ====================
# (连接超时, 读取超时)
# LCU 本机探活：连接极快，读取给 2s 兜底（connector.py:88-101 探活用）
TIMEOUT_PROBE = (1.5, 2.0)
# LCU 常规 API：champ-select 等接口偶发卡顿，读超时放宽到 5s（api.py:_req 默认值）
TIMEOUT_API = (2.0, 5.0)
# LCU 资源下载（图片等二进制，体积大）
TIMEOUT_ASSET = (2.0, 6.0)

# ==================== 子进程 / 第三方 HTTP 超时 ====================
# wmic / powershell 进程查询（系统调用慢，给足 6s / 8s）
TIMEOUT_WMIC = 6
TIMEOUT_PS = 8
# 第三方 CDN（communitydragon / gtimg / ddragon），非 loopback
TIMEOUT_CDN_CDRAGON = 8
TIMEOUT_CDN_GTIMG = 5

# ==================== 全盘 lockfile 搜索上限 ====================
# 全盘 BFS 的目录访问预算（防止机械盘上无限扫描拖死启动）
BFS_BUDGET = 60000
# 全盘 BFS 最大深度
BFS_MAX_DEPTH = 5
# _walk_find_lockfile / 日志扫描的最大遍历深度（固定根目录下的有限遍历）
WALK_MAX_DEPTH = 5

# ==================== 客户端主窗口识别阈值 ====================
# 小于该尺寸的窗口不是客户端主窗口（登录小窗/隐形窗口/WebView 子窗）
MIN_WINDOW_W = 300
MIN_WINDOW_H = 200

# ==================== REST 重试（P1-2）====================
# v4.4.0 修复：LCU 在客户端刚启动/切局时偶发 502/503/504（网关还没就绪），
# 单次请求直接失败会让上层把整轮流程判为失败。只对幂等方法重试，绝不重试 POST。
RETRY_TOTAL = 2
RETRY_BACKOFF = 0.2
RETRY_STATUS_FORCELIST = (502, 503, 504)
RETRY_ALLOWED_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})

# ==================== 连接健康度（P1-2）====================
# 连续失败达到该值即把 api.is_healthy 置 False，供上层（如重连定时器）观察
HEALTH_FAIL_THRESHOLD = 3

# ==================== 自动选英雄试错（P1-3 / P2-44）====================
# v4.4.2: pick_champion 逐候选 2~4 次 REST + sleep，同步跑在 1s 轮询线程上。
# 不加限制时 170 个偏好最坏 ≈85s 会拖死轮询/心跳。候选截断到 PICK_TRY_MAX，
# 并给整体加 PICK_TRY_DEADLINE_S 总时限，超时即返回 None 交给下轮重试。
PICK_TRY_MAX = 12
PICK_TRY_DEADLINE_S = 8.0

# ==================== 输入注入节奏（P2）====================
# v4.4.0 修复：原先这 5 个 sleep 以字面量内联在 clicker.py 的注入函数里，
# 调"点卡手感"时必须逐处找、容易漏改。集中到此便于统一调整。
# 单位均为秒。数值本身是 v31/v32 实测定下来的，不要随意改小（CEF 需要
# 一个渲染帧的时间才能把 hover 转成 hover 状态，太短会点空）。
CLICK_RESTORE_SLEEP_S = 0.05    # set_foreground: ShowWindow(SW_RESTORE) 后等窗口恢复动画
CURSOR_DOWN_SLEEP_S = 0.03      # _cursor_click: SetCursorPos 之后、按下之前
CURSOR_UP_SLEEP_S = 0.04        # _cursor_click: 按下之后、抬起之前
POSTMSG_HOVER_SLEEP_S = 0.02    # _postmsg_click: WM_MOUSEMOVE 与 WM_LBUTTONDOWN 之间
POSTMSG_UP_SLEEP_S = 0.04       # _postmsg_click: down 与 up 之间
