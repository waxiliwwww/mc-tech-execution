"""A post-compute export failure must retain identifiers and completed results."""
from contextlib import asynccontextmanager,redirect_stdout,redirect_stderr
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples'))
import model_workflow as demo


class WorkflowReceipts(unittest.TestCase):
    def invoke(self,fail=None,receipt=True,machine=False,message='export component failed'):
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)/'receipt.json';self.calls=[]
        @asynccontextmanager
        async def client(url):
            async def call(name,args):
                self.calls.append((name,args))
                if name==fail:raise RuntimeError(message)
                return {
                    'openmc_import_model':{'case_dir':'cases/example','server_path':'/server/workspace/cases/example'},
                    'openmc_list_parameters':{'parameters':[]},
                    'openmc_preview_input':{'files':[{'name':'geometry.xml','sha256':'abc'}]},
                    'openmc_validate_inputs':{'structure_valid':True,'semantic':{'ok':True}},
                    'openmc_submit_job':{'job_id':'a'*32,'state':'queued'},
                    'openmc_read_results':{'keff':{'mean':1.1,'std_dev':0.01},'tallies':[{'id':1,'scores':['flux']}]},
                    'openmc_plot_results':{'server_path':'/server/workspace/.mc-jobs/'+'a'*32+'/plot.png'},
                    'openmc_export_report':{'server_path':'/server/workspace/.mc-jobs/'+'a'*32+'/report.md'},
                    'openmc_list_artifacts':{'directory':'/server/workspace/.mc-jobs/'+'a'*32},
                    'openmc_list_jobs':{'jobs':[]},
                }[name]
            yield call
        async def wait_job(call,identifier,timeout):
            if receipt:
                # Must already exist immediately after submission, before waiting.
                snapshot=json.loads(self.path.read_text())
                assert snapshot['runs'][0]['submitted']['job_id']==identifier
            return {'job_id':identifier,'name':'test','state':'completed','input_hashes':{'geometry.xml':'abc'}}
        argv=['model_workflow.py','--source-dir','incoming','--case-dir','cases/example']
        if receipt:argv+=['--receipt',str(self.path)]
        if machine:argv+=['--json']
        stdout,stderr=io.StringIO(),io.StringIO()
        with patch.object(demo,'client',client),patch.object(demo,'wait_job',wait_job),patch.object(sys,'argv',argv),redirect_stdout(stdout),redirect_stderr(stderr):code=demo.main()
        return code,stdout.getvalue(),stderr.getvalue()

    def test_plot_failure_preserves_completed_compute_and_identifier_without_retry(self):
        code,out,err=self.invoke('openmc_plot_results')
        self.assertEqual(code,1)
        self.assertTrue(self.path.exists(),'failure must persist partial receipt')
        receipt=json.loads(self.path.read_text());self.assertEqual(receipt['state'],'failed')
        self.assertEqual(receipt['stage'],'plot');self.assertEqual(receipt['runs'][0]['job']['state'],'completed')
        self.assertEqual(receipt['runs'][0]['results']['keff']['mean'],1.1)
        self.assertTrue(receipt['runs'][0]['artifacts']['directory'].endswith('a'*32))
        self.assertIn('a'*32,err);self.assertIn('openmc_job_status',err)
        self.assertEqual(sum(name=='openmc_submit_job' for name,args in self.calls),1)

    def test_report_failure_keeps_already_created_chart(self):
        code,out,err=self.invoke('openmc_export_report')
        self.assertEqual(code,1);r=json.loads(self.path.read_text())
        self.assertEqual(r['stage'],'report');self.assertTrue(r['runs'][0]['chart']['server_path'].endswith('/plot.png'))
        self.assertEqual(sum(name=='openmc_submit_job' for name,args in self.calls),1)

    def test_failure_without_receipt_still_shows_context(self):
        code,out,err=self.invoke('openmc_plot_results',receipt=False)
        self.assertEqual(code,1);self.assertIn('a'*32,err);self.assertIn('plot',err);self.assertIn('openmc_job_status',err)

    def test_success_preserves_human_and_json_output(self):
        code,out,err=self.invoke();self.assertEqual(code,0,err);self.assertIn('模型工作流完成',out)
        self.assertIn('/report.md',out);self.assertEqual(json.loads(self.path.read_text())['state'],'completed')
        code,out,err=self.invoke(machine=True);self.assertEqual(code,0,err)
        self.assertEqual(json.loads(out)['runs'][0]['job']['job_id'],'a'*32)

    def test_failure_receipt_and_terminal_redact_known_credentials(self):
        with patch.dict('os.environ',{'MCP_AUTH_TOKEN':'private-token-123'}):
            code,out,err=self.invoke('openmc_export_report',message='failed https://user:pw@host/mcp?token=private-token-123')
        self.assertEqual(code,1)
        for text in (self.path.read_text(),err):
            self.assertNotIn('private-token-123',text);self.assertNotIn('user:pw',text)
            self.assertIn('[redacted]',text)
