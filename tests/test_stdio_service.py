"""Real stdio/EOF/reconnect contracts; mock solver only for process lifetime.
No model, network service or physics. Each test owns its source copy/workspace.
"""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import Tool
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'mcp'))
from adapters import public_tool
from tools import TOOLS


class StdioService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc stdio 中文 spaces ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('mcp','skills','config'):
            shutil.copytree(ROOT/name,self.root/name,ignore=shutil.ignore_patterns('.venv','logs','__pycache__','*.local.json'))
        shutil.copy2(ROOT/'VERSION',self.root/'VERSION')
        self.workspace = self.root/'workspace';self.workspace.mkdir()
        self.env = {k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))}
        # HTTP configuration must have no effect on stdio startup.
        self.env.update(MC_WORKSPACE=str(self.workspace), MCP_CONFIG_FILE='/no/http/config', MCP_HOST='invalid-http-host')
        self.stderr = (self.root/'stderr.log').open('w+')
        self.addCleanup(self.stderr.close)

    @contextlib.asynccontextmanager
    async def session(self, program=None):
        args = [str(program or self.root/'mcp/stdio_service.py')]
        async with stdio_client(StdioServerParameters(command=sys.executable,args=args,env=self.env),errlog=self.stderr) as (read,write):
            async with ClientSession(read,write) as session:
                await session.initialize()
                yield session

    async def test_shared_discovery_guidance_resources_errors_and_schema_drift(self):
        async with self.session() as session:
            actual = (await session.list_tools()).tools
            expected = [Tool(**public_tool(t)) for t in TOOLS]
            self.assertEqual({t.name:t.model_dump(exclude_none=True) for t in actual},
                             {t.name:t.model_dump(exclude_none=True) for t in expected})
            chapter=await session.call_tool('openmc_get_workflow',{'chapter':'start'})
            data=chapter.structuredContent
            self.assertEqual(data['content'],(self.root/'skills/openmc-workflows/SKILL.md').read_text())
            resource=await session.read_resource(data['uri'])
            self.assertEqual(resource.contents[0].text,data['content'])
            invalid=await session.call_tool('openmc_job_status',{'job_id':'中文 invalid'})
            self.assertTrue(invalid.isError);self.assertIn('无效作业编号',invalid.content[0].text)
            schema=self.root/'mcp/model_validation/schema.json';schema.write_text(schema.read_text()+'\n')
            denied=await session.call_tool('openmc_build_model',{'case_dir':'denied','spec':{}})
            self.assertTrue(denied.isError);self.assertIn('已阻止新操作',denied.content[0].text)
            self.assertFalse((self.workspace/'denied').exists())
            self.assertFalse((await session.call_tool('openmc_list_jobs',{})).isError)
        self.assertFalse((self.root/'mcp/connection.json').exists())

    @unittest.skipUnless(os.environ.get('MC_TEST_WORKFLOW_WHEEL'),'MC_TEST_WORKFLOW_WHEEL enables installed stdio entry')
    async def test_actual_wheel_uses_installed_stdio_entry(self):
        site=self.root/'site'
        result=await asyncio.to_thread(subprocess.run,[sys.executable,'-m','pip','install','--no-deps','--target',str(site),os.environ['MC_TEST_WORKFLOW_WHEEL']],capture_output=True,text=True,timeout=60)
        self.assertEqual(result.returncode,0,result.stderr)
        params=StdioServerParameters(command=sys.executable,args=['-c','from mc_agent_tools.launcher import stdio;raise SystemExit(stdio())'],cwd=str(self.root),env={**self.env,'PYTHONPATH':str(site)})
        async with stdio_client(params,errlog=self.stderr) as (read,write):
            async with ClientSession(read,write) as session:
                hello=await session.initialize()
                self.assertEqual(hello.serverInfo.version,(ROOT/'VERSION').read_text().strip())
                self.assertEqual({t.name for t in (await session.list_tools()).tools},{t['name'] for t in TOOLS})
                self.assertFalse((await session.call_tool('openmc_get_workflow',{'chapter':'start'})).isError)

    async def test_business_prints_and_event_logs_do_not_corrupt_protocol(self):
        fixture=self.root/'noisy.py'
        fixture.write_text('''import sys
sys.path.insert(0,sys.argv[0].rsplit('/',1)[0]+'/mcp')
import server,stdio_service
def noisy():
 print('ordinary 中文 diagnostic, not JSON')
 return {'value':'中文 round trip'}
server.TOOLS=[{'name':'noisy','description':'test printing business function','inputSchema':{'type':'object','properties':{}},'outputSchema':{'type':'object'},'function':noisy}]
raise SystemExit(stdio_service.main())
''')
        async with self.session(fixture) as session:
            result=await session.call_tool('noisy',{})
            self.assertFalse(result.isError);self.assertEqual(result.structuredContent['value'],'中文 round trip')
        self.stderr.flush();self.stderr.seek(0)
        self.assertIn('ordinary 中文 diagnostic, not JSON',self.stderr.read())

    async def test_disconnect_keeps_job_reconnect_queries_and_cancels_same_worker(self):
        cfg=json.loads((ROOT/'config/openmc.json').read_text())
        cfg.update(openmc_command=[sys.executable,str(ROOT/'tests/fixtures/mock_openmc.py')],
                   python_command=[sys.executable],data_kind='mock_process_fixture',job_timeout_seconds=30)
        runtime=self.root/'runtime.json';runtime.write_text(json.dumps(cfg))
        self.env['MC_OPENMC_CONFIG']=str(runtime)
        case=self.workspace/'case';case.mkdir()
        for name in ('materials','geometry','settings'):(case/(name+'.xml')).write_text('<'+name+'/>')
        (case/'mock-mode.json').write_text('{"mode":"long"}')
        identifier=None
        try:
            async with self.session() as session:
                result=await session.call_tool('openmc_submit_job',{'case_dir':'case','name':'跨会话计算'})
                self.assertFalse(result.isError,str(result));identifier=result.structuredContent['job_id']
                for _ in range(100):
                    status=(await session.call_tool('openmc_job_status',{'job_id':identifier})).structuredContent
                    if status['state']=='running':break
                    await asyncio.sleep(.05)
                self.assertEqual(status['state'],'running')
            async with self.session() as session:
                status=(await session.call_tool('openmc_job_status',{'job_id':identifier})).structuredContent
                self.assertEqual(status['state'],'running')
                found=(await session.call_tool('openmc_list_jobs',{'name':'跨会话计算'})).structuredContent
                self.assertEqual(found['total_matches'],1)
                await session.call_tool('openmc_cancel_job',{'job_id':identifier})
                for _ in range(100):
                    status=(await session.call_tool('openmc_job_status',{'job_id':identifier})).structuredContent
                    if status['state']=='cancelled':break
                    await asyncio.sleep(.05)
                self.assertEqual(status['state'],'cancelled')
        finally:
            if identifier:
                await asyncio.to_thread(subprocess.run,[sys.executable,str(ROOT/'mcp/cli.py'),'call','openmc_cancel_job',json.dumps({'job_id':identifier})],env=self.env,capture_output=True,timeout=15)
