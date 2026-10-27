# 蒙卡专用终端

这个入口在固定的 `@helix/agent-harness/standalone` 核心之上增加模型备用切换和修改前人工确认。模型循环、MCP 连接、工具发现及模型协议适配直接调用原核心。它是 MCP 宿主；业务能力由项目 MCP 服务提供。

## 启动和配置

先按项目入口启动 Python MCP 服务（默认 `http://127.0.0.1:8770/mcp`），然后**在项目根目录**执行（`run.sh` 自己解析所在目录，但站在 `harness/` 里敲 `./harness/run.sh` 会因路径不存在而失败）：

```bash
./harness/run.sh --prompt '/tools'           # 确认工具连通
./harness/run.sh                             # 默认加载同目录 model.local.json，进入对话
./harness/run.sh --config /path/other.json   # 临时改跑另一份配置
```

`model.local.json` 是本机默认工作配置，端点与模型已实测可用，**不需要再复制 `model.example.json`**；后者是发给别人的通用模板，端点与模型名只是格式示例、未在本项目验收，不要拿它覆盖本机配置。本机默认的主模型 `deepseek-v4.1-flash` 与备用模型 `qwen3.7-plus` 出自同一个网关，只能覆盖单个模型的故障，覆盖不了网关整体故障；要满足 T09 的「两个国产真实 API」，把 `fallback` 的 `url` 与 `apiKeyEnv` 换成另一家即可，业务代码不用动。

「不带 `--config` 就用同目录 `model.local.json`」这层默认由项目自己的 `run.sh` 完成（约十行）。`terminal.mjs` 是 `cli.mjs` + `lib/` 在固定纯核上的打包产物：改这三个源码或改 `cli.mjs` 的工具配置后，都要重新 `node build.mjs`，`BUILD.json` 里的 bundle 与源码哈希随之更新；`vendor/` 里的纯核 tarball 始终不改。

运行只需 Node.js 22；`terminal.mjs` 已打包依赖，不读取其他项目的文件。`--config` 为 **JSON**，相对 `mcpRegistry` 路径以该配置文件位置解析。没有 `model.local.json` 时（例如只有打包分发出去的副本）使用本目录的 `mcp-servers.json`，模型退回环境变量模式。`--registry /path/registry.json` 只覆盖注册表，不改变模型配置的来源。配置修改后重启。

模型密钥通过 `apiKeyEnv` 指向环境变量（本机用 `MC_PRIMARY_API_KEY` / `MC_FALLBACK_API_KEY`），或在本机配置的 `apiKey` 字段中提供；不要提交带密钥的本机配置。**本入口不读 `.env` 文件**——`lib/config.mjs` 只查 `process.env`，所以必须在已经 source 过密钥的终端里启动。缺密钥时报错形如 `未设置模型密钥环境变量：MC_PRIMARY_API_KEY`，按该变量名补齐即可。不要将合同资料发送到未经项目许可的外部模型。

没有 `model.local.json`（例如打包分发出去的副本）时，模型从 `MC_PRIMARY_URL`、`MC_PRIMARY_MODEL`、`MC_PRIMARY_API_KEY` 读取；备用从对应的 `MC_FALLBACK_*` 读取。可加 `MC_PRIMARY_PROTOCOL` / `MC_FALLBACK_PROTOCOL`。有显式 JSON 字段时优先 JSON。

本地兼容服务示例：

```json
{
  "primary": {
    "url": "http://127.0.0.1:8000/v1",
    "model": "your-local-model",
    "apiKey": "",
    "protocol": "chat_completions"
  },
  "mcpRegistry": "mcp-servers.json"
}
```

协议字段支持 `chat_completions` 和 `responses`；本地模型也必须提供其中一种兼容 HTTP API。显式 `apiKey:""` 表示无鉴权。不隐式借用其他项目的 `HARNESS_MODEL_API_KEY`。

第三方 MCP 可放入同一注册表；本入口只连接 HTTP/HTTPS，不运行注册表中的本地命令：

```json
[
  { "name": "mc", "transport": "streamable-http", "url": "http://127.0.0.1:8770/mcp" },
  {
    "name": "external",
    "transport": "streamable-http",
    "url": "https://your-server.example/mcp",
    "headers": { "Authorization": "Bearer ${EXTERNAL_MCP_TOKEN}" },
    "tools": ["lookup"]
  }
]
```

省略 `tools` 时发现该服务全部工具；指定后仅允许列出的工具。认证值在内存展开，已知配置凭据从显示和日志中脱敏。模型需要哪家服务的权限，取决于实际服务；“公共 URL”不表示免鉴权。

## 本地工具（read、bash、edit、write）

四个本地工具默认全部启用，实现来自固定纯核的公开 API `createBuiltinTools`（`@helix/agent-harness/standalone`）。`cli.mjs` 把它们与 MCP 工具合成同一份工具表，调用统一经过本项目的确认层。

| 工具 | 行为 | 逐次确认 |
| --- | --- | --- |
| `read` | 读 UTF-8 文本，`offset`（1 起）/`limit`（默认 2000 行），单次输出上限 50 KiB，二进制被拒 | 否（只读） |
| `bash` | `bash -c`，以当前用户身份运行，可改文件、可联网；`timeout` 默认 60 秒、上限 600 秒；输出上限 50 KiB，非零退出算错误 | 是 |
| `edit` | 精确且唯一的单处替换；找不到或不唯一则报错且不改文件；文件上限 20 MiB | 是 |
| `write` | 覆盖写入，必要时创建父目录 | 是 |

