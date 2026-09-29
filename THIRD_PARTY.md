# 第三方组件与许可证

本文件列出本项目直接与间接依赖的许可证信息。所有版本号取自  
`requirements.txt` / `requirements-build.txt`，许可证与版权行均来自各项目  
PyPI 元数据或源码仓库中的许可证文件（未做推断，证据 URL 见每行）。

本项目整体以 **AGPL-3.0-or-later** 发布（见 [LICENSE](LICENSE)）。下列依赖与本项目  
共同分发时，各自的许可证义务仍然适用；其中 **PyQt5 为 GPL-3.0（非 LGPL）**，  
是唯一会约束"闭源分发"的依赖，详见下文说明。

## 运行时依赖

| 包                  | 版本        | SPDX            | 许可证全名                                   | 版权行                                                              |
| ------------------ | --------- | --------------- | --------------------------------------- | ---------------------------------------------------------------- |
| PyQt5              | 5.15.11   | `GPL-3.0-only`  | GNU General Public License v3.0         | Riverbank Computing Limited `<info@riverbankcomputing.com>`      |
| PyQt5-Qt5          | 5.15.2    | `LGPL-3.0-only` | GNU Lesser General Public License v3.0  | The Qt Toolkit is Copyright (C) 2017 The Qt Company Ltd.         |
| PyQt5_sip          | 12.19.0   | `BSD-2-Clause`  | BSD 2-Clause "Simplified" License       | Copyright (c) 2025 Phil Thompson `<phil@riverbankcomputing.com>` |
| psutil             | 7.2.2     | `BSD-3-Clause`  | BSD 3-Clause "New" or "Revised" License | Copyright (c) 2009, Jay Loden, Dave Daeschler, Giampaolo Rodola  |
| requests           | 2.34.2    | `Apache-2.0`    | Apache License 2.0                      | Requests — Copyright 2019 Kenneth Reitz                          |
| websocket-client   | 1.9.2     | `Apache-2.0`    | Apache License 2.0                      | Copyright 2026 engn33r                                           |
| urllib3            | 2.8.0     | `MIT`           | MIT License                             | Copyright (c) 2008-2020 Andrey Petrov and contributors           |
| certifi            | 2026.7.22 | `MPL-2.0`       | Mozilla Public License 2.0              | （上游许可证文件无版权行）                                                    |
| idna               | 3.20      | `BSD-3-Clause`  | BSD 3-Clause "New" or "Revised" License | Copyright (c) 2013-2026, Kim Davies and contributors             |
| charset-normalizer | 3.5.1     | `MIT`           | MIT License                             | Copyright (c) 2025 TAHRI Ahmed R.                                |

## 打包期依赖（仅自建 exe 时需要，不随源码分发）

| 包                         | 版本     | SPDX                                                      | 说明                                                                                                                                   |
| ------------------------- | ------ | --------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| pyinstaller               | 6.22.3 | `GPL-2.0-or-later` + Bootloader Exception                 | 版权：Copyright (c) 2010-2023 PyInstaller Development Team；Copyright (c) 2005-2009 Giovanni Bajo；基于 McMillan Enterprises, Inc.（2002）的工作 |
| pyinstaller-hooks-contrib | 2026.7 | `GPL-2.0-or-later`（标准 hooks）/ `Apache-2.0`（runtime hooks） | 项目级版权行：上游许可证文件仅含 FSF 文本版权                                                                                                            |

### PyInstaller Bootloader Exception（逐字引用）

许可证文件 `COPYING.txt` 中的例外条款原文：

> In addition to the permissions in the GNU General Public License, the authors give  
> you **unlimited permission to link or embed compiled bootloader and related files into  
> combinations with other programs, and to distribute those combinations without any  
> restriction coming from the use of those files.** (The General Public License  
> restrictions do apply in other respects; for example, they cover modification of the  
> files, and distribution when not linked into a combined executable.)

即：用 PyInstaller 打包并分发程序（包括闭源/商业程序）不受 GPL 传染。  
SPDX 目前**没有**为此外例定义标准标识符，故上表标注为"`GPL-2.0-or-later` + Bootloader Exception"  
并以本段原文为准。

### pyinstaller-hooks-contrib 的双许可

