# -*- coding: utf-8 -*-
r"""make_seller_package.py —— 给卖家（朋友）生成一份「首次使用说明」，含私钥

## 为什么单独做一个

把 `授权码工具.html` 和私钥分别丢过去，卖家容易问三件事：
    · 这 64 位是什么？粘哪里？
    · 怎么确认没粘错？
    · 手机上会不会丢？

所以这里生成一份**单页说明**（可直接发微信），把私钥和自检方法写在一起。

## ⚠️ 安全提醒（我会在输出里反复说）

· 私钥 = 印钞机。拿到它就能给任意机器出码。
· 优先**当面/语音**告知，别留在聊天记录里（微信记录会被同步、会被翻）。
· 真要走微信，建议发完让朋友存好、然后你撤回。

## 用法

    # 生成说明页（默认写到 license-server/dist/卖家首次使用说明.html）
    python tools/make_seller_package.py

    # 生成不含私钥的版本（只发说明，私钥另口头给）
    python tools/make_seller_package.py --no-key

    # 自检：验证私钥与客户端内置公钥是否匹配
    python tools/make_seller_package.py --check
"""
import argparse
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
KEY_FILE = os.path.join(os.path.expanduser('~'), '.dadan-license', 'private_key.txt')
LICENSING = os.path.join(PROJECT, 'core', 'licensing.py')
OUT_DEFAULT = os.path.join(HERE, '..', 'license-server', 'dist', '卖家首次使用说明.html')

#: 与工具 dist/授权码工具.html 的相对位置（说明页里要写清怎么找到它）
TOOL_NAME = '授权码工具.html'


def read_key():
    if not os.path.exists(KEY_FILE):
        print(f'[错误] 找不到私钥：{KEY_FILE}')
        print('       先跑：python tools/gen_keys.py')
        sys.exit(2)
    txt = open(KEY_FILE, encoding='utf-8').read()
    m = re.search(r'^\s*private\s*=\s*([0-9a-fA-F]{64})\s*$', txt, re.M)
    p = re.search(r'^\s*public\s*=\s*([0-9a-fA-F]{64})\s*$', txt, re.M)
    if not m:
        print(f'[错误] {KEY_FILE} 里没有合法的 private= 行')
        sys.exit(2)
    return m.group(1).lower(), (p.group(1).lower() if p else '')


def embedded_pub():
    """读客户端内置公钥（用于核对）。"""
    if not os.path.exists(LICENSING):
        return ''
    src = open(LICENSING, encoding='utf-8').read()
    m = re.search(r'PUBLIC_KEY_HEX = \((.*?)\n\)', src, re.S)
    return m.group(1).replace("'", '').replace(' ', '').replace('\n', '').lower() if m else ''


def derive_pub(seed_hex):
    """用私钥现算公钥（独立于文件里记的那行，能查出文件被改坏）。"""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError:
        return None
    try:
        sk = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(seed_hex))
        return sk.public_key().public_bytes_raw().hex()
    except Exception:
        return None


def cmd_check():
    seed, pub_in_file = read_key()
    print('=' * 68)
    print('  私钥自检')
    print('=' * 68)
    print(f'  私钥文件 : {KEY_FILE}')
    print(f'  私钥长度 : {len(seed)} 位十六进制（应为 64）')
    derived = derive_pub(seed)
    emb = embedded_pub()
    print()
    print(f'  文件内记录的公钥 : {pub_in_file}')
    print(f'  由私钥现算的公钥 : {derived}')
    print(f'  客户端内置的公钥 : {emb}')
    print()
    ok = True
    if derived and pub_in_file and derived != pub_in_file:
        print('  ❌ 文件里记的公钥与私钥不一致 —— 文件被改坏了？')
        ok = False
    if derived and emb and derived != emb:
        print('  ❌ 私钥与客户端内置公钥**不匹配**！')
        print('     这会导致：用它出的授权码，买家全部激活失败。')
        print('     处理：核对密钥是否配错，或重新 gen_keys.py 后重新打包客户端。')
        ok = False
    if ok:
        print('  ✅ 私钥与客户端内置公钥匹配 —— 用它出的码买家能激活')
    return 0 if ok else 1


