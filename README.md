# MC技术执行工具包

为OpenMC提供模型接入、受控参数化建模、输入校验、命名作业、扫描、结果图表和报告管理。可独立用CLI，也可作为标准MCP服务接DSH等宿主，不绑定模型或智能体。

**从 [工具包总览](docs/index.html) 开始。** 源码下载/解压后用浏览器打开 `docs/index.html`，无需文档服务器。日常阅读固定为五份：

| 文档 | 读完能做什么 |
| --- | --- |
| [工具包总览](docs/index.html) | 理解用途、适用场景和能力范围；分清能生成、导入、修改与解析什么 |
| [使用手册](docs/toolkit-usage.html) | 从组件/全堆例题走完输入到图报告，再换自己的模型；看懂数据、产物和异常处理 |
| [部署与接入](docs/deployment.html) | 配置Linux/WSL环境、核数据、CAD/Office、MCP与Windows DSH |
| [架构设计](docs/toolkit.html) | 理解模型准备、作业管理、结果分析的机制、源码职责和扩展位置 |
| [接口参考](docs/reference.html) | 按工具名、用途或参数搜索实际Schema及返回含义 |

服务端使用Linux/WSL，适配OpenMC 0.16.0，源码版本0.14.2。初次准备在完整源码包根目录执行 `./quickstart.sh`；首次需网络并下载运行时、数据与依赖，准备条件及可选安装见部署说明。计算和产物默认在本包 `workspace/`，路径属于服务器。

`./verify.sh --tools-only` 做工具工程检查；`./package.sh` 生成 `dist/` 压缩包与对应版本清单/摘要，`./scripts/verify-package.sh <压缩包路径>` 校验这份交付物；`./cleanup.sh` 默认只预览。工程检查、归档校验、分发和维护各有用途，具体步骤在手册对应章节。

[官方例题参考](docs/official-examples.html) 和 [独立MCP封装](docs/mcp.html) 是按需附录，由使用/开发章节引入。合同进展、开发计划、历史验证及专家试用记录统一从 [项目资料](docs/project-records.html) 查，不作为下游使用前置阅读。旧主题页仅保留到正式章节的跳转。

当前按[开发计划](docs/development-plan.html)稳定全部已支持能力，范围、证据和完成标准对应[能力基线](config/capability-baseline.json)。原五条流程保留为代表入口，可用 `scripts/verify-pilot-workflows.py` 复现，不代表全部能力。完整发布按[测试说明](docs/测试与验收说明.md)配置真实OpenMC与CAD环境，执行 `./verify.sh --release`，再核对交付包；工程通过不替代专业验收。