- **工作目录默认项目根目录**，`--cwd 目录` 可改。它只决定相对路径怎么解析，**不是沙箱**：绝对路径、`..`、`~` 都能走。注意与 MCP 工具的差别——MCP 工具的路径相对 `workspace/`，本地工具相对项目根目录，例如同一个输入文件前者写 `examples/input.txt`、后者写 `workspace/examples/input.txt`。
- `bash`、`edit`、`write` 进的是与 `apply_repair` 同一个确认层：管道或 `--prompt` 这类非交互输入直接拒绝（记为 `confirmation.denied: non_interactive`），交互终端逐次输入 `yes` 才执行，`no` 记为 `user_rejected`；参数摘要与决策都写进会话日志。**但它们没有 diff、没有哈希校验、没有备份**——那三样只有 MCP 的 `preview_repair → apply_repair` 才有，所以改项目输入文件仍优先走 MCP 修复链（系统提示里已写明，实测模型也照此执行）。
- 收窄办法：把 `cli.mjs` 里的 `localToolNames` 改成 `['read']`（只留只读）或 `[]`（全关），再 `node build.mjs`；`localWriteToolNames` 决定哪些逐次确认，不建议删。
- 放开这四种工具会改变交付口径：它们不经过 MCP 服务，MCP 侧审计看不到；工作目录约定拦不住越界路径，`read` 能读本机任意可读文件。密钥文件（如 `~/.config/mc-tech-execution/env`）与合同资料不要交给模型读。终端显示与会话日志仍按已知凭据脱敏，但不保证能识别任意业务内容里的秘密。

## 两项执行约束

**模型备用只重试当前模型请求。** 主模型请求失败且尚未输出正文、思考或工具调用片段时，使用同一请求切到 `fallback`；只收到了响应标识等元信息时可以丢弃元信息后切换。用户取消不切换。已经出现内容后发生错误，则终止本次采样并记录 `model.failover_blocked`，不拼接另一模型回答、不重跑整轮。若前一步已经执行工具，下一步切换保留已有工具回执；不会由切换机制重新执行此前工具。两模型都失败时终止，不无限重试。

**修改必须由当前终端用户确认。** `apply_repair` 会先通过同一服务的 `read_repair_plan` 读取计划，展示实际工具名、参数、参数摘要及 diff。只有交互终端新输入 `yes` 才可执行；其他输入拒绝。确认后重读计划，与展示内容不一致就拒绝；服务端还需要检查原文件版本、备份及计划单次消费。模型传 `confirmed:true`、`/call` 或重命名 MC 连接都不会绕过此确认。通过管道或 `--prompt` 执行修改默认拒绝。本地 `bash`、`edit`、`write` 走同一条确认路径（逐次 `yes`、非交互拒绝），区别是它们没有 diff 与哈希校验，见上一节。

`confirmation.writeTools` 可额外保护第三方写入工具；若需要展示文件差异，用 `confirmation.previewTools` 将其指向只读预览工具。预览工具接收相同参数，返回含 `diff` 的 JSON 对象，可使用 `structuredContent` 或文本 JSON。MC 的 `apply_repair → read_repair_plan` 为固定保护，不能通过配置移除；本地 `bash`/`edit`/`write` 由 `cli.mjs` 固定并入该列表，配置文件同样移除不了。未登记的第三方工具不会自动被判断成写入工具，接入前需据其行为配置。

这个确认机制属于本终端。其他 MCP 宿主需要配置自己的人工确认，MCP 协议本身不会替其完成确认。不要把终端形态和插件形态计作两个已验收宿主。

## 操作与证据

- 普通文本：经模型执行任务；`/tools` 看工具，`/tool 名称` 看 schema。
- `/call 名称 {JSON}`：直接调用，用于无模型联调；仍经过同一修改确认。
- `/logs`：本次日志；`/clear` 清空本次对话；`/exit` 退出。
- Ctrl+C 取消当前轮。正在等待人工确认时 Ctrl+C 退出终端，确保挂起输入不会被后续修改当成确认。取消远程调用不等于远程计算已经停止，应核对服务端状态。

日志位于项目 `artifacts/harness/`，记录会话版本、模型名及去掉查询参数的端点、注册表哈希、工具参数/回执、确认摘要和备用切换事件。终端可能缩短显示大回执，日志保存完整回执。日志会包含业务输入和输出，应按项目资料权限管理；现有凭据脱敏不能识别任意业务内容中未知的秘密。

## 维护与已验证边界

```bash
pnpm install --frozen-lockfile
node build.mjs
node --test ../tests/harness-*.mjs
```

依赖锁定于本目录 `pnpm-lock.yaml`，纯核 tarball 位于 `vendor/`；打包时 `BUILD.json` 记录核心哈希、可维护源码哈希和 bundle 哈希。核心来源为提交 `0e10e25b6a8e782cbc4d230f66f89e0d2f2b7371` 的带未提交变更构建，完整保留这个事实；未修改纯核。终端渲染/脱敏源码复制自先前终端入口，现为本项目本地文件，无运行时跨项目引用。

自动测试使用受控模型、回调及本地 HTTP API 验证切换、取消、不重复执行、确认拒绝/同意、参数绑定与凭据脱敏。它们证明通用机制，不证明两个国产模型实际兼容、蒙卡诊断正确、真实 CLEAR-S 运行、两个宿主端到端或 72 小时稳定性验收。业务功能需结合项目工具和真实算例继续验证。
