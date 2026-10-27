"""Downstream user journeys: real examples, server paths, and selective cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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
                result=self.execute('examples/'+script)
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
                if script=='scan_report_demo.py':
                    self.assertIn('统计警告：',result.stdout)
                    self.assertIn('标准差=不可用',result.stdout)
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
