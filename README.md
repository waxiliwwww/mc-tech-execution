# MC 技术执行工具包

为已有 OpenMC 模型和结构化组合新模型提供输入检查、参数副本、命名作业、本地计算、扫描、结果图表和报告管理；也可作为标准 MCP 服务供下游调用。工具包独立运行，不绑定 Harness 或大模型。

**先打开 [HTML 使用与开发手册](docs/index.html)**。下载或解压后，用浏览器打开 `docs/index.html`；无需启动文档服务。GitHub 网页可先下载源码包再打开，HTML 源码预览不等于渲染页面。

项目执行或进展评审先看 [合同功能—中文操作—代码工具进展对照](docs/project-progress.html)：在同一张直接展开的表内，按两批15项功能列中文操作与代码名、输入输出、合同开发及验收期限、已测范围和缺口，列出实际操作与合同功能的对应关系。它是内部进展入口，不是合同验收结论。

- [模型接入与建模](docs/domain-workflows.html)：带入自己的XML，或组合材料/几何/格架/源/tally经Python API生成；保留可重跑源码，按任务名找结果。标准MCP与CLI共用接口，宿主模型可按Schema组织参数；真实自然语言及微调生成验证另记。
- [两周开发计划](docs/development-plan.html)：近期要补的具体接口、测试、顺序与协作条件；公开例题只作为基础条件。
- [完成一次计算](docs/compute.html)：环境 → 看输入 → 发现参数 → 创建修改副本 → 校核/计算 → 找到图和报告。每步说明目的、输入来源和预期结果。
- [运行全部官方例题](docs/official-examples.html)：固定官方清单、逐步CLI与MCP操作、所需输入和48项实际运行/读回记录；另2项按决定暂缓。
- [带入自己的模型、修改源码](docs/inputs.html)：原生 Python/XML、自动改参范围、实现职责和扩展入口。
- [把程序封装成 MCP](docs/mcp.html)：独立 Python/TypeScript 服务、同服务多个工具、参数说明与下游交接。
- [查工具参数](docs/reference.html)：与实际 MCP Schema 同源，按需搜索，无需通读。
- [环境与部署](docs/env.html)：当前构建串与实测版本、核数据位置、哪些能力来自编译选项而非代码、换机三步探测、跨环境比对的复现前提。

源码版本 0.10.0，适配 OpenMC 0.16.0；服务端使用 Linux/WSL。首次按手册准备环境，升级已有旧环境时可用 `./quickstart.sh --openmc-prefix .runtime/openmc-official` 在本包内另建完整SDK并保留旧环境；运行数据默认保存在本包 `workspace/`，图和报告返回服务端绝对路径，当前不自动下载。

工具包也可安装使用：在完整源码目录执行 `pip install -e .` 后得到 `mc-agent-tools`（工具直调，等同 `./mc.sh`）与 `mc-agent-tools-serve`（启动 MCP 服务）；依赖只认 `mcp/requirements.txt` 一处。独立wheel只包含接口代码，基础建模/计算需显式设置运行配置和工作区；官方例题、知识库和部署资产仍使用完整源码包，不能以wheel工具发现成功代替完整部署验证。服务端另提供 3 个只读资源：参数规格、官方例题清单、最近工具调用记录。源码运行与安装运行走同一套实现，测试见 `tests/`，CI 在 `.github/workflows/verify.yml`。

本包管理调用与产物；专业模型、核数据和结果判断需要业务依据。当前工程实现和未完成项见 [开发计划](docs/验收导向开发计划.md)，公开例题及接口自测不代表合同专业验收。

`./verify.sh --tools-only` 独立检查工具包，`./verify.sh` 还包含可选Harness检查，`./package.sh` 生成 `dist/` 源码部署包，`./cleanup.sh` 默认仅预览产物记录。具体条件和操作见手册，验证与打包用途不同。

Helix专项：已真实发现MCP、执行知识检索与原文读取；两条完整自然语言任务未通过。按用户决定停止追加模型预算，保留待验证；不计为宿主验收完成。配置与边界见 [MCP说明](docs/mcp.html#helix)。
