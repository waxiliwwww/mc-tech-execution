# Codex与DSH接入（可选扩展）

工具包的标准主入口见[MCP服务与接口](MCP服务与接口.md)。本文仅举宿主侧配置示例，不是运行工具或完成标准协议接入的前置条件。

工具服务负责OpenMC输入、计算和产物，宿主负责模型连接与自然语言编排。只调用CLI无需宿主或模型；标准MCP服务可被其他支持Streamable HTTP的客户端发现和调用。模型密钥不交给本工具服务。

## 先启动服务

在工具包根目录运行`./quickstart.sh`，随后`./mcp/start.sh`。默认仅监听本机`http://127.0.0.1:8770/mcp`，可用标准客户端先验证：

```bash
mcp/.venv/bin/python examples/scan_report_demo.py --mcp-url http://127.0.0.1:8770/mcp
```

远程时设置mcp/service.config.json中的host/publicUrl，服务端环境配置MCP_AUTH_TOKEN，使用部署方已有HTTPS入口。客户端使用实际publicUrl和同一Bearer，不把令牌写入源码。`/info`和MCP接口都需同样鉴权，`/health`只表明服务进程存活。文件上传、有限预览与按需下载使用同一MCP工具连接，操作见[使用手册](toolkit-usage.html#file-transfer)；不额外配置文件HTTP接口。

## Codex

按[Codex官方MCP说明](https://developers.openai.com/codex/mcp/)添加服务：

```bash
codex mcp add mc --url http://127.0.0.1:8770/mcp
```

启用鉴权时，先在客户端进程环境中提供MCP_AUTH_TOKEN，再添加：

```bash
codex mcp add mc --url https://实际服务地址/mcp --bearer-token-env-var MCP_AUTH_TOKEN
```

也可在Codex配置中填入（本机未启用鉴权时省略最后一行）：

```toml
[mcp_servers.mc]
url = "http://127.0.0.1:8770/mcp"
bearer_token_env_var = "MCP_AUTH_TOKEN"
```

在新会话里先让模型查看mc工具，不要把扫描提交重复当成状态查询。示例请求：

> 使用mc工具新建一个合成数据算例，发现它的可调参数，扫描材料的macro密度倍率0.8、1.0和1.2。查询完成后画flux扫描曲线，导出PDF并列出服务端产物。合成数据只验证工程流程。

Codex版本和模型对工具的审批、图像显示及调用规划行为可能不同；需在实际账号与配置下检查。提交计算、绘图、导出会写入服务端目录，宿主可能要求用户批准。非交互测试要在其配置中明确授权所需工具，不把整个服务的所有写操作一律放开。CLI/MCP示例通过不自动等于自然语言测试通过。

## DSH（DeepSeek Harness）

以下按公开项目[deepseek-ai/deepseek-harness的MCP客户端说明](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.zh.md)编写。如果部署方所称DSH是其他产品，应使用该产品自己的MCP入口。

在DSH的插件列表中加入下列条目；这只是插件条目，不替代其完整模型/账号配置：

```yaml
- id: mcp-openmc-forge
  name: '@deepseek-ai/dsh-mcp-client'
  config:
    serverName: openmc-forge
    transport: streamable-http
    url: http://127.0.0.1:8770/mcp
    failOnStartupError: true
```

鉴权时在config下增加：

```yaml
    headers:
      Authorization: !!js '`Bearer ${process.env.MCP_AUTH_TOKEN}`'
```

其余模型和插件环境沿DSH自己的安装说明准备。可使用同一自然语言示例；图像显示还取决于DSH附件设置与模型能力。本轮提供按官方规范编写的配置；未实际启动的宿主不计入“两宿主验收”。

## 简单使用的几个约定

1. 第一次先生成公开工程示例或由部署人员把完整业务算例放入MC_WORKSPACE，再使用相对目录。
2. 参数名、对象ID和单位先调用openmc_list_parameters获取，不猜材料ID或核数据适用性。
3. 提交得到job_id/scan_id后继续查同一编号。失败、partial、cancelled和interrupted需说明，不能把“已提交”当“已完成”。
4. 图表可以直接预览PNG；报告/原始HDF5仍保存服务端。用openmc_list_artifacts查看位置，再由服务端人员打开或按现场已有方式归档。
5. 输入修复必须先展示差异，由使用宿主向真实用户取得确认。普通扫描从输入副本运行，不覆盖原算例。

本机试验注意：旧CLI的模型配置可能与当前登录不匹配，或者缺少与CLI配套的code-mode-host。请使用完整安装的匹配版本和账号可用模型；这类宿主启动故障不能由工具服务伪报通过。

## 已完成的扫描与报告宿主演练

已用完整配套的Codex CLI、实际账号模型和临时MCP配置，完成自然语言→创建合成算例→参数发现→三组扫描→查询完成→flux曲线→PDF→产物清单。8次实际工具调用成功，服务端文件再次核对。仅这一条合成工程场景通过，不推算原合同成功率或两宿主验收。

演练使用临时配置，显式授权创建算例、参数发现、提交/查询扫描、扫描绘图、导出扫描报告和列产物这七个工具。真实业务采用交互确认即可；确需已授权的非交互流程时，可按[Codex官方配置参考](https://developers.openai.com/codex/config-reference/)逐工具配置，例如：

```toml
[mcp_servers.mc.tools.openmc_create_model]
approval_mode = "approve"
```

其他必要工具同样逐个配置，避免把确认修复或其他写操作一起批准。这个设置是宿主审批策略，不改变MCP服务的功能或参数。原CLI模型不匹配、配套组件缺失和审批阻断的首次记录均保留，未改日常账号配置。DSH本轮未实际启动。

## 官方资料与修复的使用

可以要求宿主：“查官方run_mode说明，运行run-mode-typo工程练习，诊断失败，在新目录准备修复。先展示diff等我确认，再应用并重新校核/计算，报告新旧编号及结果。”用户拒绝时必须停止修改和重算；未知错误不能擅自选择物理参数。

apply_repair必须保持逐次确认，不能照其他普通示例工具配置永久approve。终端演示examples/repair_demo.py通过真实TTY询问；直接CLI也强制同一确认。标准MCP服务依赖使用宿主执行确认，不能只凭调用发生就声称有人同意。自动修复范围及例题条件见公开资料与例题修复手册。
