"""Real MCP sample: prepare -> amend tally -> reject stale explanation -> execute chosen version.

A scripted user amendment demonstrates tool consistency, not LLM adoption or
proof of human consent. Source model is a public 3x2 fixture; no physics acceptance.
"""
import asyncio,hashlib,json,os,shutil,subprocess,sys,time,unittest
from pathlib import Path
import test_domain_mcp_real as common
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from scripts.mcp_test_session import check_discovery
ROOT=Path(__file__).resolve().parents[1];RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')

@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG enables real execution-preview sample')
class ExecutionPreviewReal(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=common.DomainMCP.asyncSetUp
    stop=common.DomainMCP.stop
    async def test_prepare_amend_bind_and_actual_receipt(self):
        calls=[];D=ROOT/'artifacts/execution-preview';D.mkdir(parents=True,exist_ok=True)
        async with streamablehttp_client(self.url) as (read,write,_):
            async with ClientSession(read,write) as session:
                await check_discovery(session)
                async def call(name,args,fail=False):
                    result=await session.call_tool(name,args)
                    calls.append({'tool':name,'arguments':args,'result':result.model_dump(mode='json')})
                    if fail:self.assertTrue(result.isError,str(result));return result.content[0].text
                    self.assertFalse(result.isError,str(result));return result.structuredContent or json.loads(result.content[0].text)
                try:
                    spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
                    spec['settings'].update(particles=100,batches=8,inactive=2,seed=17)
                    await call('openmc_build_model',{'case_dir':'fixture/base','spec':spec})
                    await call('openmc_import_model',{'source_dir':'fixture/base','target_dir':'models/base'})
                    base=self.workspace/'models/base';before={str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file()}
                    await call('openmc_prepare_case',{'case_dir':'models/base','target_dir':'models/variant','parameters':{'materials.1.density':10.1}})
                    first=await call('openmc_preview_input',{'case_dir':'models/variant','mode':'run','threads':1})
                    p1=first['execution_preview'];self.assertEqual(p1['parameter_changes'][0]['current'],10.1)
                    self.assertFalse(list((self.workspace/'.mc-jobs').glob('*/job.json')))
                    # Simulated user instruction changes the scientific quantity to collect.
                    await call('openmc_update_tally',{'case_dir':'models/variant','tally_id':1,'scores':['flux','fission']})
                    rejected=await call('openmc_submit_job',{'case_dir':'models/variant','preview_id':p1['preview_id'],'threads':1},fail=True)
                    self.assertIn('已过期',rejected);self.assertFalse(list((self.workspace/'.mc-jobs').glob('*/job.json')))
                    second=await call('openmc_preview_input',{'case_dir':'models/variant','mode':'run','threads':1})
                    p2=second['execution_preview'];self.assertNotEqual(p1['preview_id'],p2['preview_id'])
                    self.assertEqual(p2['tallies'][0]['scores'],['flux','fission'])
                    submitted=await call('openmc_submit_job',{'case_dir':'models/variant','preview_id':p2['preview_id'],'threads':1,'name':'execution preview sample'})
                    self.identifier=submitted['job_id'];end=time.monotonic()+90
                    while time.monotonic()<end:
                        status=await call('openmc_job_status',{'job_id':self.identifier})
                        if status['state'] in ('completed','failed','cancelled','interrupted'):break
                        await asyncio.sleep(.1)
                    self.assertEqual(status['state'],'completed',status);self.assertTrue(status['process_cleanup']['verified_empty'])
                    self.assertEqual(status['input_hashes'],second['input_hashes'])
                    receipt=status['execution_receipt'];self.assertTrue(receipt['binding_verified'])
                    self.assertEqual(receipt['preview_id'],p2['preview_id']);self.assertEqual(receipt['settings'],p2['settings'])
                    data=await call('openmc_read_results',{'job_id':self.identifier})
                    cfg=json.loads(Path(RUNTIME).read_text());oracle=json.loads(await asyncio.to_thread(subprocess.check_output,cfg['python_command']+['-c',
                        'import json,openmc,sys,xml.etree.ElementTree as E;w=sys.argv[1];p=openmc.StatePoint(w+"/statepoint.8.h5",autolink=False);t=p.tallies[1];m=E.parse(w+"/materials.xml").getroot().find("material[@id=\'1\']/density");print(json.dumps({"density":float(m.get("value")),"scores":t.scores,"mean":t.mean.ravel().tolist(),"std_dev":t.std_dev.ravel().tolist()}))',str(self.workspace/'.mc-jobs'/self.identifier/'work')],text=True))
                    self.assertEqual(oracle['density'],10.1);self.assertEqual(oracle['scores'],receipt['tallies'][0]['scores'])
                    tally=next(t for t in data['tallies'] if t['id']==1)
                    for i,mean,std in zip(tally['preview_indices'],tally['mean'],tally['std_dev']):
                        self.assertEqual(mean,oracle['mean'][i]);self.assertEqual(std,oracle['std_dev'][i])
                    self.assertEqual(len(list((self.workspace/'.mc-jobs').glob('*/job.json'))),1)
                    self.assertEqual(before,{str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in base.rglob('*') if p.is_file()})
                    retained=D/'workspace'
                    if retained.exists():shutil.rmtree(retained)
                    shutil.copytree(self.workspace,retained)
                    (D/'sample.json').write_text(json.dumps({'first':p1,'stale_submission_error':rejected,'second':p2,'submitted':submitted,'status':status,'results':data,'native_oracle':oracle,'calls':calls,'source_unchanged':True,'actual_jobs':1,'scope':'scripted user amendment; real MCP/OpenMC; no model calls; no proof of user consent'},ensure_ascii=False,indent=2)+'\n')
                finally:
                    if getattr(self,'identifier',None):await session.call_tool('openmc_cancel_job',{'job_id':self.identifier})
