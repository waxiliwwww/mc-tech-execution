import test from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createConfirmedCaller, createFailoverSampler } from '../harness/lib/policy.mjs';
import { loadConfiguration } from '../harness/lib/config.mjs';
import { createRedactor } from '../harness/lib/render.mjs';
import { createHarnessSession, createModelSampler, validateInput } from '../harness/node_modules/@helix/agent-harness/dist/standalone/index.js';

const collect = async iterable => { const result = []; for await (const item of iterable) result.push(item); return result; };
const answer = async function* () { yield { type: 'text_delta', delta: '完成' }; yield { type: 'completed', finishReason: 'stop' }; };
const failure = async function* () { throw new Error('provider unavailable'); };
const request = { stepId: 'test:0', messages: [{ role: 'user', content: '测试' }], tools: [] };

test('primary failure switches only the current sample and records the selected model', async () => {
  const events = [];
  const result = await collect(createFailoverSampler({ primary: failure, fallback: answer, primaryName: 'one', fallbackName: 'two', record: (...event) => events.push(event) })(request));
  assert.equal(result[0].delta, '完成');
  assert.deepEqual(events[0], ['model.failover', { stepId: 'test:0', from: 'one', to: 'two', reason: 'provider unavailable' }]);
});

test('metadata before failure is discarded; backup output does not mix response IDs', async () => {
  const primary = async function* () { yield { type: 'response_started', responseId: 'old' }; throw new Error('provider down'); };
  assert.deepEqual(await collect(createFailoverSampler({ primary, fallback: answer })(request)), await collect(answer()));
});

for (const chunk of [{ type: 'text_delta', delta: '部分' }, { type: 'reasoning_delta', delta: '思考' }, { type: 'tool_call_delta', index: 0, id: 'c', name: 'write', argumentsDelta: '{' }]) {
  test(`partial ${chunk.type} blocks failover and prevents automatic replay`, async () => {
    let backups = 0;
    const primary = async function* () { yield chunk; throw new Error('network connection lost'); };
    const fallback = async function* () { backups++; yield* answer(); };
    await assert.rejects(collect(createFailoverSampler({ primary, fallback })(request)), /mc_model_partial_output_no_replay/);
    assert.equal(backups, 0);
  });
}

test('cancellation never starts the fallback', async () => {
  const controller = new AbortController();
  let backups = 0;
  const primary = async function* () { controller.abort(new DOMException('取消', 'AbortError')); throw controller.signal.reason; };
  const fallback = async function* () { backups++; yield* answer(); };
  await assert.rejects(collect(createFailoverSampler({ primary, fallback })(request, controller.signal)), /取消/);
  assert.equal(backups, 0);
});

test('fallback failure ends the sample with no endless primary/fallback retry', async () => {
  const events = [];
  await assert.rejects(collect(createFailoverSampler({ primary: failure, fallback: failure, record: (...event) => events.push(event) })(request)), /mc_all_configured_models_failed/);
  assert.equal(events.filter(([kind]) => kind === 'model.failover').length, 1);
  assert.equal(events.filter(([kind]) => kind === 'model.fallback_failed').length, 1);
});

test('real core loop keeps prior tool receipt when a later sample fails over: tool executes once', async () => {
  let samples = 0, executions = 0;
  const primary = async function* () {
    if (samples++ === 0) {
      yield { type: 'tool_call_delta', index: 0, id: 'call-1', name: 'demo__add', argumentsDelta: '{"a":2,"b":3}' };
      yield { type: 'completed', finishReason: 'tool_calls' };
    } else throw new Error('API unavailable');
  };
  const fallback = async function* (input) {
    assert.equal(input.messages.filter(message => message.role === 'tool').length, 1);
    yield* answer();
  };
  const session = createHarnessSession({ tools: [{ name: 'demo__add', inputSchema: { type: 'object', properties: { a: { type: 'number' }, b: { type: 'number' } }, required: ['a', 'b'] } }], callTool: async () => { executions++; return { content: [{ type: 'text', text: '5' }] }; }, sample: createFailoverSampler({ primary, fallback }) });
  try { assert.equal((await session.run('算2+3')).status, 'completed'); assert.equal(executions, 1); }
  finally { session.close(); }
});

