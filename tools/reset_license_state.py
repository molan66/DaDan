# -*- coding: utf-8 -*-
r"""reset_license_state.py —— 把大蛋小助手的授权状态重置为「全新买家」，供实测

## 用途

你要以"第一次拿到软件的人"的身份走一遍购买流程：
    试用 → 把机器码发给卖家 → 拿到授权码 → 激活 → 验证
以及反过来测"没授权时会被拦住"。这些都需要**清掉本机已有的激活记录**。

## 它动什么、不动什么（重要）

| 文件 | 处理 | 原因 |
| --- | --- | --- |
| `license.json` | **删除** | 已激活记录。留着就测不到"未激活"状态 |
| `trial.json` | **删除** | 试用起始时间。留着试用期不重新计时 |
| `settings.json` | **默认保留** | 里面是你的偏好英雄(11个)和录制步骤(5步)，删了白丢；**测授权不需要动它** |

需要连设置一起重置（模拟真正全新装机）时加 `--include-settings`，
届时会先备份，随时可 `--restore` 撤回。

## 安全设计

· 删除前**先把要动的文件备份**到 `%APPDATA%\LOLScript\_reset_backup_<时间戳>\`
· 删除后**逐个复核目标已不存在**（不靠"命令没报错"下结论）
· `--restore` 可一键还原最近一次备份
· **绝不碰** `%USERPROFILE%\.dadan-license\`（私钥在那，是资产不是状态）
· 默认 `--dry-run` 之外必须显式 `--yes` 才真删（防手滑）

## 常用命令

    # 看当前状态（不改任何东西）
    python tools/reset_license_state.py --status

    # 预览将要删除什么
    python tools/reset_license_state.py --dry-run

    # 真重置（授权 + 试用，保留设置）
    python tools/reset_license_state.py --yes

    # 连设置一起重置（完全模拟全新装机）
    python tools/reset_license_state.py --yes --include-settings

    # 测"试用到期后被拦住"：把试用起始时间往前挪 N 天
    python tools/reset_license_state.py --simulate-days 8

    # 还原最近一次备份
    python tools/reset_license_state.py --restore
"""
import argparse
import glob
import json
import os
import shutil
import sys
import time

#: 授权状态目录（与 core/licensing.py 的 state_path 保持同一口径）
def state_dir():
    base = os.environ.get('APPDATA') or os.path.expanduser('~')
    return os.path.join(base, 'LOLScript')


LICENSE = 'license.json'
TRIAL = 'trial.json'
SETTINGS = 'settings.json'
BACKUP_PREFIX = '_reset_backup_'

#: 重置时**总是**删除的文件（授权与试用记录）
ALWAYS = (LICENSE, TRIAL)
#: 仅在 --include-settings 时删除（保护用户的偏好英雄/录制步骤）
OPTIONAL = (SETTINGS,)


def fmt_time(ts):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))


def describe(path):
    """返回该文件的一句话说明（用于列清单）。"""
    name = os.path.basename(path)
    if name == LICENSE:
        try:
            d = json.load(open(path, encoding='utf-8'))
            return f'已激活记录（到期 {d.get("expires")}，机器码 {str(d.get("machine_id"))[:16]}…）'
        except Exception:
            return '已激活记录（内容无法解析）'
    if name == TRIAL:
        try:
            d = json.load(open(path, encoding='utf-8'))
            return f'试用起始记录（开始于 {d.get("started_text")}）'
        except Exception:
            return '试用起始记录（内容无法解析）'
    if name == SETTINGS:
        try:
            d = json.load(open(path, encoding='utf-8'))
            return (f'使用设置（{len(d)} 个键；偏好英雄 '
                    f'{len(d.get("preferred_champions") or [])} 个、'
                    f'录制步骤 {len(d.get("lol_steps") or [])} 步）')
        except Exception:
            return '使用设置（内容无法解析）'
    return ''


def list_targets(include_settings):
    d = state_dir()
    names = list(ALWAYS) + (list(OPTIONAL) if include_settings else [])
    out = []
    for n in names:
        p = os.path.join(d, n)
        if os.path.exists(p):
            out.append(p)
    return out


