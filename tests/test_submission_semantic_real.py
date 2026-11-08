"""Real MCP model/check/submit/scan handoff and selector recovery journey.

Owned loopback service/workspace, actual OpenMC, no LLM or user service. Six
faults are injected into copied XML only; semantic findings must survive both
ordinary/scan admission without creating jobs. Then a legal MG model completes
and invalid chart selection cannot prevent a correct retry/report. Independent
StatePoint values and input hashes are the output oracle, not file existence.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid
import xml.etree.ElementTree as ET
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
import test_domain_mcp_real as support
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')

@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG启用真实MCP校核交接')
class SubmissionSemanticReal(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.evidence={'calls':[], 'kind':'real_mcp_no_model', 'version':(ROOT/'VERSION').read_text().strip()}
        out=ROOT/'artifacts/runs/20261108-gate-fixes';out.mkdir(parents=True,exist_ok=True)
        self.proof=out/('mcp-handoff-'+uuid.uuid4().hex+'.json')
        self.addCleanup(lambda:self.proof.write_text(json.dumps(self.evidence,ensure_ascii=False,indent=2)+'\n'))
        await support.DomainMCP.asyncSetUp(self)
    stop=support.DomainMCP.stop

    async def test_semantic_errors_survive_both_admissions_and_valid_model_remains_usable(self):
        async with streamablehttp_client(self.url) as (read,write,_):
            async with ClientSession(read,write) as session:
                await session.initialize()
                async def call(name,args,ok=True):
                    result=await session.call_tool(name,args)
                    self.evidence['calls'].append({'tool':name,'arguments':args,'is_error':bool(result.isError),
                        'data':result.structuredContent or [c.model_dump(mode='json') for c in result.content]})
                    if ok:self.assertFalse(result.isError,str(result))
                    else:self.assertTrue(result.isError,str(result));return str(result)
                    return result.structuredContent or json.loads(result.content[0].text)
                await call('openmc_create_model',{'case_dir':'ce','template':'pwr-pin-cell','particles':100,'batches':6,'inactive':2})
                for kind in ('missing_nuclide','missing_sab','density_value','invalid_score','filter_reference','tally_material_reference'):
                    with self.subTest(kind=kind):
                        target=self.workspace/kind;shutil.copytree(self.workspace/'ce',target)
                        mat=ET.parse(target/'materials.xml');material=mat.getroot().find('material')
                        if kind=='missing_nuclide':material.find('nuclide').set('name','Zz999')
                        if kind=='missing_sab':ET.SubElement(material,'sab',{'name':'not_in_library'})
                        if kind=='density_value':material.find('density').set('value','-1')
                        mat.write(target/'materials.xml')
                        if kind in ('invalid_score','filter_reference','tally_material_reference'):
                            score='not-a-score' if kind=='invalid_score' else 'flux'
                            ref='999' if kind=='filter_reference' else '1'
                            matid='999999' if kind=='tally_material_reference' else material.get('id')
                            (target/'tallies.xml').write_text(f'<tallies><filter id="1" type="material"><bins>{matid}</bins></filter><tally id="1"><filters>{ref}</filters><scores>{score}</scores></tally></tallies>')
                        before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()}
                        checked=await call('openmc_validate_inputs',{'case_dir':kind,'semantic_check':True})
                        self.assertFalse(checked['semantic']['ok'])
                        self.assertIn(kind,[p['kind'] for p in checked['semantic']['problems']])
                        for name,args in [('openmc_submit_job',{'case_dir':kind}),('openmc_submit_scan',{'case_dir':kind,'axes':{'settings.particles':[100,101]}})]:
                            self.assertIn(kind,await call(name,args,False))
                        self.assertEqual(before,{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in target.iterdir() if p.is_file()})
                        self.assertEqual(list(self.workspace.glob('.mc-jobs/*/job.json')),[])
                        self.assertEqual(list(self.workspace.glob('.mc-scans/*/job.json')),[])
                # Legal workflow remains usable; no shrinking to rejection-only tests.
                await call('openmc_create_model',{'case_dir':'mg','template':'synthetic-mgxs','particles':100,'batches':6,'inactive':2})
                for identifier,energy in ((10,False),(20,True)):
                    filters=[{'type':'mesh','dimension':[2,2,1],'lower_left':[-10,-10,-10],'upper_right':[10,10,10]}]
                    if energy:filters.insert(0,{'type':'energy','bins':[0,1000000,20000000]})
                    await call('openmc_define_tally',{'case_dir':'mg','tally_id':identifier,'scores':['flux'],'filters':filters})
                preview=await call('openmc_preview_input',{'case_dir':'mg'})
                submitted=await call('openmc_submit_job',{'case_dir':'mg'})
                self.identifier=submitted['job_id']
                for _ in range(180):
                    status=await call('openmc_job_status',{'job_id':self.identifier})
                    if status['state'] in ('completed','failed','interrupted','cancelled'):break
                    await asyncio.sleep(.1)
                self.assertEqual(status['state'],'completed',str(status))
                self.assertEqual(status['input_hashes'],preview['input_hashes'])
                selection={'job_id':self.identifier,'score':'flux'}
                for tally,args in ((10,{'energy_bin':0}),(10,{'energy_bin':5}),(10,{'level':1}),(20,{}),(20,{'energy_bin':2})):
                    await call('openmc_plot_mesh',{**selection,'tally_id':tally,**args},False)
                mesh=await call('openmc_plot_mesh',{**selection,'tally_id':20,'energy_bin':1})
                report=await call('openmc_export_report',{**selection,'tally_id':20,'format':'markdown'})
                python=json.loads(Path(RUNTIME).read_text())['python_command'][0]
                code='''import openmc,json,sys
from pathlib import Path
with openmc.StatePoint(str(next(Path(sys.argv[1]).glob('statepoint.*.h5'))),autolink=False) as p:
 t=p.get_tally(id=20);print(json.dumps({'mesh':t.mean[:,0,0].reshape(2,4)[1].reshape(2,2).tolist(),'mean':t.mean.ravel().tolist(),'std_dev':t.std_dev.ravel().tolist()}))
'''
                native=await asyncio.to_thread(subprocess.run,[python,'-c',code,str(self.workspace/status['work_dir'])],capture_output=True,text=True,timeout=30)
                self.assertEqual(native.returncode,0,native.stderr);truth=json.loads(native.stdout)
                self.evidence['independent_statepoint']=truth
                self.assertEqual(json.loads((self.workspace/mesh['values_json']).read_text())['values'],truth['mesh'])
                series=json.loads((self.workspace/report['series_json']).read_text())
                self.assertEqual(series['mean'],truth['mean']);self.assertEqual(series['std_dev'],truth['std_dev'])
                # The same normal initialization log cannot become a diagnosis fault.
                log=self.workspace/'normal.log';log.write_text('Loading file dagmc.h5m\nMOAB: 0 errors\n')
                self.assertEqual((await call('diagnose_log',{'path':'normal.log'}))['findings'],[])
if __name__=='__main__':unittest.main()
