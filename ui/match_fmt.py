"""对局展示格式化 —— 战绩列表(ui/main_window)与对局详情(ui/recent_detail)共用。

单独抽一个模块的原因: 详情弹窗需要同一套 queueId/位置/时间 的中文映射, 而它
不能 import ui.main_window(会形成环形导入)。本模块**只依赖标准库** ——
不要在模块级 import 任何 UI 模块。
"""
import datetime

# queueId → 中文队列名(缺失时回退 gameMode 英文→中文)
QUEUE_CN = {
    420: '单双排位', 440: '灵活排位', 400: '普通匹配', 430: '匹配模式',
    450: '极地大乱斗', 2400: '海克斯大乱斗', 1700: '斗魂竞技场',
    900: '无限火力', 1020: '无限乱斗', 1300: '极限闪击', 1400: '终极魔典',
    1900: '无限火力', 700: '魔典终章', 720: '克隆大作战', 2000: '极限闪击',
}

GAME_MODE_CN = {
    'CLASSIC': '经典', 'ARAM': '大乱斗', 'URF': '无限火力',
    'NEXUSBLITZ': '极限闪击', 'ONEFORALL': '克隆大作战',
    'TUTORIAL': '新手教程', 'PRACTICETOOL': '训练模式',
}

LANE_CN = {'TOP': '上单', 'JUNGLE': '打野', 'MIDDLE': '中单',
           'BOTTOM': '下路', 'UTILITY': '辅助'}

# v4.4.0 修复(P2 硬编码): LCU gameflow phase → 主界面中文阶段名。
# 原来这张表写死在 main_window.MainWindow._do_phase 里, 与"对局展示格式化"
# 同一类事情(英文协议字段 → 中文文案), 按项目约定集中到本模块。
PHASE_CN = {
    'None': '就绪', 'Lobby': '房间中', 'Matchmaking': '匹配中...',
    'ReadyCheck': '🎯 已找到对局!', 'ChampSelect': '英雄选择中',
    'InProgress': '游戏中', 'EndOfGame': '对局结束', 'WaitingForStats': '等待统计',
    'PreEndOfGame': '即将结束',
}

# fallback 也用中文兜底, 主界面不允许漏英文 (Riot 后续可能增加 phase)
PHASE_UNKNOWN = '未知状态'


def phase_text(phase):
    """LCU phase → 中文阶段名(未知 phase 回退 PHASE_UNKNOWN)。"""
    return PHASE_CN.get(phase, PHASE_UNKNOWN)


def game_mode_text(game):
    """队列名优先(2400=海克斯大乱斗等), 缺失时回退 gameMode 英文→中文"""
    qid = 0
    try:
        qid = int(game.get('queue_id') or 0)
    except (TypeError, ValueError):
        qid = 0
    t = QUEUE_CN.get(qid)
    if t:
        return t
    gm = str(game.get('mode') or '').upper()
    return GAME_MODE_CN.get(gm, gm or '未知模式')


def dur_text(sec):
    """对局时长 → '23分12秒'(缺失返回空串)"""
    try:
        sec = int(sec or 0)
    except (TypeError, ValueError):
        sec = 0
    if sec <= 0:
        return ''
    return f'{sec // 60}分{sec % 60:02d}秒'


def time_text(ms):
    """开局时间戳(ms) → '今天 21:03' / '昨天 20:41' / '09-12 19:22'"""
    try:
        ms = int(ms or 0)
    except (TypeError, ValueError):
        ms = 0
    if ms <= 0:
        return ''
    try:
        dt = datetime.datetime.fromtimestamp(ms / 1000.0)
    except (OSError, OverflowError, ValueError):
        return ''
    now = datetime.datetime.now()
    delta_days = (now.date() - dt.date()).days
    if delta_days == 0:
        return f'今天 {dt:%H:%M}'
    if delta_days == 1:
        return f'昨天 {dt:%H:%M}'
    return f'{dt:%m-%d %H:%M}'


def lane_text(lane):
    """位置英文 → 中文(未知返回空串, 大乱斗没有位置)"""
    return LANE_CN.get(str(lane or '').upper(), '')


def kda_text(k, d, a):
    """'4 / 7 / 24'; 任一缺失给占位"""
    if k is None or d is None or a is None:
        return '— / — / —'
    return f'{k} / {d} / {a}'


def kda_ratio(k, d, a):
    """KDA 比 = (击杀+助攻)/max(1,死亡) —— 国服习惯的 'KDA' 数字"""
    try:
        k = int(k or 0)
        a = int(a or 0)
        d = int(d or 0)
    except (TypeError, ValueError):
        return ''
    return f'{(k + a) / max(1, d):.1f}'


def num_text(n, dash='—'):
    """整数 → '12,345'(缺失/0 给占位)"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return dash
    if n <= 0:
        return dash
    return f'{n:,}'


def wan_text(n, dash='—'):
    """大数字紧凑显示: 12345 → '1.2万'(经济/输出列用, 避免撑宽表格)"""
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        return dash
    if n <= 0:
        return dash
    if n >= 10000:
        return f'{n / 10000:.1f}万'
    return str(n)


def rate_text(kills, assists, team_kills):
    """参团率 = (击杀+助攻)/全队击杀; 队内没击杀时返回空串"""
    try:
        k = int(kills or 0)
        a = int(assists or 0)
        tk = int(team_kills or 0)
    except (TypeError, ValueError):
        return ''
    if tk <= 0:
        return ''
    return f'{min(100, round((k + a) * 100 / tk))}%'
