"""Real SDK/solver regressions for the researcher add -> derive -> scan workflow.

No LLM calls; small statistics only, not a physical-accuracy assessment.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
RUNTIME=os.environ.get('MC_TEST_OPENMC_CONFIG')

@unittest.skipUnless(RUNTIME,'MC_TEST_OPENMC_CONFIG启用真实计分生命周期')
class TallyWorkflowReal(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc tally workflow ')
        self.workspace=Path(self.temp.name)/'workspace'
        self.env={**os.environ,'MC_WORKSPACE':str(self.workspace),'MC_OPENMC_CONFIG':str(Path(RUNTIME).resolve())}
    def tearDown(self):self.temp.cleanup()
    def call(self,name,args,success=True):
        p=subprocess.run([sys.executable,str(ROOT/'mcp/cli.py'),'call',name,json.dumps(args)],env=self.env,capture_output=True,text=True,timeout=180)
        if success:
            self.assertEqual(p.returncode,0,p.stderr);return json.loads(p.stdout)
        self.assertNotEqual(p.returncode,0);return p.stderr
    def hashes(self,directory):
        return {p.relative_to(directory).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    def wait(self,kind,identifier):
        for _ in range(180):
            v=self.call('openmc_'+kind+'_status',{kind+'_id':identifier})
            if v['state'] in ('completed','failed','partial','cancelled','interrupted'):return v
            time.sleep(.1)
        self.fail('等待终态超时')
    def test_incomplete_lattice_is_a_coverage_failure_not_a_generic_engine_error(self):
        spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
        spec['lattices'][0]['pitch']=[.5,.5]
        spec['settings']['source']={'type':'point','point':[-1.55,-.95,0]}
        spec['settings'].update(particles=100,batches=6,inactive=2)
        self.call('openmc_build_model',{'case_dir':'incomplete','spec':spec})
        before=self.hashes(self.workspace/'incomplete')
        check=self.call('openmc_validate_inputs',{'case_dir':'incomplete','geometry_check':True})
        self.assertFalse(check['geometry']['ok'])
        self.assertEqual(check['geometry']['geometry_coverage'],'failed')
        self.assertIn('lattice 10',str(check['geometry']['findings']))
        self.assertEqual(self.hashes(self.workspace/'incomplete'),before)

    def test_delete_last_tally_publishes_empty_definition_and_still_derives(self):
        spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
        self.assertEqual(len(spec['tallies']),1)
        self.call('openmc_build_model',{'case_dir':'one','spec':spec})
        deleted=self.call('openmc_delete_tally',{'case_dir':'one','tally_id':spec['tallies'][0]['id']})
        self.assertEqual(deleted['tally_count'],0)
        self.assertEqual(ET.parse(self.workspace/'one/tallies.xml').getroot().findall('tally'),[])
        self.call('openmc_prepare_case',{'case_dir':'one','target_dir':'none-derived','parameters':{'settings.particles':201}})
        self.assertEqual(ET.parse(self.workspace/'none-derived/tallies.xml').getroot().findall('tally'),[])

    def test_native_ce_and_mg_missing_material_rejected_without_changes(self):
        for template in ('synthetic-mgxs','pwr-pin-cell'):
            self.call('openmc_create_model',{'case_dir':template,'template':template,'particles':100,'batches':6,'inactive':2})
            d=self.workspace/template;before=self.hashes(d)
            error=self.call('openmc_define_tally',{'case_dir':template,'scores':['flux'],'filters':[{'type':'material','bins':[999999]}]},False)
            self.assertIn('999999',error);self.assertEqual(self.hashes(d),before)
            # Successful native writes use the split XML path too, not just
            # rejected calls; preserve physics sections across all CRUD actions.
            self.call('openmc_define_tally',{'case_dir':template,'tally_id':9,'scores':['flux'],'filters':[{'type':'material','bins':[1]}]})
            self.call('openmc_update_tally',{'case_dir':template,'tally_id':9,'scores':['fission']})
            self.call('openmc_delete_tally',{'case_dir':template,'tally_id':9})
            for name in ('materials.xml','geometry.xml','settings.xml'):
                self.assertEqual(hashlib.sha256((d/name).read_bytes()).hexdigest(),before[name])

            # Hand-edited native inputs must fail semantic checks AND submit preflight.
            (d/'tallies.xml').write_text('<tallies><filter id="91" type="material"><bins>999999</bins></filter><tally id="91"><filters>91</filters><scores>flux</scores></tally></tallies>')
            check=self.call('openmc_validate_inputs',{'case_dir':template,'semantic_check':True})
            self.assertFalse(check['semantic']['ok'])
            self.assertIn('tally_material_reference',[p['kind'] for p in check['semantic']['problems']])
            count=len(list((self.workspace/'.mc-jobs').glob('*/job.json'))) if (self.workspace/'.mc-jobs').exists() else 0
            self.assertIn('999999',self.call('openmc_submit_job',{'case_dir':template},False))
            self.assertEqual(len(list((self.workspace/'.mc-jobs').glob('*/job.json'))),count)
    def test_settings_mesh_id_collision_is_rejected_before_writing(self):
        self.call('openmc_create_model',{'case_dir':'native','template':'synthetic-mgxs'})
        d=self.workspace/'native';tree=ET.parse(d/'settings.xml');root=tree.getroot()
        mesh=ET.SubElement(root,'mesh',id='77',type='regular')
        for name,value in [('dimension','2 2 1'),('lower_left','-10 -10 -10'),('upper_right','10 10 10')]:ET.SubElement(mesh,name).text=value
        ET.SubElement(root,'entropy_mesh').text='77';tree.write(d/'settings.xml')
        before=self.hashes(d)
        error=self.call('openmc_define_tally',{'case_dir':'native','scores':['flux'],'filters':[{'type':'mesh','mesh_id':77,'dimension':[3,3,1],'lower_left':[-10,-10,-10],'upper_right':[10,10,10]}]},False)
        self.assertIn('mesh_id=77',error);self.assertEqual(self.hashes(d),before)
    def test_declarative_mutations_survive_derive_and_scan_then_external_drift_is_rejected(self):
        spec=json.loads((ROOT/'examples/model-specs/rectangular-assembly.json').read_text())
        for m in spec['materials']:m.pop('name',None)
        spec['settings'].update(particles=100,batches=6,inactive=2)
        self.call('openmc_build_model',{'case_dir':'source','spec':spec})
        self.call('openmc_define_tally',{'case_dir':'source','tally_id':9,'scores':['flux'],'filters':[{'type':'particle','bins':['neutron']},{'type':'energy','bins':[0,2e7]}]})
        self.call('openmc_update_tally',{'case_dir':'source','tally_id':9,'scores':['fission']})
        self.call('openmc_define_tally',{'case_dir':'source','tally_id':10,'scores':['flux']})
        self.call('openmc_delete_tally',{'case_dir':'source','tally_id':10})
        original=self.hashes(self.workspace/'source')
        self.call('openmc_prepare_case',{'case_dir':'source','target_dir':'derived','parameters':{'settings.particles':101}})
        scan=self.call('openmc_submit_scan',{'case_dir':'derived','axes':{'settings.seed':[71,72]}})
        status=self.wait('scan',scan['scan_id']);self.assertEqual(status['state'],'completed',status)
        results=[]
        for row in status['cases']:
            result=self.call('openmc_read_results',{'job_id':row['job_id']})
            job=self.call('openmc_job_status',{'job_id':row['job_id']})
            self.assertEqual(job['final_result']['keff'],result['keff'])
            self.assertEqual(job['final_result']['statepoint_sha256'],result['statepoint_sha256'])
            self.assertEqual(next(t for t in result['tallies'] if t['id']==9)['scores'],['fission'])
            self.assertNotIn(10,[t['id'] for t in result['tallies']]);results.append({'job':job,'result':result})
            mesh=self.call('openmc_plot_mesh',{'job_id':row['job_id'],'tally_id':1,'level':0,'score':'flux'})
            self.assertEqual(mesh['mesh']['level'],0)
            self.assertIn('level',self.call('openmc_plot_mesh',{'job_id':row['job_id'],'tally_id':1,'level':1},False))
        self.assertEqual(self.hashes(self.workspace/'source'),original)
        source=self.workspace/'source';(source/'tallies.xml').write_text((source/'tallies.xml').read_text()+'\n')
        error=self.call('openmc_prepare_case',{'case_dir':'source','target_dir':'must-not-publish','parameters':{'settings.particles':101}},False)
        self.assertIn('tallies.xml',error);self.assertFalse((self.workspace/'must-not-publish').exists())
        evidence=ROOT/'artifacts/runs/20261108-boundary-audit';evidence.mkdir(parents=True,exist_ok=True)
        (evidence/'scan-result-identity.json').write_text(json.dumps({'scan':status,'jobs':results},ensure_ascii=False,indent=2))

if __name__=='__main__':unittest.main()
