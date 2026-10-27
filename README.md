# 大蛋小助手

> 项目主页：<https://github.com/molan66/DaDan> —— 源码、问题反馈与更新都在该仓库。

英雄联盟客户端辅助小工具（国服 / 外服通用）。通过客户端本地接口 **LCU**  
（REST + WebSocket）与客户端通信：**不注入游戏进程、不修改游戏内存、不涉及对局内的任何操作**，  
只把客户端里的重复点击自动化。

功能：自动匹配 / 自动接受 / 自动返回 / 自动选英雄 / 自动换英雄 / 自动点赞，  
另有首页账号卡与「战绩」页（近期对局列表，点整行可看本局 10 人完整详情）、  
设置页的「清理缓存」（图标/头像/日志/英雄数据，先显示实际占用再让你选）与授权状态卡。

- 运行环境：Windows 10/11，Python 3.14（实测 3.14.7）+ PyQt5 5.15.11
- 界面：无边框毛玻璃（Windows Mica/Acrylic），侧边栏六页（首页/设置/英雄/战绩/日志/关于）
- 通信：LCU REST 与 WebSocket，端口与 token 从客户端 `lockfile` 读取
- 网络：除本机 `127.0.0.1` 外，只在需要显示英雄/物品图标时按需访问官方 CDN

> 说明：客户端未启动时，定位 lockfile 的 `find_client()` 会做一次**全盘 BFS 扫描**  
> （按 League of Legends 安装目录特征逐盘查找，最多几十秒、只在后台线程执行），  
> 连接阶段有「单飞」去重，不会并发多次扫描；一旦客户端启动并缓存了端口/token，  
> 后续连接即走缓存校验，不再重复扫盘。

## 关于授权（本仓库不含出码工具）

程序内置一套**离线授权**（机器绑定 + Ed25519 验签），源码在
`core/licensing.py` 与 `core/license_crypto.py`，激活界面在 `ui/activation_dialog.py`。

设计要点：**exe 里只有公钥，没有私钥**。私钥由作者本机保管（项目目录之外），
所以公钥公开无害 —— 没有私钥就签不出有效授权码。

因此本仓库**有意不包含**以下卖家专用工具：

| 不包含 | 原因 |
| --- | --- |
| `tools/make_license.py` | 用私钥签发授权码（出码工具） |
| `tools/gen_keys.py` | 生成密钥对 |
| `tools/reset_license_state.py` | 重置本机授权状态（调试用） |
| 离线发卡器 HTML | 内部使用的出码界面 |

由此产生一个**预期行为**：`tests/test_licensing.py` 里有 4 个用例会显示
`skipped "无法导入签发工具"`——它们验证的是出码工具的换算逻辑，缺文件即跳过，
不是失败。其余 **199 个用例应当全绿**。

自己从源码运行时会处于「未激活」状态，有 7 天试用期（`licensing.TRIAL_DAYS`，
设为 0 可关闭）。想跳过授权做本地开发，可临时把 `main.py` 里 `_license_ok()`
的调用注释掉 —— 那只影响你自己构建的这份源码，不会影响任何发布产物。

## AI 开发声明

本项目**完全由 AI 编写**：全部代码、界面与文档均出自 **DeepSeek Harness** 环境中的  
**deepseek-v4.1-flash** 模型；人类作者只负责提出需求、运行验证与拍板取舍，没有逐行手写代码。

主动写明这一点，是为了让你对代码质量、维护节奏与安全审计程度有合理预期——  
请把它当作一个能用的工具来评估，而不是一份经过人工反复打磨的工程作品。

## 自己构建

```bash
python -m pip install -r requirements.txt          # 运行时依赖（已钉精确版本）
python -m pip install -r requirements-build.txt    # 打包依赖（pyinstaller）
python -m PyInstaller --noconfirm build.spec       # 产物: dist/大蛋小助手.exe
```

> 依赖分两个文件：`requirements.txt` 是运行程序所需，`requirements-build.txt` 只在  
> 自己打包 exe 时需要。两者都用 `==` 钉死验证过的版本——UI 渲染对 Qt 小版本、  
> HTTP 行为对 urllib3 大版本都敏感，浮动下限会让不同时间重装的环境行为不一致。

> 产物目录是 PyInstaller 默认的 `dist/`（与 `build.spec` 同目录）。  
> exe 同级的 `data/` 存放运行时缓存与日志，**打包不会清理它**（`--noconfirm` 只删 exe 本身），  
> 需要清空缓存时手动删 `dist/data/`。