def cmd_status():
    d = state_dir()
    print('=' * 70)
    print('  授权状态')
    print('=' * 70)
    print(f'  目录: {d}')
    print(f'  存在: {os.path.isdir(d)}')
    print()

    if not os.path.isdir(d):
        print('  （目录不存在 → 相当于全新装机，下次启动会创建）')
        return 0

    files = sorted(os.listdir(d))
    if not files:
        print('  （目录为空 → 全新装机状态）')
        return 0

    print('  文件：')
    for n in files:
        p = os.path.join(d, n)
        if os.path.isfile(p):
            print(f'    {n:22s} {os.path.getsize(p):7d} B   {fmt_time(os.path.getmtime(p))}')
            desc = describe(p)
            if desc:
                print(f'    {"":22s} └─ {desc}')
        else:
            print(f'    {n:22s} <目录>')

    print()
    print('  === 影响判定 ===')
    has_lic = os.path.exists(os.path.join(d, LICENSE))
    has_tri = os.path.exists(os.path.join(d, TRIAL))
    print(f'    license.json 存在 = {has_lic}  '
          f'→ {"当前是【已激活】状态，需删除才能测未激活流程" if has_lic else "已是未激活状态"}')
    print(f'    trial.json   存在 = {has_tri}  '
          f'→ {"试用期沿用旧记录" if has_tri else "下次启动会新建 7 天试用"}')

    backups = sorted(glob.glob(os.path.join(d, BACKUP_PREFIX + '*')))
    print()
    print(f'  已有备份: {len(backups)} 份')
    for b in backups[-3:]:
        print(f'    {os.path.basename(b)}')
    return 0


def do_backup(targets):
    """把待删文件复制到带时间戳的备份目录。返回备份目录路径（无文件则 None）。"""
    if not targets:
        return None
    stamp = time.strftime('%Y%m%d_%H%M%S')
    bdir = os.path.join(state_dir(), BACKUP_PREFIX + stamp)
    os.makedirs(bdir, exist_ok=True)
    for p in targets:
        shutil.copy2(p, os.path.join(bdir, os.path.basename(p)))
    return bdir


def cmd_reset(include_settings, assume_yes, dry_run):
    d = state_dir()
    targets = list_targets(include_settings)

    # ---- 先列清单（规则：删除前列清单）----
    print('=' * 70)
    print('  将要删除的文件' + ('（预览，不会真删）' if dry_run else ''))
    print('=' * 70)
    if not targets:
        print('  （没有需要删除的文件 —— 已经是全新状态）')
        return 0
    for p in targets:
        print(f'  · {os.path.basename(p):22s} {describe(p)}')
    if not include_settings and os.path.exists(os.path.join(d, SETTINGS)):
        print()
        print(f'  （保留 {SETTINGS} —— 你的偏好英雄与录制步骤在里面。'
              f'要一起重置加 --include-settings）')

    if dry_run:
        print()
        print('  [dry-run] 未删除任何文件。确认无误后加 --yes 执行。')
        return 0

    if not assume_yes:
        print()
        print('  未加 --yes，未执行删除（防手滑）。')
        print('  确认要删就重跑并加 --yes。')
        return 2

    # ---- 备份 ----
    bdir = do_backup(targets)
    if bdir:
        print()
        print(f'  [备份] {bdir}')
        print(f'         共 {len(targets)} 个文件；还原: --restore')

    # ---- 删除 ----
    print()
    print('  [删除]')
    for p in targets:
        try:
            os.remove(p)
            print(f'    ✓ 已删除 {os.path.basename(p)}')
        except OSError as e:
            print(f'    ✗ 删除失败 {os.path.basename(p)}: {e}')

    # ---- 复核（规则：删完复核目标不存在）----
    print()
    print('  [复核]')
    left = [p for p in targets if os.path.exists(p)]
    for p in targets:
        state = '仍然存在 ❌' if os.path.exists(p) else '已不存在 ✅'
        print(f'    {os.path.basename(p):22s} {state}')
    if left:
        print()
        print('  ⚠️ 有文件未能删除。程序可能正在运行并持有句柄 ——')
        print('     请先完全退出大蛋小助手（含托盘），再重跑本命令。')
        return 1

    print()
    print('=' * 70)
    print('  ✅ 已重置为「全新买家」状态')
    print('=' * 70)
    print()
    print('  下一步：双击启动大蛋小助手，应出现：')
    print('    · 首次启动会弹「使用须知」', end='')
    if include_settings:
        print('（因为设置也清了）')
    else:
        print('（若之前已确认过则不再弹）')
    print('    · 授权状态应为【试用中】，剩余约 7 天')
    print('    · 点「启动脚本」可以正常用（试用期内放行）')
    print()
    print('  然后测完整流程：')
    print('    1) 在软件里复制「机器码」')
    print('    2) 手机打开 dist/授权码工具.html，粘贴机器码 → 生成授权码')
    print('    3) 回到软件粘贴授权码 → 应显示「激活成功」')
    print()
    print('  测「试用到期会被拦住」：')
    print('    python tools/reset_license_state.py --simulate-days 8')
    return 0


