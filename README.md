# MC技术执行工具包

为OpenMC提供模型接入、受控参数化建模、输入校验、命名作业、扫描、结果图表和报告管理。可独立用CLI，也可作为标准MCP服务接DSH等宿主，不绑定模型或智能体。

**第一次接入先选使用方式：** 个人本机使用[stdio按需启动](docs/deployment.html#local-process)，由宿主启动工具；需要常驻连接时使用[HTTP服务](docs/deployment.html#mcp-service)。两条路线使用同一套工具。接好MCP后，告诉Agent模型、修改目标和所需结果，它会处理内部参数、校核、等待与文件取回；物理条件不明确时再补充。

**从 [工具包总览](docs/index.html) 开始。** 源码下载/解压后用浏览器打开 `docs/index.html`，无需文档服务器。日常阅读固定为五份：

| 文档 | 读完能做什么 |
| --- | --- |
| [工具包总览](docs/index.html) | 理解用途、适用场景和能力范围；分清能生成、导入、修改与解析什么 |
| [使用手册](docs/toolkit-usage.html) | 从组件/全堆例题走完输入到图报告，再换自己的模型；看懂数据、产物和异常处理 |
| [部署与接入](docs/deployment.html) | 配置Linux/WSL环境、核数据、CAD/Office、MCP与Windows DSH |
| [架构设计](docs/toolkit.html) | 理解模型准备、作业管理、结果分析的机制、源码职责和扩展位置 |
| [接口参考](docs/reference.html) | 按工具名、用途或参数搜索实际Schema及返回含义 |

已有模型的单次改参与批量扫描共用逐参数落盘读回，确认请求值真正生效后才发布副本或提交扫描子作业。原输入、计分和作业来源可回查；CLI与MCP共享这些能力，特殊分析继续使用原工具或Shell。

常见结果交付可用 `openmc_generate_result_bundle` 一次生成同源图、完整数据、报告、脚本和指纹清单；不重算，原单项工具及自定义分析保留。见[使用手册](docs/toolkit-usage.html)。

自有模型可先只读检查依赖，再显式绑定服务器已有文件并导入；失败批次可在用户确认后续算未完成项，保留成功结果。比较按原始计分箱对齐，参数与核数据身份可回查。详见[使用自己的模型](docs/toolkit-usage.html#input-more)和[批量计算与续算](docs/toolkit-usage.html#scan)。

服务端优先Linux，适配OpenMC 0.16.0，源码版本0.27.1。运行一次 `bash scripts/local-setup.sh`，再把输出配置导入Helix等MCP宿主：stdio由宿主按需启动；HTTP用 `bash mcp/start.sh` 常驻运行。两种方式共用一份个人配置 `~/.config/openmc-forge/config.json`，工作目录、日志、计算环境、Slurm默认资源、HTTP和传输设置分组存放；已有配置首次准备时自动沿用，普通启动不安装、不搬数据。Agent可通过所在Linux节点已有的文件/终端能力代改，用户也可手改；现有 `openmc_environment` 显示实际生效的常用值。细节见[配置与迁移](docs/deployment.html#configuration)。Windows exe＋WSL保留为可选入口，无需在Linux部署时配置exe。首次准备需网络下载运行时、核数据和依赖。工作目录未设置时仍使用本包 `workspace/`。

`./scripts/test-mcp.sh` 自启临时服务，核对版本和完整工具定义后自动停止；带测试命令的用法见[部署手册的测试入口](docs/deployment.html#test-session)。`./verify.sh --tools-only` 通过该入口做工具工程检查；`./package.sh` 生成 `dist/` 压缩包与对应版本清单/摘要，`./scripts/verify-package.sh <压缩包路径>` 校验这份交付物；`./cleanup.sh` 默认只预览。工程检查、归档校验、分发和维护各有用途，具体步骤在手册对应章节。

[官方例题参考](docs/official-examples.html) 和 [独立MCP封装](docs/mcp.html) 是按需附录，由使用/开发章节引入。合同进展、开发计划、历史验证及专家试用记录统一从 [项目资料](docs/project-records.html) 查，不作为下游使用前置阅读。旧主题页仅保留到正式章节的跳转。

当前按[开发计划](docs/development-plan.html)稳定全部已支持能力，范围、证据和完成标准对应[能力基线](config/capability-baseline.json)。原五条流程保留为代表入口，可用 `scripts/verify-pilot-workflows.py` 复现，不代表全部能力。完整发布按[测试说明](docs/测试与验收说明.md)配置真实OpenMC与CAD环境，执行 `./verify.sh --release`，再核对交付包；工程通过不替代专业验收。

异机文件交接见 [使用手册](docs/toolkit-usage.html#file-transfer)：MCP函数上传、有限预览和分块下载，默认完整文件5 MiB可配；`examples/transfer_files.py` 在宿主直接读写字节，不通过模型复述文件。

通过智能体使用时，只需连接MCP；服务在instructions直接提供Skill中的核心工作流，完整章节通过 `openmc_get_workflow` 按需读取，任务指引与随包 [openmc-workflows Skill](skills/openmc-workflows/SKILL.md)同源。[使用方法与可选本地安装](docs/deployment.html#agent-skill)见部署页。核心指引按改参、扫描、绘图/报告等用户目标优先路由已有MCP能力，无需点名MCP；自然语言触发排查见部署页。宿主自动采纳需现场验证；本轮未启用官方Skills扩展或修改宿主/模型配置。

绘图统一采用科研样式，图旁提供完整JSON/CSV和独立Python复现脚本；无需重新计算即可调整字号、标签、尺寸并导出PNG/SVG。模板选择、取回与重画见[使用手册](docs/toolkit-usage.html#replot)。
