# -*- coding: utf-8 -*-
r"""sync_to_github.py —— 把本地 commits 推到 GitHub（走 Git Data API 绕开被阻断的 443）。

## 为什么不能用 git push

本机 **github.com:443 的 TLS 被 SNI 层阻断**（实测 7 个官方 IP 全部
`curl (28) timed out`），而 `git push` 走的就是 `https://github.com/...`，
所以必然报 `Recv failure: Connection was reset`。

但 **api.github.com 完全可达**（实测 200），所以改走 Git Data API：
    POST /git/blobs      上传文件内容（增量：只传本 commit 改过的）
    POST /git/trees      建树（带 base_tree，只提交变更项）
    POST /git/commits    建 commit（复用原 author/committer/message/日期）
    PATCH /git/refs/...  移动分支指针

## 关键实现细节（踩过的坑，别改）

1. **message 必须用 rstrip 掉尾部换行**再重建 commit 对象。
   用 `git show --format=%B` 拿到后 `.strip()` 会让 sha 和远端不一致 ——
   因为 GitHub 存的 commit 对象里 message 末尾**没有**换行。
   本脚本用 `git cat-file commit` 直接读原始字节，只替换 parent 行，
   保证逐字节可复现。
2. **必须用完整 40 位 SHA** 调 `/git/commits/{sha}`，短 SHA 返回 404。
3. 推送完成后要把本地 main 对齐到远端 SHA，否则下次推送会被判
   non-fast-forward（远端有本地没有的 commit）。脚本自动做。

## 用法

    python tools/sync_to_github.py --dry-run     # 只看要推什么
    python tools/sync_to_github.py               # 真推
    python tools/sync_to_github.py --force-ref   # 远端分支已有别人的提交时强制覆盖

token 从系统 git 凭据管理器取（`git credential fill`），不写盘、不打印。
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

API = 'https://api.github.com'
OWNER_REPO = 'molan66/DaDan'
#: 本脚本在 <项目>/dadan-build/tools/ 下，git 仓库在同级的 <项目>/git。
#: 往上是 tools -> dadan-build -> 项目根，再拼 'git'。
DEFAULT_REPO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), 'git')


def git(repo, *args, inp=None):
    r = subprocess.run(['git', '-C', repo] + list(args),
                       capture_output=True, input=inp)
    if r.returncode != 0:
        raise SystemExit('git %s 失败: %s'
                         % (' '.join(args), r.stderr.decode('utf-8', 'replace')))
    return r.stdout


def git_text(repo, *args):
    return git(repo, *args).decode('utf-8', 'replace').strip()


def http(method, path, token, body=None):
    data = json.dumps(body).encode('utf-8') if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Accept', 'application/vnd.github+json')
    req.add_header('User-Agent', 'dadan-sync')
    if data:
        req.add_header('Content-Type', 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            raw = r.read().decode('utf-8', 'replace')
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise SystemExit('%s %s -> HTTP %s\n%s'
                         % (method, path, e.code,
                            e.read().decode('utf-8', 'replace')))


def get_token():
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    r = subprocess.run(['git', 'credential', 'fill'],
                       input='protocol=https\nhost=github.com\n\n',
                       capture_output=True, text=True, env=env)
    for line in r.stdout.splitlines():
        if line.startswith('password='):
            return line.split('=', 1)[1]
    raise SystemExit('取不到 token —— 检查 git credential 是否配了 GitHub 凭据')


def commit_raw(repo, sha):
    """读 commit 原始字节并拆成 (header_lines, message_bytes)。"""
    raw = git(repo, 'cat-file', 'commit', sha)
    i = raw.find(b'\n\n')
    return raw[:i].split(b'\n'), raw[i + 2:]


def changed_files(repo, sha):
    """本 commit 相对第一父的改动 [(status, path)]。"""
    parents = git_text(repo, 'rev-list', '--parents', '-n', '1', sha).split()[1:]
    if not parents:
        out = git(repo, 'ls-tree', '-r', '-z', '--name-only', sha)
        return [('A', p) for p in out.decode('utf-8', 'replace').split('\0') if p]
    out = git(repo, 'diff-tree', '-r', '-z', '--no-commit-id',
               '--name-status', parents[0], sha).decode('utf-8', 'replace')
    items = [x for x in out.split('\0')]
    res, i = [], 0
    while i < len(items):
        st = items[i].strip()
        if not st:
            i += 1
            continue
        if st.startswith(('R', 'C')):
            res.append((st[0], items[i + 2] if i + 2 < len(items) else ''))
            i += 3
        else:
            res.append((st[0], items[i + 1] if i + 1 < len(items) else ''))
            i += 2
    return [(s, p) for s, p in res if p]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default=DEFAULT_REPO)
    ap.add_argument('--branch', default='main')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--force-ref', action='store_true',
                    help='强制移动远端分支（远端有别人的提交时才需要）')
    a = ap.parse_args()

    token = get_token()
    repo = a.repo
    if not os.path.isdir(os.path.join(repo, '.git')):
        raise SystemExit('不是 git 仓库: %s' % repo)

    ref = http('GET', '/repos/%s/git/ref/heads/%s' % (OWNER_REPO, a.branch), token)
    remote_sha = ref['object']['sha']
    remote_commit = http('GET', '/repos/%s/git/commits/%s'
                         % (OWNER_REPO, remote_sha), token)
    print('远端 %s = %s' % (a.branch, remote_sha[:8]))

    commits = [c for c in git_text(repo, 'rev-list', '--reverse',
                                   '%s..HEAD' % remote_sha).splitlines() if c]
    if not commits:
        print('已是最新，无需推送。')
        return 0
    print('待推送 %d 个 commit：' % len(commits))
    for c in commits:
        title = git_text(repo, 'show', '-s', '--format=%s', c)
        print('  %s %s' % (c[:8], title))

    if a.dry_run:
        print('\n[dry-run] 不做写操作。')
        return 0

    parent = remote_sha
    tree = remote_commit['tree']['sha']
    for idx, c in enumerate(commits, 1):
        title = git_text(repo, 'show', '-s', '--format=%s', c)
        print('\n[%d/%d] %s  %s' % (idx, len(commits), c[:8], title))

        entries = []
        for st, path in changed_files(repo, c):
            if st == 'D':
                entries.append({'path': path, 'mode': '100644',
                                'type': 'blob', 'sha': None})
                continue
            meta = git_text(repo, 'ls-tree', c, '--', path)
            if not meta:
                continue
            head = meta.partition('\t')[0]
            mode, typ, bsha = head.split()
            if typ != 'blob':
                continue
            blob = git(repo, 'cat-file', 'blob', bsha)
            r = http('POST', '/repos/%s/git/blobs' % OWNER_REPO, token, {
                'content': base64.b64encode(blob).decode('ascii'),
                'encoding': 'base64',
            })
            entries.append({'path': path, 'mode': mode, 'type': 'blob',
                            'sha': r['sha']})

        if entries:
            r = http('POST', '/repos/%s/git/trees' % OWNER_REPO, token,
                     {'base_tree': tree, 'tree': entries})
            tree = r['sha']
            print('  tree -> %s (%d 项)' % (tree[:8], len(entries)))

        # 用原始字节重建 commit，只替换 parent/tree 行 —— 避开 message
        # 尾部换行被代码编辑器/格式化吃掉导致 sha 漂移的问题。
        header, message = commit_raw(repo, c)
        lines = [b'parent ' + parent.encode()
                 if ln.startswith(b'parent ') else ln for ln in header]
        lines = [b'tree ' + tree.encode()
                 if ln.startswith(b'tree ') else ln for ln in lines]
        body = b'\n'.join(lines) + b'\n\n' + message.rstrip(b'\n')
        # 关键坑：GitHub 存的 commit 对象里 **message 末尾没有换行**。
        # 所以重建本地对象时必须 rstrip 掉尾部换行 —— 带换行算出的 sha 与
        # 远端不一致，会连锁导致「本地缺对象 -> reset 跳过 -> 下次推送被判
        # non-fast-forward」。实测：带换行不匹配，rstrip 后逐字节命中。
        local_obj = git(repo, 'hash-object', '-w', '-t', 'commit',
                        '--stdin', inp=body).decode().strip()
        r = http('POST', '/repos/%s/git/commits' % OWNER_REPO, token, {
            'message': message.decode('utf-8', 'replace').rstrip('\n'),
            'tree': tree,
            'parents': [parent],
            'author': _ident(repo, c, 'A'),
            'committer': _ident(repo, c, 'C'),
        })
        parent = r['sha']
        if local_obj == parent:
            print('  commit -> %s  (本地对象一致)' % parent[:8])
        else:
            # 不静默：说明本地重建与远端序列化有差异，后续 reset 会失败。
            print('  commit -> %s  [WARN] 本地重建为 %s（不一致）'
                  % (parent[:8], local_obj[:8]))

    print('\n移动 refs/heads/%s -> %s' % (a.branch, parent[:8]))
    http('PATCH', '/repos/%s/git/refs/heads/%s' % (OWNER_REPO, a.branch), token, {
        'sha': parent, 'force': bool(a.force_ref),
    })

    # 本地对齐远端 sha，避免下次被判 non-fast-forward
    print('本地对齐远端 sha ...')
    has_obj = subprocess.run(['git', '-C', repo, 'cat-file', '-e', parent],
                             capture_output=True).returncode == 0
    if has_obj:
        subprocess.run(['git', '-C', repo, 'reset', '--hard', parent],
                       check=False, capture_output=True)
        subprocess.run(['git', '-C', repo, 'update-ref',
                        'refs/remotes/origin/%s' % a.branch, parent],
                       check=False, capture_output=True)
        print('  已对齐（reset --hard + origin/%s）' % a.branch)
    else:
        print('  [WARN] 本地缺该对象，跳过 reset。下次推送前先跑：')
        print('     git fetch 或手动把 main 指到远端 sha')

    print('\n[OK] 推送完成: https://github.com/%s' % OWNER_REPO)
    return 0


def _ident(repo, sha, which):
    fmt = '%an%n%ae%n%aI' if which == 'A' else '%cn%n%ce%n%cI'
    out = git_text(repo, 'show', '-s', '--format=' + fmt, sha).split('\n')
    return {'name': out[0], 'email': out[1], 'date': out[2]}


if __name__ == '__main__':
    sys.exit(main())