def cmd_simulate(days):
    """把试用起始时间往前挪 N 天，用于测"试用到期"的行为。"""
    d = state_dir()
    p = os.path.join(d, TRIAL)
    now = time.time()
    new_start = now - days * 86400

    if not os.path.exists(p):
        # 没有记录时先建一个（否则 trial_state 会按"现在"新建，就测不出过期）
        os.makedirs(d, exist_ok=True)
        data = {'started_at': new_start,
                'started_text': fmt_time(new_start),
                '_note': '由 reset_license_state.py --simulate-days 写入，用于测试'}
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'  已创建 trial.json，试用起始时间设为 {days} 天前')
    else:
        with open(p, encoding='utf-8') as f:
            old = json.load(f)
        old_start = old.get('started_at')
        old['started_at'] = new_start
        old['started_text'] = fmt_time(new_start)
        old['_note'] = f'由 reset_license_state.py 调整（原为 {fmt_time(old_start) if old_start else "?"}）'
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(old, f, ensure_ascii=False, indent=2)
        print(f'  已把试用起始时间改为 {days} 天前（原 {fmt_time(old_start) if old_start else "?"}）')

    # 顺便报一下"还能不能用"
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from core import licensing as L
        ok_, remaining, _ = L.trial_state(now=now)
        print()
        print(f'  TRIAL_DAYS = {L.TRIAL_DAYS}')
        print(f'  试用是否仍有效 = {ok_}')
        print(f'  剩余天数 = {remaining if remaining is None else round(remaining, 2)}')
        if ok_:
            print('  → 仍可试用；想测"已过期被拦住"，把天数调大到超过 7')
        else:
            print('  → 试用已结束：启动软件后点「启动脚本」应弹出激活窗且不放行')
    except Exception as e:
        print(f'  （无法加载 licensing 判定：{type(e).__name__}: {e}）')
    return 0


def cmd_restore():
    d = state_dir()
    backups = sorted(glob.glob(os.path.join(d, BACKUP_PREFIX + '*')))
    if not backups:
        print('  没有找到任何备份。')
        return 1
    latest = backups[-1]
    print(f'  从备份还原: {latest}')
    n = 0
    for name in os.listdir(latest):
        src = os.path.join(latest, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(d, name))
            print(f'    ✓ 还原 {name}')
            n += 1
    print()
    print(f'  已还原 {n} 个文件。运行 --status 复核。')
    return 0


def main():
    ap = argparse.ArgumentParser(
        description='重置大蛋小助手的授权状态为「全新买家」（带备份，可还原）',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--status', action='store_true', help='只看当前状态，不改任何东西')
    ap.add_argument('--dry-run', action='store_true', help='预览将要删除的文件')
    ap.add_argument('--yes', action='store_true', help='确认执行删除（不加则不动手）')
    ap.add_argument('--include-settings', action='store_true',
                    help='连 settings.json 一起重置（会丢偏好英雄与录制步骤！）')
    ap.add_argument('--simulate-days', type=float, metavar='N',
                    help='把试用起始时间往前挪 N 天（测"试用到期"）')
    ap.add_argument('--restore', action='store_true', help='还原最近一次备份')
    a = ap.parse_args()

    if a.status:
        return cmd_status()
    if a.restore:
        return cmd_restore()
    if a.simulate_days is not None:
        return cmd_simulate(a.simulate_days)
    return cmd_reset(a.include_settings, a.yes, a.dry_run)


if __name__ == '__main__':
    sys.exit(main())
