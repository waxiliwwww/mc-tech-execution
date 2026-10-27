# MC 技术执行工具包

把OpenMC等蒙卡程序的公开能力封装成独立软件工具，提供直接CLI、标准MCP服务、配置和调用示例，供下游平台接入与甲方联调。专业计算复用已有求解器，运行不依赖Harness。

当前源码版本0.2.0，OpenMC适配基线0.16.0（本轮核实的最新正式版）。这是接口工程迭代，全部15项合同功能与正式验收的状态见[开发计划](docs/验收导向开发计划.md)。

## 现在能做什么

| 能力 | 当前范围 |
| --- | --- |
| 环境探测 | 分别检查OpenMC Python API、可执行程序和版本；核数据单独配置 |
| 示例输入 | 通过公开API生成合成单群截面示例或官方PWR pin-cell，输出实际XML文件 |
| 输入检查 | XML结构、基础运行参数和可选官方API模型读取；不作专业正确性判定 |
| 本地作业 | 计算、绘图、几何调试的提交/查询/取消，保存输入快照、退出码、日志和本作业产物 |
| 结果与报告 | 用StatePoint读取keff、tally均值/标准差及维度，导出Markdown/CSV |
| 辅助接口 | 保留日志规则提示、人工确认文本修复、本地文本检索、基准目录查询 |
| 分发 | 独立MCP服务、可选Harness客户端、源码包和校验清单 |

新8个OpenMC接口与原7个辅助接口共用同一注册表。工具数量不等于合同功能完成数；CAD、HPC、批量、微调生成、完整专业规则、PDF/DOC等仍需按计划补齐。

## 什么时候用

- 开发者先用CLI验证接口，无需模型密钥或启动MCP服务。
- 下游接入智能体/其他MCP宿主时启动MCP服务，工具定义由协议发现。
- 甲方接到源码或部署包后，按示例复现，再替换为正式模型、核数据和环境开展联调。

## 环境与安装

首版在Linux/WSL运行。本地持久作业依赖Linux进程接口；Windows用户可在WSL或Linux服务器部署服务，再远程接入。

- MCP：Python 3.10+及`mcp/requirements.txt`，启动脚本自动创建项目虚拟环境。
- OpenMC：求解器及Python API均为0.16.0；API要求Python≥3.12，可与MCP使用不同Python环境。
- Harness：可选，需要Node.js 22。仅调用CLI/MCP时无需Node。
- 核数据：合成示例自行生成单群数据，无需下载真实核数据库；正式模型使用对应核数据。合成示例不代表真实物理结果或合同验收。

安装OpenMC可参照[官方安装说明](https://docs.openmc.org/en/stable/quickinstall.html)，例如已有Conda时：

```bash
conda create -n mc-openmc -c conda-forge python=3.12 openmc=0.16.0
conda run -n mc-openmc python -c 'import sys; print(sys.prefix)'
```

将上一条输出的环境目录填入：

```bash
python3 mcp/configure_openmc.py --prefix /实际的环境目录
```

脚本先核对API和可执行程序版本，再生成本机`config/openmc.local.json`。它不入Git、不进入部署包；换机器后重新配置。配置优先级：CLI `--config` > `MC_OPENMC_CONFIG` > 本机local文件 > `config/openmc.json`。默认配置通过PATH查找python3和openmc，不自动安装/切换求解器或用模拟结果兜底。

## 最快跑一次：不需要智能体

在项目根目录执行：

```bash
./mc.sh list
./mc.sh call openmc_environment
mcp/.venv/bin/python examples/openmc_demo.py
```

示例会新建独立目录，依次生成合成截面及XML、检查输入、真实调用OpenMC、查询完成、读取statepoint、导出两种报告。标准输出为完整JSON记录；进度在标准错误。结果返回`job_id`和报告相对路径，文件位于服务端`workspace/`，不是客户端本地路径。

示例失败会返回非0并说明原因，不自动回退到模拟计算。可按返回作业编号继续查询。逐步命令、参数与返回值见[OpenMC工具使用说明](docs/OpenMC工具使用说明.md)。

## 通过MCP调用

终端A：

```bash
./mcp/start.sh
```

默认地址`http://127.0.0.1:8770/mcp`；`/info`提供便于阅读的接入信息，正式工具发现及调用遵守MCP。

终端B，可先用标准MCP客户端运行同一示例：

```bash
mcp/.venv/bin/python examples/openmc_demo.py --mcp-url http://127.0.0.1:8770/mcp
```

其他宿主填同一服务URL即可发现工具。远程部署设置`mcp/service.config.json`的监听地址、publicUrl及服务器环境中的`MCP_AUTH_TOKEN`，通过已有HTTPS入口接入；见[mcp/README](mcp/README.md)。跨机器时输入模型和产物位于服务端工作区，远程文件传输按现场环境安排，本版没有新增任意文件上传下载服务。

如需终端自然语言交互，按[Harness手册](harness/README.md)配置模型后执行`./harness/run.sh`。MCP包可配其他宿主；Harness也可接其他标准MCP，不互相绑定。输入修改的人工确认由使用宿主执行，换宿主要配置其审批策略。

## 验证与打包

**验证检查当前代码行为；打包用于交给别人部署和保留版本。**

```bash
./verify.sh
./package.sh
```

`verify.sh`运行工程测试及原Harness回归，不访问外部模型、不替代正式指标测试；成功会明确输出“校验成功”，详细日志在`artifacts/`。真实OpenMC示例另外运行，具体见[测试与验收说明](docs/测试与验收说明.md)。

`package.sh`生成`dist/mc-tech-execution-0.2.0.tar.gz`、`MANIFEST.json`和`SHA256SUMS`，包含源码、运行入口、配置模板、固定Harness核心、示例和文档。它不自动验证，也不包含已安装环境、密钥、本机配置、运行结果或原始合同材料。接收方按本README安装运行依赖、配置OpenMC后使用；这不是预装核数据库的离线镜像。

同版本重打包会覆盖同名文件，正式留版应先保存旧包与测试证据，更新`VERSION`并提交Git。修改Harness源码后按其手册先构建再验证。

## 开发与交付入口

- [开发计划](docs/验收导向开发计划.md)：15项功能、公开接口、测试方法、协作条件及当前状态。
- [工具使用说明](docs/OpenMC工具使用说明.md)：新8工具、调用方法、来源和限制。
- [测试与验收说明](docs/测试与验收说明.md)：工程/真实运行/专业验收分层及执行方法。
- [实施与验收边界](docs/实施与验收.md)：当前实现与原合同要求之间的关系。

## Git管理

远端为私有仓库`waxiliwwww/mc-tech-execution`。Git保存源码和公共配置/示例；原始合同`references/`、本机密钥与local配置、运行数据、环境和测试日志均忽略。私有仓库权限不代替项目资料使用权限。

日常先看`git status`，完成一批修改后检查差异、自测并提交，再推送。不要把“代码已推送”写成“服务已重启”或“甲方验收通过”。源码打包与测试记录分开保存，实际运行环境按说明复现。
