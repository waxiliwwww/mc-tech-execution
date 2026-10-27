# MC 技术执行工具包

将OpenMC公开能力封装为可供下游部署、调用和扩展的独立工具包。支持直接CLI和标准MCP服务，共用参数校验和业务实现；不依赖Harness、特定智能体或大模型账号。

当前源码版本0.5.1，OpenMC适配基线0.16.0。工具已具备可运行的工程能力；CAD、HPC、微调输入生成、专业规则和指定基准等尚未全部完成，范围见[开发计划](docs/验收导向开发计划.md)。工程示例与自测不等于专业验收。

## 能做什么

| 能力 | 当前支持 |
| --- | --- |
| 环境与输入 | 首次环境准备、独立探测API/求解器/版本；公开例题或已有输入接入、XML及可选API校核 |
| 本地计算 | 提交、状态、取消、输入快照与持久记录；服务重启后继续查询 |
| 参数扫描 | 发现已适配参数、独立输入副本、笛卡尔组合串行执行、逐项失败/取消记录 |
| 结果与报告 | 官方StatePoint读取；PNG/SVG及对应数据；Markdown/CSV/DOCX/PDF/真实DOC |
| 资料与修复 | 精选官方原文/出处、公开例题；真实日志→新副本差异→逐次确认→校核/重算，自动修复当前限运行模式拼写 |
| 运维与扩展 | 服务端产物位置/哈希、维护清理脚本；配置、接口和架构扩展说明 |

## 先跑通一次，再接自己的业务

收到包后解压并进入mc-tech-execution根目录。服务端使用Linux/WSL；首次需准备Python3.10+、venv、Bash、curl、tar/bzip2，自动准备Office还需apt-get/dpkg-deb及软件源网络。仅运行工具不需要Node或语言模型。

```bash
./quickstart.sh
mcp/.venv/bin/python examples/public_case_demo.py
```

第一条安装/配置项目内运行依赖；第二条创建官方公开多群例题，完成输入检查、真实计算、结果图和报告。默认输出简明结果、警告与**服务端绝对路径**。公开C5G7数据随例题提供，正式业务核数据库另配。已有环境、跳过Office、配置优先级和远程部署见[配置与部署](docs/配置与部署.md)。

程序需要完整JSON时加--json，进度仍在stderr：

```bash
mcp/.venv/bin/python examples/public_case_demo.py --json > result.json
```

四个业务演示采用同样规则。旧版本依赖演示stdout JSON的脚本应加--json；底层CLI/MCP仍返回结构化结果。合成扫描及确认修复演练分别是examples/scan_report_demo.py、examples/repair_demo.py，作用与边界见[使用指南](docs/使用指南.md)。

## 接自己的算例，直到拿到产物

1. [准备运行配置与工作区](docs/配置与部署.md)：分别设置程序、核数据、数据根和服务配置。
2. [导入已有输入并执行](docs/使用指南.md)：放入算例→校核→提交→按编号查询→选择结果→画图/报告。
3. [定位并管理产物](docs/产物与清理.md)：查绝对位置、打开日志/数据/文档、归档和维护清理。
4. [出现问题时定位](docs/故障排查.md)：从第一条错误与实际终态继续处理，保留失败记录。

先查工具和服务端路径，无需启动MCP：

```bash
./mc.sh list
./mc.sh call openmc_environment
```

**所有工作区相对路径都相对服务端MC_WORKSPACE，默认是本包根目录/workspace。** .mc-jobs与.mc-scans是其下的隐藏目录，终端用ls -la查看。不是相对examples、mcp或客户端当前目录。

产物按计算/扫描保存在服务器，openmc_list_artifacts返回实际目录、文件位置、大小与哈希。图表/报告保留旧path字段，同时给出workspace、server_path和location。当前没有远程下载，服务端位置不等于本机文件。

## 通过标准MCP提供工具

```bash
./mcp/start.sh
```

默认http://127.0.0.1:8770/mcp。下游按标准协议initialize→tools/list→tools/call接入，见[MCP服务与接口](docs/MCP服务与接口.md)。无需绑定具体宿主；协议客户端示例：

```bash
mcp/.venv/bin/python examples/mcp_client.py --url http://127.0.0.1:8770/mcp --tool openmc_environment
```

远程部署需实际可达URL、鉴权及部署方HTTPS入口。[Codex/DSH等宿主说明](docs/MCP平台接入.md)和[harness目录](harness/README.md)均为可选扩展，不是工具服务运行前提。

## 维护、开发与分发

- [文档总目录](docs/README.md)：按部署者、使用者、开发者查阅。
- [工具接口参考](docs/工具接口参考.md)：从实际注册表生成的完整参数、默认值和Schema。
- [架构与扩展](docs/架构与扩展.md)：每个模块负责什么，如何加函数、模板、结果和资料。
- [产物与清理](docs/产物与清理.md)：本机维护脚本默认预览，显式--apply才删除；保护运行记录与原输入。
- [测试与验收说明](docs/测试与验收说明.md)：局部、真实OpenMC与正式验收分别验证。

```bash
./cleanup.sh
./verify.sh
./package.sh
```

清理默认只列出记录，不删除。verify检查工程行为；完整仓库检查包含可选Harness的Node22/pnpm回归，工具运行本身无需Node。只验证Python工具时按测试说明运行对应套件。package生成dist/mc-tech-execution-0.5.1.tar.gz及MANIFEST.json/SHA256SUMS；打包不代替测试。

包内有源码、依赖清单、配置模板、示例、测试与文档；不含个人密钥、本机配置、已安装环境、运行产物和原始合同。接收方按部署文档准备环境；解压即可找到完整说明，不必依赖此前聊天。
