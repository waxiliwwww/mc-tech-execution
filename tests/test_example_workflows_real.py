"""Opt-in public-example lifecycle with real solver, processes and standard MCP."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME,'设置MC_TEST_OPENMC_CONFIG运行真实官方例题工作流')
class OfficialWorkflowReal(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='official workflow ')
        self.root=Path(self.temp.name);self.workspace=self.root/'workspace'
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))}
        self.env.update(MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()),MC_WORKSPACE=str(self.workspace))

    def tearDown(self):
        for record in (self.workspace/'.mc-jobs').glob('*/job.json'):
            value=json.loads(record.read_text())
            if value['state'] not in ('completed','failed','cancelled','interrupted'):
                self.call('openmc_cancel_job',{'job_id':value['job_id']})
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    current=self.call('openmc_job_status',{'job_id':value['job_id']})
                    if current['state'] in ('completed','failed','cancelled','interrupted'):break
                    time.sleep(.05)
                else:self.fail('测试清理尚未结束，保留工作区：'+str(self.workspace))
        self.temp.cleanup()

    def call(self,name,args,success=True):
        p=subprocess.run([sys.executable,str(ROOT/'mcp/cli.py'),'call',name,json.dumps(args)],env=self.env,capture_output=True,text=True,timeout=180)
        if not success:
            self.assertNotEqual(p.returncode,0);return p.stderr
        self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)

    def wait(self,instance,step):
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            s=self.call('openmc_example_status',{'instance_id':instance})
            if s['steps'][step].get('state') in ('completed','failed','cancelled','interrupted'):
                self.assertEqual(s['steps'][step]['state'],'completed',s);return s
            time.sleep(.1)
        self.fail('官方例题步骤超时，保留状态，未自动重提')

    def test_order_duplicate_submission_and_snapshot_integrity_before_real_calculation(self):
        x=self.call('openmc_prepare_official_example',{'example_id':'source-pincell-multigroup','case_dir':'cases/pin','parameters':{'particles':400,'batches':20,'inactive':2}});i=x['instance_id']
        self.assertEqual(x['case_dir'],'cases/pin')
        self.assertIn('顺序无效',self.call('openmc_run_example_step',{'instance_id':i,'step':'compute'},False))
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs=list(pool.map(lambda _:self.call('openmc_run_example_step',{'instance_id':i,'step':'prepare'}),range(2)))
        self.assertEqual(len({x['job_id'] for x in jobs}),1)
        self.assertEqual(jobs[0]['case_dir'],'cases/pin')
        self.assertEqual(jobs[0]['job_case_dir'],'.mc-jobs/'+jobs[0]['job_id']+'/work')
        ready=self.wait(i,'prepare')
        self.assertNotIn('current_case_dir',ready)
        self.assertTrue(all(step['case_dir']=='cases/pin' for step in ready['steps'].values()))
        self.assertEqual(ready['steps']['prepare']['job_case_dir'],jobs[0]['job_case_dir']);self.assertEqual(self.call('openmc_run_example_step',{'instance_id':i,'step':'prepare'})['job_id'],jobs[0]['job_id'])
        p=self.workspace/'.mc-jobs'/jobs[0]['job_id']/'work/settings.xml';original=p.read_bytes();p.write_bytes(original+b'\n<!-- changed -->')
        before={p.name for p in (self.workspace/'.mc-jobs').iterdir()}
        self.assertIn('已改变',self.call('openmc_run_example_step',{'instance_id':i,'step':'data'},False));self.assertEqual(before,{p.name for p in (self.workspace/'.mc-jobs').iterdir()})
        p.write_bytes(original)
        for step in ['data','compute']:
            self.call('openmc_run_example_step',{'instance_id':i,'step':step});ready=self.wait(i,step)
        self.assertEqual(ready['state'],'completed')
        self.assertTrue(all(step['case_dir']=='cases/pin' for step in ready['steps'].values()))
        result=self.call('openmc_read_example_results',{'instance_id':i});self.assertEqual(result['calculation']['particles'],400)
        self.assertEqual({t['id'] for t in result['calculation']['tallies']},{1,2})
        self.assertTrue(Path(result['calculation']['statepoint_server_path']).is_file())
        saved=json.loads((self.workspace/'.mc-jobs'/jobs[0]['job_id']/'work/prepared-example.json').read_text())
        self.assertEqual(saved['original_settings']['batches'],'100');self.assertEqual(saved['effective_settings']['batches'],'20')

    def test_runtime_snapshot_survives_administrator_config_change_without_exposing_private_configuration(self):
        cfg=json.loads(Path(RUNTIME).read_text());cfg['administrator_private_note']='not-for-tool-output'
        local=self.root/'runtime.json';local.write_text(json.dumps(cfg));self.env['MC_OPENMC_CONFIG']=str(local)
        value=self.call('openmc_prepare_official_example',{'example_id':'source-pincell-multigroup','case_dir':'cases/snapshot'})
        self.assertNotIn('not-for-tool-output',json.dumps(value))
        self.assertEqual(value['runtime_summary']['nuclear_data_library'],cfg.get('nuclear_data_library','endfb-vii.1'))
        cfg['python_command']=['/does/not/exist'];cfg['openmc_command']=['/does/not/exist']
        local.write_text(json.dumps(cfg))
        for step in ['prepare','data','compute']:
            self.call('openmc_run_example_step',{'instance_id':value['instance_id'],'step':step})
            result=self.wait(value['instance_id'],step)
        self.assertEqual(result['state'],'completed')
        self.assertNotIn('not-for-tool-output',json.dumps(result))
        job=self.workspace/'.mc-jobs'/result['steps']['data']['job_id']
        saved=json.loads((job/'runtime.json').read_text())
        self.assertNotEqual(saved['python_command'],['/does/not/exist'])
        self.assertEqual((job/'runtime.json').stat().st_mode & 0o777,0o600)

    def test_modified_official_script_cannot_be_executed(self):
        x=self.call('openmc_prepare_official_example',{'example_id':'source-pincell-multigroup','case_dir':'cases/changed'})
        script=self.workspace/'cases/changed/build_xml.py';script.write_text(script.read_text()+'\nraise RuntimeError("injected")\n')
        self.assertIn('被修改',self.call('openmc_run_example_step',{'instance_id':x['instance_id'],'step':'prepare'},False))
        self.assertFalse(list((self.workspace/'.mc-jobs').glob('*/job.json')))

    def test_one_command_timeout_reports_resumable_identity_without_resubmission(self):
        p=subprocess.run([sys.executable,str(ROOT/'mcp/cli.py'),'examples','run','source-pincell-multigroup',
            '--dir','cases/cli-timeout','--timeout','0','--json'],env=self.env,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(p.returncode,0,p.stdout)
        failure=json.loads(p.stderr);self.assertIn('等待超时',failure['error'])
        self.assertTrue(failure['instance_id']);self.assertTrue(failure['job_id'])
        self.assertIn(failure['instance_id'],failure['next_actions'][0])
        instance=failure['instance_id'];before=self.call('openmc_example_status',{'instance_id':instance})
        repeated=self.call('openmc_run_example_step',{'instance_id':instance,'step':'prepare'})
        self.assertEqual(repeated['job_id'],failure['job_id'])
        self.call('openmc_cancel_example',{'instance_id':instance})

    def test_one_command_invalid_fixed_source_override_reports_failed_identity(self):
        p=subprocess.run([sys.executable,str(ROOT/'mcp/cli.py'),'examples','run','source-pincell-pulsed',
            '--dir','cases/cli-failed','--inactive','2','--json'],env=self.env,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(p.returncode,0,p.stdout)
        failure=json.loads(p.stderr);self.assertEqual(failure['state'],'failed');self.assertTrue(failure['instance_id'])
        job=json.loads((self.workspace/'.mc-jobs'/failure['job_id']/'job.json').read_text())
        self.assertNotEqual(job['exit_code'],0)
        self.assertIn('固定源模式不接受inactive',(self.workspace/'.mc-jobs'/failure['job_id']/'solver.log').read_text())

    def test_notebook_stage_selection_and_tamper_detection_use_the_chosen_native_file(self):
        x=self.call('openmc_prepare_official_example',{'example_id':'notebook-depletion','case_dir':'cases/notebook-selected'})
        identifier=x['instance_id'];job=self.call('openmc_run_example_step',{'instance_id':identifier,'step':'execute'})
        self.wait(identifier,'execute')
        result=self.call('openmc_read_example_results',{'instance_id':identifier})
        candidates=[x for x in result['notebook']['calculations'] if x['state']=='completed' and any(meta.get('role')=='statepoint' for meta in x['outputs'].values())]
        self.assertGreaterEqual(len(candidates),2)
        expected=[]
        for stage in candidates[:2]:
            native=self.call('openmc_read_example_results',{'instance_id':identifier,'calculation_id':stage['id']})['calculation']
            self.assertEqual(native['calculation_id'],stage['id']);self.assertEqual(native['origin_job_id'],job['job_id'])
            self.assertNotIn('job_id',native)
            self.assertIn(native['statepoint_sha256'],[x['sha256'] for x in stage['outputs'].values() if x.get('role')=='statepoint'])
            self.assertTrue(Path(native['statepoint_server_path']).is_file());expected.append(native)
        self.assertNotEqual(expected[0]['statepoint_sha256'],expected[1]['statepoint_sha256'])
        # A real private test output changes; no reference corpus evidence or
        # user files are touched. An unselected intact stage remains readable.
        path=Path(expected[0]['statepoint_server_path']);path.write_bytes(path.read_bytes()+b'test mutation')
        self.assertIn('已改变',self.call('openmc_read_example_results',{'instance_id':identifier,'calculation_id':candidates[0]['id']},False))
        intact=self.call('openmc_read_example_results',{'instance_id':identifier,'calculation_id':candidates[1]['id']})['calculation']
        self.assertEqual(intact['statepoint_sha256'],expected[1]['statepoint_sha256'])
        self.assertIn('不能默认取最后StatePoint',self.call('openmc_read_results',{'job_id':job['job_id']},False))

    def test_same_stepwise_calls_over_standard_mcp(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        service=self.root/'service.json';service.write_text(json.dumps({'host':'127.0.0.1','port':port,'path':'/mcp','name':'official-workflows','version':'test','clientName':'mc'}))
        env={**self.env,'MCP_CONFIG_FILE':str(service),'MCP_CONNECTION_FILE':str(self.root/'connection.json')}
        with (self.root/'mcp.log').open('w+') as log:
            process=subprocess.Popen([sys.executable,str(ROOT/'mcp/server.py')],env=env,stdout=log,stderr=log,cwd=ROOT/'mcp')
            try:
                for _ in range(200):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.2):break
                    except OSError:
                        if process.poll() is not None:log.seek(0);self.fail(log.read())
                        time.sleep(.05)
                async def exercise():
                    from mcp import ClientSession
                    from mcp.client.streamable_http import streamablehttp_client
                    async with streamablehttp_client(f'http://127.0.0.1:{port}/mcp') as (r,w,_):
                        async with ClientSession(r,w) as session:
                            await session.initialize()
                            tools={t.name:t for t in (await session.list_tools()).tools}
                            self.assertIn('example_id',tools['openmc_prepare_official_example'].inputSchema['properties'])
                            async def call(name,args):
                                out=await session.call_tool(name,args);self.assertFalse(out.isError,out);return out.structuredContent or json.loads(out.content[0].text)
                            inventory=await call('openmc_list_official_examples',{});self.assertEqual(inventory['count'],50)
                            x=await call('openmc_prepare_official_example',{'example_id':'source-pincell-multigroup','case_dir':'cases/mcp-original'});i=x['instance_id']
                            for step in ['prepare','data','compute']:
                                await call('openmc_run_example_step',{'instance_id':i,'step':step})
                                deadline=time.monotonic()+120
                                while True:
                                    status=await call('openmc_example_status',{'instance_id':i,'wait_seconds':30});state=status['steps'][step]['state']
                                    if state in ('completed','failed','cancelled','interrupted'):break
                                    self.assertLess(time.monotonic(),deadline);await asyncio.sleep(.1)
                                self.assertEqual(state,'completed',status)
                            result=await call('openmc_read_example_results',{'instance_id':i});self.assertEqual(result['calculation']['particles'],1000)
                            import hashlib
                            import xml.etree.ElementTree as ET
                            record=json.loads((self.workspace/'.mc-examples'/i/'example.json').read_text())
                            settings=ET.parse(self.workspace/record['current_case_dir']/'settings.xml').getroot()
                            self.assertEqual([settings.findtext(k) for k in ('particles','batches','inactive')],['1000','100','10'])
                            self.assertEqual(record['parameters'],{})
                            self.assertTrue(record['source_files'])
                            for filename,expected in record['source_files'].items():
                                self.assertEqual(hashlib.sha256((self.workspace/x['case_dir']/filename).read_bytes()).hexdigest(),expected)
                                self.assertEqual(hashlib.sha256((self.workspace/record['current_case_dir']/filename).read_bytes()).hexdigest(),expected)

                            folder=ROOT/'artifacts/runs/official-examples-20261030';folder.mkdir(parents=True,exist_ok=True)
                            (folder/'standard-mcp-workflow.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
                asyncio.run(exercise())
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()


@unittest.skipUnless(RUNTIME,'设置MC_TEST_OPENMC_CONFIG运行真实Notebook内核检查')
class NotebookFailureEvidence(unittest.TestCase):
    def test_failed_notebook_does_not_display_or_count_unreached_upstream_outputs(self):
        cfg=json.loads(Path(RUNTIME).read_text());python=cfg['python_command']
        with tempfile.TemporaryDirectory(prefix='notebook evidence ') as folder:
            code='''import json,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import nbformat
from notebook_runner import execute_notebook
notebook=nbformat.v4.new_notebook(cells=[
 nbformat.v4.new_code_cell('raise ValueError("intentional-first-failure")'),
 nbformat.v4.new_code_cell('print("unreached code")',execution_count=99,
   outputs=[nbformat.v4.new_output('stream',name='stdout',text='STALE_UPSTREAM_OUTPUT')])])
nbformat.write(notebook,'fixture.ipynb')
try:execute_notebook({'id':'fixture','name':'failure evidence','source_file':'fixture.ipynb','source_revision':'test'})
except Exception:pass
else:raise AssertionError('first failure must remain a failure')
finished=nbformat.read('notebook.executed.ipynb',as_version=4)
assert finished.cells[-1].execution_count is None
assert finished.cells[-1].outputs==[]
assert 'STALE_UPSTREAM_OUTPUT' not in Path('notebook-report.html').read_text()
result=json.loads(Path('notebook-result.json').read_text())
assert result['executed_code_cells']==1
assert len(result['errors'])==1
assert result['upstream_cached_outputs_cleared'] is True
print(json.dumps(result))
'''
            result=subprocess.run(python+['-c',code,str(ROOT/'mcp')],cwd=folder,
                 env={**os.environ,'MC_OPENMC_CONFIG':str(Path(RUNTIME).resolve())},capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)


if __name__=='__main__':unittest.main()
