"""Shared mechanics for the real MCP declarative-model cases.

This module only starts/stops an isolated MCP server and calls public tools.
Scenario descriptions, assertions and expected outcomes belong in each case
file, so a case can be read without opening this helper.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from scripts.mcp_test_session import check_discovery

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples" / "model-specs"
RUNTIME = os.environ.get("MC_TEST_OPENMC_CONFIG")
SKIP_REASON = "MC_TEST_OPENMC_CONFIG启用真实MCP/model-spec校验用例"


class RealMCPCase(unittest.IsolatedAsyncioTestCase):
    """Harness base: one fresh MCP process per test method."""

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mc validation mcp ")
        self.directory = Path(self.temp.name)
        self.workspace = self.directory / "workspace"
        self.workspace.mkdir()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        service = self.directory / "service.json"
        service.write_text(json.dumps({"host": "127.0.0.1", "port": port, "path": "/mcp",
                                       "name": "model-validation-test", "versionFile": str(ROOT / "VERSION"),
                                       "clientName": "model-validation-test"}))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("MC_", "MCP_", "HARNESS_"))}
        env.update(MC_WORKSPACE=str(self.workspace),
                   MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()),
                   MCP_CONFIG_FILE=str(service),
                   MCP_CONNECTION_FILE=str(self.directory / "connection.json"))
        self.log = (self.directory / "server.log").open("w+")
        self.server = subprocess.Popen([sys.executable, str(ROOT / "mcp/server.py")],
                                       cwd=ROOT, env=env,
                                       stdout=self.log, stderr=self.log)
        self.addAsyncCleanup(self._stop)
        for _ in range(150):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=0.2):
                    break
            except OSError:
                if self.server.poll() is not None:
                    self.log.seek(0)
                    self.fail("MCP server failed to start: " + self.log.read())
                await asyncio.sleep(0.05)
        else:
            self.fail("MCP server startup timed out")

    async def _stop(self):
        if getattr(self, "server", None) is not None:
            self.server.terminate()
            try:
                await asyncio.to_thread(self.server.wait, 8)
            except subprocess.TimeoutExpired:
                self.server.kill()
                await asyncio.to_thread(self.server.wait)
        if getattr(self, "log", None) is not None:
            self.log.close()
        if getattr(self, "temp", None) is not None:
            self.temp.cleanup()

    async def call(self, name, arguments):
        async with streamablehttp_client(self.url) as (read, write, _):
            async with ClientSession(read, write) as session:
                await check_discovery(session)
                return await session.call_tool(name, arguments)

    def error_text(self, result):
        return " ".join(getattr(item, "text", "") for item in result.content)

    def data(self, result):
        self.assertFalse(result.isError, self.error_text(result))
        return result.structuredContent or json.loads(result.content[0].text)

    def example(self, name):
        return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
