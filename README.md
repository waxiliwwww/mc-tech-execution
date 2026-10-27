# MC 技术执行工具包

把OpenMC等蒙卡程序的公开能力封装为独立工具包。可直接在终端调用，也可作为标准MCP服务接入智能体。源码、配置模板、示例、测试与说明随包提供；运行不依赖其他项目源码，也不依赖Harness。Harness是可选客户端，可以换成其他MCP宿主。

当前版本0.4.0，OpenMC适配基线0.16.0。这是可执行的接口工程版本；全部15项合同功能的状态、专业验证条件和后续路线见[开发计划](docs/验收导向开发计划.md)。

## 能做什么、什么时候用

| 功能 | 当前范围 |
| --- | --- |
| 准备与检查环境 | Quick Start准备运行依赖，分别检查OpenMC API、求解器和版本 |
| 输入模型 | 生成合成截面示例或官方pin-cell，检查XML及API解析，发现并修改已适配参数 |
| 计算与扫描 | 本地计算、几何绘图/调试；参数笛卡尔组合串行扫描；查询、取消、逐项结果与失败记录 |
| 结果图表 | 从完整StatePoint选择tally、score、nuclide和filter，生成PNG/SVG及对应数据；支持扫描曲线 |
| 报告 | 单作业或扫描导出Markdown、CSV、DOCX、PDF及真实Word97 DOC |
| 产物管理 | 服务端按计算/扫描目录保存；清单含路径、大小和SHA256；本轮不提供远程下载 |
| 官方资料 | 随包8份固定OpenMC0.16.0官方RST原文及MIT许可，中文参数导航、原文/行号/版本/来源查询，运行时不联网 |
| 公开例题 | 官方多群燃料棒自带C5G7数据，可准备/运行/取结果；合成单群工程例题；CLEAR-S与17×17仍待材料 |
| 诊断与确认修复 | 真实日志/输入诊断；新副本展示差异→终端/宿主真人确认→校核→关联重算；当前自动建议仅run_mode唯一近似拼写 |
| 调用方式 | CLI无需模型；MCP可供Codex等宿主使用；工具服务独立于Harness |

先用示例确认环境和接口，再换入正式模型/核数据。业务联调时核对参数、结果及产物；智能体只负责选择和编排这些工具。工具数量不等于合同功能完成数，CAD、HPC、微调输入生成及专业规则等仍在后续计划中。

## Quick Start：准备一次，随后直接使用

服务端支持Linux/WSL，建议Debian/Ubuntu。准备基础环境：Bash、Python3.10+（含venv）、curl、tar/bzip2；自动准备Office还需要apt-get和dpkg-deb。首次需要访问软件源并预留数GB磁盘，后续计算不访问其他开发目录；正式核数据库另行配置。

```bash
./quickstart.sh
mcp/.venv/bin/python examples/scan_report_demo.py
```

第一条创建项目内CLI/MCP虚拟环境、OpenMC0.16.0运行时和图表/报告依赖；没有系统LibreOffice时在项目内提取Office与中文字体，不修改系统安装或shell启动文件。安装阶段输出进度，失败则停止并保留诊断。可重新执行；不会覆盖计算数据。包不预装完整操作系统或核数据库。

第二条无需模型密钥：创建合成单群截面输入→扫描三个宏观截面倍率→真实调用OpenMC→生成flux曲线和单个结果图→导出五种报告→列出服务端产物。进度在标准错误，JSON结果在标准输出。合成数据只验证工程链路，不代表真实材料或专业验收。

已有OpenMC环境可复用：

```bash
./quickstart.sh --openmc-prefix /实际的OpenMC环境目录
```