const tools = [
  { name: 'mc__apply_repair', originalName: 'apply_repair', serverName: 'mc', inputSchema: { type: 'object', properties: { plan_id: { type: 'string' } }, required: ['plan_id'], additionalProperties: true } },
  { name: 'mc__read_repair_plan', originalName: 'read_repair_plan', serverName: 'mc', inputSchema: { type: 'object', properties: { plan_id: { type: 'string' } }, required: ['plan_id'] } }
];
function approvalFixture(overrides = {}) {
  const calls = [], events = [];
  let previews = 0;
  const callTool = async (name, input) => {
    calls.push({ name, input: structuredClone(input) });
    if (name.endsWith('read_repair_plan')) {
      previews++;
      return { content: [{ type: 'text', text: JSON.stringify({ plan_id: input.plan_id, diff: overrides.previewDiff?.(previews) ?? '-旧\n+新', before_sha256: 'before', after_sha256: 'after' }) }] };
    }
    return { content: [{ type: 'text', text: '已写入' }] };
  };
  const caller = createConfirmedCaller({ tools, callTool, writeTools: ['mc__apply_repair'], previewTools: { mc__apply_repair: 'mc__read_repair_plan' }, interactive: true, confirm: async () => true, record: (...event) => events.push(event), validateInput, ...overrides });
  return { caller, calls, events };
}
test('user rejects a write: preview is read but no write is executed', async () => {
  const fixture = approvalFixture({ confirm: async () => false });
  assert.equal((await fixture.caller('mc__apply_repair', { plan_id: 'p1' })).executionState, 'not_started');
  assert.equal(fixture.calls.filter(call => call.name === 'mc__apply_repair').length, 0);
});
test('explicit confirmation binds exact args and preview to the actual write', async () => {
  const input = { plan_id: 'p1' };
  const fixture = approvalFixture({ confirm: async shown => { assert.equal(shown.preview.diff, '-旧\n+新'); assert.equal(shown.arguments.plan_id, 'p1'); input.plan_id = 'changed-externally'; shown.arguments.plan_id = 'changed-by-callback'; return true; } });
  assert.equal((await fixture.caller('mc__apply_repair', input)).isError, undefined);
  assert.deepEqual(fixture.calls.at(-1), { name: 'mc__apply_repair', input: { plan_id: 'p1' } });
  assert.ok(fixture.events.find(([name]) => name === 'confirmation.approved'));
});
test('non-interactive and model confirmed:true cannot authorize a write', async () => {
  let confirmations = 0;
  const fixture = approvalFixture({ interactive: false, confirm: async () => { confirmations++; return true; } });
  assert.equal((await fixture.caller('mc__apply_repair', { plan_id: 'p1', confirmed: true })).executionState, 'not_started');
  assert.equal(fixture.calls.length, 0);
  assert.equal(confirmations, 0);
});
test('preview changed while awaiting approval: no write', async () => {
  const fixture = approvalFixture({ previewDiff: count => count === 1 ? '-旧\n+新' : '-旧\n+另一个' });
  assert.equal((await fixture.caller('mc__apply_repair', { plan_id: 'p1' })).executionState, 'not_started');
  assert.equal(fixture.calls.filter(call => call.name === 'mc__apply_repair').length, 0);
});
test('preview unavailable denies write before asking for approval', async () => {
  let confirmations = 0;
  const fixture = approvalFixture({ callTool: async () => ({ isError: true, content: [] }), confirm: async () => { confirmations++; return true; } });
  assert.equal((await fixture.caller('mc__apply_repair', { plan_id: 'p1' })).executionState, 'not_started');
  assert.equal(confirmations, 0);
});
test('cancellation after yes but before dispatch prevents the write', async () => {
  const controller = new AbortController();
  const fixture = approvalFixture({ confirm: async () => { controller.abort(new DOMException('取消', 'AbortError')); return true; } });
  await assert.rejects(fixture.caller('mc__apply_repair', { plan_id: 'p1' }, { signal: controller.signal }), /取消/);
  assert.equal(fixture.calls.filter(call => call.name === 'mc__apply_repair').length, 0);
});
test('renaming the MC connection does not disable apply_repair confirmation', async () => {
  const renamedTools = tools.map(tool => ({ ...tool, name: tool.name.replace('mc__', 'other__'), serverName: 'other' }));
  const fixture = approvalFixture({ tools: renamedTools, interactive: false });
  assert.equal((await fixture.caller('other__apply_repair', { plan_id: 'p1' })).executionState, 'not_started');
  assert.equal(fixture.calls.length, 0);
});

