# MC Python MCP 服务

此目录可单独启动服务，协议层不依赖 Harness。默认服务名 `mc-agent-tools`，端口8770，连接别名mc。`tools.py` 是项目工具注册表；`business.py` 提供项目函数；`server.py`、`adapters.py`、`start.sh` 和锁定依赖来自通用Python模板，来源及哈希见 `UPSTREAM.json`。

项目配置与业务资料位于config/、knowledge/、workspace/；分发时使用项目包。OpenMC运行时、核数据等按部署说明配置。新增OpenMC接口与原辅助接口共用注册表，直接CLI也可调用，见根README及docs/OpenMC工具使用说明.md。若只需封装普通函数，可使用vendor中的通用模板快照。

## 配置与远程部署

执行 `./start.sh`。本机默认无需凭据。远程监听配置：

```json
{"host":"0.0.0.0","port":8770,"path":"/mcp","publicUrl":"https://your-server.example/mcp","name":"mc-agent-tools","version":"0.4.0","clientName":"mc"}
```

服务器环境设置 `MCP_AUTH_TOKEN`，通过现有HTTPS入口提供服务。publicUrl必须是真实客户端地址，不是监听通配地址。启动成功后生成connection.json，其中密钥是环境变量占位符；不要把真实token写入配置或工具说明。`/info`与MCP使用相同Bearer，`/health`只检查进程。

## 工具边界

diagnose_log只提供待业务审定的日志规则提示；preview_repair/read_repair_plan提供精确文本修改计划；apply_repair检查原始文件哈希、备份和单次应用。真正的人类确认由调用宿主执行，接其他宿主时必须配置，服务端不把模型传来的confirmed当作用户确认。知识查询目前是文本检索，算例目录明确记录缺料，不生成虚构蒙卡结果。

通用程序/HTTP适配器仍在adapters.py中。后续接入实际蒙卡脚本时提供固定argv、工作目录、输入输出schema、运行依赖和参考样例。长计算应由业务接口返回任务ID并实现查询、取消，不能占用同步工具无限等待。

本服务已扩展参数扫描、图表、报告及产物清单，原7个辅助接口保留；实时清单以标准MCP发现为准。宿主终端（本项目 `harness/`）另有本地工具 `read`、`bash`、`edit`、`write`，不经过本服务；边界见 [Harness 手册](../harness/README.md)。

图表工具可返回标准MCP图像内容及结构化结果；所有文件保存在服务端。openmc_list_artifacts提供清单/路径/大小/哈希，本轮未增加远程下载路由。Codex与DSH配置见[平台接入](../docs/MCP平台接入.md)。

官方资料和公开例题可离线使用；诊断/确认修复/重验的完整操作见[新手册](../docs/公开资料与例题修复.md)。MCP调用本身不能证明真人确认，宿主须逐次展示预览并批准apply_repair；不要将该工具永久自动批准。直接CLI已有TTY确认，拒绝非交互直接应用。
