#!/usr/bin/env node
import { appendFileSync, mkdirSync, readFileSync } from 'node:fs';
import { randomUUID, createHash } from 'node:crypto';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { createHarnessSession, createModelSampler, connectMcpServers, createBuiltinTools, validateInput } from '@helix/agent-harness/standalone';
import { createConfirmedCaller, createFailoverSampler } from './lib/policy.mjs';
import { loadConfiguration } from './lib/config.mjs';
import { createRedactor, createTerminalRenderer } from './lib/render.mjs';

const root = dirname(fileURLToPath(import.meta.url));
const help = `蒙卡技术执行 Harness（Node.js 22）
./run.sh [--config model.json] [--registry mcp-servers.json] [--cwd 目录] [--prompt '任务']
默认注册表为本目录 mcp-servers.json；模型配置只支持 JSON。
无 --config 时：MC_PRIMARY_URL / MC_PRIMARY_MODEL / MC_PRIMARY_API_KEY；
可选 MC_FALLBACK_URL / MC_FALLBACK_MODEL / MC_FALLBACK_API_KEY。
兼容 chat_completions、responses；无鉴权本地模型显式 apiKey:""。
命令：/tools、/tool 名称、/call 名称 {JSON}、/logs、/clear、/exit。
写入工具必须在真实交互终端逐次输入 yes；管道/--prompt 默认拒绝。
本地工具 read、bash、edit、write 默认全部启用；工作目录默认项目根目录，--cwd 可改。
其中 bash、edit、write 属写入工具，同样逐次确认。工作目录不是沙箱。`;
const args = process.argv.slice(2);
let configPath, registryPath, prompt, cwdOption;
for (let i = 0; i < args.length; i++) {
  if (['--help', '-h'].includes(args[i])) { console.log(help); process.exit(0); }
  if (['--config', '--registry', '--cwd', '--prompt'].includes(args[i]) && args[i + 1] !== undefined) {
    const option = args[i], value = args[++i];
    if (option === '--config') configPath = value;
    else if (option === '--registry') registryPath = value;
    else if (option === '--cwd') cwdOption = value;
    else prompt = value;
  } else { console.error('未知选项或缺少值。\n' + help); process.exit(2); }
}
let loaded;
try { loaded = loadConfiguration({ root, configPath, registryPath }); }
catch (error) { console.error(error.message); process.exit(2); }
const { config, primary, fallback, mcpServers, secrets, confirmation } = loaded;
// 本地工具默认全部启用。要收窄：把 localToolNames 改成 ['read']（只留只读）或 []（全关）。
const localToolNames = ['read', 'bash', 'edit', 'write'];
const localWriteToolNames = ['bash', 'edit', 'write'];
const localRoot = resolve(cwdOption ?? resolve(root, '..'));
const localTools = createBuiltinTools({ cwd: localRoot, names: localToolNames });
const redactor = createRedactor(secrets);
const logDir = resolve(root, '../artifacts/harness');
mkdirSync(logDir, { recursive: true });
const logPath = resolve(logDir, `${new Date().toISOString().replaceAll(':', '-')}-${randomUUID().slice(0, 8)}.jsonl`);
const record = (kind, content) => appendFileSync(logPath, JSON.stringify(redactor.value({ time: new Date().toISOString(), kind, content })) + '\n', { mode: 0o600 });
const renderer = createTerminalRenderer({ redactor, record, stream: config.stream ?? true, reasoning: config.reasoning ?? 'off' });
const { print, onEvent } = renderer;
const interactive = Boolean(process.stdin.isTTY && process.stdout.isTTY && prompt === undefined);
const rl = prompt === undefined ? createInterface({ input: process.stdin, output: process.stdout, terminal: interactive, historySize: 0 }) : undefined;
const lines = rl?.[Symbol.asyncIterator]();
let active, connections, session, asking = false, closing = false;
const startup = new AbortController();
function interrupt() {
  if (active) active.abort(new DOMException('用户取消', 'AbortError'));
  else startup.abort(new DOMException('用户取消', 'AbortError'));
  if (!active || asking) { closing = true; rl?.close(); }
}
process.on('SIGINT', interrupt);
rl?.on('SIGINT', interrupt);
async function confirm(request) {
  print(`[等待人工确认] ${request.name}\n参数：${JSON.stringify(request.arguments, null, 2)}\n参数摘要：${request.argumentDigest}`);
  if (request.preview) print(`拟修改差异：\n${request.preview.diff}`);
  process.stdout.write('仅输入 yes 执行本次修改；其他输入拒绝 > ');
  asking = true;
  try {
    const answer = await lines.next();
    request.signal?.throwIfAborted();
    return !answer.done && answer.value.trim().toLowerCase() === 'yes';
  } finally { asking = false; }
}
try {
  print(`连接 MCP 注册表：${loaded.registryFile}`);
  connections = await connectMcpServers(mcpServers, { baseDir: root, timeoutMs: config.toolTimeoutMs ?? 30000, signal: startup.signal, log: message => record('mcp.diagnostic', { message }) });
  const localByName = new Map(localTools.map(tool => [tool.name, tool]));
  const tools = [...localTools, ...connections.tools].map(({ execute, ...definition }) => definition);
  const dispatch = (name, input, context) => {
    const local = localByName.get(name);
    return local ? local.execute(input, { signal: context?.signal }) : connections.callTool(name, input, context);
  };
  // 本地写工具走项目原有的确认层：非交互拒绝、交互逐次 yes，与 MCP 写工具同一策略。
  const policy = { writeTools: [...confirmation.writeTools, ...localWriteToolNames], previewTools: confirmation.previewTools };
  const callTool = createConfirmedCaller({ callTool: dispatch, tools, ...policy, confirm, interactive, record, validateInput });
  // Construct and validate the adapters at the first natural-language request.
  // /tools and /call therefore need no model credential or model API call.
  let sample;
  const configuredSampler = model => {
    if (model.apiKey === undefined) throw new Error('请显式配置 apiKeyEnv 或 apiKey；无鉴权本地模型使用 apiKey:""。');
    return createModelSampler(model, { stream: config.stream ?? true, reasoning: config.reasoning ?? 'off' });
  };
  const sampler = (request, signal) => {
    sample ??= createFailoverSampler({ primary: configuredSampler(primary), fallback: fallback ? configuredSampler(fallback) : undefined, primaryName: primary.model, fallbackName: fallback?.model, record });
    return sample(request, signal);
  };
  session = createHarnessSession({ tools, callTool, sample: sampler, onEvent, maxSteps: config.maxSteps ?? 12, maxOutputTokens: primary.maxOutputTokens ?? 4096, timeoutMs: primary.timeoutMs ?? 60000, stream: config.stream ?? true, reasoning: config.reasoning ?? 'off', systemPrompt: config.systemPrompt ?? '你是蒙卡软件智能助手。通过提供的 MCP 工具读取真实资料和结果；MCP 工具的路径相对 workspace/，本地工具 read、bash、edit、write 的工作目录是项目根目录。读取已知路径的文件用 read（无需确认）；只有需要列目录或搜索内容时才用 bash，它每次都要用户确认。修改项目输入文件时优先走 preview_repair 再 apply_repair：它会展示差异并做哈希校验与备份；本地 edit/write 没有这些保护，只在 MCP 修复链不适用时才用。本地 bash、edit、write 需要用户在真实终端逐次输入 yes，非交互环境下会被拒绝；被拒绝就停下，既不重试也不换工具绕开，更不能代替用户确认。bash 能改文件、能联网，只在确有必要时使用。根据工具实际回执回答，不把模拟例题、静态资料或未执行计划当成真实蒙卡运行结果。' });
  const modelMetadata = model => model && ({ model: model.model, protocol: model.protocol ?? 'chat_completions', url: model.url ? (() => { const url = new URL(model.url); url.search = ''; return url.href; })() : undefined });
  let build;
  try { build = JSON.parse(readFileSync(resolve(root, 'BUILD.json'), 'utf8')); } catch { build = { unavailable: true }; }
  record('session.started', { tools: tools.map(tool => ({ name: tool.name, serverName: tool.serverName, originalName: tool.originalName })), localTools: { names: localTools.map(tool => tool.name), workingDirectory: localRoot }, models: { primary: modelMetadata(primary), fallback: modelMetadata(fallback) }, confirmation: policy, build, registrySha256: createHash('sha256').update(readFileSync(loaded.registryFile)).digest('hex'), interactive });
  print(`已连接 ${mcpServers.length} 个 MCP 服务，发现 ${connections.tools.length} 个工具 + ${localTools.length} 个本地工具（工作目录 ${localRoot}）。日志：${logPath}`);
  if (!primary.url || !primary.model) print('模型尚未配置；/tools 和 /call 可用。自然语言任务需要模型配置。');
  async function handle(line) {
    line = line.trim();
    if (!line) return;
    if (line === '/exit') { closing = true; return; }
    if (line === '/help') { print(help); return; }
    if (line === '/logs') { print(logPath); return; }
    if (line === '/tools') { for (const tool of tools) print(`${tool.name} — ${tool.description}`); return; }
    if (line.startsWith('/tool ')) { print(JSON.stringify(tools.find(tool => tool.name === line.slice(6).trim()) ?? { error: '未注册工具' }, null, 2)); return; }
    if (line === '/clear') { session.clear(); record('session.cleared', {}); print('已清空对话。'); return; }
    active = new AbortController();
    try {
      if (line.startsWith('/call ')) {
        const match = /^\/call\s+(\S+)\s+([\s\S]+)$/.exec(line);
        if (!match) throw new Error('用法：/call 工具名 {JSON}');
        let input;
        try { input = JSON.parse(match[2]); } catch { throw new Error('工具参数不是有效 JSON'); }
        record('direct_call.requested', { name: match[1], arguments: input });
        const result = await callTool(match[1], input, { signal: active.signal });
        onEvent({ kind: 'tool.result', content: { name: match[1], result } });
        if (!interactive && result?.isError) process.exitCode = 1;
      } else {
        if (line.startsWith('/')) throw new Error('未知命令；输入 /help');
        const outcome = await session.run(line, active.signal);
        if (!interactive && outcome.status !== 'completed') process.exitCode = 1;
      }
    } catch (error) {
      renderer.finish();
      print(`[失败] ${error.message}`);
      record('command.failed', { message: error.message, cancelled: active.signal.aborted });
      if (!interactive) process.exitCode = 1;
    } finally { active = undefined; }
  }
  if (prompt !== undefined) await handle(prompt);
  else while (!closing) {
    if (interactive) process.stdout.write('你> ');
    const line = await lines.next();
    if (line.done) break;
    await handle(line.value);
  }
} catch (error) {
  print(`[失败] ${error.message}`);
  record('session.failed', { message: error.message });
  process.exitCode = 1;
} finally {
  renderer.finish();
  session?.close();
  rl?.close();
  if (connections) { try { await connections.close(); } catch (error) { print(`[关闭失败] ${error.message}`); process.exitCode = 1; } }
  record('session.closed', {});
  process.removeListener('SIGINT', interrupt);
}
