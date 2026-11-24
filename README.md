# 大蛋小助手

英雄联盟（国服）客户端辅助小工具。通过客户端本地接口（LCU）实现自动化，
**不注入游戏、不修改内存**：自动匹配 / 自动接受 / 自动选英雄 / 自动换英雄 /
自动返回 / 自动点赞，另有首页账号卡与「战绩」页（近期对局列表，点整行可看本局
10 人完整详情）。

- 运行环境：Windows 10/11，单文件 exe，免安装
- 技术栈：Python 3.14（实测 3.14.7）+ PyQt5（无边框毛玻璃界面）+ requests/websocket-client
- 自动化依赖 LCU REST 与 WebSocket（端口与 token 从客户端 lockfile 读取）

> 说明：客户端未启动时，定位 lockfile 的 `find_client()` 会做一次**全盘 BFS 扫描**
> （按 League of Legends 安装目录特征逐盘查找，最多几十秒、只在后台线程执行），
> 连接阶段有「单飞」去重，不会并发多次扫描；一旦客户端启动并缓存了端口/token，
> 后续连接即走缓存校验，不再重复扫盘。

## 自己构建

```bash
python -m pip install -r requirements.txt          # 运行时依赖（4 个，已钉精确版本）
python -m pip install -r requirements-build.txt    # 打包依赖（pyinstaller）
python -m PyInstaller --noconfirm build.spec       # 产物: dist/大蛋小助手.exe
```

> 依赖分两个文件：`requirements.txt` 是运行程序所需，`requirements-build.txt` 只在
> 自己打包 exe 时需要。两者都用 `==` 钉死本机验证过的版本——UI 渲染对 Qt 小版本、
> HTTP 行为对 urllib3 大版本都敏感，浮动下限会让不同时间重装的环境行为不一致。

> 产物目录就是 `dadan-build/dist/`（PyInstaller 默认 distpath，与 spec 同目录）。
> exe 同级的 `data/` 存放运行时缓存与日志，**打包不会清理它**（`--noconfirm` 只删 exe 本身），
> 需要清空缓存时手动删 `dist/data/`。

打包提示：`build.spec` 里已排除 Qt 冗余模块与 `cryptography`（基础包约 22MB）；
**改动依赖后请核对 exe 体积**，明显变大通常是新依赖顺着可选项被收了进来。

## 关于「检查更新」

v4.3.8 起**已整体移除**：设置页的更新卡片、启动后的静默检查、发现新版本的弹窗
全部删除，程序不会因版本检查产生任何网络请求。

`core/updater.py` 现在只保留一个常量 `APP_VERSION`（全项目版本号唯一来源，启动
日志与侧边栏都读它）。项目根的 `version.json` 只作为更新说明与版本号来源，
被 `build.spec` 在打包时读取（写入 exe 的 Windows 版本资源），程序运行时不读取。

## 目录结构

```
main.py              程序入口 + AppController（探活/图标缓存）
core/                script_core 自动化状态机、日志、updater 版本号
lcu/                 connector 客户端发现、api REST 封装、clicker 模拟点击
ui/                  主窗/小窗/选择器/战绩页 等界面
tools/               开发期诊断脚本（不参与打包）
data/                图标缓存与英雄元数据缓存（运行时生成/更新）
```
