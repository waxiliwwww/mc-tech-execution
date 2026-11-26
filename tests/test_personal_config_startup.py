"""Actual stdio and HTTP reconnect after first migration and a workspace move.
The process fixture produces a job receipt; no model/HPC/physics calls.
"""
import asyncio,hashlib,json,os,shutil,socket,subprocess,sys,unittest,urllib.request
from pathlib import Path
from unittest.mock import patch
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import test_workspace_startup as startup
import personal_config
import test_stdio_service as fixture

class PersonalConfigStartup(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=startup.WorkspaceStartup.asyncSetUp
    local=startup.WorkspaceStartup.local
    call=startup.WorkspaceStartup.call
    async def test_migrate_edit_and_move_keep_original_job_on_local_and_http(self):
        self.env['MC_PERSONAL_CONFIG']=str(self.root/'personal/config.json')
        old=self.root/'original workspace';new=self.root/'moved workspace';old.mkdir()
        launcher=self.root/'config/launcher.local.json';launcher.write_text(json.dumps({'workspace':str(old)}))
        legacy=self.root/'config/openmc.local.json';shutil.copyfile(self.env.pop('MC_OPENMC_CONFIG'),legacy)
        before={p:p.read_bytes() for p in (launcher,legacy)}
        case=old/'original-case';case.mkdir()
        for name in ('materials','geometry','settings'):(case/(name+'.xml')).write_text('<'+name+'/>')
        (case/'mock-mode.json').write_text('{"mode":"success"}')
        async with self.local() as session:
            first=await self.call(session,'openmc_submit_job',{'case_dir':'original-case','name':'personal configuration original task'})
            identifier=first['job_id']
            self.assertEqual((await self.call(session,'openmc_job_status',{'job_id':identifier,'wait_seconds':30}))['state'],'completed')
        def hashes(path):return {str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest() for p in path.rglob('*') if p.is_file()}
        work=old/'.mc-jobs'/identifier/'work';outputs=hashes(work);inputs=hashes(case)
        with patch.dict(os.environ,self.env,clear=True):
            personal_config.ensure(self.root)
            self.assertEqual(personal_config.workspace(self.root),old)
            shutil.move(str(old),str(new))
            with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            personal_config.update({'workspace':str(new),'http':{'host':'127.0.0.1','port':port,'path':'/custom-mcp'}},self.root)
        self.url=f'http://127.0.0.1:{port}/custom-mcp'
        self.log=(self.root/'http.log').open('w+');self.addCleanup(self.log.close)
        env={**self.env,'MCP_CONNECTION_FILE':str(self.root/'connection.json'),'PIP_NO_INDEX':'1'}
        self.http=subprocess.Popen(['/bin/bash',str(self.root/'mcp/start.sh')],env=env,stdout=self.log,stderr=self.log,start_new_session=True)
        try:
            for _ in range(200):
                if self.http.poll() is not None:
                    self.log.seek(0);self.fail(self.log.read())
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.2):break
                except OSError:await asyncio.sleep(.05)
            else:self.fail('owned service did not start')
            async with streamablehttp_client(self.url) as streams:
                async with ClientSession(streams[0],streams[1]) as session:
                    await session.initialize()
                    listed=await self.call(session,'openmc_list_jobs',{})
                    self.assertEqual([j['job_id'] for j in listed['jobs']],[identifier]);self.assertEqual(listed['workspace'],str(new))
                    status=await self.call(session,'openmc_job_status',{'job_id':identifier})
                    self.assertEqual(status['state'],'completed');self.assertIn('MOCK COMPLETED',status['log_tail'])
                    active=await self.call(session,'openmc_environment',{})
                    self.assertEqual(active['configuration']['workspace'],str(new))
                    self.assertEqual(active['configuration']['http']['port'],port)
                    self.assertEqual(active['configuration']['log_directory'],str(new/'.mc-logs'))
                    calls=json.loads((await session.read_resource('mc://logs/tool-calls')).contents[0].text)
                    self.assertIn('openmc_list_jobs',[event['tool'] for event in calls['events']])
                    # An already running process reports its actual workspace/log,
                    # even if the file is edited for the next connection.
                    with patch.dict(os.environ,self.env,clear=True):personal_config.update({'workspace':str(self.root/'next workspace'),'log_directory':str(self.root/'next logs')},self.root)
                    still=await self.call(session,'openmc_environment',{})
                    self.assertEqual(still['configuration']['workspace'],str(new))
                    self.assertEqual(still['configuration']['log_directory'],str(new/'.mc-logs'))
                    calls=json.loads((await session.read_resource('mc://logs/tool-calls')).contents[0].text)
                    self.assertIn('openmc_environment',[event['tool'] for event in calls['events']])
            with patch.dict(os.environ,self.env,clear=True):personal_config.update({'workspace':str(new),'log_directory':str(new/'.mc-logs')},self.root)
            async with self.local() as session:
                listed=await self.call(session,'openmc_list_jobs',{})
                self.assertEqual([j['job_id'] for j in listed['jobs']],[identifier])
            self.assertEqual(hashes(new/'original-case'),inputs)
            self.assertEqual(hashes(new/'.mc-jobs'/identifier/'work'),outputs)
            self.assertEqual({p:p.read_bytes() for p in before},before)
            self.assertFalse(old.exists());self.assertTrue(list((new/'.mc-logs').rglob('*')))
            evidence=fixture.ROOT/'artifacts/personal-config-20261126';evidence.mkdir(parents=True,exist_ok=True)
            (evidence/'journey-migrate-move.json').write_text(json.dumps({'actual_local_and_http':True,'original_job_same':True,'original_inputs_outputs_unchanged':True,'legacy_configs_unchanged':True,'personal_http_settings_used':True,'live_log_workspace_summary_verified':True,'provider_requests':0,'physics_solver':'controlled process fixture'},indent=2))
        finally:
            cleanup=self.tree.stop(self.http);self.assertTrue(cleanup['verified_empty'],cleanup)
            with socket.socket() as sock:self.assertNotEqual(sock.connect_ex(('127.0.0.1',port)),0)

if __name__=='__main__':unittest.main()
