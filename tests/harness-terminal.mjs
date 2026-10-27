import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { mkdtempSync, writeFileSync, rmSync, copyFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { McpServer } from '../harness/node_modules/@modelcontextprotocol/sdk/dist/esm/server/mcp.js';
import { StreamableHTTPServerTransport } from '../harness/node_modules/@modelcontextprotocol/sdk/dist/esm/server/streamableHttp.js';
import { z } from '../harness/node_modules/zod/index.js';

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..');
function run(command, args) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', chunk => stdout += chunk);
    child.stderr.on('data', chunk => stderr += chunk);
    child.on('error', reject);
    child.on('exit', code => resolve({ code, stdout, stderr }));
  });
}

async function fixture(callback) {
  const directory = mkdtempSync(join(tmpdir(), 'mc-terminal-'));
  let writes = 0;
  const server = createServer(async (req, res) => {
    const mcp = new McpServer({ name: 'confirmation-fixture', version: '1' });
    mcp.registerTool('read_repair_plan', { inputSchema: { plan_id: z.string() } }, async ({ plan_id }) => ({ content: [{ type: 'text', text: JSON.stringify({ plan_id, diff: '-before\n+after', before_sha256: 'a', after_sha256: 'b' }) }] }));
    mcp.registerTool('apply_repair', { inputSchema: { plan_id: z.string() } }, async () => { writes++; return { content: [{ type: 'text', text: 'fixture write applied' }] }; });
    const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
    res.on('close', () => { void transport.close(); void mcp.close(); });
    try { await mcp.connect(transport); await transport.handleRequest(req, res); }
    catch (error) { if (!res.headersSent) { res.writeHead(500); res.end(error.message); } }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const registry = join(directory, 'registry.json');
  writeFileSync(registry, JSON.stringify([{ name: 'mc', transport: 'streamable-http', url: `http://127.0.0.1:${server.address().port}/mcp` }]));
  try { await callback({ directory, registry, writes: () => writes }); }
  finally { await new Promise(resolve => server.close(resolve)); rmSync(directory, { recursive: true, force: true }); }
}

const ptyScript = String.raw`
import os, pty, subprocess, select, sys, time
master, slave = pty.openpty()
child = subprocess.Popen(['node', sys.argv[1], '--registry', sys.argv[2]], stdin=slave, stdout=slave, stderr=slave, close_fds=True)
os.close(slave)
out = b''
state = 0
deadline = time.monotonic() + 45
handled = 0
try:
  while time.monotonic() < deadline:
    ready, _, _ = select.select([master], [], [], 0.1)
    if ready:
      try: data = os.read(master, 65536)
      except OSError: break
      if not data: break
      out += data
      text = out.decode('utf-8', 'replace')
      if state == 0 and '你> ' in text:
        os.write(master, b'/call mc__apply_repair {"plan_id":"fixture-plan"}\n')
        state = 1
      if state == 1 and text.count('仅输入 yes') > handled:
        handled += 1
        os.write(master, (sys.argv[3] + '\n').encode())
        state = 2
      if state == 2 and text.count('你> ') >= 2:
        os.write(master, ('/call write {"path":"' + sys.argv[4] + '","content":"local builtin write"}\n').encode())
        state = 3
      if state == 3 and text.count('仅输入 yes') > handled:
        handled += 1
        os.write(master, (sys.argv[3] + '\n').encode())
        state = 4
      if state == 4 and text.count('你> ') >= 3:
        os.write(master, b'/exit\n')
        state = 5
    if child.poll() is not None: break
  if child.poll() is None:
    child.terminate()
    child.wait(timeout=3)
  sys.stdout.write(out.decode('utf-8', 'replace'))
  if state != 5: raise RuntimeError('confirmation prompt sequence incomplete, state=%d' % state)
  sys.exit(child.returncode)
finally:
  if child.poll() is None: child.kill()
  os.close(master)
`;

test('real HTTP MCP and actual terminal prompt: yes executes once with diff shown, local write behind the same gate', async () => {
  await fixture(async ({ directory, registry, writes }) => {
    for (const choice of ['no', 'yes']) {
      const localFile = join(directory, `builtin-write-${choice}.txt`);
      const result = await run('python3', ['-c', ptyScript, join(project, 'harness/terminal.mjs'), registry, choice, localFile]);
      assert.equal(result.code, 0, result.stderr + result.stdout);
      assert.match(result.stdout, /-before[\s\S]*\+after/);
      assert.match(result.stdout, /fixture-plan/);
      assert.equal(writes(), choice === 'yes' ? 1 : 0);
      assert.match(result.stdout, /\[等待人工确认\] write/, 'local write must ask for confirmation');
      if (choice === 'no') assert.match(result.stdout, /用户拒绝本次修改/);
      assert.equal(existsSync(localFile), choice === 'yes', `local write result for ${choice}`);
    }
  });
});

test('bundled terminal calls real MCP without node_modules and non-interactive writes are denied', async () => {
  await fixture(async ({ directory, registry, writes }) => {
    // Copy only the delivered bundle. No source checkout or dependencies are reachable
    // by relative resolution from this temporary directory.
    const terminal = join(directory, 'terminal.mjs');
    copyFileSync(join(project, 'harness/terminal.mjs'), terminal);
    const list = await run('node', [terminal, '--registry', registry, '--prompt', '/tools']);
    assert.equal(list.code, 0, list.stderr + list.stdout);
    assert.match(list.stdout, /mc__apply_repair/);
    for (const name of ['read', 'bash', 'edit', 'write']) assert.match(list.stdout, new RegExp(`^${name} — `, 'm'), `missing local tool ${name}`);
    const denied = await run('node', [terminal, '--registry', registry, '--prompt', '/call mc__apply_repair {"plan_id":"fixture-plan"}']);
    assert.equal(denied.code, 1, denied.stderr + denied.stdout);
    assert.match(denied.stdout, /非交互输入不能代替人工确认/);
    // Local read-only tool works without a model; the three local writers stay behind the gate.
    const localRead = await run('node', [terminal, '--registry', registry, '--prompt', `/call read {"path":${JSON.stringify(registry)}}`]);
    assert.equal(localRead.code, 0, localRead.stderr + localRead.stdout);
    assert.match(localRead.stdout, /streamable-http/);
    const localWriters = [
      ['bash', { command: 'echo builtin' }],
      ['edit', { path: registry, oldText: 'streamable-http', newText: 'stdio' }],
      ['write', { path: join(directory, 'builtin-denied.txt'), content: 'x' }],
    ];
    for (const [name, input] of localWriters) {
      const refusal = await run('node', [terminal, '--registry', registry, '--prompt', `/call ${name} ${JSON.stringify(input)}`]);
      assert.equal(refusal.code, 1, `${name}: ${refusal.stderr}${refusal.stdout}`);
      assert.match(refusal.stdout, /非交互输入不能代替人工确认/);
    }
    assert.equal(existsSync(join(directory, 'builtin-denied.txt')), false);
    assert.equal(writes(), 0);
  });
});
