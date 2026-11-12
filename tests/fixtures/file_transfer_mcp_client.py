"""Host-side standard MCP journey; file byte handling never calls a model."""
import argparse
import asyncio
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'examples'))
from transfer_files import client
from transfer_files import upload, download

async def run():
    url=os.environ['MC_TEST_MCP_URL'];work=Path(os.environ['MC_WORKSPACE'])
    real=os.environ.get('TRANSFER_REAL_OPENMC')=='1'
    evidence=Path(os.environ['TRANSFER_EVIDENCE']);evidence.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='host-files-') as tmp:
        host=Path(tmp);source=host/'model';source.mkdir()
        if real:
            cfg=json.loads(Path(os.environ['MC_OPENMC_CONFIG']).read_text())
            build='import openmc,sys;from pathlib import Path;'+\
                  f'sys.path.insert(0,{str(ROOT/"mcp")!r});from openmc_worker import create_model;'
            # Use public-tool generation only for fixture setup, not the file journey.
            async with client(url) as call:
                made=await call('openmc_create_model',{'case_dir':'setup-model','template':'synthetic-mgxs','particles':200,'batches':6,'inactive':1})
            for p in (work/made['case_dir']).iterdir():
                if p.is_file():shutil.copyfile(p,source/p.name)
        else:
            (source/'geometry.xml').write_text('<geometry><cell id="1" material="1" region="-1"/><surface id="1" type="sphere" coeffs="0 0 0 1" boundary="vacuum"/></geometry>')
            (source/'materials.xml').write_text('<materials><material id="1" name="fuel"><density units="macro" value="1"/><macroscopic name="test"/></material></materials>')
            (source/'settings.xml').write_text('<settings><energy_mode>multi-group</energy_mode><particles>10</particles><batches>6</batches><inactive>1</inactive></settings>')
        archive=host/'model.zip'
        with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
            for p in source.iterdir():z.write(p,'model/'+p.name)
        async with client(url) as call:
            limits=await call('openmc_file_limits',{});assert limits['max_file_bytes']==5*1024**2
            args=argparse.Namespace(mcp_url=url,file=str(archive),archive=True,receipt=str(host/'upload.json'))
            uploaded=await upload(call,args)
            imported=await call('openmc_import_model',{'source_dir':uploaded['source_dir'],'target_dir':'cases/uploaded','api_check':real})
            for p in source.iterdir():
                if p.name!='mc-case.json':assert p.read_bytes()==(work/imported['case_dir']/p.name).read_bytes()
            preview=await call('openmc_preview_input',{'case_dir':imported['case_dir']});assert preview['manifest_complete']
            # Host byte client can resume after a lost reply without new publication.
            again=await upload(call,args);assert again['source_dir']==uploaded['source_dir']
            original=(work/uploaded['source_dir']/'geometry.xml').read_bytes()
            fetched=host/'geometry.xml'
            await download(call,argparse.Namespace(mcp_url=url,kind='upload',identifier=uploaded['transfer_id'],paths=[uploaded['source_dir']+'/geometry.xml'],output=str(fetched),receipt=None))
            assert fetched.read_bytes()==original
            final={'real_openmc':real,'upload_id':uploaded['transfer_id'],'case_dir':imported['case_dir'],'uploaded_bytes_match':True,'file_roundtrip':True}
            if real:
                validation=await call('openmc_validate_inputs',{'case_dir':imported['case_dir'],'api_check':True});assert validation['structure_valid']
                job=await call('openmc_submit_job',{'case_dir':imported['case_dir'],'name':'MCP文件交接真实计算'});j=job['job_id']
                for _ in range(300):
                    status=await call('openmc_job_status',{'job_id':j})
                    if status['state'] in ('completed','failed','cancelled','interrupted'):break
                    await asyncio.sleep(.1)
                assert status['state']=='completed',status
                for f in preview['files']:
                    if f.get('sha256'):assert status['input_hashes'][f['name']]==f['sha256']
                data=await call('openmc_read_results',{'job_id':j});t=data['tallies'][0];tid=t['id'];score=t['scores'][0]
                selection={'job_id':j,'tally_id':tid,'score':score}
                plotted=await call('openmc_plot_results',selection)
                report=await call('openmc_export_report',{**selection,'format':'markdown'})
                artifacts=await call('openmc_list_artifacts',{'kind':'job','identifier':j});assert artifacts['download_supported']
                # Download report automatically includes its actual linked image/CSV/JSON.
                target=host/'report.zip'
                await download(call,argparse.Namespace(mcp_url=url,kind='job',identifier=j,paths=[report['path']],output=str(target),receipt=None))
                unpack=host/'unpacked'
                with zipfile.ZipFile(target) as z:z.extractall(unpack)
                assert (unpack/'report.md').read_bytes()==Path(report['server_path']).read_bytes()
                series=json.loads((work/report['series_json']).read_text());fetched_series=json.loads((unpack/Path(report['series_json']).name).read_text());assert series==fetched_series
                for name in (report['series_json'],report['series_csv']):assert (unpack/Path(name).name).read_bytes()==(work/name).read_bytes()
                # Independent OpenMC native oracle, not another tool-generated JSON.
                jobdir=work/'.mc-jobs'/j;statepoint=next((jobdir/'work').glob('statepoint.*.h5'))
                script='import openmc,json,sys;p=openmc.StatePoint(sys.argv[1]);t=p.get_tally(id=int(sys.argv[2]));print(json.dumps({"mean":t.get_values(scores=[sys.argv[3]],value="mean").ravel().tolist(),"std_dev":t.get_values(scores=[sys.argv[3]],value="std_dev").ravel().tolist()}))'
                native=json.loads(subprocess.run(cfg['python_command']+['-c',script,str(statepoint),str(tid),score],capture_output=True,text=True,check=True).stdout)
                assert native['mean']==series['mean'];assert native['std_dev']==series['std_dev']
                picture=await call('openmc_preview_file',{'kind':'job','identifier':j,'path':plotted['path']})
                assert picture['preview_type']=='image' and 'image_base64' not in picture
                text=await call('openmc_preview_file',{'kind':'job','identifier':j,'path':report['path'],'max_bytes':30});assert text['next_offset']<=30 and text['truncated']
                shutil.copyfile(statepoint,evidence/statepoint.name);shutil.copyfile(target,evidence/'report.zip')
                final.update(job_id=j,native_values_match=True,report_dependencies_match=True,preview_image_mcp=True,
                             input_hashes=status['input_hashes'],statepoint_sha256=hashlib.sha256(statepoint.read_bytes()).hexdigest())
            (evidence/'result.json').write_text(json.dumps(final,ensure_ascii=False,indent=2))
        # Assert actual standard MCP image content, not only its JSON metadata.
        if real:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            async with streamablehttp_client(url) as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    content=await session.call_tool('openmc_preview_file',{'kind':'job','identifier':j,'path':plotted['path']})
                    images=[c for c in content.content if c.type=='image'];assert len(images)==1
                    assert hashlib.sha256(base64.b64decode(images[0].data)).hexdigest()==hashlib.sha256((work/plotted['path']).read_bytes()).hexdigest()

asyncio.run(run())
