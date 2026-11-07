"""Real worker/CLI advisories with actual C5G7 multi-group transport and CE data.

Gated by MC_TEST_OPENMC_CONFIG. No provider/DSH calls. Creates only temporary
cases; preserves inputs/results/log evidence before cleanup. Does not assess
physical accuracy, source convergence or the adequacy of small statistics.
"""
import hashlib,json,os,shutil,subprocess,sys,tempfile,time,unittest,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')

@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG启用真实科学提示检查')
class ScientificAdvisoriesReal(unittest.TestCase):
    def test_mg_mismatch_zero_bins_and_ce_temperature_advice_are_visible(self):
        with tempfile.TemporaryDirectory(prefix='mc science audit ') as directory:
            work=Path(directory);workspace=work/'workspace';env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','HARNESS_'))}
            env.update(MC_WORKSPACE=str(workspace),MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()));calls=[]
            def call(name,args):
                run=subprocess.run([sys.executable,str(ROOT/'mcp/cli.py'),'call',name,json.dumps(args)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=90)
                calls.append({'tool':name,'arguments':args,'returncode':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
                self.assertEqual(run.returncode,0,run.stderr);return json.loads(run.stdout)
            try:
                call('openmc_create_model',{'case_dir':'mg','template':'pincell-multigroup','particles':200,'batches':12,'inactive':2})
                before=(workspace/'mg/materials.xml').read_bytes()
                added=call('openmc_define_tally',{'case_dir':'mg','scores':['flux'],'filters':[{'type':'energy','bins':[1e-5,.1,.5,1.,20.,100.,6e5,1e7]}],'tally_id':23})
                energy=added['scientific_checks']['energy_groups'];self.assertEqual(energy['status'],'mismatch');self.assertEqual(energy['library_groups'],7);self.assertTrue(added['warnings'])
                self.assertEqual((workspace/'mg/materials.xml').read_bytes(),before)
                submitted=call('openmc_submit_job',{'case_dir':'mg'})
                for _ in range(120):
                    status=call('openmc_job_status',{'job_id':submitted['job_id']})
                    if status['state'] in ('completed','failed','cancelled','interrupted'):break
                    time.sleep(.1)
                self.assertEqual(status['state'],'completed')
                result=call('openmc_read_results',{'job_id':submitted['job_id']})
                tally=next(t for t in result['tallies'] if t['id']==23)
                self.assertGreater(tally['zero_mean_std_values'],0);self.assertTrue(any('不等于物理量为零' in w for w in result['warnings']))
                self.assertEqual(result['scientific_checks']['energy_groups']['status'],'mismatch')
                call('openmc_create_model',{'case_dir':'ce','template':'pwr-pin-cell'})
                original={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (workspace/'ce').glob('*.xml')}
                checked=call('openmc_validate_inputs',{'case_dir':'ce','semantic_check':True,'api_check':True})
                self.assertTrue(checked['openmc_api']['api_parsed']);self.assertTrue(checked['warnings'])
                temp=checked['openmc_api']['scientific_checks']['temperature']
                self.assertEqual(temp['status'],'metadata_checked',temp)
                self.assertTrue(any(r['source']=='engine_default' for r in temp['materials']))
                self.assertGreater(checked['semantic']['warning_count'],0)
                self.assertFalse(temp['applicability_verified'])
                self.assertEqual(original,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (workspace/'ce').glob('*.xml')})
            finally:
                evidence=ROOT/'artifacts/audit-repair/scientific-real'/uuid.uuid4().hex[:12];evidence.mkdir(parents=True,exist_ok=True)
                print('Scientific evidence: '+str(evidence),file=sys.stderr)
                (evidence/'calls.json').write_text(json.dumps(calls,ensure_ascii=False,indent=2)+'\n')
                if workspace.exists():shutil.copytree(workspace,evidence/'workspace',dirs_exist_ok=True)
if __name__=='__main__':unittest.main()
