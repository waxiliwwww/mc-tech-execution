# MC 技术执行工具包

为已有 OpenMC 模型和结构化组合新模型提供输入检查、参数副本、命名作业、本地计算、扫描、结果图表和报告管理；也可作为标准 MCP 服务供下游调用。工具包独立运行，不绑定 Harness 或大模型。

**先打开 [HTML 使用与开发手册](docs/index.html)**。下载或解压后，用浏览器打开 `docs/index.html`；无需启动文档服务。GitHub 网页可先下载源码包再打开，HTML 源码预览不等于渲染页面。

使用者先看 [MC工具包使用说明](docs/toolkit-usage.html)：选择操作路线、理解结果和文件，再进入具体步骤。开发者看 [MC工具包架构设计](docs/toolkit.html)：简洁总图、常见场景与M1/M2/M3展开机制；接口、格式和源码细节可展开查阅，HPC设计与已有实现分别标明。

先查 [能力范围](docs/capabilities.html)：区分能生成、能接入、能修改和能解析的功能；[公开模型复现手册](docs/reproduce.html) 提供官方17×17组件和简化全堆的逐步MCP/DSH流程、输入来源、产物及数据核对。完整运行按本轮安排留待用户实测。

专家技术试用按 [专家试用清单](docs/expert-trial.html) 准备：由我们在自有Linux服务器部署，专家连接，按同一模型核对输入、计算、结果与异常；不代替合同验收。

项目执行或进展评审先看 [合同功能—中文操作—代码工具进展对照](docs/project-progress.html)：在同一张直接展开的表内，按两批15项功能列中文操作与代码名、输入输出、合同开发及验收期限、已测范围和缺口，列出实际操作与合同功能的对应关系。它是内部进展入口，不是合同验收结论。

- [CAD接入与计算](docs/cad.html)：从同一个STEP开始，转换网格、组装模型、真实计算、核对数据与图报告；包含CAD安装和MCP调用条件。
- [模型接入与建模](docs/domain-workflows.html)：带入自己的XML，或组合材料/几何/格架/源/tally经Python API生成；保留可重跑源码，按任务名找结果。标准MCP与CLI共用接口，宿主模型可按Schema组织参数；真实自然语言及微调生成验证另记。
- [近期交付总览](docs/development-plan.html)：先看要做哪几件事、当前状态和下一步；接口与测试细节放到补充说明。
- [Windows DSH接入](docs/dsh.html)：连接WSL中的MCP服务，导入插件配置，检查发现，再用代表任务调试。
- [完成一次计算](docs/compute.html)：环境 → 看输入 → 发现参数 → 创建修改副本 → 校核/计算 → 找到图和报告。每步说明目的、输入来源和预期结果。
- [运行全部官方例题](docs/official-examples.html)：固定官方清单、逐步CLI与MCP操作、所需输入和48项实际运行/读回记录；另2项按决定暂缓。
- [带入自己的模型、修改源码](docs/inputs.html)：原生 Python/XML、自动改参范围、实现职责和扩展入口。
- [把程序封装成 MCP](docs/mcp.html)：独立 Python/TypeScript 服务、同服务多个工具、参数说明与下游交接。
- [查工具参数](docs/reference.html)：与实际 MCP Schema 同源，按需搜索，无需通读。
- [环境与部署](docs/env.html)：当前构建串与实测版本、核数据位置、哪些能力来自编译选项而非代码、换机三步探测、跨环境比对的复现前提。

源码版本 0.11.2，适配 OpenMC 0.16.0；服务端使用 Linux/WSL。首次按手册准备环境，升级已有旧环境时可用 `./quickstart.sh --openmc-prefix .runtime/openmc-official` 在本包内另建完整SDK并保留旧环境；运行数据默认保存在本包 `workspace/`，图和报告返回服务端绝对路径，当前不自动下载。

工具包也可安装使用：在完整源码目录执行 `pip install -e .` 后得到 `mc-agent-tools`（工具直调，等同 `./mc.sh`）与 `mc-agent-tools-serve`（启动 MCP 服务）；依赖只认 `mcp/requirements.txt` 一处。独立wheel只包含接口代码，基础建模/计算需显式设置运行配置和工作区；官方例题、知识库和部署资产仍使用完整源码包，不能以wheel工具发现成功代替完整部署验证。服务端另提供 3 个只读资源：参数规格、官方例题清单、最近工具调用记录。源码运行与安装运行走同一套实现，测试见 `tests/`，CI 在 `.github/workflows/verify.yml`。

本包管理调用与产物；专业模型、核数据和结果判断需要业务依据。当前工程实现和未完成项见 [开发计划](docs/验收导向开发计划.md)，公开例题及接口自测不代表合同专业验收。

作业提交前检查工作区可用磁盘和输入副本大小；低于配置保留空间时拒绝新计算，不自动删除旧结果。`openmc_storage_report` 可查占用，`cleanup.sh` 默认只预览。

`./verify.sh --tools-only` 独立检查工具包，`./verify.sh` 还包含可选Harness检查，`./package.sh` 生成 `dist/` 源码部署包，`./cleanup.sh` 默认仅预览产物记录。具体条件和操作见手册，验证与打包用途不同。

Helix专项：已真实发现MCP、执行知识检索与原文读取；两条完整自然语言任务未通过。按用户决定停止追加模型预算，保留待验证；不计为宿主验收完成。配置与边界见 [MCP说明](docs/mcp.html#helix)。

DSH专项：Windows DSH Desktop已实际连接并请求55个工具定义；原有模型配置保留。本轮按决定不调用模型、不提交计算，完整自然语言任务仍待验证。
