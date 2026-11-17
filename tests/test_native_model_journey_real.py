"""Real MCP user journey: inspect/bind native input -> change -> partial batch -> explicit continuation -> compare.

MC_TEST_OPENMC_CONFIG gates real OpenMC. One test-only launcher fails once before
seed=2's solver starts, to exercise a reproducible partial batch. Successes and
resumed jobs execute real OpenMC; no professional physics or model calls.
"""
import asyncio,csv,hashlib,json,os,shutil,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_domain_mcp_real as common
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from scripts.mcp_test_session import check_discovery
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')

@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG enables real isolated native-model journey')
class NativeModelJourney(unittest.IsolatedAsyncioTestCase):
    stop=common.DomainMCP.stop

    async def asyncSetUp(self):
        self.fault_temp=tempfile.TemporaryDirectory(prefix='mc explicit continuation ')
        self.addCleanup(self.fault_temp.cleanup)
        fault=Path(self.fault_temp.name);cfg=json.loads(Path(RUNTIME).read_text())
        self.actual_runtime=dict(cfg)
        real=cfg['openmc_command'];wrapper=fault/'solver.py';self.failed_once=fault/'failed-once'
        wrapper.write_text("import sys,os,pathlib,xml.etree.ElementTree as E\n"
            "p=pathlib.Path('model.xml' if pathlib.Path('model.xml').exists() else 'settings.xml')\n"
            f"flag=pathlib.Path({str(self.failed_once)!r})\n"
            "if '--version' not in sys.argv and p.is_file() and E.parse(p).getroot().findtext('.//seed')=='2' and not flag.exists():\n"
            " flag.write_text('injected once before actual solver');sys.stderr.write('Injected one-time test launcher failure\\n');sys.exit(2)\n"
            f"os.execv({real[0]!r},{real!r}+sys.argv[1:])\n")
        cfg['openmc_command']=cfg['python_command']+[str(wrapper)];cfg['job_timeout_seconds']=30
        runtime=fault/'runtime.json';runtime.write_text(json.dumps(cfg));self.recorded_calls=[]
        with patch.object(common,'RUNTIME',str(runtime)):
            await common.DomainMCP.asyncSetUp(self)

    async def test_native_model_binding_continuation_and_bin_comparison(self):
        scans=[]
        async with streamablehttp_client(self.url) as (read,write,_):
            async with ClientSession(read,write) as session:
                await check_discovery(session)
                async def call(name,args):
                    result=await session.call_tool(name,args)
                    self.assertFalse(result.isError,str(result))
                    data=result.structuredContent or json.loads(result.content[0].text)
                    self.recorded_calls.append({'tool':name,'arguments':args,'result':data});return data
                async def wait_scan(identifier):
                    end=time.monotonic()+90
                    while time.monotonic()<end:
                        data=await call('openmc_scan_status',{'scan_id':identifier})
                        if data['state'] in ('completed','partial','failed','cancelled','interrupted'):return data
                        await asyncio.sleep(.1)
                    self.fail('scan timeout')
                try:
                    await call('openmc_create_model',{'case_dir':'inbox/native','template':'synthetic-mgxs','particles':80,'batches':6,'inactive':2})
                    source=self.workspace/'inbox/native';material=source/'materials.xml'
                    import xml.etree.ElementTree as E
                    tree=E.parse(material);ref=tree.getroot().find('cross_sections');old=ref.text
                    external=Path(self.fault_temp.name)/'shared-mgxs.h5';external.write_bytes((source/old).read_bytes())
                    ref.text='/retired/server/mgxs.h5';tree.write(material,encoding='utf-8',xml_declaration=True)
                    hashes={str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()}
                    missing=await call('openmc_inspect_model',{'source_dir':'inbox/native'});self.assertFalse(missing['ready'])
                    bindings={'/retired/server/mgxs.h5':str(external)}
                    ready=await call('openmc_inspect_model',{'source_dir':'inbox/native','dependency_bindings':bindings});self.assertTrue(ready['ready'])
                    imported=await call('openmc_import_model',{'source_dir':'inbox/native','target_dir':'models/imported','dependency_bindings':bindings})
                    self.assertTrue(imported['validation']['openmc_api']['api_parsed'])
                    # A legitimate local library can look like an old StatePoint filename.
                    # Fixture preparation represents a user editing their newly imported model.
                    imported_dir=self.workspace/'models/imported'
                    (imported_dir/'statepoint.library.h5').write_bytes(external.read_bytes())
                    imported_xml=E.parse(imported_dir/'materials.xml')
                    imported_xml.getroot().find('cross_sections').text='statepoint.library.h5'
                    imported_xml.write(imported_dir/'materials.xml',encoding='utf-8',xml_declaration=True)
                    params=await call('openmc_list_parameters',{'case_dir':'models/imported'})
                    density=next(p for p in params['parameters'] if p['name'].endswith('.density'))
                    self.assertEqual(density['unit'],'macro');self.assertIn('name',density['object'])
                    prepared=await call('openmc_prepare_case',{'case_dir':'models/imported','target_dir':'models/variant','parameters':{density['name']:.9}})
                    self.assertTrue(prepared['parameter_diff']['verified'])
                    await call('openmc_define_tally',{'case_dir':'models/variant','tally_id':7,'scores':['flux'],
                        'filters':[{'type':'mesh','mesh_id':7,'dimension':[20,20,1],'lower_left':[-10,-10,-10],'upper_right':[10,10,10]}]})
                    preview=await call('openmc_preview_input',{'case_dir':'models/variant','include_content':False});self.assertTrue(preview['manifest_complete']);self.assertIn('statepoint.library.h5',preview['input_hashes'])
                    checked=await call('openmc_validate_inputs',{'case_dir':'models/variant','api_check':True,'semantic_check':True});self.assertTrue(checked['semantic']['ok'])
                    first=await call('openmc_submit_scan',{'case_dir':'models/variant','axes':{'settings.seed':[1,2,3]},'stop_on_error':True,'name':'native-resume'})
                    scans.append(first['scan_id']);partial=await wait_scan(scans[-1])
                    self.assertEqual([r['state'] for r in partial['cases']],['completed','failed','not_run'])
                    frozen=(self.workspace/'.mc-scans'/scans[0]/'job.json').read_bytes()
                    continued=await call('openmc_resume_scan',{'scan_id':scans[0]});scans.append(continued['scan_id'])
                    repeated=await call('openmc_resume_scan',{'scan_id':scans[0]});self.assertEqual(repeated['scan_id'],scans[1])
                    finished=await wait_scan(scans[1]);self.assertEqual(finished['state'],'completed')
                    self.assertEqual(finished['reused_cases'],1);self.assertEqual(finished['cases'][0]['job_id'],partial['cases'][0]['job_id'])
                    self.assertEqual((self.workspace/'.mc-scans'/scans[0]/'job.json').read_bytes(),frozen)
                    self.assertEqual(len(list((self.workspace/'.mc-jobs').glob('*/job.json'))),4,'one original success + injected failure + two real continuations')
                    a,b=finished['cases'][0]['job_id'],finished['cases'][1]['job_id']
                    compare=await call('openmc_job_compare',{'job_a':a,'job_b':b,'compare_tallies':True})
                    self.assertTrue(compare['comparison']['nuclear_data']['same'])
                    self.assertIn('settings.seed',[r['name'] for r in compare['comparison']['parameters']['changes']])
                    native_cmd=self.actual_runtime['python_command']+['-c',
                        'import openmc,json,sys; p=openmc.StatePoint(sys.argv[1],autolink=False);t=p.tallies[7];print(json.dumps({"mean":t.mean.ravel().tolist(),"std_dev":t.std_dev.ravel().tolist()}))']
                    native=[]
                    for job in (a,b):native.append(json.loads(await asyncio.to_thread(subprocess.check_output,native_cmd+[str(self.workspace/'.mc-jobs'/job/'work/statepoint.6.h5')],text=True)))
                    for job in (a,b):self.assertTrue((self.workspace/'.mc-jobs'/job/'work/statepoint.library.h5').is_file())
                    entry=next(t for t in compare['comparison']['tallies']['by_id'] if t['id']==7)
                    expected_zero=sum((ma==sa==0) or (mb==sb==0) for ma,sa,mb,sb in zip(native[0]['mean'],native[0]['std_dev'],native[1]['mean'],native[1]['std_dev']))
                    self.assertTrue(entry['complete']);self.assertEqual(entry['zero_count_bins'],expected_zero)
                    eligible=[abs(mb-ma) for ma,sa,mb,sb in zip(native[0]['mean'],native[0]['std_dev'],native[1]['mean'],native[1]['std_dev']) if not ((ma==sa==0) or (mb==sb==0))]
                    self.assertEqual(entry['max_abs_diff'],max(eligible) if eligible else None)
                    bundle=await call('openmc_generate_result_bundle',{'job_id':b,'tally_id':7,'score':'flux','view':'mesh','level':0})
                    series=json.loads((self.workspace/bundle['data_json']).read_text())
                    self.assertEqual([v for row in series['values'] for v in row],native[1]['mean'])
                    self.assertEqual({str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source.rglob('*') if p.is_file()},hashes)
                    evidence=ROOT/'artifacts/native-workflows';evidence.mkdir(parents=True,exist_ok=True)
                    retained=evidence/'journey-workspace'
                    if retained.exists():shutil.rmtree(retained)
                    shutil.copytree(self.workspace,retained)
                    shutil.copy2(external,evidence/'shared-mgxs.h5')
                    (evidence/'real-native-journey.json').write_text(json.dumps({'calls':self.recorded_calls,'native':native,'partial':partial,'finished':finished,'comparison':compare,'bundle':bundle,'source_unchanged':True},ensure_ascii=False,indent=2)+'\n')
                finally:
                    for identifier in scans:
                        await session.call_tool('openmc_cancel_scan',{'scan_id':identifier})
