# 标准MCP服务与接口

工具包提供Streamable HTTP MCP Server，任何支持相应协议、鉴权与工具调用的客户端都可接入。服务不要求Harness或特定模型平台；模型账号和宿主策略由调用方管理。

## 1. 启动与连接

完成[部署配置](配置与部署.md)，运行./mcp/start.sh。默认URL为http://127.0.0.1:8770/mcp。异机使用部署方实际可达的publicUrl；启用鉴权时客户端进程设置MCP_AUTH_TOKEN。

随包提供独立协议客户端：

```bash
mcp/.venv/bin/python examples/mcp_client.py --url http://127.0.0.1:8770/mcp
mcp/.venv/bin/python examples/mcp_client.py --url http://127.0.0.1:8770/mcp --tool openmc_environment
```

第一条执行标准initialize/tools/list，输出完整工具定义；第二条执行tools/call。协议客户端的stdout是JSON，错误/进度在stderr；它是编程接入参考，不需要大模型。四个业务演示默认是人读摘要，程序使用时加--json。

客户端基于随包锁定的官方Python MCP SDK，核心调用形式为：

```python
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

async with streamablehttp_client(url, headers=headers) as (read, write, _):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("openmc_environment", {})
        if result.isError:
            raise RuntimeError(result.content[0].text)
        data = result.structuredContent
```

url、headers由部署配置提供；完整可运行形式见examples/mcp_client.py。不要省略协议初始化，把裸HTTP JSON POST当作完整MCP接入。

## 2. 工具发现与参数

以服务返回的name、description、inputSchema、outputSchema和annotations为准。必填项、类型、枚举/范围由Schema检查；同一参数约束在直接CLI与MCP共用。完整参数和默认值见[生成的接口参考](工具接口参考.md)。

工具数不等于合同功能完成数。已有工程范围包括输入与基础校核、本地作业、受限扫描、StatePoint读取、图表/报告、资料/例题与受限确认修复；CAD、HPC、微调生成、专业规则与指定基准等仍在开发计划中。

## 3. 返回值怎样消费

- 常规调用返回结构化对象；标准MCP也提供文本表示，图表可附标准图像内容。
- path、case_dir等相对服务端MC_WORKSPACE；文件结果的server_path、workspace、location=server直接说明所在机器和基准。保留旧字段以兼容调用方。
- 图表image通常是文件名；读取path/server_path或产物清单定位文件。文件内容和服务端位置不是同一概念。
- 计算提交返回job_id，扫描提交返回scan_id。调用返回成功只说明请求受理/该操作完成，应继续查询各自终态。
- 统计不可用保留null和warnings，失败/缺失点不填0。来源和professional_acceptance标识随结果保留，不据成功回执判定专业精度。
- 工具错误使用isError及可读原因；CLI错误写stderr并返回非0。网络断连和超时后先按已有编号查询，不盲目重提有副作用的操作。

openmc_list_artifacts返回清单、目录、大小和哈希；目前没有远程下载接口。远程客户端要通过部署方实际可用的文件交付方式另行取回，不能把服务端绝对路径在本机解析。

## 4. 写操作和逐次确认

生成、提交、绘图和导出都会在服务器写文件，调用方按实际权限授权。apply_repair要求展示当前差异并取得用户针对该次修改的明确确认，不接受模型自行传confirmed=true，也不应配置永久自动批准。

MCP协议调用本身不能证明背后已经有人确认。CLI有TTY确认；标准宿主必须落实并验证相应策略。修改后还要校核和关联重算，保留旧失败记录；专业修改范围由相应规则与审定条件决定。

## 5. 服务扩展与具体宿主

/health和/info是该服务的运维扩展，标准调用不要求依赖它们。Bearer启用后/info与/mcp按同一规则鉴权。清理脚本是本机维护操作，不作为MCP删除工具提供。

[Codex/DSH等宿主配置](MCP平台接入.md)属于可选扩展说明。协议客户端通过不等于全部宿主、模型或原合同兼容性验证已完成。
