# NOTICE — 第三方作品归属、修改声明与作者构成

本文件是本项目对第三方作品的归属声明、修改声明与作者构成声明。项目整体以  
**GNU Affero General Public License v3.0（AGPL-3.0）** 发布，许可证全文见 [LICENSE](LICENSE)。

---

## 1. sona —— 功能设计与实现参考

- 上游项目：**sona**
- 上游仓库：<https://github.com/WJZ-P/sona>
- 上游许可证：**GNU Affero General Public License v3.0（AGPL-3.0）**
- 上游技术栈：Pengu Loader 插件（JavaScript / TypeScript，运行于客户端 CEF 环境内）

### 修改声明（依据 AGPL-3.0 §5(a)）

> 本项目 **基于 sona 的功能设计与部分实现细节进行了改写与移植**，并做了修改。  
> 修改日期：2026-09-29。

具体而言：

- 本项目在**功能设计层面**参考了 sona 的自动化流程划分（自动接受对局、大乱斗  
  备选席换英雄、对局结束自动返回房间等），并为其中若干功能编写了 Python 实现。
- 本项目在**部分实现细节**上与 sona 存在对应关系，包括常量表的取值、若干处中文  
  注释的表述，以及少量局部逻辑的分支结构。
- 本项目**并非** sona 的逐行翻译，也**没有**复制其 DOM 注入、CEF 环境或  
  Pengu Loader 相关的任何代码。

完整的差异对照与逐项分析见 [docs/PROVENANCE.md](docs/PROVENANCE.md)。

### 许可声明（依据 AGPL-3.0 §5(b)）

本项目整体以 **AGPL-3.0** 授权。任何对本项目的修改与再分发，均须遵守 AGPL-3.0  
的全部条款，包括但不限于：向接收者提供完整对应源码（§6）、保留本声明与许可证  
（§5）、不得附加任何进一步限制（§10）。

---

## 2. 英雄联盟素材与元数据

- 英雄中文名、别名与拼音索引基于官方公开数据整理（Riot Games / 腾讯官方公布  
  的英雄名称与别名）。
- 英雄头像、技能图标、装备图标、符文图标等在**程序运行时**从官方 CDN 按需下载  
  （Data Dragon 与国服官方 CDN），**不随本仓库分发**。
- 这些素材的著作权归 Riot Games, Inc. 及其关联公司所有。本仓库中的代码仅以  
  运行时引用的方式使用其公开地址，不对素材本身主张任何权利。

---

## 3. 商标声明

League of Legends、英雄联盟、Riot Games 及相关标志是 Riot Games, Inc. 的商标或  
注册商标。腾讯及相关标志是腾讯公司的商标或注册商标。

> [大蛋小助手] isn't endorsed by Riot Games and doesn't reflect the views or  
> opinions of Riot Games or anyone officially involved in producing or managing  
> Riot Games properties. Riot Games, and all associated properties are  
> trademarks or registered trademarks of Riot Games, Inc.

本项目的名称与图标均为独立创作，与上述公司无任何隶属或背书关系。

---

## 4. Python 第三方库

本项目依赖的 Python 第三方库的许可证与版权归属，见  
[THIRD\_PARTY.md](THIRD_PARTY.md)。

---

## 5. 作者构成 —— 本项目完全由 AI 编写

本项目的全部代码、界面与文档**均由 AI 生成**，不是在人类逐行手写的基础上修改而来。

- 生成环境：**DeepSeek Harness**（简称 DSH）—— 一个可自主读写文件、执行命令、  
  运行测试并迭代修复的编码 Agent 运行环境。
- 使用模型：**`deepseek-v4.1-flash`**。
- 人类参与的部分：提出需求、描述期望行为、运行程序并反馈现象、在若干方案之间  
  拍板取舍（例如许可证选择、功能取舍、是否保留某项实现）。

也就是说，人类是**需求方与验收方**，AI 是**实现方**。

### 为什么要把这件事写在显眼处

这不是免责技巧，而是给使用者一个准确的质量预期：

- **没有人类工程师逐行审阅过全部代码。** 代码能编译、能运行、主要功能经实测可用，  
  但这不等于每一处边界条件都经过人工推敲。
- **应把它当作一个可用的工具来评估，而不是一份经过人工反复打磨的工程作品。**  
  若用于任何严肃或高风险场景，请自行完成充分的安全审计与测试。
- 正因如此，本项目以 [LICENSE](LICENSE) 中的**「不提供任何担保」**条款发布。

同时，本节也**不会**减少本项目对第三方作品的义务：对 sona 的归属与修改声明（第 1 节）  
以及 AGPL-3.0 的全部条款，同样完整适用。
