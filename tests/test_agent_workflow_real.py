"""Five user tasks over real MCP/OpenMC/files, with scripted business decisions.

No LLM calls. This proves tool paths and durable file/job behavior, not that a
model follows the skill or a desktop displays results. Download faults discard
real replies; storage, solver, byte checks and resumed sessions remain real.
"""
import argparse
import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid
import zipfile

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from scripts.mcp_test_session import check_discovery

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'examples'))
import transfer_files as host
from follow_job import follow


def hashes(folder):
    return {str(p.relative_to(folder)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in folder.rglob('*') if p.is_file()}


@unittest.skipUnless(os.environ.get('MC_TEST_MCP_URL') and os.environ.get('MC_TEST_OPENMC_CONFIG'),
                     'Owned MCP and real OpenMC enable five scripted user journeys')
class AgentWorkflowsReal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prefix='agent-'+uuid.uuid4().hex[:10]
        self.workspace=Path(os.environ['MC_WORKSPACE'])
        self.evidence=Path(os.environ['MC_TEST_SESSION_DIR'])/self.prefix
        self.evidence.mkdir();self.calls=[]

    @asynccontextmanager
    async def session(self):
        async with streamablehttp_client(os.environ['MC_TEST_MCP_URL']) as (read,write,_):
            async with ClientSession(read,write) as session:
                await check_discovery(session)
                async def call(name,args,fail=False):
                    res=await session.call_tool(name,args)
                    self.calls.append({'tool':name,'arguments':args,'result':res.model_dump(mode='json')})
                    if fail:
                        self.assertTrue(res.isError,str(res));return res.content[0].text
                    self.assertFalse(res.isError,str(res))
                    return res.structuredContent or json.loads(res.content[0].text)
                yield call

    async def asyncTearDown(self):
        (self.evidence/'calls.json').write_text(json.dumps(self.calls,ensure_ascii=False,indent=2))

    def job_ids(self):return {p.parent.name for p in (self.workspace/'.mc-jobs').glob('*/job.json')}

    async def base(self,call):
        spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
        spec['settings'].update(particles=100,batches=8,inactive=2,seed=17)
        case=self.prefix+'/base'
        await call('openmc_build_model',{'case_dir':case,'spec':spec})
        return case,hashes(self.workspace/case)

    async def variant(self,call,base):
        params=await call('openmc_list_parameters',{'case_dir':base})
        key=next(p['name'] for p in params['parameters'] if p.get('object',{}).get('id')==1 and p['name'].endswith('.density'))
        changed=await call('openmc_prepare_case',{'case_dir':base,'target_dir':self.prefix+'/changed','parameters':{key:10.5}})
        self.assertTrue(changed['parameter_diff']['verified'])
        found=await call('openmc_list_parameters',{'case_dir':changed['case_dir']})
        self.assertEqual(next(p['current'] for p in found['parameters'] if p['name']==key),10.5)
        return changed['case_dir']

    async def submit(self,call,case):
        before=self.job_ids()
        preview=await call('openmc_preview_input',{'case_dir':case,'mode':'run','threads':1})
        result=await call('openmc_submit_job',{'case_dir':case,'preview_id':preview['execution_preview']['preview_id'],'name':self.prefix})
        self.assertEqual(self.job_ids()-before,{result['job_id']})
        return result['job_id'],preview['input_hashes']

    async def finished(self,call,job,inputs):
        result=await follow(call,job,90)
        self.assertEqual(result['state'],'completed',result)
        self.assertEqual(result['status']['input_hashes'],inputs)
        self.assertTrue(result['status']['process_cleanup']['verified_empty'])
        return result

    async def bundle(self,call,job,score='flux'):
        bundle=await call('openmc_generate_result_bundle',{'job_id':job,'tally_id':1,'score':score,'view':'mesh'})
        data=json.loads((self.workspace/bundle['data_json']).read_text())
        values=[v for row in data['values'] for v in row]
        errors=[v for row in data['errors'] for v in row]
        self.assertEqual(bundle['data_quality']['zero_mean_bins'],sum(v==0 for v in values))
        self.assertEqual(bundle['data_quality']['points'],len(values))
        runtime=json.loads(Path(os.environ['MC_TEST_OPENMC_CONFIG']).read_text())
        jobdir=self.workspace/'.mc-jobs'/job/'work'
        oracle=json.loads(await asyncio.to_thread(subprocess.check_output,runtime['python_command']+['-c',
            'import json,openmc,sys,pathlib;w=pathlib.Path(sys.argv[1]);p=openmc.StatePoint(str(next(w.glob("statepoint.*.h5"))),autolink=False);t=p.tallies[1];s=sys.argv[2];print(json.dumps({"mean":t.get_values(scores=[s],value="mean").ravel().tolist(),"std":t.get_values(scores=[s],value="std_dev").ravel().tolist()}))',str(jobdir),score],text=True))
        self.assertEqual(values,oracle['mean']);self.assertEqual(errors,oracle['std'])
        self.assertIn('均值为零'+str(sum(v==0 for v in values))+'箱',bundle['delivery_summary'])
        for warning in bundle['warnings']:self.assertIn(warning,bundle['delivery_summary'])
        for p in bundle['artifacts']:
            self.assertEqual(hashlib.sha256((self.workspace/p['path']).read_bytes()).hexdigest(),p['sha256'])
        return bundle

    def download_args(self,bundle,name='成果.zip'):
        paths=[bundle[k] for k in ['chart','data_json','data_csv','plot_script','manifest','delivery_file']]
        paths.extend(p['path'] for p in bundle['reports'])
        return argparse.Namespace(mcp_url=os.environ['MC_TEST_MCP_URL'],kind='job',identifier=bundle['job_id'],paths=paths,
                                  output=str(self.evidence/name),receipt=None)

    def verify_download(self,result,bundle):
        self.assertTrue(result['local_file_verified'])
        output=Path(result['file']);self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(),result['sha256'])
        with zipfile.ZipFile(output) as z:
            self.assertIsNone(z.testzip())
            for path in [bundle['data_json'],bundle['data_csv'],bundle['delivery_file']]:
                member=next(n for n in z.namelist() if n.endswith('/'+Path(path).name) or n==Path(path).name)
                self.assertEqual(z.read(member),(self.workspace/path).read_bytes())

    async def test_01_complete_request_changes_density_and_delivers_both_scores(self):
        async with self.session() as call:
            base,before=await self.base(call);changed=await self.variant(call,base)
            await call('openmc_update_tally',{'case_dir':changed,'tally_id':1,'scores':['flux','fission']})
            job,inputs=await self.submit(call,changed);await self.finished(call,job,inputs)
            for score in ['flux','fission']:
                b=await self.bundle(call,job,score);self.verify_download(await host.download(call,self.download_args(b,score+'.zip')),b)
            self.assertEqual(hashes(self.workspace/base),before)
            self.assertFalse(any(c['tool'] in ('openmc_import_model','openmc_inspect_model','openmc_list_artifacts') for c in self.calls))

    async def test_02_ambiguous_material_keeps_inputs_until_scripted_user_selects(self):
        async with self.session() as call:
            base,before=await self.base(call);jobs=self.job_ids()
            error=await call('openmc_prepare_case',{'case_dir':base,'target_dir':self.prefix+'/rejected','parameters':{'density':10.5}},fail=True)
            self.assertIn('materials.1.density',error);self.assertIn('materials.2.density',error);self.assertIn('g/cm3',error)
            self.assertEqual(hashes(self.workspace/base),before);self.assertEqual(self.job_ids(),jobs)
            self.assertFalse((self.workspace/self.prefix/'rejected').exists())
            # Scripted user chooses material 1; no claim that an LLM asked this.
            await self.variant(call,base);self.assertEqual(self.job_ids(),jobs)

    async def test_03_amendment_rejects_stale_preview_and_executes_latest_tallies(self):
        async with self.session() as call:
            base,before=await self.base(call);changed=await self.variant(call,base);jobs=self.job_ids()
            old=await call('openmc_preview_input',{'case_dir':changed,'mode':'run'})
            self.assertEqual(self.job_ids(),jobs)
            await call('openmc_update_tally',{'case_dir':changed,'tally_id':1,'scores':['flux','fission']})
            error=await call('openmc_submit_job',{'case_dir':changed,'preview_id':old['execution_preview']['preview_id']},fail=True)
            self.assertIn('过期',error);self.assertEqual(self.job_ids(),jobs)
            job,inputs=await self.submit(call,changed);await self.finished(call,job,inputs)
            await self.bundle(call,job,'fission');self.assertEqual(hashes(self.workspace/base),before)

    async def test_04_client_disconnect_reconnects_existing_job_without_resubmission(self):
        async with self.session() as call:
            base,before=await self.base(call);job,inputs=await self.submit(call,base)
        # Actual MCP client session closes immediately after submission.
        jobs=self.job_ids()
        async with self.session() as call:
            status=await self.finished(call,job,inputs)
            self.assertEqual(status['job_id'],job);self.assertEqual(self.job_ids(),jobs)
            self.assertEqual(hashes(self.workspace/base),before)

    async def test_05_failed_download_resumes_and_close_failure_does_not_erase_delivery(self):
        async with self.session() as call:
            base,before=await self.base(call);job,inputs=await self.submit(call,base);await self.finished(call,job,inputs)
            bundle=await self.bundle(call,job);args=self.download_args(bundle);jobs=self.job_ids()
            chunks=0
            async def drop(name,data):
                nonlocal chunks
                result=await call(name,data)
                if name=='openmc_prepare_download':
                    result=dict(result,chunk_bytes=min(1024,result['chunk_bytes']))
                if name=='openmc_download_chunk':
                    chunks+=1
                    if chunks==2:raise ConnectionError('controlled lost MCP reply')
                return result
            with self.assertRaises(ConnectionError):await host.download(drop,args)
            part=Path(args.output+'.part');self.assertTrue(part.is_file());self.assertGreater(part.stat().st_size,0)
            self.assertFalse(Path(args.output).exists())
            receipt=Path(args.output+'.mc-download.json');transfer=json.loads(receipt.read_text())['transfer_id']
            async def lose_close(name,data):
                if name=='openmc_close_transfer':raise ConnectionError('controlled close acknowledgement loss')
                return await call(name,data)
            result=await host.download(lose_close,args);self.verify_download(result,bundle)
            self.assertEqual(result['transfer_cleanup']['state'],'pending')
            again=await host.download(call,args);self.verify_download(again,bundle)
            self.assertEqual(again['transfer_cleanup']['state'],'closed')
            self.assertEqual(json.loads(receipt.read_text())['transfer_id'],transfer)
            self.assertEqual(self.job_ids(),jobs);self.assertEqual(hashes(self.workspace/base),before)
            prepared=await call('openmc_prepare_download',{'kind':'job','identifier':job,'paths':[bundle['data_json']]})
            destination=self.evidence/'已准备数据.json'
            raw=await asyncio.to_thread(subprocess.check_output,[sys.executable,str(ROOT/'examples/transfer_files.py'),
                '--mcp-url',os.environ['MC_TEST_MCP_URL'],'download','--transfer-id',prepared['transfer_id'],
                '--output',str(destination)],cwd=ROOT,text=True)
            delivered=json.loads(raw);self.assertTrue(delivered['local_file_verified'])
            self.assertEqual(destination.read_bytes(),(self.workspace/bundle['data_json']).read_bytes())
