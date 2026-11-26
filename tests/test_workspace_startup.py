"""Same durable job through real local/HTTP starts; no provider or physics calls."""
import asyncio,contextlib,hashlib,json,os,socket,subprocess,sys,unittest,urllib.request
from pathlib import Path
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client
import test_stdio_service as fixture
from owned_processes import OwnedProcessTree
sys.path.insert(0,str(fixture.ROOT/'scripts'))
from mcp_test_session import check_discovery

class WorkspaceStartup(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StdioService.asyncSetUp(self)
        self.workspace.rmdir()  # the reused fixture precreates an empty default directory
        self.tree=OwnedProcessTree();self.http=None
        (self.root/'mcp/.venv').symlink_to(Path(sys.prefix),target_is_directory=True)
        self.env.pop('MC_WORKSPACE');self.env.pop('MCP_CONFIG_FILE');self.env.pop('MCP_HOST')
        self.version=(self.root/'VERSION').read_text().strip()
        cfg=json.loads((self.root/'config/openmc.json').read_text())
        cfg.update(openmc_command=[sys.executable,str(fixture.ROOT/'tests/fixtures/mock_openmc.py')],python_command=[sys.executable],data_kind='mock_process_fixture',job_timeout_seconds=30)
        runtime=self.root/'runtime.json';runtime.write_text(json.dumps(cfg));self.env['MC_OPENMC_CONFIG']=str(runtime)

    @contextlib.asynccontextmanager
    async def local(self):
        params=StdioServerParameters(command='/bin/bash',args=[str(self.root/'mcp/stdio.sh')],env=self.env)
        async with stdio_client(params,errlog=self.stderr) as streams:
            async with ClientSession(*streams) as session:
                await check_discovery(session,self.version);yield session

    async def call(self,session,name,args):
        value=await session.call_tool(name,args);self.assertFalse(value.isError,str(value));return value.structuredContent

    async def start_http(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        self.url=f'http://127.0.0.1:{port}/mcp';cfg=self.root/'service.json'
        cfg.write_text(json.dumps({'host':'127.0.0.1','port':port,'path':'/mcp','name':'workspace-regression','versionFile':'VERSION','clientName':'mc'}))
        env={**self.env,'MCP_CONFIG_FILE':str(cfg),'MCP_CONNECTION_FILE':str(self.root/'connection.json'),'PIP_NO_INDEX':'1'}
        self.log=(self.root/'http.log').open('w+');self.addCleanup(self.log.close)
        self.http=subprocess.Popen(['/bin/bash',str(self.root/'mcp/start.sh')],env=env,stdout=self.log,stderr=self.log,start_new_session=True)
        for _ in range(200):
            if self.http.poll() is not None:
                self.log.seek(0);self.fail('owned HTTP service exited: '+self.log.read())
            try:
                with urllib.request.urlopen(self.url.replace('/mcp','/health'),timeout=.2):return
            except OSError:await asyncio.sleep(.05)
        self.fail('owned HTTP service did not become ready')

    async def exercise(self,mode):
        saved=self.root/'saved calculations';explicit=self.root/'explicit calculations'
        descriptor=self.root/'config/launcher.local.json'
        if mode!='default':descriptor.write_text(json.dumps({'workspace':str(saved)}))
        if mode=='explicit':self.env['MC_WORKSPACE']=str(explicit)
        self.workspace=explicit if mode=='explicit' else saved if mode=='saved' else self.root/'workspace'
        case=self.workspace/'original-case';case.mkdir(parents=True,exist_ok=True)
        for name in ('materials','geometry','settings'):(case/(name+'.xml')).write_text('<'+name+'/>')
        (case/'mock-mode.json').write_text('{"mode":"success"}')
        descriptor_before=descriptor.read_bytes() if descriptor.exists() else None
        def hashes(path):return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
        inputs_before=hashes(case)
        try:
            async with self.local() as session:
                original=await self.call(session,'openmc_submit_job',{'case_dir':'original-case','name':'original workspace task'})
                identifier=original['job_id']
                terminal=await self.call(session,'openmc_job_status',{'job_id':identifier,'wait_seconds':30})
                self.assertEqual(terminal['state'],'completed')
            work=self.workspace/'.mc-jobs'/identifier/'work';outputs_before=hashes(work)
            await self.start_http()
            async with streamablehttp_client(self.url) as streams:
                async with ClientSession(streams[0],streams[1]) as session:
                    await check_discovery(session,self.version)
                    listed=await self.call(session,'openmc_list_jobs',{'name':'original workspace task'})
                    self.assertEqual({v['job_id'] for v in listed['jobs']},{identifier})
                    self.assertEqual(Path(listed['workspace']),self.workspace)
                    status=await self.call(session,'openmc_job_status',{'job_id':identifier})
                    self.assertEqual(status['state'],'completed')
                    self.assertIn('MOCK COMPLETED',status['log_tail'])
            async with self.local() as session:
                listed=await self.call(session,'openmc_list_jobs',{'name':'original workspace task'})
                self.assertEqual([v['job_id'] for v in listed['jobs']],[identifier])
            cli=subprocess.run([sys.executable,str(self.root/'mcp/cli.py'),'call','openmc_list_jobs','{}'],env=self.env,capture_output=True,text=True,timeout=15)
            self.assertEqual(cli.returncode,0,cli.stderr);self.assertEqual(json.loads(cli.stdout)['jobs'][0]['job_id'],identifier)
            self.assertEqual(hashes(case),inputs_before);self.assertEqual(hashes(work),outputs_before)
            self.assertEqual(descriptor.read_bytes() if descriptor.exists() else None,descriptor_before)
            if mode!='default':self.assertFalse((self.root/'workspace').exists())
            if mode=='explicit':self.assertFalse(saved.exists())
            record={'mode':mode,'same_original_job_visible_after_stdio_HTTP_and_reconnect':True,'cli_same_workspace':True,'original_inputs_and_work_outputs_unchanged':True,'saved_configuration_unchanged':True,'real_mcp':True,'physics_solver':'controlled process fixture; no physics','provider_requests':0,'tool_count':77}
        finally:
            if self.http is not None:
                cleanup=self.tree.stop(self.http);self.assertTrue(cleanup['verified_empty'],cleanup)
                with socket.socket() as probe:self.assertNotEqual(probe.connect_ex(('127.0.0.1',int(self.url.split(':')[-1].split('/')[0]))),0)
                if 'record' in locals():record['service_cleanup_verified']=True
        evidence=fixture.ROOT/'artifacts/workspace-read-20261126';evidence.mkdir(parents=True,exist_ok=True)
        (evidence/('journey-'+mode+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2))

    async def test_saved_workspace_preserves_original_job_across_transports(self):await self.exercise('saved')
    async def test_explicit_workspace_overrides_saved_across_transports(self):await self.exercise('explicit')
    async def test_existing_default_workspace_is_unchanged_without_saved_config(self):await self.exercise('default')

if __name__=='__main__':unittest.main()