test('JSON config accepts env/literal model keys and authenticated public MCP; logs redact each secret', () => {
  const directory = mkdtempSync(join(tmpdir(), 'mc-harness-config-'));
  try {
    writeFileSync(join(directory, 'models.json'), JSON.stringify({ primary: { url: 'http://127.0.0.1:1234/v1', model: 'local', apiKey: '' }, fallback: { url: 'https://provider.invalid/v1', model: 'other', apiKey: 'literal-secret' }, mcpRegistry: 'registry.json', confirmation: { writeTools: [] } }));
    writeFileSync(join(directory, 'registry.json'), JSON.stringify([{ name: 'third', transport: 'streamable-http', url: 'https://example.invalid/mcp', headers: { Authorization: 'Bearer ${THIRD_TOKEN}' }, tools: ['lookup'] }]));
    const loaded = loadConfiguration({ root: directory, configPath: join(directory, 'models.json'), environment: { THIRD_TOKEN: 'fixture-secret' } });
    assert.equal(loaded.primary.apiKey, '');
    assert.equal(loaded.fallback.apiKey, 'literal-secret');
    assert.equal(loaded.mcpServers[0].headers.Authorization, 'Bearer fixture-secret');
    assert.ok(loaded.confirmation.writeTools.includes('mc__apply_repair'));
    const redactor = createRedactor(loaded.secrets);
    assert.doesNotMatch(redactor.redact('literal-secret and fixture-secret'), /literal-secret|fixture-secret/);
    writeFileSync(join(directory, 'models.json'), JSON.stringify({ primary: { url: 'https://provider.invalid/v1', model: 'remote', apiKeyEnv: 'MODEL_KEY' }, mcpRegistry: 'registry.json' }));
    assert.equal(loadConfiguration({ root: directory, configPath: join(directory, 'models.json'), environment: { THIRD_TOKEN: 'fixture-secret', MODEL_KEY: 'model-env-secret', MC_PRIMARY_API_KEY: 'unrelated-default' } }).primary.apiKey, 'model-env-secret');
  } finally { rmSync(directory, { recursive: true, force: true }); }
});

test('real model HTTP adapter handles local API failure and falls back without an external request', async () => {
  const requests = [];
  const server = createServer(async (req, res) => {
    let source = ''; for await (const chunk of req) source += chunk;
    const body = JSON.parse(source); requests.push(body.model);
    if (body.model === 'primary') { res.writeHead(503, { 'Content-Type': 'application/json' }); res.end('{"error":{"message":"fixture unavailable"}}'); }
    else { res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ id: 'fixture-reply', choices: [{ index: 0, message: { role: 'assistant', content: '本地测试通过' }, finish_reason: 'stop' }], usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 } })); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/v1`;
    const sampler = createFailoverSampler({ primary: createModelSampler({ url, model: 'primary', apiKey: '' }, { stream: false }), fallback: createModelSampler({ url, model: 'fallback', apiKey: '' }, { stream: false }) });
    const chunks = await collect(sampler(request));
    assert.ok(chunks.some(chunk => chunk.type === 'text_delta' && chunk.delta === '本地测试通过'));
    assert.deepEqual(requests, ['primary', 'fallback']);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test('local Responses API passes through the fixed core adapter', async () => {
  let path, body;
  const server = createServer(async (req, res) => {
    path = req.url;
    let source = ''; for await (const chunk of req) source += chunk;
    body = JSON.parse(source);
    assert.equal(req.headers.authorization, undefined);
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ id: 'response-fixture', status: 'completed', output: [{ type: 'message', id: 'msg-fixture', role: 'assistant', status: 'completed', content: [{ type: 'output_text', text: 'Responses本地测试', annotations: [] }] }], usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 } }));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    const url = `http://127.0.0.1:${server.address().port}/v1`;
    const chunks = await collect(createModelSampler({ url, model: 'local-responses', protocol: 'responses', apiKey: '' }, { stream: false })(request));
    assert.equal(path, '/v1/responses');
    assert.equal(body.model, 'local-responses');
    assert.ok(chunks.some(chunk => chunk.type === 'text_delta' && chunk.delta === 'Responses本地测试'));
  } finally { await new Promise(resolve => server.close(resolve)); }
});
