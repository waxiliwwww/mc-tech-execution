"""Real MCP tools + controlled scheduler; optional real OpenMC, never real HPC.

The common lifecycle owns startup/version/full discovery/cleanup on a random
port and workspace. Existing user services, profiles and model config are untouched.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class SlurmMCP(unittest.TestCase):
    def exercise(self, real):
        with tempfile.TemporaryDirectory(prefix='slurm mcp harness ') as tmp:
            folder=Path(tmp);cfg={'transport':'ssh','host':'controlled.invalid','ssh_command':[sys.executable,str(ROOT/'tests/fixtures/slurm_ssh.py')],
                'remote_root':str(folder/'remote'),'python_command':[sys.executable],
                'commands':{name:[sys.executable,str(ROOT/'tests/fixtures/slurm_scheduler.py'),name] for name in ('sbatch','squeue','sacct','scancel')}}
            env={k:v for k,v in os.environ.items() if not k.startswith(('MC_','MCP_','SLURM_'))}
            if real:
                actual=json.loads(Path(os.environ['MC_TEST_OPENMC_CONFIG']).read_text())
                cfg['openmc_command']=actual['openmc_command']
                env.update(MC_TEST_OPENMC_CONFIG=os.environ['MC_TEST_OPENMC_CONFIG'],SLURM_REAL_OPENMC='1')
            else:
                cfg['openmc_command']=[sys.executable,str(ROOT/'tests/fixtures/slurm_solver.py')]
            profile=folder/'slurm.json';profile.write_text(json.dumps(cfg));receipt=folder/'result.json'
            env.update(MC_TEST_SLURM_CONFIG=str(profile),SLURM_FIXTURE_ROOT=str(folder/'scheduler'),SLURM_MCP_RECEIPT=str(receipt))
            output=folder/'session'
            result=subprocess.run([sys.executable,str(ROOT/'scripts/mcp_test_session.py'),'--output',str(output),'--timeout','120','--',sys.executable,str(ROOT/'tests/fixtures/slurm_mcp_client.py')],
                                  env=env,capture_output=True,text=True,timeout=160)
            if result.returncode:
                messages='\n'.join(p.read_text(errors='replace')[-12000:] for p in output.glob('*.log'))
                self.fail(result.stdout+result.stderr+messages)
            value=json.loads(receipt.read_text());self.assertFalse(value['real_slurm']);self.assertTrue(value['original_inputs_unchanged'])
            lifecycle=json.loads((output/'receipt.json').read_text());self.assertTrue(lifecycle['cleanup']['verified_empty'])
            self.assertTrue(lifecycle['port_released'])
            if real:self.assertTrue(value['native_values_match'])
            evidence=ROOT/'artifacts/runs/20261109-slurm-offline';evidence.mkdir(parents=True,exist_ok=True)
            (evidence/('real-openmc-mcp.json' if real else 'controlled-mcp.json')).write_text(json.dumps({'result':value,'lifecycle':lifecycle},ensure_ascii=False,indent=2))
            # Retain native data and receipts outside the disposable test service.
            import shutil,uuid
            retained=evidence/('native-' if real else 'controlled-')/uuid.uuid4().hex
            retained.mkdir(parents=True)
            job=lifecycle['workspace']+'/.mc-jobs/'+value['job_id']
            shutil.copytree(job,retained/'job')
            (retained/'result.json').write_text(receipt.read_text())
            (retained/'lifecycle.json').write_text((output/'receipt.json').read_text())

    def test_standard_mcp_prepare_submit_collect_with_controlled_transport(self):self.exercise(False)

    @unittest.skipUnless(os.environ.get('MC_TEST_OPENMC_CONFIG'),'需真实OpenMC配置；调度边界仍为受控替身，不是HPC验收')
    def test_controlled_scheduler_real_openmc_and_native_selected_results(self):self.exercise(True)
