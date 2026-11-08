#!/usr/bin/env python3
"""Own a Linux/WSL test service, verify its identity/contract, then drain children.

No existing endpoint is accepted. Command arguments are executed without shell
expansion; the verified URL is supplied as MC_TEST_MCP_URL in the environment.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from adapters import public_tool
from owned_processes import OwnedProcessTree
import runtime_identity
from tools import TOOLS
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.types import Tool


def definitions():
    return {t['name']: Tool(**public_tool(t)).model_dump(mode='json', exclude_none=True) for t in TOOLS}


async def check_discovery(session, expected_version=None, expected_tools=None):
    """Validate the exact client session used by a real workflow."""
    expected_version = expected_version or (ROOT / "VERSION").read_text().strip()
    expected_tools = definitions() if expected_tools is None else expected_tools
    initialized = await session.initialize()
    actual_version = initialized.serverInfo.version
    if actual_version != expected_version:
        raise ValueError(f'服务版本不一致：待测包 {expected_version}，实际 {actual_version}；停止测试')
    actual = {}
    cursor = None
    while True:
        result = await session.list_tools(cursor=cursor)
        for tool in result.tools:
            if tool.name in actual:
                raise ValueError('工具清单重复：' + tool.name)
            actual[tool.name] = tool.model_dump(mode='json', exclude_none=True)
        cursor = result.nextCursor
        if not cursor:
            break
    changed = sorted(k for k in actual.keys() & expected_tools.keys() if actual[k] != expected_tools[k])
    missing = sorted(expected_tools.keys() - actual.keys())
    extra = sorted(actual.keys() - expected_tools.keys())
    if changed or missing or extra:
        raise ValueError(f'工具定义不一致，停止测试：missing={missing}, extra={extra}, changed={changed}')
    return actual_version, actual


async def verify_session(url, expected_version, expected_tools, fingerprint):
    """Compare actual MCP discovery, not /info alone or a cached host signature."""
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            actual_version, actual = await check_discovery(session, expected_version, expected_tools)
    with urllib.request.urlopen(url.rsplit('/', 1)[0] + '/info', timeout=5) as response:
        identity = json.load(response)['service']
    if (identity.get('startup_code_fingerprint') != fingerprint
            or identity.get('code_matches_disk') is not True
            or identity.get('version_matches_disk') is not True
            or identity.get('restart_required') is not False):
        raise ValueError('服务代码与待测包不一致或已漂移；停止测试')
    return {'version': actual_version, 'tools': len(actual), 'definitions': actual,
            'definitions_sha256': hashlib.sha256(json.dumps(actual, sort_keys=True).encode()).hexdigest(),
            'code_fingerprint': fingerprint, 'matched': True}


class Interrupted(Exception):
    def __init__(self, signum):
        self.signum = signum


def error_text(error):
    nested = getattr(error, 'exceptions', ())
    return '; '.join(error_text(child) for child in nested) if nested else type(error).__name__ + ': ' + str(error)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='新的证据目录；默认artifacts/test-sessions/随机编号')
    parser.add_argument('--timeout', type=float, default=1800, help='命令超时秒数，默认1800')
    parser.add_argument('command', nargs=argparse.REMAINDER, help='-- 后为测试命令；省略时只验证服务发现')
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    directory = (args.output or ROOT / 'artifacts/test-sessions' / uuid.uuid4().hex).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    workspace = directory / 'workspace'
    workspace.mkdir()
    version = (ROOT / 'VERSION').read_text().strip()
    fingerprint = runtime_identity.code_fingerprint()
    expected = definitions()
    tree = OwnedProcessTree()  # Adopt even workers that start independent sessions.
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    url = f'http://127.0.0.1:{port}/mcp'
    config = directory / 'service.json'
    config.write_text(json.dumps({'host':'127.0.0.1', 'port':port, 'path':'/mcp',
        'name':'openmc-forge', 'clientName':'mc-test', 'versionFile':str(ROOT / 'VERSION')}))
    env = {k:v for k,v in os.environ.items() if not k.startswith(('MC_', 'MCP_')) or k.startswith('MC_TEST_')}
    env.update(MC_WORKSPACE=str(workspace), MCP_CONFIG_FILE=str(config),
               MCP_CONNECTION_FILE=str(directory / 'connection.json'), MC_TEST_MCP_URL=url,
               MC_TEST_SESSION_DIR=str(directory))
    if os.environ.get('MC_TEST_OPENMC_CONFIG'):
        env['MC_OPENMC_CONFIG'] = str(Path(os.environ['MC_TEST_OPENMC_CONFIG']).resolve())
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    receipt = {'package_root':str(ROOT), 'expected_version':version, 'url':url,
               'workspace':str(workspace), 'command':command, 'preflight':None, 'exit_code':1}
    server = worker = None
    previous = {}
    def interrupted(signum, _frame):
        raise Interrupted(signum)
    try:
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            previous[sig] = signal.signal(sig, interrupted)
        with (directory / 'server.log').open('w') as log:
            server = subprocess.Popen([sys.executable, str(ROOT / 'mcp/server.py')], cwd=ROOT,
                                      env=env, stdout=log, stderr=log, start_new_session=True)
            receipt['service_pid'] = server.pid
            deadline = time.monotonic() + 30
            while True:
                if server.poll() is not None:
                    raise RuntimeError('测试服务启动失败；查看server.log，不复用其他服务')
                try:
                    # A health response alone could belong to an unrelated
                    # listener that won the free-port race. Our fresh connection
                    # file is only published after this child successfully binds.
                    if not (directory / 'connection.json').exists():
                        raise OSError('owned service has not bound yet')
                    with urllib.request.urlopen(url.replace('/mcp', '/health'), timeout=.25):
                        break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('测试服务启动超时')
                    time.sleep(.05)
            receipt['preflight'] = asyncio.run(verify_session(url, version, expected, fingerprint))
            (directory / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(f'测试服务已核对：{version}，{len(expected)}个工具，{url}', flush=True)
            if command:
                worker = subprocess.Popen(command, cwd=ROOT, env=env, start_new_session=True)
                receipt['command_pid'] = worker.pid
                receipt['exit_code'] = worker.wait(timeout=args.timeout)
            else:
                receipt['exit_code'] = 0
            # Freeze the same candidate throughout, including command execution.
            if version != (ROOT / 'VERSION').read_text().strip() or fingerprint != runtime_identity.code_fingerprint():
                raise RuntimeError('测试期间待测源码/版本已改变；不能将本轮记为通过')
    except Interrupted as error:
        receipt.update(exit_code=128 + error.signum, error='测试收到信号 ' + str(error.signum))
    except Exception as error:
        receipt.update(exit_code=1, error=error_text(error))
    finally:
        # A second Ctrl-C must not cut the cleanup itself in half.
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        if server is not None:
            if server.poll() is None:
                server.terminate()
                try: server.wait(timeout=5)
                except subprocess.TimeoutExpired: pass
            cleanup = tree.stop(worker or server)
            receipt['cleanup'] = cleanup
            if worker is not None: worker.poll()
            server.poll()
        else:
            receipt['cleanup'] = {'verified_empty':not tree.descendants()}
        with socket.socket() as sock:
            sock.settimeout(.5)
            receipt['port_released'] = sock.connect_ex(('127.0.0.1', port)) != 0
        if not receipt['cleanup']['verified_empty'] or not receipt['port_released']:
            receipt.update(exit_code=1, cleanup_error='测试进程或端口未确认释放，不记通过')
        (directory / 'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    print(f"测试结束：exit={receipt['exit_code']}；清理确认={receipt['cleanup']['verified_empty']}；证据：{directory}", flush=True)
    if receipt.get('error'): print(receipt['error'], file=sys.stderr)
    code = receipt['exit_code']
    return 128 - code if code < 0 else code


if __name__ == '__main__':
    sys.exit(main())
