"""Downstream user journeys: real examples, server paths, and selective cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

from test_repair_cli import terminal_run

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME,'设置MC_TEST_OPENMC_CONFIG启用下游真实流程测试')
class DownstreamRealFlows(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc downstream ')
        self.root=Path(self.temp.name);self.workspace=self.root/'server workspace';self.workspace.mkdir()
        self.client=self.root/'client cwd';self.client.mkdir()
        self.env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))}
        self.env.update(MC_WORKSPACE=str(self.workspace),MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()))

    def tearDown(self):self.temp.cleanup()

    def execute(self,script,*arguments,timeout=180):
        result=subprocess.run([sys.executable,str(ROOT/script),*arguments],env=self.env,cwd=self.client,capture_output=True,text=True,timeout=timeout)
        self.assertEqual(result.returncode,0,result.stderr)
        return result

    def evidence(self,name,result):
        folder=ROOT/'artifacts/runs/20261027-downstream-docs';folder.mkdir(parents=True,exist_ok=True)
        (folder/(name+'.stdout.txt')).write_text(result.stdout)
        (folder/(name+'.stderr.txt')).write_text(result.stderr)

    def test_three_default_cli_examples_show_files_and_statistics(self):
        for script in ['openmc_demo.py','public_case_demo.py','scan_report_demo.py']:
            with self.subTest(script=script):
                result=self.execute('examples/'+script,*(['--particles','400'] if script=='public_case_demo.py' else []))
                self.evidence('human-'+script,result)
                self.assertIn('工作区（服务端）：'+str(self.workspace),result.stdout)
                self.assertIn('未作专业准确率验收',result.stdout)
                self.assertIn('记录目录：',result.stdout)
                self.assertNotIn('"tallies": [',result.stdout)
                paths=[]
                for line in result.stdout.splitlines():
                    if line.startswith('  ') and '：' in line:
                        value=line.split('：',1)[1]
                        if value.startswith(str(self.workspace)):paths.append(Path(value))
                self.assertTrue(paths)
                self.assertTrue(all(path.exists() for path in paths),paths)
                if script=='public_case_demo.py':
                    record=next(path for path in paths if path.is_dir())/'job.json'
                    value=json.loads(record.read_text());settings=self.workspace/value['case_dir']/'settings.xml'
                    self.assertEqual(ET.parse(settings).getroot().findtext('particles'),'400')
                if script=='scan_report_demo.py':
                    self.assertIn('统计警告：',result.stdout)
                    # 统计量可能是有限值，也可能因非有限值显示“不可用”；只守
                    # “显示标准差”这一用户承诺，不把某次运行的数值状态固化成预期。
                    self.assertRegex(result.stdout, r'标准差=(?:不可用|[0-9])')
                    self.assertIn('macro 是宏观截面倍率',result.stdout)
                    self.assertEqual({path.suffix for path in paths if path.is_file()} & {'.pdf','.doc','.docx','.md','.csv'},
                                     {'.pdf','.doc','.docx','.md','.csv'})

    def test_scan_json_and_real_cleanup_preserve_case_inputs(self):
        result=self.execute('examples/scan_report_demo.py','--json','--formats','markdown')
        data=json.loads(result.stdout);self.evidence('scan-json',result)
        identifier=data['scan']['scan_id']
        self.assertEqual(data['scan']['state'],'completed')
        self.assertEqual(data['scan_plot']['workspace'],str(self.workspace))
        self.assertTrue(Path(data['scan_plot']['server_path']).is_file())
        original=self.workspace/data['case']['case_dir']/'materials.xml';before=original.read_bytes()
        preview=self.execute('scripts/cleanup_artifacts.py','--workspace',str(self.workspace),'--scan-id',identifier,'--json')
        self.assertEqual(json.loads(preview.stdout)['state'],'preview')
        self.assertTrue((self.workspace/'.mc-scans'/identifier).exists())
        removed=self.execute('scripts/cleanup_artifacts.py','--workspace',str(self.workspace),'--scan-id',identifier,'--apply','--json')
        self.evidence('real-cleanup',removed)
        self.assertEqual(json.loads(removed.stdout)['state'],'deleted')
        self.assertFalse((self.workspace/'.mc-scans'/identifier).exists())
        for row in data['scan']['cases']:self.assertFalse((self.workspace/'.mc-jobs'/row['job_id']).exists())
        self.assertEqual(original.read_bytes(),before)
        status=subprocess.run([sys.executable,str(ROOT/'mcp/cli.py'),'call','openmc_scan_status',json.dumps({'scan_id':identifier})],
                              env=self.env,capture_output=True,text=True)
        self.assertNotEqual(status.returncode,0)
        self.assertIn('不存在',status.stderr)
        self.assertEqual(list((self.workspace/'.mc-scans').iterdir()),[])

    def test_human_repair_decline_preserves_confirmation_and_original_failure(self):
        command=[sys.executable,str(ROOT/'examples/repair_demo.py'),'--case-dir','demos/human-repair']
        code,output,transcript,answered=terminal_run(command,self.env,'n')
        self.assertTrue(answered);self.assertEqual(code,0,transcript)
        self.assertIn('未提交重算',output);self.assertIn('原失败编号',output)
        self.assertIn('修复副本（尚未应用修改）：'+str(self.workspace/'demos/human-repair-repaired'),output)
        self.assertIn('eigenvale',(self.workspace/'demos/human-repair/settings.xml').read_text())
        self.assertLess(transcript.index('-  <run_mode>eigenvale'),transcript.index('[y/N]'))

    def test_documented_existing_input_parameter_copy_compute_and_artifact_flow(self):
        def call(name,arguments):
            result=subprocess.run([str(ROOT/'mc.sh'),'call',name,json.dumps(arguments)],env=self.env,
                                  cwd=self.client,capture_output=True,text=True,timeout=180)
            self.assertEqual(result.returncode,0,result.stderr);return json.loads(result.stdout)
        created=call('openmc_prepare_example',{'case_id':'openmc-pincell-mg','case_dir':'cases/tutorial'})
        original=self.workspace/'cases/tutorial/settings.xml';before=original.read_bytes()
        parameters=call('openmc_list_parameters',{'case_dir':created['case_dir']})
        self.assertIn('settings.particles',[p['name'] for p in parameters['parameters']])
        prepared=call('openmc_prepare_case',{'case_dir':created['case_dir'],'target_dir':'cases/tutorial-400','parameters':{'settings.particles':400}})
        self.assertEqual(original.read_bytes(),before)
        self.assertEqual(ET.parse(self.workspace/prepared['case_dir']/'settings.xml').getroot().findtext('particles'),'400')
        validation=call('openmc_validate_inputs',{'case_dir':prepared['case_dir'],'api_check':True});self.assertTrue(validation['structure_valid'])
        job=call('openmc_submit_job',{'case_dir':prepared['case_dir']})
        deadline=time.monotonic()+120
        while True:
            status=call('openmc_job_status',{'job_id':job['job_id']})
            if status['state'] in ('completed','failed','cancelled','interrupted'):break
            self.assertLess(time.monotonic(),deadline);time.sleep(.1)
        self.assertEqual(status['state'],'completed',status)
        result=call('openmc_read_results',{'job_id':job['job_id'],'max_values':10})
        selected=next(t for t in result['tallies'] if t['name']=='Flux spectrum')
        chart=call('openmc_plot_results',{'job_id':job['job_id'],'tally_id':selected['id'],'score':'flux'})
        report=call('openmc_export_report',{'job_id':job['job_id'],'format':'markdown'})
        artifacts=call('openmc_list_artifacts',{'kind':'job','identifier':job['job_id']})
        self.assertTrue(Path(chart['server_path']).is_file());self.assertTrue(Path(report['server_path']).is_file())
        head=Path(report['server_path']).read_text()
        for label in ('模板版本','参数','OpenMC版本','运行时间','随机种子'):
            self.assertIn(label,head)
        self.assertTrue(report['metadata']['openmc_version'],report['metadata'])
        self.assertIsNotNone(report['metadata']['runtime']['duration_seconds'])
        self.assertEqual(report['metadata']['parameters']['particles'],'400')
        self.assertTrue(artifacts['artifacts']);self.assertEqual(original.read_bytes(),before)
        folder=ROOT/'artifacts/runs/20261027-downstream-flow';folder.mkdir(parents=True,exist_ok=True)
        (folder/'existing-input-real.json').write_text(json.dumps({'created':created,'prepared':prepared,'validation':validation,'job':status,'results':result,'chart':chart,'report':report,'original_input_unchanged':True},ensure_ascii=False,indent=2))
