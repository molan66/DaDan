# -*- coding: utf-8 -*-
"""更新 version.json 的 4.9.1 条目为 4.9.2（含真机反证），并同步 updater.py。"""
import json
import os
import re

PROJ = r'D:\AI\大蛋小助手\dadan-build'
VJSON = os.path.join(PROJ, 'version.json')

NOTE = """v4.9.2（修「关闭后弹 Failed to remove temporary directory」，含一次错误修复的纠正）
- 你截图那个灰色 Warning 框，确认是本程序的：exe 里就有
  `Failed to remove temporary directory: %s`（PyInstaller onefile bootloader 原文），
  目录 `_MEI000052182` 的创建时间 22:42:30 也正是这个 exe 首次启动的时刻。
  它不是功能报错，是退出时删不掉临时目录。

【根因（实测，不是推断）】
- 目录里最后剩 `VCRUNTIME140.dll` + `VCRUNTIME140_1.dll`。
  用 Restart Manager 查占用者，报 **PID 20084 icreateLol.exe**（英雄联盟的进程）；
  对同一份 DLL 的新副本查则是空 —— 所以不是误报，是真被 LoL 映射着。
- 被映像映射的 DLL，`DeleteFileW` 恒返回 ERROR_ACCESS_DENIED(5)，
  **用 runas 提权后删除照样 FAIL**（已实测）。所以提权解决不了，别在这上面花时间。

【这轮怎么修的】
- 退出时：① 把自己的 `_MEIPASS` 改名为 `_MEIxxxxx_QUAR`；
  ② **在原路径重建一个空目录**。
  bootloader 收尾时删原路径，看到空目录 -> 删干净 -> 静默退出，不再弹框。
  被锁的文件留在 `_QUAR` 里，由下次启动的清理逻辑收掉。

【⚠️ 这里有一次自我纠正，值得记下来】
- 最初只做了第 ①步（改名），并用 console 模式的最小探针验证，看到
  "exit code 0 + 无窗口" 就以为修好了。**那是假阳性。**
- 装到真机上一测：Warning 框**照样弹**，弹框里写的还是原路径 `_MEI000065942`。
- 用窗口模式(console=False，与真实产物一致)做三组对照，才看清：
    none（不处理）        → 卡在模态框
    rename（只改名）      → 卡在模态框  ← 我的第一版无效
    rename + 重建空目录   → exit 0，干净退出
  根因：bootloader 是按**原路径**去删的，改名只让它"找不到"，同样算失败；
  必须是"原路径还在、且已是空目录"才算删干净。
- 教训：console 模式的探针看不到窗口模式的弹框；判据应看 **exit code**，
  且必须**先自证前提**（文件真被锁住），否则对照组不复现，任何"成功"都不可信。

【顺带修掉一个从 v4.6.6 起就一直没生效的缺陷】
- 清理逻辑用 `os.open(fp, O_RDWR)` 判断"目录是否被占用"。提权实例创建的 DLL
  只给读权限(RX)，`O_RDWR` 必然 PermissionError -> 判"在用" -> **永久跳过**。
  实测：文件 DACL 设成 RX-only（删掉 OWNER RIGHTS）后 `os.open(O_RDWR)` 报
  PermissionError，而同一个文件 `os.remove` **成功** —— 说明它测的是写权限，
  不是占用。现改为只看 Win32 错误码：**32(SHARING_VIOLATION)=真占用**（跳过），
  **5(ACCESS_DENIED)=只是没写权**（照常清理）。

【试过但放弃的方案（留给下一个人，别再走一遍）】
- ❌ 提权删除：runas 后仍 ACCESS_DENIED（文件被映射，与权限无关）。
- ❌ MoveFileEx(MOVEFILE_DELAY_UNTIL_REBOOT)：中完整性下 FAIL 5；提权后
  `PendingFileRenameOperations` 也不接受已映射文件。
- ❌ 只改名不移内容：见上面的自我纠正，无效。
- ❌ runtime_tmpdir 挪解压目录：相对路径以**工作目录**为基准（换启动方式就跑偏），
  绝对路径会把打包机目录硬编进 exe。都不可移植。
- ❌ "目录改名成功 = 没人在用"：被映射的 dll 只锁文件、不锁父目录，不可靠。

【验证】
- `tools/probe_runtime_dir_in_use.py`：4 用例（空目录 / RX-only 可删 / 真被映射 /
  `_QUAR` 能被清理），修前 2 红，修后 4 绿。
- `tools/probe_runtime_dir_in_use_teeth.py`：把缺陷逐个放回（退回 O_RDWR、把错误码 5
  当占用、去掉空目录短路、不认 `_MEI` 前缀、漏掉重建空目录），**5 项全部判红**，
  源码 sha256 复原一致。
- `tools/verify_quarantine_exit.py`：窗口模式 onefile 三组对照，含前提自证，
  只认 exit code。结果：卡住 / 卡住 / exit0。
- `tools/verify_quarantine_unit.py`：改名+重建、幂等、目标占用、非 frozen 四例。
- 既有 184 个单元测试全绿，无回归。
"""


def main():
    with open(VJSON, encoding='utf-8') as f:
        data = json.load(f)
    old = data.get('version')
    data['version'] = '4.9.2'
    # 4.9.1 的说明一并保留在后面（那是同一轮工作的前半段，有纠正过程）
    data['notes'] = NOTE + '\n' + (data.get('notes') or '')
    with open(VJSON, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print('version.json: %s -> 4.9.2' % old)

    upd = os.path.join(PROJ, 'core', 'updater.py')
    with open(upd, encoding='utf-8') as f:
        src = f.read()
    new_src, n = re.subn(r"^(APP_VERSION\s*=\s*)['\"][^'\"]+['\"]",
                         r"\g<1>'4.9.2'", src, count=1, flags=re.M)
    if n != 1:
        print('updater.py APP_VERSION 替换失败')
        return
    with open(upd, 'w', encoding='utf-8', newline='') as f:
        f.write(new_src)
    m = re.search(r"^APP_VERSION\s*=\s*['\"]([^'\"]+)['\"]", new_src, re.M)
    print('updater.py APP_VERSION =', m.group(1))


if __name__ == '__main__':
    main()
