"""Standard MCP consumer run inside the owned lifecycle, no model/provider."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'examples'))
from openmc_demo import client

async def run():
    workspace=Path(os.environ['MC_WORKSPACE']);case='slurm/mcp-input'
    async with client(os.environ['MC_TEST_MCP_URL']) as call:
        if os.environ.get('SLURM_REAL_OPENMC')=='1':
            await call('openmc_create_model',{'case_dir':case,'particles':200,'batches':6,'inactive':1})
        else:
            d=workspace/case;d.mkdir(parents=True)
            (d/'materials.xml').write_text('<materials><material id="1"><density units="macro" value="1"/><macroscopic name="fixture"/></material></materials>')
            (d/'geometry.xml').write_text('<geometry><cell id="1" material="1" region="-1"/><surface id="1" type="sphere" coeffs="0 0 0 10" boundary="vacuum"/></geometry>')
            (d/'settings.xml').write_text('<settings><energy_mode>multi-group</energy_mode><particles>100</particles><batches>6</batches><inactive>1</inactive></settings>')
        before={str(p.relative_to(workspace/case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (workspace/case).rglob('*') if p.is_file()}
        prepared=await call('openmc_slurm_prepare',{'case_dir':case,'name':'MCP远端边界验证','resources':{'cpus_per_task':1}})
        assert prepared['network_used'] is False
        s=prepared['submission_id']
        await call('openmc_slurm_submit',{'submission_id':s})
        status=await call('openmc_slurm_status',{'submission_id':s});assert status['state']=='completed',status
        collected=await call('openmc_slurm_collect',{'submission_id':s});j=collected['job_id']
        listed=await call('openmc_list_artifacts',{'kind':'job','identifier':j})
        assert all(item['matches_recorded_output'] is True for item in listed['artifacts']),listed
        final={'submission_id':s,'cluster_job_id':status['cluster_job_id'],'job_id':j,'real_slurm':False,
               'real_openmc':os.environ.get('SLURM_REAL_OPENMC')=='1','professional_acceptance':False}
        if final['real_openmc']:
            data=await call('openmc_read_results',{'job_id':j,'max_values':1000})
            tally=data['tallies'][0];tid=tally['id'];score=tally['scores'][0]
            plotted=await call('openmc_plot_results',{'job_id':j,'tally_id':tid,'score':score})
            report=await call('openmc_export_report',{'job_id':j,'tally_id':tid,'score':score,'format':'markdown'})
            final.update(results=data,plot=plotted,report=report)
            # Independent original StatePoint reader in the actual OpenMC runtime.
            cfg=json.loads(Path(os.environ['MC_OPENMC_CONFIG']).read_text())
            job=json.loads((workspace/'.mc-jobs'/j/'job.json').read_text())
            point=next(name for name in job['output_files'] if name.startswith('statepoint.'))
            script="import openmc,json,sys; p=openmc.StatePoint(sys.argv[1]);t=p.get_tally(id=int(sys.argv[2])); print(json.dumps({'mean':t.mean.reshape(-1).tolist(),'std_dev':t.std_dev.reshape(-1).tolist()}))"
            raw=subprocess.run(cfg['python_command']+['-c',script,str(workspace/'.mc-jobs'/j/'work'/point),str(tid)],capture_output=True,text=True,check=True)
            native=json.loads(raw.stdout)
            assert tally['mean']==native['mean'], (tally.keys(),native)
            assert tally['std_dev']==native['std_dev']
            final['native_values_match']=True
        after={str(p.relative_to(workspace/case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (workspace/case).rglob('*') if p.is_file()}
        assert before==after
        final['original_inputs_unchanged']=True
        Path(os.environ['SLURM_MCP_RECEIPT']).write_text(json.dumps(final,ensure_ascii=False,indent=2))

asyncio.run(run())
