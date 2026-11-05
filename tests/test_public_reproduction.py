"""No solver/provider: check handoff, mandatory validation, and failure receipts."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples'))
sys.path.insert(0,str(ROOT/'mcp'))
import reproduce_public_models as demo
from tools import TOOLS
from jsonschema import Draft202012Validator


class ReproductionContract(unittest.TestCase):
    def run_flow(self, model='assembly', incomplete=False, fail=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        receipt=Path(temp.name)/'receipt.json'
        args=argparse.Namespace(model=model,case_dir='reproduction/test',receipt=str(receipt),
            particles=1000,batches=12,inactive=2,threads=1,timeout=60)
        calls=[];n=289 if model=='assembly' else 64
        schemas={t['name']:t['inputSchema'] for t in TOOLS}
        async def call(name,params):
            Draft202012Validator(schemas[name]).validate(params)
            calls.append(name)
            if name==fail:raise RuntimeError('component unavailable')
            if name=='openmc_create_model' and fail=='known_reject':raise demo.ToolCallError('directory exists')
            if name=='openmc_submit_job':
                before=json.loads(receipt.read_text())
                self.assertEqual(before['calls'][-1]['state'],'calling')
            if name=='openmc_job_status':
                self.assertEqual(json.loads(receipt.read_text())['job_id'],'a'*32)
            return {
                'openmc_environment':dict(api_available=True,executable_available=True,version_matches=True),
                'openmc_create_model':{'case_dir':args.case_dir},
                'openmc_define_tally':{'tally_id':73},
                'openmc_preview_input':{'files':[{'name':'geometry.xml','sha256':'abc'}]},
                'openmc_validate_inputs':{'structure_valid':True,'openmc_api':{'api_parsed':True},
                    'semantic':dict(checked=True,ok=True,complete=not incomplete)},
                'openmc_submit_job':{'job_id':'a'*32},
                'openmc_job_status':{'state':'completed','input_hashes':{'geometry.xml':'abc'}},
                'openmc_read_results':{'tallies':[{'id':73,'scores':['flux'],'nuclides':['total'],
                    'values_truncated':False,'mean':[1.0]*n,'std_dev':[.1]*n}], 'warnings':[]},
                'openmc_plot_mesh':{},'openmc_plot_results':{},
                'openmc_export_report':{'selection_mode':'selected','points':n,
                    'selection':{'tally_id':999 if fail=='wrong_report' else 73,'score':'flux',
                                 'nuclide':'total','filter_selection':[]}},
                'openmc_list_artifacts':{'directory':'/server/workspace/.mc-jobs/'+'a'*32},
            }[name]
        error=None
        try:result=asyncio.run(demo.reproduce(args,call))
        except RuntimeError as exc:error=str(exc);result=None
        return result,json.loads(receipt.read_text()),calls,error

    def test_both_models_use_returned_ids_and_valid_schema(self):
        for model in ('assembly','core'):
            with self.subTest(model=model):
                result,receipt,calls,error=self.run_flow(model)
                self.assertIsNone(error)
                self.assertEqual(result['state'],'completed')
                selected=[x['arguments'] for x in receipt['calls'] if x['tool'] in
                          ('openmc_plot_mesh','openmc_plot_results','openmc_export_report')]
                self.assertTrue(all(x['tally_id']==73 and x['job_id']=='a'*32 for x in selected))
                self.assertEqual(calls.count('openmc_submit_job'),1)
                self.assertEqual(receipt['checks']['file_data_crosscheck'],'pending_manual_steps_in_docs/reproduce.html')

    def test_incomplete_semantic_check_stops_before_submission(self):
        result,receipt,calls,error=self.run_flow(incomplete=True)
        self.assertIsNone(result);self.assertNotIn('openmc_submit_job',calls)
        self.assertIn('校核未完整通过',error);self.assertEqual(receipt['state'],'failed')

    def test_same_sized_but_wrong_report_selection_is_rejected(self):
        result,receipt,calls,error=self.run_flow(fail='wrong_report')
        self.assertIsNone(result);self.assertIn('报告未沿用',error)
        self.assertEqual(receipt['job_id'],'a'*32)
        self.assertNotIn('openmc_list_artifacts',calls)

    def test_explicit_rejection_is_separate_from_lost_response(self):
        result,receipt,calls,error=self.run_flow(fail='known_reject')
        self.assertEqual(receipt['calls'][-1]['state'],'tool_error')
        self.assertNotIn('openmc_submit_job',calls)
        result,receipt,calls,error=self.run_flow(fail='openmc_submit_job')
        self.assertEqual(receipt['calls'][-1]['state'],'response_unknown')
        self.assertEqual(calls.count('openmc_submit_job'),1)
        self.assertIsNone(receipt['job_id'])

    def test_export_failure_keeps_original_job_and_never_resubmits(self):
        result,receipt,calls,error=self.run_flow(fail='openmc_export_report')
        self.assertIsNone(result);self.assertIn('a'*32,error)
        self.assertEqual(receipt['job_id'],'a'*32)
        self.assertEqual(calls.count('openmc_submit_job'),1)
        self.assertTrue(any(c['tool']=='openmc_read_results' and c['state']=='returned' for c in receipt['calls']))


if __name__=='__main__':unittest.main()
