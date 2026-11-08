"""Actual OpenMC, terminal confirmation and standard MCP flows with public/engineering data."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
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
import xml.etree.ElementTree as ET

from test_repair_cli import terminal_run

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME, '设置MC_TEST_OPENMC_CONFIG启用真实例题与修复流程')
class RealKnowledgeRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='real knowledge repair ')
        cls.directory = Path(cls.temp.name); cls.workspace = cls.directory/'workspace'
        cls.env = {k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))}
        cls.env.update(MC_WORKSPACE=str(cls.workspace),MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()))
        cls.call('openmc_prepare_example', {'case_id':'run-mode-typo','case_dir':'fault-base'})
        cls.failed = cls.call('openmc_submit_job', {'case_dir':'fault-base'})['job_id']
        status=cls.wait(cls.failed)
        if status['state']!='failed':raise AssertionError(status)
        cls.origin=cls.workspace/'.mc-jobs'/cls.failed
        cls.originals={p.relative_to(cls.origin).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in cls.origin.rglob('*') if p.is_file()}

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    @classmethod
    def call(cls,name,args,success=True):
        result=subprocess.run([sys.executable,str(ROOT/'mcp/cli.py'),'call',name,json.dumps(args)],env=cls.env,
                              capture_output=True,text=True,timeout=180)
        if success and result.returncode:raise AssertionError(result.stderr)
        if not success:
            if result.returncode==0:raise AssertionError('Expected failure for '+name)
            return result.stderr
        return json.loads(result.stdout)

    @classmethod
    def wait(cls,identifier):
        end=time.monotonic()+120
        while time.monotonic()<end:
            result=cls.call('openmc_job_status',{'job_id':identifier})
            if result['state'] in ('completed','failed','cancelled','interrupted'):return result
            time.sleep(.1)
        raise AssertionError('Real OpenMC calculation did not finish')

    def evidence(self,name,data):
        directory=ROOT/'artifacts/runs/20261027-knowledge-repair';directory.mkdir(parents=True,exist_ok=True)
        (directory/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')

    def apply_plan(self,plan):
        command=[sys.executable,str(ROOT/'mcp/cli.py'),'call','apply_repair',json.dumps({'plan_id':plan['plan_id']})]
        code,output,transcript,answered=terminal_run(command,self.env,'y')
        self.assertTrue(answered);self.assertEqual(code,0,transcript)
        return json.loads(output),transcript

    def assert_origin_unchanged(self):
        current={p.relative_to(self.origin).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in self.origin.rglob('*') if p.is_file()}
        self.assertEqual(current,self.originals,'Original failed record, input and log must stay intact')

    def test_real_diagnosis_has_evidence_and_source(self):
        data=self.call('openmc_diagnose_job',{'job_id':self.failed})
        self.assertEqual(data['state'],'failed')
        self.assertTrue(any(f.get('source')=='solver.log' for f in data['findings']))
        proposal=data['proposed_repairs'][0]
        self.assertIn('eigenvale',proposal['old_text']);self.assertIn('eigenvalue',proposal['new_text'])
        self.assertIn('"eigenvalue"',proposal['reference']['text'])
        self.assertEqual(proposal['reference']['content_kind'],'official_original')
        self.assert_origin_unchanged();self.evidence('real-diagnosis.json',data)

    def test_legacy_failure_with_remaining_semantic_error_stops_before_rerun_reservation(self):
        # A legacy failed-record fixture (not a newly solved physical case):
        # before this guard existed, a failed snapshot could contain both a
        # run-mode typo and a second definite tally error. Repairing one cannot
        # silently admit the other. Reuse an actual failure's format/log only.
        import shutil, uuid
        identifier=uuid.uuid4().hex
        legacy=self.workspace/'.mc-jobs'/identifier
        shutil.copytree(self.origin,legacy)
        tally=legacy/'work/tallies.xml'
        tree=ET.parse(tally);tree.getroot().find('tally/scores').text='not-a-score';tree.write(tally)
        record=json.loads((legacy/'job.json').read_text())
        record.update(job_id=identifier,work_dir=f'.mc-jobs/{identifier}/work',
                      input_files={p.relative_to(legacy/'work').as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
                                   for p in (legacy/'work').rglob('*') if p.is_file()},
                      provenance={'data_kind':'legacy_failed_record_fixture'},output_files={})
        (legacy/'job.json').write_text(json.dumps(record))
        snapshot={p.relative_to(legacy).as_posix():p.read_bytes() for p in legacy.rglob('*') if p.is_file()}
        prepared=self.call('openmc_prepare_repair',{'job_id':identifier,'target_dir':'legacy-residual-error'})
        plan=prepared['plan'];self.apply_plan(plan)
        count=len(list((self.workspace/'.mc-jobs').glob('*/job.json')))
        result=self.call('openmc_recheck_repair',{'plan_id':plan['plan_id']})
        self.assertIsNone(result['job'])
        self.assertFalse(result['validation']['semantic']['ok'])
        self.assertIn('invalid_score',[p['kind'] for p in result['validation']['semantic']['problems']])
        saved=json.loads((self.workspace/'.mc-plans'/(plan['plan_id']+'.json')).read_text())
        self.assertNotIn('rerun_job_id',saved['context'])
        self.assertEqual(len(list((self.workspace/'.mc-jobs').glob('*/job.json'))),count)
        self.assertEqual(snapshot,{p.relative_to(legacy).as_posix():p.read_bytes() for p in legacy.rglob('*') if p.is_file()})

    def test_confirmed_repair_rechecks_and_reuses_one_new_calculation(self):
        data=self.call('openmc_prepare_repair',{'job_id':self.failed,'target_dir':'accepted-repair'})
        plan=data['plan'];self.assert_origin_unchanged()
        self.assertIn('尚未应用',self.call('openmc_recheck_repair',{'plan_id':plan['plan_id']},False))
        application,transcript=self.apply_plan(plan)
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(lambda _:self.call('openmc_recheck_repair',{'plan_id':plan['plan_id']}),range(2)))
        identifiers={x['job']['job_id'] for x in outcomes}
        self.assertEqual(len(identifiers),1)
        self.assertEqual(sum(not x['reused_submission'] for x in outcomes),1)
        identifier=next(iter(identifiers));status=self.wait(identifier)
        self.assertEqual(status['state'],'completed',status)
        result=self.call('openmc_read_results',{'job_id':identifier})
        self.assertGreater(result['keff']['mean'],0)
        self.assertFalse(result['professional_acceptance'])
        self.assert_origin_unchanged()
        self.evidence('real-repair-rechecked.json',{'prepared':data,'application':application,'rechecks':outcomes,'job':status,'results':result,'terminal':transcript})

    def test_changed_other_input_blocks_recalculation(self):
        data=self.call('openmc_prepare_repair',{'job_id':self.failed,'target_dir':'changed-after-approval'})
        self.apply_plan(data['plan'])
        with (self.workspace/'changed-after-approval/geometry.xml').open('a') as stream:stream.write('\n<!-- external edit -->\n')
        before=set((self.workspace/'.mc-jobs').iterdir())
        self.assertIn('再次改变',self.call('openmc_recheck_repair',{'plan_id':data['plan']['plan_id']},False))
        self.assertEqual(set((self.workspace/'.mc-jobs').iterdir()),before)

    def test_input_change_after_validation_is_rejected_before_solver_starts(self):
        data=self.call('openmc_prepare_repair',{'job_id':self.failed,'target_dir':'validation-copy-race'})
        self.apply_plan(data['plan'])
        before={p for p in (self.workspace/'.mc-jobs').glob('*/job.json')}
        driver=self.directory/'input_race.py'
        driver.write_text('''import json,sys
sys.path.insert(0,sys.argv[1])
import openmc_diagnostics as diagnostics, openmc_jobs as jobs
from openmc_runtime import workspace
original=jobs.submit
def inject(case_dir,*args,**kw):
    target=workspace()/case_dir/'geometry.xml'
    target.write_text(target.read_text()+'\\n<!-- changed after validation -->\\n')
    return original(case_dir,*args,**kw)
jobs.submit=inject
try:
    diagnostics.recheck_repair(sys.argv[2])
except ValueError as error:
    print(json.dumps({'error':str(error)}));sys.exit(1)
''')
        result=subprocess.run([sys.executable,str(driver),str(ROOT/'mcp'),data['plan']['plan_id']],env=self.env,capture_output=True,text=True,timeout=60)
        self.assertNotEqual(result.returncode,0,result.stderr)
        self.assertIn('快照之间发生变化',json.loads(result.stdout)['error'])
        self.assertEqual({p for p in (self.workspace/'.mc-jobs').glob('*/job.json')},before)
        self.assertIn('不能自动重复提交',self.call('openmc_recheck_repair',{'plan_id':data['plan']['plan_id']},False))
        self.evidence('real-repair-input-race.json',{'first_error':json.loads(result.stdout),'plan':self.call('read_repair_plan',{'plan_id':data['plan']['plan_id']})})

    def test_snapshot_mutation_is_not_used_as_original_evidence(self):
        self.call('openmc_prepare_example',{'case_id':'run-mode-typo','case_dir':'mutated-snapshot'})
        identifier=self.call('openmc_submit_job',{'case_dir':'mutated-snapshot'})['job_id'];self.wait(identifier)
        (self.workspace/'.mc-jobs'/identifier/'work/settings.xml').write_text('<settings/>')
        self.assertIn('快照已改变',self.call('openmc_prepare_repair',{'job_id':identifier,'target_dir':'must-not-create'},False))
        self.assertFalse((self.workspace/'must-not-create').exists())

    def test_model_xml_entry_does_not_repair_unused_settings_file(self):
        self.call('openmc_prepare_example',{'case_id':'run-mode-typo','case_dir':'mixed-inputs'})
        folder=self.workspace/'mixed-inputs';model=ET.Element('model')
        for name in ['materials','geometry','settings','tallies']:
            element=ET.parse(folder/(name+'.xml')).getroot()
            if name=='settings':element.find('run_mode').text='eigenvalue'
            if name=='materials':element.find('cross_sections').text='missing-data.h5'
            model.append(element)
        ET.ElementTree(model).write(folder/'model.xml',encoding='utf-8',xml_declaration=True)
        identifier=self.call('openmc_submit_job',{'case_dir':'mixed-inputs'})['job_id']
        self.assertEqual(self.wait(identifier)['state'],'failed')
        diagnosis=self.call('openmc_diagnose_job',{'job_id':identifier})
        self.assertEqual(diagnosis['proposed_repairs'],[])
        self.assertIn('eigenvale',(folder/'settings.xml').read_text())

    def test_plot_failure_cannot_silently_turn_into_ordinary_run(self):
        self.call('openmc_prepare_example',{'case_id':'run-mode-typo','case_dir':'plot-failure'})
        (self.workspace/'plot-failure/plots.xml').write_text('<plots><plot id="1" type="not-a-type"/></plots>')
        identifier=self.call('openmc_submit_job',{'case_dir':'plot-failure','mode':'plot'})['job_id']
        self.assertEqual(self.wait(identifier)['state'],'failed')
        self.assertIn('仅支持普通计算run',self.call('openmc_prepare_repair',{'job_id':identifier,'target_dir':'not-an-ordinary-run'},False))
        self.assertFalse((self.workspace/'not-an-ordinary-run').exists())

    def test_recalculation_can_still_fail_without_erasing_first_failure(self):
        self.call('openmc_prepare_example',{'case_id':'run-mode-typo','case_dir':'two-errors'})
        path=self.workspace/'two-errors/materials.xml';path.write_text(path.read_text().replace('synthetic-mgxs.h5','missing-library.h5'))
        identifier=self.call('openmc_submit_job',{'case_dir':'two-errors'})['job_id'];self.assertEqual(self.wait(identifier)['state'],'failed')
        data=self.call('openmc_prepare_repair',{'job_id':identifier,'target_dir':'partly-repaired'})
        self.apply_plan(data['plan'])
        checked=self.call('openmc_recheck_repair',{'plan_id':data['plan']['plan_id']})
        self.assertTrue(checked['validation']['structure_valid'])
        failed_again=self.wait(checked['job']['job_id'])
        self.assertEqual(failed_again['state'],'failed')
        self.assertNotEqual(identifier,failed_again['job_id'])
        diagnostic=self.call('openmc_diagnose_job',{'job_id':failed_again['job_id']})
        self.assertEqual(diagnostic['proposed_repairs'],[])
        self.assertIn('没有唯一',self.call('openmc_prepare_repair',{'job_id':failed_again['job_id'],'target_dir':'no-invented-fix'},False))
        self.evidence('real-repair-still-fails.json',{'origin_job_id':identifier,'recheck':checked,'failed_again':failed_again,'diagnosis':diagnostic})

    def test_public_official_case_cli_end_to_end(self):
        result=subprocess.run([sys.executable,str(ROOT/'examples/public_case_demo.py'), '--json'],env=self.env,capture_output=True,text=True,timeout=180)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(result.stdout)
        self.assertEqual(data['job']['state'],'completed')
        self.assertEqual(data['results']['provenance']['data_kind'],'public_c5g7_multigroup_example')
        self.assertEqual(data['chart']['points'],500)
        self.assertIn('examples/pincell_multigroup/build_xml.py',data['results']['provenance']['source_url'])
        self.assertFalse(data['professional_acceptance'])
        self.evidence('real-public-case-cli.json',data)

    def test_repair_demo_cli_decline_then_accept(self):
        for choice,expected in [('n','not_applied'),('y','recalculation_completed')]:
            command=[sys.executable,str(ROOT/'examples/repair_demo.py'), '--json','--case-dir','demo-cli-'+choice]
            code,output,transcript,answered=terminal_run(command,self.env,choice)
            self.assertTrue(answered);self.assertEqual(code,0,transcript)
            data=json.loads(output);self.assertEqual(data['outcome'],expected)
            self.assertLess(transcript.index('-  <run_mode>eigenvale'),transcript.index('[y/N]'))
            if choice=='n':self.assertIsNone(data['rerun'])
            else:self.assertNotEqual(data['failed_job']['job_id'],data['rerun']['job_id'])
            self.evidence('real-repair-cli-'+choice+'.json',{'flow':data,'terminal':transcript})

    def test_standard_mcp_official_case_and_interactive_repair(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        service=self.directory/'service.json';service.write_text(json.dumps({'host':'127.0.0.1','port':port,'path':'/mcp','name':'knowledge-repair-test','versionFile':str(ROOT/'VERSION'),'clientName':'mc'}))
        env={**self.env,'MCP_CONFIG_FILE':str(service),'MCP_CONNECTION_FILE':str(self.directory/'connection.json')}
        url=f'http://127.0.0.1:{port}/mcp'
        with (self.directory/'server.log').open('w+') as log:
            server=subprocess.Popen([sys.executable,str(ROOT/'mcp/server.py')],env=env,stdout=log,stderr=log)
            try:
                for _ in range(100):
                    try:urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=.2).close();break
                    except OSError:time.sleep(.05)
                result=subprocess.run([sys.executable,str(ROOT/'examples/public_case_demo.py'), '--json','--mcp-url',url],env=env,capture_output=True,text=True,timeout=180)
                self.assertEqual(result.returncode,0,result.stderr)
                data=json.loads(result.stdout);self.assertEqual(data['job']['state'],'completed')
                self.evidence('real-public-case-mcp.json',data)
                client_folder=self.directory/'separate-client';client_folder.mkdir()
                remote=subprocess.run([sys.executable,str(ROOT/'examples/public_case_demo.py'),'--mcp-url',url],
                    env={**env,'MC_WORKSPACE':str(client_folder/'not-server-data')},cwd=client_folder,capture_output=True,text=True,timeout=180)
                self.assertEqual(remote.returncode,0,remote.stderr)
                self.assertIn('工作区（服务端）：'+str(self.workspace),remote.stdout)
                self.assertNotIn(str(client_folder/'not-server-data'),remote.stdout)
                self.evidence('real-mcp-human-remote-paths.json',{'stdout':remote.stdout,'stderr':remote.stderr})
                discovery=subprocess.run([sys.executable,str(ROOT/'examples/mcp_client.py'),'--url',url],
                    env=env,cwd=client_folder,capture_output=True,text=True,timeout=30)
                self.assertEqual(discovery.returncode,0,discovery.stderr)
                self.assertIn('openmc_submit_job',[tool['name'] for tool in json.loads(discovery.stdout)['tools']])

                for choice in ['n','y']:
                    before_jobs={p.name for p in (self.workspace/'.mc-jobs').iterdir()}
                    command=[sys.executable,str(ROOT/'examples/repair_demo.py'), '--json','--mcp-url',url,'--case-dir','demo-mcp-'+choice]
                    code,output,transcript,answered=terminal_run(command,env,choice)
                    self.assertTrue(answered);self.assertEqual(code,0,transcript)
                    result=json.loads(output)
                    self.assertEqual(result['outcome'],'not_applied' if choice=='n' else 'recalculation_completed')
                    failed=result['failed_job'];plan=result['prepared']['plan']
                    source=self.workspace/result['case']['case_dir']
                    failed_work=self.workspace/'.mc-jobs'/failed['job_id']/'work'
                    self.assertEqual(failed['state'],'failed')
                    for filename,expected in failed['input_hashes'].items():
                        self.assertEqual(hashlib.sha256((source/filename).read_bytes()).hexdigest(),expected)
                        self.assertEqual(hashlib.sha256((failed_work/filename).read_bytes()).hexdigest(),expected)
                    actual_new_jobs={p.name for p in (self.workspace/'.mc-jobs').iterdir()}-before_jobs
                    expected_new_jobs={failed['job_id']}
                    if choice=='n':
                        self.assertIsNone(result['rerun'])
                        current=self.call('read_repair_plan',{'plan_id':plan['plan_id']})
                        self.assertEqual(current['state'],'proposed');self.assertTrue(current['file_unchanged'])
                    else:
                        self.assertEqual(result['rerun']['state'],'completed')
                        self.assertNotEqual(result['rerun']['job_id'],failed['job_id'])
                        expected_new_jobs.add(result['rerun']['job_id'])
                    self.assertEqual(actual_new_jobs,expected_new_jobs)
                    self.assertLess(transcript.index('-  <run_mode>eigenvale'),transcript.index('[y/N]'))

                    self.evidence('real-repair-mcp-'+choice+'.json',{'flow':result,'terminal':transcript})
            finally:server.terminate();server.wait(timeout=15)


if __name__ == '__main__':unittest.main()
