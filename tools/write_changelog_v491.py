# -*- coding: utf-8 -*-
"""v4.9.1 变更：写入 version.json 的 notes + 追加到 updater.py 的注释区。"""
import json
import os

PROJ = r'D:\AI\大蛋小助手\dadan-build'
VJSON = os.path.join(PROJ, 'version.json')

NOTE = """v4.9.1（修「关闭后弹 Failed to remove temporary directory」）
- 你截图那个灰色 Warning 框，确认是本程序的：exe 里就有
  `Failed to remove temporary directory: %s` 这句（PyInstaller onefile
  bootloader 的原文），目录 `_MEI000052182` 的创建时间 22:42:30 也正好是
  这个 exe 首次启动的时刻。它不是功能报错，是退出时删不掉临时目录。

【根因（实测，不是推断）】
- 那个目录里最后剩 `VCRUNTIME140.dll` + `VCRUNTIME140_1.dll`。
  用 Restart Manager 查占用者，报 **PID 20084 icreateLol.exe**（英雄联盟的
  进程）；对同一份 DLL 的新副本查则是空，所以不是误报 —— 是真被 LoL 映射着。
- 被映像映射的 DLL，`DeleteFileW` 恒返回 ERROR_ACCESS_DENIED(5)，
  **用 runas 提权后删除照样 FAIL**（已实测），所以提权解决不了这个问题。
- 于是 bootloader 注定删不掉，它就把这件事变成一个模态框甩给用户，
  点「确定」才退。这就是你看到的那个框。

【这轮修了什么】
- 退出时主动把自己的 `_MEIPASS` 目录改名成 `_MEIxxxxx_QUAR`。Windows 允许
  改**已映射文件**的父目录名（实测 OK），改名后 bootloader 按原路径去找，
  发现不存在 -> 认为无需清理 -> **静默退出，不再弹框**。
  实测（独立 onefile 探针）：exit code 0、无窗口，Temp 里留下 `_MEI..._QUAR`。
  留下的 `_QUAR` 由下次启动的清理逻辑收掉。
- 顺手修掉一个**从 v4.6.6 起就存在、一直没生效**的缺陷：清理逻辑用
  `os.open(fp, O_RDWR)` 判断"目录是否被占用"，但提权实例创建的 DLL 只给
  读权限（RX），`O_RDWR` 必然报 PermissionError -> 被判"在用" -> **永久跳过**。
  实测：把文件 DACL 设成 RX-only（去掉 OWNER RIGHTS），`os.open(O_RDWR)` 报
  PermissionError，而同一个文件 `os.remove` **成功** —— 说明它测的是写权限，
  不是占用。现改为只看 Win32 错误码：**32(SHARING_VIOLATION)=真占用**，
  **5(ACCESS_DENIED)=只是没写权, 照常清理**。
- 清理逻辑同时认得 `_MEI*_QUAR`（按 `_MEI` 前缀扫描，天然覆盖）。

【试过但放弃的方案（留给下一个人，别再走一遍）】
- ❌ 提权删除：实测 runas 提权后仍然 ACCESS_DENIED（文件被映射，与权限无关）。
- ❌ MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)：中完整性下直接 FAIL 5；
  即使提权，注册表 `PendingFileRenameOperations` 也不接受已映射文件。
- ❌ runtime_tmpdir 把解压目录挪走：相对路径以**工作目录**为基准（换启动方式
  就落到意外位置），绝对路径会把打包机目录硬编进 exe。两者都不可移植。
- ❌ "目录改名成功 = 没人在用"：被映射的 dll 只锁文件、不锁父目录，
  活目录改名照样成功，不可靠。

【验证】
- `tools/probe_runtime_dir_in_use.py`：4 个用例（空目录 / RX-only 可删 /
  真被映射 / `_QUAR` 能被清理），修前 2 红，修后 4 绿。
- `tools/probe_runtime_dir_in_use_teeth.py`：把缺陷逐个放回去（退回 O_RDWR、
  把错误码 5 当占用、去掉空目录短路、不再认 `_MEI` 前缀），**4 项全部判红**，
  源码 sha256 复原一致。
- `tools/verify_quarantine_exit.py`：真打一个 onefile exe 跑退出，
  exit code 0、不弹框、留下 `_MEI*_QUAR`。
"""


def main():
    with open(VJSON, encoding='utf-8') as f:
        data = json.load(f)
    old_version = data.get('version')
    data['version'] = '4.9.1'
    data['notes'] = NOTE + '\n' + (data.get('notes') or '')
    with open(VJSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('version.json: %s -> 4.9.1' % old_version)

    upd = os.path.join(PROJ, 'core', 'updater.py')
    with open(upd, encoding='utf-8') as f:
        src = f.read()
    import re
    m = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
    print('updater.py 原 APP_VERSION =', m.group(1) if m else '(未找到)')
    if m:
        src = src[:m.start(1)] + '4.9.1' + src[m.end(1):]
        with open(upd, 'w', encoding='utf-8', newline='') as f:
            f.write(src)
    m2 = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", src, re.M)
    print('updater.py 新 APP_VERSION =', m2.group(1) if m2 else '(未找到)')


if __name__ == '__main__':
    main()
