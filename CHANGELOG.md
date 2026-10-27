# 变更记录

## 未发布

- 启用固定纯核提供的四个本地工具 `read`、`bash`、`edit`、`write`（核心公开 API `createBuiltinTools`），工作目录默认项目根目录、`--cwd` 可覆盖；其中三个写工具并入既有确认层：非交互一律拒绝，交互终端逐次输入 `yes` 才执行。`vendor/` 里的纯核 tarball 未改，但 `cli.mjs` 改动后 `terminal.mjs` 与 `BUILD.json` 哈希随之重建。
- 这次放开改变了交付口径：工作目录不是沙箱（绝对路径、`..` 可达，`bash` 以当前用户身份执行并可联网），本地工具也不经过 MCP 服务审计。已在 Harness 手册与 README 写明边界、收窄办法（改 `cli.mjs` 的 `localToolNames` 后重建）和「改输入文件仍优先走 MCP 修复链」的理由。
- `tests/harness-terminal.mjs` 增加本地工具回归：只读 `read` 非交互可用、`bash`/`edit`/`write` 非交互被拒且不落盘、pty 下 `yes` 才写入而 `no` 不写入。
- 本机启动不再需要显式 `--config`：`harness/run.sh` 在未传 `--config` 且同目录存在 `model.local.json` 时默认加载它；没有该文件（例如打包分发出去的副本）时仍走 `MC_PRIMARY_*` / `MC_FALLBACK_*` 环境变量模式。
- `harness/model.local.json` 填为本机实测可用的端点与模型（主 `deepseek-v4.1-flash`、备 `qwen3.7-plus`，同一网关），密钥只从环境变量读取；`model.example.json` 降为通用模板，并注明它不是本机配置。
- 本机密钥存放在 `~/.config/mc-tech-execution/env`（权限 600），由 `~/.bashrc` 引入新终端；项目目录内不含任何密钥明文，`package.sh` 仍排除 `model.local.json` 与 `.env*`。
- README 补齐前置条件、模型凭据配置、环境自检和常见失败对照；演示文件路径修正为 `workspace/examples/`。

## 0.1.0 — 2026-10-13

- 建立不依赖其他工作目录的合同技术执行项目，保留规格书、技术PDF和既有文档草稿。
- 复用官方Python MCP模板和固定纯净Harness核心；协议服务与宿主可独立使用。
- 增加修复计划、实际终端确认、哈希保护与备份；增加模型主备切换和不重放边界。
- 增加本地文本查询、日志规则提示和明确标注缺料的算例目录，专业业务仍待接入。
- 以真实MCP和终端、受控模型、搬迁包验证工程能力，未宣称业务或合同验收完成。
## 0.2.0 — 2026-10-27

- 新增8个OpenMC接口和共用直接CLI：示例生成、分层校核、本地计算作业、StatePoint结果及Markdown/CSV报告。
- 固定OpenMC0.16.0；新增运行时配置、合成MGXS示例及CLI/MCP客户端示例。
- 输入快照排除旧产物，读取核对本作业输出哈希；取消/超时清理进程组，非有限统计量明确保留为空。
- 更新15项封装/API/测试/协作计划和使用、测试说明；原辅助能力及独立Harness保留。
- 初始化私有Git；打包加入mc.sh，排除本机配置、运行数据和原始合同资料。
