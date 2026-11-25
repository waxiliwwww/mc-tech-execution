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
        (workspace/case/'in.slurm').write_text('#!/bin/bash\n#SBATCH --nodes=1\n#SBATCH --ntasks=1\n#SBATCH --cpus-per-task=2\n#SBATCH --partition=CPU\n#SBATCH --time=00:02:00\nexit 97\n')
        before={str(p.relative_to(workspace/case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (workspace/case).rglob('*') if p.is_file()}
        offline=await call('openmc_environment',{});assert not offline['slurm']['network_used']
        context=await call('openmc_environment',{'check_slurm':True,'slurm_reference':case})
        assert context['slurm']['queue_discovery']['status']=='available',context
        material=context['slurm']['reference_materials']['scripts'][0]
        assert material['executed'] is False and material['suggested_resources']['cpus_per_task']==2
        prepared=await call('openmc_slurm_prepare',{'case_dir':case,'name':'MCP远端边界验证','resources':material['suggested_resources']})
        assert prepared['resource_summary']['cpus_per_task']==2
        assert 'exit 97' not in prepared['batch_script']
        assert not (workspace/'.mc-jobs').exists()
        original=await call('openmc_list_jobs',{'include_slurm':True,'name':'MCP远端边界验证'})
        assert original['slurm_submissions'][0]['submission_id']==prepared['submission_id']
        assert original['slurm_submissions'][0]['state_source']=='last_recorded_not_live'

        assert prepared['network_used'] is False
        s=prepared['submission_id']
        await call('openmc_slurm_submit',{'submission_id':s})
        status=await call('openmc_slurm_status',{'submission_id':s});assert status['state']=='completed',status
        # Drop two actual MCP replies. Each replacement session performs real
        # initialize/tools discovery; the failure itself is explicitly controlled.
        from contextlib import asynccontextmanager
        from unittest.mock import patch
        from types import SimpleNamespace
        import slurm_workflow
        faults={'sessions':0,'status':False,'collect':False}
        @asynccontextmanager
        async def reconnecting(url):
            faults['sessions']+=1;dead=False
            async with client(url) as real_call:
                async def drop(name,arguments):
                    nonlocal dead
                    if dead:raise ConnectionError('controlled dead connection')
                    value=await real_call(name,arguments)
                    key='status' if name=='openmc_slurm_status' else 'collect' if name=='openmc_slurm_collect' else None
                    if key and not faults[key]:faults[key]=True;dead=True;raise ConnectionError('controlled reply lost after '+name)
                    return value
                yield drop
        args=SimpleNamespace(submission_id=s,mcp_url=os.environ['MC_TEST_MCP_URL'],output=workspace.parent/'slurm-client-recovery.json',submit=True,timeout=60,poll_seconds=.01)
        with patch.object(slurm_workflow,'client',reconnecting):
            recovered=await slurm_workflow.resilient_workflow(args)
        assert recovered['job_id']==s and faults['sessions']==3,(recovered,faults)
        collected=await call('openmc_slurm_collect',{'submission_id':s});j=collected['job_id']
        assert collected['directory']==str(workspace/'.mc-jobs'/j)

        listed=await call('openmc_list_artifacts',{'kind':'job','identifier':j})
        assert all(item['matches_recorded_output'] is True for item in listed['artifacts']),listed
        final={'submission_id':s,'cluster_job_id':status['cluster_job_id'],'job_id':j,'real_slurm':False,
               'real_openmc':os.environ.get('SLURM_REAL_OPENMC')=='1','professional_acceptance':False,'queue_discovery_verified':True,'original_submission_lookup_verified':True,'controlled_reconnect_sessions':faults['sessions'],'fault_scope':'actual MCP calls with controlled dropped replies; not real HPC/socket-outage verification'}
        if final['real_openmc']:
            data=await call('openmc_read_results',{'job_id':j,'max_values':1000})
            tally=data['tallies'][0];tid=tally['id'];score=tally['scores'][0]
            plotted=await call('openmc_plot_results',{'job_id':j,'tally_id':tid,'score':score})
            report=await call('openmc_export_report',{'job_id':j,'tally_id':tid,'score':score,'format':'markdown'})
            final.update(results=data,plot=plotted,report=report)
            bundle=await call('openmc_generate_result_bundle',{'job_id':j,'tally_id':tid,'score':score,'report_formats':['markdown']})
            import host_transfer,zipfile
            target=workspace.parent/'slurm-user-results.zip'
            selected=[bundle['data_csv'],bundle['delivery_file']]
            download=await host_transfer.download(call,SimpleNamespace(mcp_url=os.environ['MC_TEST_MCP_URL'],kind='job',identifier=j,paths=selected,transfer_id=None,output=target,receipt=workspace.parent/'slurm-download.json'))
            assert download['local_file_verified'] is True and hashlib.sha256(target.read_bytes()).hexdigest()==download['sha256']
            with zipfile.ZipFile(target) as archive:
                for name in selected:
                    candidates=[v for v in archive.namelist() if v==name or name.endswith('/'+v)]
                    assert len(candidates)==1,(name,archive.namelist())
                    assert archive.read(candidates[0])==(workspace/name).read_bytes()
            final.update(result_bundle=bundle,host_download=download,host_file_bytes_verified=True)

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
