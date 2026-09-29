"""parsing.py —— LCU 数据的小工具函数(v4.4.0, P1-25)。

为什么单独一个文件
-----------------
"英雄表构建"这段逻辑原先在两处各写了一遍, 文字完全一致:

    main.py:892                    (AppController 的后台刷新, 还要顺带存 roles)
    core/script_core.py:252        (ScriptCore 的初始化加载)

两份一旦有一边改了过滤范围或名字兜底规则, 就会出现"设置页的英雄表"和"自动选英雄
用的英雄表"不一致 —— 这类问题极难定位(界面看得见, 逻辑却用另一份)。抽到这里后
两处共用同一份判断。

⚠️ 只放"纯函数 + 无状态"的解析辅助。需要网络/文件/线程的都不进这里。
⚠️ 零第三方依赖, 绝不 import Qt(lcu/ 层的硬约束, 见 README 的线程/分层约定)。
"""


def is_real_champion(cid) -> bool:
    """判断 championId 是否是"真实可用"的英雄。

    v17 的过滤规则: 只接受 1..1000。LCU 的 champion 列表里混着一批 6xxxx 的
    重做前幽灵英雄(客户端里选中会报错/无图标), 必须排除。

    为什么单独一个函数: 上下限是"魔法数字", 且被两处复用; 之前每次复制这段
    `if cid and 0 < cid <= 1000` 都可能漏掉 `cid is None` 的判断(None 与 0 都
    是 falsy, 但 `0 < None` 会直接抛 TypeError, 所以 `cid and` 不能省)。
    """
    if not cid:
        return False
    try:
        return 0 < int(cid) <= 1000
    except (TypeError, ValueError):
        # cid 是字符串/浮点时 int() 可能失败, 视为非法英雄而不是让调用方的
        # 整个循环炸掉(LCU 在版本更新期间偶发下发异常字段)。
        return False


def champion_display_name(champ) -> str:
    """取英雄的展示名: 优先 `name`, 缺失/字面量 'None' 时回落到 `alias`。

    为什么需要: LCU 在英雄上线初期 `name` 会是字符串 'None'(不是 null), 直接
    显示到界面上就是一排 "None"; 而 `alias`(如 "Aatrox")始终存在。
    """
    if not isinstance(champ, dict):
        return ''
    name = champ.get('name', '')
    if name and name != 'None':
        return name
    return champ.get('alias', '') or ''