其 `LICENSE` 文件按目录区分：

- 标准 hooks 与其它文件 → `GPL-2.0-or-later`；
- `_pyinstaller_hooks_contrib/rthooks` 下的 runtime hooks（会被打包进可执行文件）→ `Apache-2.0`。

**实际随 exe 分发的是 runtime hooks，即 Apache-2.0 那一部分。**

## 关于 PyQt5（重要）

PyQt5 采用**双许可**：GNU GPL v3 或 Riverbank 商业许可。Riverbank 官方明确写道：

> PyQt is dual licensed on all supported platforms under the GNU GPL v3 and the  
> Riverbank Commercial License. **Unlike Qt, PyQt is not available under the LGPL.**  
> — <https://www.riverbankcomputing.com/software/pyqt/>

对本项目的影响：

- 本项目以 **AGPL-3.0** 开源发布，与 PyQt5 的 GPL-3.0 兼容（GPLv3 第 13 条明确允许  
  GPLv3 作品与 AGPLv3 作品组合为一个整体作品），**无需购买商业许可**。
- 若有人想把本项目（或其衍生作品）**闭源分发**，则须自行取得  
  [Riverbank 商业许可](https://www.riverbankcomputing.com/commercial/buy)，  
  或改用其它许可更宽松的 Qt 绑定。

另注：PyQt5 的二进制 wheel 内含 **LGPL-3.0 的 Qt 运行库**（即上表的 `PyQt5-Qt5`）。  
该 wheel 中的 Qt 许可证文件为标准 LGPLv3 + GPLv3 全文，未包含 Qt 按模块划分的  
第三方许可清单；如需逐模块的 Qt 例外声明，请查阅 Qt 官方文档。

## 证据 URL

- PyQt5 5.15.11：<https://pypi.org/pypi/PyQt5/5.15.11/json>、<https://www.riverbankcomputing.com/software/pyqt/>
- PyQt5-Qt5 5.15.2：<https://pypi.org/pypi/PyQt5-Qt5/5.15.2/json>（wheel 内 `PyQt5_Qt5-5.15.2.dist-info/LICENSE`）
- PyQt5_sip 12.19.0：<https://pypi.org/pypi/PyQt5_sip/12.19.0/json>（sdist 内 `LICENSE`）、<https://github.com/Python-SIP/sip>
- psutil 7.2.2：<https://raw.githubusercontent.com/giampaolo/psutil/master/LICENSE>
- requests 2.34.2：<https://raw.githubusercontent.com/psf/requests/main/NOTICE>
- websocket-client 1.9.2：<https://raw.githubusercontent.com/websocket-client/websocket-client/master/setup.py>
- urllib3 2.8.0：<https://raw.githubusercontent.com/urllib3/urllib3/main/LICENSE.txt>
- certifi 2026.7.22：<https://raw.githubusercontent.com/certifi/python-certifi/master/LICENSE>
- idna 3.20：<https://raw.githubusercontent.com/kjd/idna/master/LICENSE.md>
- charset-normalizer 3.5.1：<https://raw.githubusercontent.com/jawah/charset_normalizer/master/LICENSE>
- pyinstaller 6.22.3：<https://raw.githubusercontent.com/pyinstaller/pyinstaller/develop/COPYING.txt>
- pyinstaller-hooks-contrib 2026.7：<https://raw.githubusercontent.com/pyinstaller/pyinstaller-hooks-contrib/v2026.7/LICENSE>

## 已知不确定性

1. PyQt5 与 PyQt5-Qt5 的许可证，上游仅写 "GPL v3" / "LGPL v3"，**未出现 `or later` 字样**；  
   本表按"未声明 or-later"记为 `-only`，属保守推断。
2. PyQt5 自身许可证文件内无独立版权行，"Riverbank Computing Limited" 取自其包元数据与官方网站。
3. certifi、pyinstaller-hooks-contrib 的上游许可证文件内均无项目级版权行。
4. 本项目**未**包含任何依赖的源代码副本，全部依赖由用户通过 `pip install -r` 自行安装；  
   若你重新分发打包好的 exe，请自行确认是否需要在分发物中附带上述许可证全文  
   （Apache-2.0、BSD、MIT 等均要求保留版权与许可声明）。
