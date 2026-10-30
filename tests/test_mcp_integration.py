"""Actual project MCP and terminal integration using synthetic files; no model calls."""
import asyncio
import json
import os
from pathlib import Path
import pty
import select
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[1]


class ProjectIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mc integration ")
        self.directory = Path(self.temp.name)
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        self.input = self.workspace / "input.txt"
        self.input.write_text("particles = -1\n")
        self.log = (self.directory / "server.log").open("w+")
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.config = self.directory / "service.json"
        self.config.write_text(json.dumps({"host": "127.0.0.1", "port": port, "path": "/mcp", "name": "mc-integration", "version": "0.1.0", "clientName": "mc"}))
        self.registry = self.directory / "registry.json"
        self.registry.write_text(json.dumps([{"name": "mc", "transport": "streamable-http", "url": self.url}]))
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("MCP_", "MC_", "HARNESS_"))}
        self.env.update(MCP_CONFIG_FILE=str(self.config), MC_WORKSPACE=str(self.workspace), MCP_CONNECTION_FILE=str(self.directory / "connection.json"))
        runtime = json.loads((ROOT / 'config/openmc.json').read_text())
        runtime.update(openmc_command=[sys.executable, str(ROOT / 'tests/fixtures/mock_openmc.py')],
                       python_command=[sys.executable], data_kind='mock_process_fixture')
        self.runtime_config = self.directory / 'openmc.json'
        self.runtime_config.write_text(json.dumps(runtime))
        self.env['MC_OPENMC_CONFIG'] = str(self.runtime_config)
        self.server = subprocess.Popen([sys.executable, str(ROOT / "mcp/server.py")], cwd=ROOT / "mcp", env=self.env, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                with urllib.request.urlopen(self.url.replace("/mcp", "/health"), timeout=.2):
                    break
            except OSError:
                if self.server.poll() is not None:
                    self.log.seek(0)
                    self.fail("server failed: " + self.log.read())
                await asyncio.sleep(.05)
        else:
            self.fail("MCP server startup timed out")

    async def asyncTearDown(self):
        self.server.terminate()
        try:
            self.server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.server.kill()
            self.server.wait()
        self.log.close()
        self.temp.cleanup()

    async def call(self, name, arguments):
        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)

    def data(self, result):
        self.assertFalse(result.isError, str(result))
        return result.structuredContent or json.loads(result.content[0].text)

    async def plan(self):
        return self.data(await self.call("preview_repair", {"path": "input.txt", "old_text": "-1", "new_text": "100"}))["plan_id"]

    async def test_discovery_matches_info_and_queries_do_not_fabricate_results(self):
        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
        with urllib.request.urlopen(self.url.replace("/mcp", "/info")) as response:
            info = json.load(response)
        self.assertEqual({t.name for t in tools}, {t["name"] for t in info["tools"]})
        found = {tool.name:tool for tool in tools}
        for tool in tools:
            for field in tool.inputSchema.get('properties',{}).values():self.assertTrue(field.get('description'))
        for name in ['search_knowledge','read_knowledge','openmc_get_official_example','openmc_example_status']:
            self.assertTrue(found[name].annotations.readOnlyHint)
        for name in ['openmc_run_example_step','openmc_submit_job','apply_repair','openmc_export_report']:
            self.assertFalse(found[name].annotations and found[name].annotations.readOnlyHint)
        self.assertEqual(found['openmc_example_status'].inputSchema['properties']['wait_seconds']['maximum'],30)
        submitted = found['openmc_submit_job'].inputSchema['properties']
        self.assertIn('MC_WORKSPACE',submitted['case_dir']['description'])
        self.assertEqual(submitted['threads']['default'],1)
        copied = found['openmc_prepare_case'].inputSchema['properties']
        self.assertIn('新副本目录',copied['target_dir']['description'])
        plotted = found['openmc_plot_scan'].inputSchema['properties']
        self.assertIn('axes键',plotted['x_parameter']['description'])
        self.assertIn('核素',plotted['nuclide']['description'])
        self.assertIn('计分类型',plotted['score']['description'])
        self.assertIn('批次编号',plotted['scan_id']['description'])
        self.assertIn('job_id',found['openmc_submit_job'].outputSchema['properties'])
        self.assertEqual(found['openmc_submit_scan'].inputSchema,
                         next(t for t in info['tools'] if t['name']=='openmc_submit_scan')['inputSchema'])
        self.assertTrue({'openmc_submit_job', 'openmc_job_status', 'openmc_read_results'} <= {t.name for t in tools})
        self.assertFalse(self.data(await self.call("get_case", {"case_id": "CLEAR-S"}))["runnable"])
        self.assertEqual(self.data(await self.call("search_knowledge", {"query": "unfindable_76044865"}))["matches"], [])
        self.assertTrue(self.data(await self.call("search_knowledge", {"query": "particles"}))["matches"])

    async def test_stale_and_ambiguous_errors_are_actionable_over_mcp(self):
        bad = await self.call("preview_repair", {"path": "input.txt", "old_text": "missing", "new_text": "x"})
        self.assertTrue(bad.isError)
        self.assertIn("恰好出现一次", bad.content[0].text)
        plan_id = await self.plan()
        self.input.write_text("edited elsewhere\n")
        bad = await self.call("apply_repair", {"plan_id": plan_id})
        self.assertTrue(bad.isError)
        self.assertIn("文件在预览后发生变化", bad.content[0].text)
        self.assertEqual(self.input.read_text(), "edited elsewhere\n")

    async def test_openmc_job_failure_and_query_use_actual_mcp(self):
        case = self.workspace / 'case'; case.mkdir()
        for name in ('materials', 'geometry', 'settings'):
            (case / (name + '.xml')).write_text('<' + name + '/>')
        (case / 'mock-mode.json').write_text(json.dumps({'mode': 'fail'}))
        submitted = self.data(await self.call('openmc_submit_job', {'case_dir': 'case'}))
        self.assertEqual(submitted['provenance']['execution_kind'], 'mock_process_fixture')
        for _ in range(80):
            status = self.data(await self.call('openmc_job_status', {'job_id': submitted['job_id']}))
            if status['state'] in ('failed', 'completed', 'interrupted'):break
            await asyncio.sleep(.05)
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['exit_code'], 7)
        bad = await self.call('openmc_read_results', {'job_id': submitted['job_id']})
        self.assertTrue(bad.isError)
        self.server.terminate(); self.server.wait(timeout=8)
        self.server = subprocess.Popen([sys.executable, str(ROOT / 'mcp/server.py')],
                                       cwd=ROOT / 'mcp', env=self.env, stdout=self.log, stderr=self.log)
        for _ in range(100):
            try:
                with urllib.request.urlopen(self.url.replace('/mcp', '/health'), timeout=.2):break
            except OSError:await asyncio.sleep(.05)
        recovered = self.data(await self.call('openmc_job_status', {'job_id': submitted['job_id']}))
        self.assertEqual(recovered['state'], 'failed')
        self.assertEqual(recovered['exit_code'], 7)

    async def test_openmc_bad_xml_returns_problem_list_without_computing(self):
        case = self.workspace / 'case'; case.mkdir()
        (case / 'geometry.xml').write_text('<geometry>')
        result = self.data(await self.call('openmc_validate_inputs', {'case_dir': 'case'}))
        self.assertFalse(result['structure_valid'])
        self.assertTrue(result['problems'])

    @unittest.skipIf(os.environ.get('MC_VERIFY_TOOLS_ONLY')=='1','--tools-only明确排除可选Harness宿主测试')
    async def test_noninteractive_harness_refuses_real_project_write(self):
        plan_id = await self.plan()
        result = await asyncio.to_thread(subprocess.run,
            ["node", str(ROOT / "harness/terminal.mjs"), "--registry", str(self.registry), "--prompt", '/call mc__apply_repair ' + json.dumps({"plan_id": plan_id})],
            env=self.env, capture_output=True, text=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("非交互输入不能代替人工确认", result.stdout + result.stderr)
        self.assertEqual(self.input.read_text(), "particles = -1\n")

    @unittest.skipIf(os.environ.get('MC_VERIFY_TOOLS_ONLY')=='1','--tools-only明确排除可选Harness宿主测试')
    async def test_actual_terminal_no_then_yes_changes_real_project_file_once(self):
        plan_id = await self.plan()
        def terminal(decision):
            master, slave = pty.openpty()
            process = subprocess.Popen(["node", str(ROOT / "harness/terminal.mjs"), "--registry", str(self.registry)], stdin=slave, stdout=slave, stderr=slave, env=self.env, close_fds=True)
            os.close(slave)
            captured = bytearray()
            def until(needle):
                deadline = time.monotonic() + 15
                start = len(captured)
                while time.monotonic() < deadline:
                    if needle in captured[start:].decode("utf-8", errors="replace"):
                        return
                    if select.select([master], [], [], .1)[0]:
                        try: chunk = os.read(master, 65536)
                        except OSError: break
                        if not chunk: break
                        captured.extend(chunk)
                raise AssertionError(f"Did not receive {needle}: " + captured.decode("utf-8", errors="replace"))
            try:
                until("你> ")
                command = '/call mc__apply_repair ' + json.dumps({"plan_id": plan_id})
                os.write(master, (command + "\n").encode())
                until("其他输入拒绝 > ")
                self.assertIn("-particles = -1", captured.decode("utf-8", errors="replace"))
                os.write(master, (decision + "\n").encode())
                until("你> ")
                os.write(master, b"/exit\n")
                process.wait(timeout=10)
                return captured.decode("utf-8", errors="replace")
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
                os.close(master)
        output = await asyncio.to_thread(terminal, "no")
        self.assertIn("用户拒绝", output)
        self.assertEqual(self.input.read_text(), "particles = -1\n")
        await asyncio.to_thread(terminal, "yes")
        self.assertEqual(self.input.read_text(), "particles = 100\n")
        duplicate = await self.call("apply_repair", {"plan_id": plan_id})
        self.assertTrue(duplicate.isError)
        self.assertIn("已经执行", duplicate.content[0].text)


if __name__ == "__main__":
    unittest.main()