非Debian/Ubuntu可先按[OpenMC官方安装说明](https://docs.openmc.org/en/stable/quickinstall.html)及LibreOffice安装说明准备环境，再指定已有prefix；也可`--skip-office`先用Markdown/CSV/DOCX（示例加`--formats markdown csv docx`）。PDF/DOC需要转换组件，缺依赖会明确失败，不伪造文件。

配置优先级：CLI `--config` > `MC_OPENMC_CONFIG` > `config/openmc.local.json` > `config/openmc.json`。本机local配置不入Git/部署包。换目录或换机器后重新执行Quick Start配置运行环境。其他设置和逐步命令见[工具手册](docs/OpenMC工具使用说明.md)。

## 查资料、运行公开例题、演练确认修复

```bash
./mc.sh call search_knowledge '{"query":"粒子数"}'
mcp/.venv/bin/python examples/public_case_demo.py
mcp/.venv/bin/python examples/repair_demo.py
```

第一条返回官方原文及固定版本/行号/出处，中文导航不是官方译文；精选RST的引用/include/autodoc未展开。

第二条生成并运行OpenMC官方多群燃料棒，所需C5G7数据由随包官方源码生成，无需另下载核数据库；随后取结果、画能谱计分图和导出报告。运行设置由调用参数指定，未按甲方基准/参考判据验收。

第三条故意提交一个运行模式拼写错误的合成工程算例，保留真实失败，诊断后在新副本生成差异，询问`确认修改？[y/N]`。输入`n`或回车不改、不重算；输入`y`后才应用差异、校核和提交新计算。原算例与失败快照不改。没有`--yes`或模型`confirmed`参数；直接CLI的apply_repair也执行相同确认，非交互管道不会代替人。

三个入口均无需LLM；两个演示脚本加`--mcp-url http://127.0.0.1:8770/mcp`即可走标准MCP。MCP服务器无法独自证明调用背后是否有真人，使用宿主必须展示预览并逐次确认apply_repair；演示客户端和可选Harness已提供确认入口，其他宿主按其策略配置。详见[公开资料、例题与修复手册](docs/公开资料与例题修复.md)。

## 用终端或智能体调用

先查工具、参数，再调用，无需启动MCP：

```bash
./mc.sh list
./mc.sh call openmc_environment
./mc.sh call openmc_list_parameters '{"case_dir":"实际算例相对目录"}'
```

需要智能体使用时：

```bash
./mcp/start.sh
```

默认地址`http://127.0.0.1:8770/mcp`。Codex添加一次即可发现工具：

```bash
codex mcp add mc --url http://127.0.0.1:8770/mcp
```

在新的Codex会话中可说：“用mc工具创建一个合成示例，查看支持的参数；扫描材料宏观截面倍率0.8、1.0、1.2，完成后画flux曲线、导出PDF并列出产物。合成数据只做工程验证。”宿主仍需可用的模型及工具调用权限。此前已完成一次真实Codex扫描/报告流程；本轮资料与确认修复已通过CLI和标准MCP交互验证。DSH及专业结果另行验证。

[Codex与DSH接入说明](docs/MCP平台接入.md)提供带鉴权配置、DSH示例及自然语言用法。远程部署在[mcp配置](mcp/README.md)设置真实访问URL、Bearer环境变量和现有HTTPS入口；工具与宿主解耦。仅运行工具无需Node.js；可选Harness需要Node22，使用方法见[harness/README](harness/README.md)。

先不用智能体也能检验MCP整条流程：

```bash
mcp/.venv/bin/python examples/scan_report_demo.py --mcp-url http://127.0.0.1:8770/mcp
```

## 产物在哪里

默认在服务端`workspace/`：每次计算在`.mc-jobs/<job_id>/`，每次扫描在`.mc-scans/<scan_id>/`。扫描记录保存参数与每次计算编号，汇总JSON/CSV、图表及报告在扫描目录。调用`openmc_list_artifacts`返回文件相对路径、服务端绝对位置、大小及哈希；返回路径不表示客户端已经下载文件。

原始输入不被扫描覆盖，各组从同一基线复制。失败/取消/未执行项保留；缺失结果不填0。MCP服务重启后按已有编号查询，不重新提交；监控进程丢失标interrupted，需人工查询并取消其已记录计算。结果会占用磁盘，不自动清理；结束后可由部署人员归档，清理后对应编号将不能继续查询。远程下载按本轮决定后续再做。

## 验证与打包各有什么用

**验证检查代码和实际行为；打包用于交给别人部署及保留源码版本。**

```bash
./verify.sh
./package.sh
```

verify成功明确输出“校验成功”，日志在`artifacts/`。默认包含受控工程与Harness测试；真实OpenMC套件需显式启用，方法见[测试与验收说明](docs/测试与验收说明.md)。工具/报告的自测不替代全部合同验收。

package生成`dist/mc-tech-execution-0.4.0.tar.gz`、`MANIFEST.json`和`SHA256SUMS`，包含源码、Quick Start、示例、配置和文档。打包不自动验证，不包含已安装环境、密钥、本机配置、计算产物或原始合同。接收方解压后先运行Quick Start；这份源码包不是完全离线的预装镜像。相同版本打包会覆盖同名文件，留版前保留旧包与测试证据。

## 开发与Git入口

- [开发计划](docs/验收导向开发计划.md)：15项工具、公开接口、测法、协作条件和实际状态。
- [工具使用说明](docs/OpenMC工具使用说明.md)：支持参数、调用示例、失败处理和来源。
- [测试与验收说明](docs/测试与验收说明.md)：工程用例、真实运行、专业验收和证据。
- [实施与验收边界](docs/实施与验收.md)：当前实现与合同要求的关系。

私有仓库为`waxiliwwww/mc-tech-execution`。Git保存源码和公共配置/示例；`references/`原始合同、密钥、local配置、环境及运行证据均忽略。修改一批后检查差异、自测、提交再推送。源码推送、服务启动和甲方验收分别记录。
