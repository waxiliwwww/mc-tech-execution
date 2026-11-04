"""Names label durable jobs; ambiguous names never select a job implicitly."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import business
import openmc_jobs as jobs


class JobNames(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc named jobs ')
        self.root=Path(self.temp.name);self.old=business.WORKSPACE;business.WORKSPACE=self.root/'workspace';business.WORKSPACE.mkdir()
        self.addCleanup(setattr,business,'WORKSPACE',self.old);self.addCleanup(self.temp.cleanup)
        config=json.loads((ROOT/'config/openmc.json').read_text())
        config.update(openmc_command=[sys.executable,str(ROOT/'tests/fixtures/mock_openmc.py')],python_command=[sys.executable],data_kind='mock_process_fixture',job_timeout_seconds=15,cpu_count=64)
        self.cfg=self.root/'runtime.json';self.cfg.write_text(json.dumps(config))
        self.env={'MC_WORKSPACE':str(business.WORKSPACE),'MC_OPENMC_CONFIG':str(self.cfg)}
        self.patch=patch.dict(os.environ,self.env);self.patch.start();self.addCleanup(self.patch.stop)
        case=business.WORKSPACE/'new-input';case.mkdir()
        for name in ('materials','geometry','settings'):(case/(name+'.xml')).write_text('<'+name+'/>')
        (case/'mock-mode.json').write_text('{"mode":"success"}')
        self.ids=[];self.addCleanup(self.stop)

    def stop(self):
        for i in self.ids:
            try:jobs.cancel(i);self.wait(i)
            except Exception:pass

    def wait(self,i):
        end=time.monotonic()+10
        while time.monotonic()<end:
            value=jobs.status(i)
            if value['state'] in jobs.TERMINAL:return value
            time.sleep(.04)
        self.fail('worker did not finish')

    def test_duplicate_chinese_names_survive_restart_and_remain_unambiguous(self):
        for _ in range(2):
            value=jobs.submit('new-input',name='组件密度对照');self.ids.append(value['job_id']);self.wait(value['job_id'])
        status=jobs.status(self.ids[0])
        self.assertEqual(status['input_hashes']['settings.xml'],jobs.file_hash(business.WORKSPACE/'new-input/settings.xml'))
        self.assertEqual(status['input_files'],sorted(status['input_hashes']))
        results=jobs.list_jobs(name='组件密度对照',state='completed',limit=1)
        self.assertEqual(results['total_matches'],2);self.assertTrue(results['truncated'])
        self.assertEqual({j['job_id'] for j in jobs.list_jobs(name='组件密度对照')['jobs']},set(self.ids))
        code="import sys,json;sys.path.insert(0,sys.argv[1]);import openmc_jobs;print(json.dumps(openmc_jobs.list_jobs(name='组件密度对照')))"
        run=subprocess.run([sys.executable,'-c',code,str(ROOT/'mcp')],env={**os.environ,**self.env},capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(json.loads(run.stdout)['total_matches'],2)

    def test_unnamed_old_record_and_invalid_name(self):
        value=jobs.submit('new-input');self.ids.append(value['job_id']);self.wait(value['job_id'])
        d=jobs.job_directory(value['job_id']);record=jobs.read(d);record.pop('name',None);jobs.atomic_json(d/'job.json',record)
        self.assertIsNone(jobs.list_jobs()['jobs'][0]['name'])
        for name in ('',' ','x\n','\x00','a'*121,42):
            with self.subTest(name=repr(name)),self.assertRaises(ValueError):jobs.submit('new-input',name=name)
        self.assertEqual(len(list((business.WORKSPACE/'.mc-jobs').glob('*/job.json'))),1)
        for args in ({'limit':True},{'state':'madeup'}):
            with self.assertRaises(ValueError):jobs.list_jobs(**args)


@unittest.skipUnless(os.environ.get('MC_TEST_OPENMC_CONFIG'), '设置MC_TEST_OPENMC_CONFIG验证扫描名称关联')
class NamedScanReal(unittest.TestCase):
    def test_scan_name_is_not_replaced_by_axis_and_child_names_are_searchable(self):
        import openmc_tools as tools
        import openmc_scan as scans
        with tempfile.TemporaryDirectory(prefix='mc named scan ') as temp:
            previous=business.WORKSPACE;business.WORKSPACE=Path(temp)
            try:
                with patch.dict(os.environ,{'MC_WORKSPACE':temp,'MC_OPENMC_CONFIG':os.environ['MC_TEST_OPENMC_CONFIG']}):
                    tools.create_model('base',particles=100,batches=6,inactive=2)
                    scan=scans.submit_scan('base',{'settings.seed':[31,41]},name='组件参数扫描')
                    self.assertEqual(scan['name'],'组件参数扫描')
                    deadline=time.monotonic()+90
                    while time.monotonic()<deadline:
                        result=scans.status(scan['scan_id'])
                        if result['state'] in scans.TERMINAL:break
                        time.sleep(.15)
                    self.assertEqual(result['state'],'completed',result)
                    self.assertEqual(result['name'],'组件参数扫描')
                    for i,child in enumerate(result['cases']):
                        self.assertEqual(jobs.status(child['job_id'])['name'],f'组件参数扫描[{i}]')
                        self.assertEqual(jobs.list_jobs(name=f'组件参数扫描[{i}]')['jobs'][0]['job_id'],child['job_id'])
                    summary=json.loads((Path(temp)/'.mc-scans'/scan['scan_id']/'summary.json').read_text())
                    self.assertEqual(summary['name'],'组件参数扫描')
            finally:business.WORKSPACE=previous
