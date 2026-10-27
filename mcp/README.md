# MC MCP组件

本目录是独立MC工具包的服务组件，使用根目录分发包；运行所需config、knowledge和workspace按主手册配置。协议层不依赖Harness，CLI/MCP共用工具定义、参数校验与业务实现。

- 部署、连接信息和完整调用流程：[使用指南](../docs/使用指南.md#部署mcp与交给调用方)。
- 接自己的函数、程序或API：[已有程序接入](../docs/已有程序接入.md)。
- 实际参数及返回Schema：[接口参考](../docs/工具接口参考.md)。
- 实现职责：[架构与扩展](../docs/架构与扩展.md)。

server.py、adapters.py与启动/锁定依赖基于固定Python模板，来源见UPSTREAM.json；业务工具在tools.py，语义说明在tool_metadata.py。新增普通业务建议用根目录create-mcp.sh创建独立服务；不要求修改这个MC服务。

直接CLI对apply_repair执行TTY逐次确认；MCP调用方须落实用户确认策略，协议本身不能证明真人批准。原失败、备份与专业判定边界按主手册保留。所有计算产物保存服务端，目前没有远程下载。