def make_html(seed, pub, include_key):
    emb = embedded_pub()
    match = (pub and emb and pub == emb)
    key_block = ''
    if include_key:
        key_block = f'''
  <div class="keybox">
    <div class="keylabel">私钥（64 位十六进制，整段复制）</div>
    <div class="keyval" id="keyval">{seed}</div>
    <button class="btn" onclick="copyKey()">复制私钥</button>
    <div class="warn">
      ⚠️ 这就是印钞机。别转发、别截图、别存聊天记录。<br>
      存好后建议让发你的人在微信里<b>撤回</b>这条消息。
    </div>
  </div>'''
    else:
        key_block = '''
  <div class="keybox">
    <div class="keylabel">私钥未包含在本文件里</div>
    <div class="note">请向作者当面向索取那 64 位十六进制字符串，然后按下面步骤粘贴。</div>
  </div>'''

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>大蛋小助手 · 卖家首次使用说明</title>
<style>
  :root{{--pri:#6366f1;--pri2:#818cf8;--ok:#34d399;--warn:#fbbf24;--err:#f87171;
        --tx:#f1f5f9;--tx2:#c8c8d8;--mut:#9aa0b8;}}
  *{{box-sizing:border-box}}
  body{{margin:0;padding:16px 16px calc(30px + env(safe-area-inset-bottom));
       background:linear-gradient(180deg,#1e1b4b,#111827 45%,#0b0f1a);
       background-attachment:fixed;color:var(--tx);
       font:15px/1.7 -apple-system,BlinkMacSystemFont,"Microsoft YaHei UI","PingFang SC",Arial,sans-serif;
       max-width:640px;margin:0 auto}}
  h1{{font-size:19px;margin:6px 0 4px}}
  h2{{font-size:15px;margin:22px 0 8px;color:var(--pri2)}}
  .card{{background:rgba(22,24,36,.92);border:1px solid rgba(99,102,241,.28);
        border-radius:12px;padding:14px;margin:12px 0}}
  ol,ul{{padding-left:22px;margin:8px 0}}
  li{{margin:6px 0}}
  .mono{{font-family:ui-monospace,Menlo,Consolas,monospace;word-break:break-all}}
  .keybox{{background:rgba(10,12,20,.95);border:1px solid rgba(251,191,36,.4);
          border-radius:12px;padding:14px;margin:14px 0}}
  .keylabel{{font-size:13px;color:var(--warn);font-weight:700;margin-bottom:8px}}
  .keyval{{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px;
          line-height:1.8;word-break:break-all;color:#fde68a;user-select:all;-webkit-user-select:all;
          background:rgba(0,0,0,.35);border-radius:8px;padding:11px;margin-bottom:10px}}
  .warn{{font-size:12.5px;color:var(--warn);line-height:1.65;margin-top:10px}}
  .note{{font-size:13px;color:var(--mut)}}
  .ok{{color:var(--ok)}} .err{{color:var(--err)}}
  .btn{{width:100%;font:inherit;font-weight:700;padding:12px;border-radius:10px;border:none;
       background:linear-gradient(135deg,var(--pri2),var(--pri));color:#fff;cursor:pointer}}
  .step{{display:inline-flex;width:21px;height:21px;border-radius:50%;background:var(--pri);
        color:#fff;font-size:12px;font-weight:800;align-items:center;justify-content:center;
        margin-right:7px;vertical-align:middle}}
  .toast{{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);
         background:rgba(25,27,37,.98);border:1px solid rgba(255,255,255,.12);
         padding:11px 16px;border-radius:10px;font-size:13.5px;font-weight:600;
         opacity:0;transition:.18s;pointer-events:none;z-index:9}}
  .toast.show{{opacity:1;transform:translate(-50%,0)}}
  .check{{font-size:13px;padding:9px 11px;border-radius:9px;margin-top:10px}}
  .check.ok{{background:rgba(52,211,153,.1);border:1px solid rgba(52,211,153,.32);color:var(--ok)}}
  .check.err{{background:rgba(248,113,113,.1);border:1px solid rgba(248,113,113,.34);color:var(--err)}}
  footer{{font-size:11.5px;color:var(--mut);text-align:center;margin-top:18px;line-height:1.7}}
</style>
</head>
<body>

<h1>大蛋小助手 · 卖家首次使用说明</h1>
<div class="note">你负责卖，出码工具在这台手机上就能用。跟着做一遍，以后每单大约 10 秒。</div>

{key_block}

<h2>第一次：把工具存到手机</h2>
<div class="card">
  <ol>
    <li>把 <b>{TOOL_NAME}</b> 发到你自己的微信（作者会一起给你）</li>
    <li>在手机上<b>点开它</b> —— 用浏览器打开（不要用微信内置浏览器打开后长按转发）</li>
    <li>点右上角「<b>密钥</b>」→ 把上面那 64 位粘进去 → 点「保存到本机」</li>
    <li>状态栏应显示 <span class="ok">✔ 与本软件匹配</span></li>
  </ol>
  <div class="check {'ok' if match else 'err'}">
    {'✅ 这份说明里的私钥，与正式版软件内置的公钥是配对的 —— 出的码买家能激活。' if match else '⚠️ 私钥与本机内置公钥未能核对一致，请先用 python tools/make_seller_package.py --check 检查！'}
  </div>
  <div class="note" style="margin-top:10px">
    工具是<b>完全离线</b>的网页，不联网、不上传任何东西。密钥只存在你这台手机的浏览器里。
  </div>
</div>

<h2>以后每一单：出码（约 10 秒）</h2>
<div class="card">
  <ol>
    <li><span class="step">1</span>买家把 <b>机器码</b>（52 位）发给你 —— 他会在软件「激活」页点「复制机器码」</li>
    <li><span class="step">2</span>打开工具，<b>粘进第 1 个框</b>（带连字符也不用管，直接粘）</li>
    <li><span class="step">3</span>选有效期（1 年 / 永久 / 或手填天数）</li>
    <li><span class="step">4</span>点「<b>生成授权码</b>」→ 点「<b>复制授权码</b>」→ 发回给买家</li>
  </ol>
  <div class="note">
    买家粘进软件点激活，就完成了。<b>不需要联网</b>，也不用你守着。
  </div>
</div>

<h2>几个常见问题</h2>
<div class="card">
  <p><b>Q：有效期从哪天算？</b><br>
  A：从<b>买家激活那天</b>起算，不是你出码那天。所以你提前备码不会短斤少两。</p>

  <p><b>Q：买家换电脑了怎么办？</b><br>
  A：让他把<b>新电脑</b>的机器码发你，重新出一张码即可（旧码作废，不用管）。</p>

  <p><b>Q：买家说提示「授权码与当前电脑不匹配」？</b><br>
  A：说明机器码弄错了（多半是给了旧电脑的码，或抄错了）。让他重新点「复制机器码」再发一次。</p>

  <p><b>Q：换手机 / 清了浏览器数据？</b><br>
  A：重新打开工具，点「密钥」把那 64 位再粘一次。所以<b>私钥要存好</b>（建议存密码管理器）。</p>

  <p><b>Q：买家点「启动脚本」没反应？</b><br>
  A：看是不是试用期过了且没激活。正常情况有 <b>7 天试用</b>，足够你把码给他。</p>
</div>

<h2>别做的事</h2>
<div class="card">
  <ul>
    <li><b>别把私钥发给任何人</b>（包括买家、别的代理）。拿到它就能给任意电脑出码。</li>
    <li>别把私钥存微信收藏、别截图发群里。</li>
    <li>别把 <span class="mono">{TOOL_NAME}</span> 和私钥一起打包发出去 —— 工具本身不含私钥，可以发；私钥不要跟着走。</li>
    <li>手机若丢失或转手，立刻告诉作者<b>换密钥重发</b>。</li>
  </ul>
</div>

<footer>
  生成时间：{time.strftime('%Y-%m-%d %H:%M:%S')}<br>
  公钥：<span class="mono">{pub[:32]}…</span>（可公开，用于核对）
</footer>

<div class="toast" id="toast"></div>
<script>
function toast(m){{var t=document.getElementById('toast');t.textContent=m;t.className='toast show';
  setTimeout(function(){{t.className='toast';}},2200);}}
function copyKey(){{
  var el=document.getElementById('keyval');
  var txt=el?el.textContent.trim():'';
  if(navigator.clipboard&&navigator.clipboard.writeText){{
    navigator.clipboard.writeText(txt).then(function(){{toast('已复制，去工具里粘贴');}},
      function(){{selectKey();}});
  }} else {{ selectKey(); }}
}}
function selectKey(){{
  var el=document.getElementById('keyval');
  if(!el) return;
  var r=document.createRange();r.selectNodeContents(el);
  var s=window.getSelection();s.removeAllRanges();s.addRange(r);
  toast('已选中，请长按复制');
}}
</script>
</body>
</html>
'''


def main():
    ap = argparse.ArgumentParser(description='生成给卖家的首次使用说明（含私钥）')
    ap.add_argument('--no-key', action='store_true', help='不把私钥写进文件（另口头给）')
    ap.add_argument('--check', action='store_true', help='只做私钥自检')
    ap.add_argument('--out', default=OUT_DEFAULT, help='输出路径')
    a = ap.parse_args()

    if a.check:
        return cmd_check()

    seed, pub_in_file = read_key()
    derived = derive_pub(seed) or pub_in_file
    emb = embedded_pub()

    print('=' * 68)
    print('  生成卖家首次使用说明')
    print('=' * 68)
    print(f'  私钥来源   : {KEY_FILE}')
    print(f'  私钥长度   : {len(seed)}')
    print(f'  由私钥算公钥: {derived}')
    print(f'  客户端内置  : {emb}')
    if derived and emb and derived != emb:
        print()
        print('  ❌ 公钥不匹配 —— 用它出的码买家激活不了。')
        print('     先跑 --check 排查，别把这套发出去。')
        return 1
    print('  ✅ 公钥匹配')

    html = make_html(seed, derived, not a.no_key)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'w', encoding='utf-8', newline='') as f:
        f.write(html)
    print()
    print(f'  已写出: {out}')
    print(f'  大小  : {os.path.getsize(out)} 字节')
    if a.no_key:
        print('  （未含私钥 —— 请另行当面告知那 64 位）')
    else:
        print()
        print('  ⚠️ 这个文件含**私钥**，等于印钞机。')
        print('     · 优先当面/语音给，别留在聊天记录')
        print('     · 若走微信，让对方存好后你<b>撤回</b>该消息')
    return 0


if __name__ == '__main__':
    sys.exit(main())
