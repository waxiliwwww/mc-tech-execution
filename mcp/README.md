# MC Python MCP 服务

此目录可单独启动服务，协议层不依赖 Harness。默认服务名 `mc-agent-tools`，端口8770，连接别名mc。`tools.py` 是项目工具注册表；`business.py` 提供项目函数；`server.py`、`adapters.py`、`start.sh` 和锁定依赖来自通用Python模板，来源及哈希见 `UPSTREAM.json`。

项目业务资料位于本项目的 config/、knowledge/、workspace/，因此分发本项目服务要复制项目包，不能只把本目录中的server.py单独拿走。它不读取其他工作目录。若只需要接自己的普通函数，请使用vendor保存的通用模板快照，替换其业务文件即可。

## 配置与远程部署

执行 `./start.sh`。本机默认无需凭据。远程监听配置：

```json
{"host":"0.0.0.0","port":8770,"path":"/mcp","publicUrl":"https://your-server.example/mcp","name":"mc-agent-tools","version":"0.1.0","clientName":"mc"}
```

服务器环境设置 `MCP_AUTH_TOKEN`，通过现有HTTPS入口提供服务。publicUrl必须是真实客户端地址，不是监听通配地址。启动成功后生成connection.json，其中密钥是环境变量占位符；不要把真实token写入配置或工具说明。`/info`与MCP使用相同Bearer，`/health`只检查进程。

## 工具边界

diagnose_log只提供待业务审定的日志规则提示；preview_repair/read_repair_plan提供精确文本修改计划；apply_repair检查原始文件哈希、备份和单次应用。真正的人类确认由调用宿主执行，接其他宿主时必须配置，服务端不把模型传来的confirmed当作用户确认。知识查询目前是文本检索，算例目录明确记录缺料，不生成虚构蒙卡结果。

通用程序/HTTP适配器仍在adapters.py中。后续接入实际蒙卡脚本时提供固定argv、工作目录、输入输出schema、运行依赖和参考样例。长计算应由业务接口返回任务ID并实现查询、取消，不能占用同步工具无限等待。

上面说的是本服务的七个工具。宿主终端（本项目 `harness/`）另外启用了核心自带的本地工具 `read`、`bash`、`edit`、`write`，它们不经过本服务、不受这里的工作目录约束，边界与收窄办法见 [Harness 手册](../harness/README.md)。
