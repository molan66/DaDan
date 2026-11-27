# -*- coding: utf-8 -*-
"""验证授权入口测试"有牙"：故意破坏，测试必须判红。

对应真实缺陷链：
  · v4.7.0 机器码只在弹窗里 → 用户看不到（"没看到7天试用和获取机器码按钮"）
  · v4.7.1 放首页 → 挤走右下角按钮、位置不合理
  · v4.7.3 落设置页（当前形态）
所以本探针对三件事都做破坏实验：入口消失 / 错误位置 / 提权文案退化。
"""
import io, os, re, subprocess, sys

P = r'D:\AI\大蛋小助手\dadan-build'
MW = os.path.join(P, 'ui', 'main_window.py')
MAIN = os.path.join(P, 'main.py')
PY = sys.executable
RESULTS = []


def run():
    r = subprocess.run([PY, '-X', 'utf8', '-m', 'unittest', 'tests.test_license_card'],
                       cwd=P, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    return r.returncode, (r.stdout or '') + (r.stderr or '')


def clear_pycache():
    for root, dirs, files in os.walk(P):
        if os.path.basename(root) in ('node_modules', '.wrangler', 'build', 'dist'):
            dirs[:] = []
            continue
        if os.path.basename(root) == '__pycache__':
            for f in files:
                if f.endswith('.pyc'):
                    try:
                        os.remove(os.path.join(root, f))
                    except OSError:
                        pass


def patch(path, pairs):
    """按 (old,new) 就地改文件，返回原文。"""
    orig = io.open(path, encoding='utf-8').read()
    txt = orig
    for old, new in pairs:
        if old not in txt:
            raise RuntimeError(f'{os.path.basename(path)} 里找不到：{old[:60]}')
        txt = txt.replace(old, new, 1)
    io.open(path, 'w', encoding='utf-8', newline='').write(txt)
    return orig


def restore(path, orig, st):
    io.open(path, 'w', encoding='utf-8', newline='').write(orig)
    os.utime(path, (st.st_atime, st.st_mtime))
    clear_pycache()


def report(step, code, expect_red=True):
    ok = (code != 0) if expect_red else (code == 0)
    print(f'  [{"PASS" if ok else "FAIL"}] {step} → 退出码 {code}')
    RESULTS.append((step, ok))
    return ok


mw_orig = io.open(MW, encoding='utf-8').read()
mw_st = os.stat(MW)
main_orig = io.open(MAIN, encoding='utf-8').read()
main_st = os.stat(MAIN)

print('=' * 70)
print('  授权入口测试"有牙"验证')
print('=' * 70)

try:
    # 1) 基线
    clear_pycache()
    c, _ = run()
    report('基线应通过', c, expect_red=False)

    # 2) 设置页不再插入授权卡（入口消失）
    patch(MW, [('        # v4.7.3: 授权/激活（用户要求放设置页，不放首页）\n'
                '        self._add_license_card(lay)\n', '')])
    clear_pycache()
    c, out = run()
    report('设置页移除授权卡 → 应判红', c)
    if c:
        print('        判红用例:', re.findall(r'^(?:FAIL|ERROR): (\S+)', out, re.M)[:3])
    restore(MW, mw_orig, mw_st)

    # 3) 把卡片又放回首页（用户明确否决过的位置）
    patch(MW, [('        cl.addLayout(status_row)\n        lay.addWidget(card)\n',
                '        cl.addLayout(status_row)\n        lay.addWidget(card)\n'
                '        self._add_license_card(lay)\n')])
    clear_pycache()
    c, out = run()
    report('授权卡又放回首页 → 应判红', c)
    if c:
        print('        判红用例:', re.findall(r'^(?:FAIL|ERROR): (\S+)', out, re.M)[:3])
    restore(MW, mw_orig, mw_st)

    # 4) 断开复制按钮
    patch(MW, [('self._lic_copy_btn.clicked.connect(self._on_copy_machine_id)',
                'pass  # 故意断开')])
    clear_pycache()
    c, _ = run()
    report('复制按钮未连接 → 应判红', c)
    restore(MW, mw_orig, mw_st)

    # 5) v4.8.0: 把自动点击的提权调用**加回** _launch_lol —— 必须判红。
    #
    # 旧版这一条是"把 reason_key 退化成含糊文案"(那时代码里还有 lol_steps 分支)。
    # 录制回放整体删除后, 对应风险变了: 真正的回归是**"启动 LOL 又开始要管理员权限"**
    # ——那意味着自动点击被接回来了(只打开登录器根本不需要提权)。
    # 锚点带上下文并断言只匹配 1 处, 避免改到别的地方还算"破坏成功"。
    _anchor = ("        self._lol_launching = True\n"
               "        print('[启动LOL] 正在启动英雄联盟登录器")
    if main_orig.count(_anchor) != 1:
        raise RuntimeError(f'探针锚点应唯一, 实得 {main_orig.count(_anchor)} 处')
    patch(MAIN, [(_anchor,
                  "        self._elevate_for_lol(action='launch')\n" + _anchor)])
    clear_pycache()
    c, out = run()
    report('启动LOL又索取管理员权限（自动点击回归）→ 应判红', c)
    if c:
        print('        判红用例:', re.findall(r'^(?:FAIL|ERROR): (\S+)', out, re.M)[:3])
    restore(MAIN, main_orig, main_st)

    # 6) 取消提权时无条件回退「自动选英雄」（会误关用户没碰过的开关）
    patch(MAIN, [("                    if reason == 'auto_select':\n"
                  "                        self._revert_auto_select_champion()\n",
                  "                    self._revert_auto_select_champion()\n")])
    clear_pycache()
    c, _ = run()
    report('取消提权误关「自动选英雄」→ 应判红', c)
    restore(MAIN, main_orig, main_st)

finally:
    restore(MW, mw_orig, mw_st)
    restore(MAIN, main_orig, main_st)

# 复原核对
print('\n[复原核对]')
same_mw = io.open(MW, encoding='utf-8').read() == mw_orig
same_main = io.open(MAIN, encoding='utf-8').read() == main_orig
print(f'  main_window.py 与原文一致 = {same_mw}')
print(f'  main.py 与原文一致        = {same_main}')
clear_pycache()
c, _ = run()
report('恢复后应通过', c, expect_red=False)

print()
print('=' * 70)
for label, ok in RESULTS:
    print(f'  {"✓" if ok else "✗"} {label}')
print('=' * 70)
allok = all(v for _, v in RESULTS) and same_mw and same_main
print('结论：' + ('授权入口测试真的有牙 ✅' if allok else '有实验组没判红 ❌'))
sys.exit(0 if allok else 1)
