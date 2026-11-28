"""版本号 —— 与 version.json 双源，打包脚本负责强制同步。

v4.3.8: 原先的"检查更新"功能(候选源拉取 version.json + 比对 + 弹窗提示)已按用户
要求**整体移除**。用户明确表示"根本不需要检查更新的功能", 所以本模块只保留版本号:

    from core.updater import APP_VERSION

main.py 的启动日志与侧边栏版本号标签都读它, 不要再散落硬编码版本字符串。

⚠️ APP_REPO / APP_REPO_GITEE 也不再有任何代码读取, 已删除。v4.4.0 起本项目为
纯本地自用(不再上传任何仓库), 故不再保留"发布后自动提示更新"的入口; 若将来真要
做, 从这里重新长出来即可。

⚠️ v5.1(ultra M7): 历史版本 changelog(v4.4.0~v4.8.1 流水账)原嵌在本文件 18~491
行, 与 APP_VERSION 脱节且非运行时代码。已迁移到 docs/CHANGELOG.md, 这里只留版本号。
"""

# 当前版本(与 version.json 双源, 打包脚本负责强制同步; 打包/侧边栏/日志都读这里)
# ⚠️ 本文件不是全项目唯一来源 —— version.json 的 `version` 也是来源之一,
#    两处必须一致, 同步责任在打包脚本(E:\HuanJing\_installers\_build_dadan.py)。
APP_VERSION = '5.1.3'
