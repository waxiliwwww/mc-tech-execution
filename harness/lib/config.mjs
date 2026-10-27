import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

function jsonFile(path) {
  try { return JSON.parse(readFileSync(path, 'utf8')); }
  catch { throw new Error(`无法读取有效 JSON 配置：${path}`); }
}

export function loadConfiguration({ root, configPath, registryPath, environment = process.env }) {
  const path = configPath ? resolve(configPath) : undefined;
  const config = path ? jsonFile(path) : {};
  if (!config || typeof config !== 'object' || Array.isArray(config)) throw new Error('模型配置必须是 JSON 对象');
  const base = path ? dirname(path) : root;
  const secrets = new Set();
  function profile(value, prefix) {
    if (value !== undefined && (!value || typeof value !== 'object' || Array.isArray(value))) throw new Error(`${prefix} 模型配置必须是对象`);
    const result = { ...(value ?? {}) };
    for (const [field, suffix] of [['url', 'URL'], ['model', 'MODEL'], ['apiKey', 'API_KEY'], ['protocol', 'PROTOCOL']]) {
      if (field === 'apiKey' && result.apiKeyEnv !== undefined) continue;
      if (result[field] === undefined && environment[`${prefix}_${suffix}`] !== undefined) result[field] = environment[`${prefix}_${suffix}`];
    }
    if (result.apiKey === undefined && result.apiKeyEnv) {
      if (environment[result.apiKeyEnv] === undefined) throw new Error(`未设置模型密钥环境变量：${result.apiKeyEnv}`);
      result.apiKey = environment[result.apiKeyEnv];
    }
    if (result.apiKey !== undefined && typeof result.apiKey !== 'string') throw new Error(`${prefix} apiKey 必须是字符串`);
    if (result.apiKeyEnv !== undefined && (typeof result.apiKeyEnv !== 'string' || !/^[A-Za-z_][A-Za-z0-9_]*$/.test(result.apiKeyEnv))) throw new Error(`${prefix} apiKeyEnv 必须是环境变量名称`);
    if (result.apiKey) secrets.add(result.apiKey);
    if (result.url !== undefined) {
      let url;
      try { url = new URL(result.url); } catch { throw new Error(`${prefix} 模型 URL 无效`); }
      if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error(`${prefix} 模型需使用不含凭据的 HTTP/HTTPS URL`);
    }
    return result;
  }
  const primary = profile(config.primary, 'MC_PRIMARY');
  const hasFallback = config.fallback !== undefined || environment.MC_FALLBACK_URL;
  const fallback = hasFallback ? profile(config.fallback, 'MC_FALLBACK') : undefined;
  const registryFile = registryPath ? resolve(registryPath) : resolve(base, config.mcpRegistry ?? 'mcp-servers.json');
  const registry = jsonFile(registryFile);
  if (!Array.isArray(registry)) throw new Error('MCP 注册表必须是 JSON 数组');
  const mcpServers = registry.map(server => {
    if (!server || typeof server.name !== 'string' || !server.name) throw new Error('MCP 服务缺少 name');
    if (server.transport !== 'streamable-http') throw new Error('本入口仅接入 HTTP/HTTPS MCP；不会执行注册表中的本地命令');
    let url;
    try { url = new URL(server.url); } catch { throw new Error(`MCP ${server.name} URL 无效`); }
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) throw new Error(`MCP ${server.name} 需使用不含凭据的 HTTP/HTTPS URL`);
    if (server.tools !== undefined && (!Array.isArray(server.tools) || server.tools.some(name => typeof name !== 'string'))) throw new Error(`MCP ${server.name} tools 必须为字符串数组`);
    const headers = {};
    for (const [key, value] of Object.entries(server.headers ?? {})) {
      if (typeof value !== 'string') throw new Error(`MCP ${server.name} header 必须为字符串`);
      headers[key] = value.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, variable) => {
        const secret = environment[variable];
        if (secret === undefined) throw new Error(`未设置 MCP 认证环境变量：${variable}`);
        if (secret) secrets.add(secret);
        return secret;
      });
      if (headers[key]) secrets.add(headers[key]);
      const bearer = /^Bearer\s+(.+)$/i.exec(headers[key]);
      if (bearer) secrets.add(bearer[1]);
    }
    return { name: server.name, transport: 'streamable-http', url: url.href, ...(server.tools ? { tools: server.tools } : {}), ...(Object.keys(headers).length ? { headers } : {}) };
  });
  const confirmation = config.confirmation ?? {};
  if (!confirmation || typeof confirmation !== 'object' || Array.isArray(confirmation)) throw new Error('confirmation 必须是对象');
  if (confirmation.writeTools !== undefined && !Array.isArray(confirmation.writeTools)) throw new Error('confirmation.writeTools 必须为工具名称数组');
  const writeTools = [...new Set(['mc__apply_repair', ...(confirmation.writeTools ?? [])])];
  if (writeTools.some(name => typeof name !== 'string')) throw new Error('confirmation.writeTools 必须为工具名称数组');
  const previewTools = { ...(confirmation.previewTools ?? {}), mc__apply_repair: 'mc__read_repair_plan' };
  return { config, primary, fallback, mcpServers, registryFile, secrets, confirmation: { writeTools, previewTools } };
}