打包提示：`build.spec` 里已排除 Qt 冗余模块与 `cryptography`（基础包约 22MB）；  
**改动依赖后请核对 exe 体积**，明显变大通常是新依赖顺着可选项被收了进来。

## 目录结构

```
main.py              程序入口 + AppController（探活/图标缓存/授权门禁/崩溃兜底）
core/                script_core 自动化状态机、logger 日志轮转与脱敏、
                     icon_cache 四类图标缓存、cache_cleaner 清理逻辑、
                     licensing / license_crypto 授权验签、updater 版本号
lcu/                 connector 客户端发现、api REST 封装、clicker 模拟点击、launcher 启动器
ui/                  主窗/小窗/选择器/战绩页/详情页/激活窗/须知 等界面
data/                程序图标、英雄元数据；图标缓存在运行时生成
tests/               回归测试（Python unittest，零额外依赖）
docs/PROVENANCE.md   上游来源与改写范围的逐项说明
```

## 跑测试

```bash
python -m unittest tests.test_licensing tests.test_license_gate \
                   tests.test_license_card tests.test_regressions \
                   tests.test_v493_fixes
```

预期：**199 passed**（另有 4 个 skipped，见上面「关于授权」一节 —— 缺出码工具所致）。

跑 UI 相关用例需要 Qt 能创建窗口；无显示环境时设 `QT_QPA_PLATFORM=offscreen`。

> ⚠️ 本项目历史上出现过多次「逻辑对但没接线」的缺陷（比如三态指示器实现了
> connected 分支却从没人推过；授权门禁的三行代码被删掉而 73 个测试全绿）。
> 所以 `tests/test_license_gate.py` 里有一批**源码级断言**（门禁必须排在线程派发
> 之前、`self._core.start()` 全项目只能有一个调用点），它们不测行为、
> 只防结构被改坏 —— 看着"不像测试"，但正是它们能挡住白送。

## 许可证

本项目以 **GNU Affero General Public License v3.0 or later（AGPL-3.0-or-later）**  
发布，完整条款见 [LICENSE](LICENSE)。

这意味着你可以自由使用、修改、再分发本程序，但：

- 分发时必须附带完整源代码，或按 AGPL-3.0 第 6 条提供获取源代码的途径；
- 你修改后的版本必须同样以 AGPL-3.0 授权，并显著标注"已修改"及修改日期；
- 通过计算机网络提供本程序的服务时，同样需要向使用者提供源代码。

程序打包（PyInstaller）不会改变许可证，分发的 exe 同样受 AGPL-3.0 约束。

## 第三方来源与致谢

- 功能设计参考并**改写了** [sona](https://github.com/WJZ-P/sona)（AGPL-3.0，  
  Pengu Loader 插件）的若干功能实现——自动接受、大乱斗换将、对局结束返回、点赞等。  
  本项目**不是**独立实现，与本项目技术栈完全不同（Python 直连 LCU，无 DOM 注入 / CEF）。  
  逐项对应关系与修改日期见 [NOTICE.md](NOTICE.md) 与 [docs/PROVENANCE.md](docs/PROVENANCE.md)。
- 界面基于 [PyQt5](https://www.riverbankcomputing.com/software/pyqt/)（GPL-3.0）构建。
- 其它依赖及其许可证见 [THIRD\_PARTY.md](THIRD_PARTY.md)。
- 英雄中文名、别名与拼音索引基于官方公开数据整理；英雄与物品图标由程序在运行时  
  从 Data Dragon 与国服官方 CDN 获取，**不随本仓库分发**，其著作权归 Riot Games 所有。

## 免责声明

大蛋小助手 isn't endorsed by Riot Games and doesn't reflect the views or opinions of  
Riot Games or anyone officially involved in producing or managing Riot Games properties.  
Riot Games, and all associated properties are trademarks or registered trademarks of  
Riot Games, Inc.

- 本程序为非官方第三方工具，与 Riot Games、腾讯及其关联公司**无任何关系**。
- 本程序不修改游戏内存、不注入客户端进程、不参与对局内的任何操作，只与客户端  
  本地接口通信；但这**不构成合规保证**——使用自动化工具仍可能违反游戏用户协议，  
  并可能导致账号受到处罚。
- 请自行评估风险使用，作者不对任何后果负责。

## 版权

Copyright © 2026 大蛋小助手 contributors

本程序是自由软件，在 AGPL-3.0 条款下发布；**不提供任何担保**，包括但不限于  
适销性或特定用途适用性。详见 [LICENSE](LICENSE)。
