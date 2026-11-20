"""Optional preview binding: stale inputs/resources never spawn; old calls remain valid.

The API parser is stubbed here; real parser/solver/MCP evidence lives in the
separate gated journey. Hashes, snapshot copying and persistent receipts are real.
"""
import hashlib,json,os,sys,tempfile,unittest,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
import business,openmc_tools as tools,openmc_jobs as jobs

class ExecutionPreview(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.ws=self.root/'workspace';self.ws.mkdir();old=business.WORKSPACE;business.WORKSPACE=self.ws
        self.addCleanup(setattr,business,'WORKSPACE',old)
        self.cfg=json.loads((ROOT/'config/openmc.json').read_text());self.cfg.update(python_command=[sys.executable],openmc_command=[sys.executable],min_free_disk_bytes=0)
        self.cf=self.root/'runtime.json';self.cf.write_text(json.dumps(self.cfg))
        env=patch.dict(os.environ,MC_WORKSPACE=str(self.ws),MC_OPENMC_CONFIG=str(self.cf));env.start();self.addCleanup(env.stop)
        self.case=self.ws/'case';self.case.mkdir()
        (self.case/'settings.xml').write_text('<settings><run_mode>eigenvalue</run_mode><energy_mode>multi-group</energy_mode><particles>100</particles><batches>6</batches><inactive>2</inactive><seed>17</seed></settings>')
        (self.case/'geometry.xml').write_text('<geometry/>')
        self.library=self.root/'xs.h5';self.library.write_bytes(b'unit identity, no physics')
        (self.case/'materials.xml').write_text(f'<materials><cross_sections>{self.library}</cross_sections><material id="1"><density value="0.9" units="macro"/></material></materials>')
        (self.case/'mc-case.json').write_text(json.dumps({'data_kind':'user_supplied_unverified','parameter_changes':[{'name':'materials.1.density','before':1.,'after':.9,'unit':'macro'}]}))
        for target,value in [('openmc_tools.validate_inputs',{'structure_valid':True,'semantic':{'checked':True,'ok':True,'complete':True}}),
                             ('openmc_tools.environment',{'executable_available':True,'openmc_version_matches':True}),
                             ('openmc_tools._semantic_summary',{'checked':True,'ok':True,'error_count':0})]:
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch('openmc_runtime.api',side_effect=self.fake_api);p.start();self.addCleanup(p.stop)
    def fake_api(self,action,directory,payload,cfg=None):
        self.assertEqual(action,'execution-summary')
        density=float(ET.parse(directory/'materials.xml').getroot().find('material/density').get('value'))
        return {'parameters':[{'name':'materials.1.density','current':density,'object':{'id':1,'name':'fixture'}}],
                'settings':{'run_mode':'eigenvalue','energy_mode':'multi-group','particles':100,'batches':6,'inactive':2,'generations_per_batch':1,'seed':17},'warnings':['unit fixture, no physics']}
    def preview(self,threads=1):return tools.preview_input('case',mode='run',threads=threads)['execution_preview']
    def test_readable_facts_and_unchanged_legacy_preview(self):
        self.assertNotIn('execution_preview',tools.preview_input('case'))
        p=self.preview();self.assertIn('尚未提交',p['human_summary']);self.assertEqual(p['parameter_changes'][0]['current'],.9)
        self.assertEqual(p['preview_id'],self.preview()['preview_id'])
        self.assertNotEqual(p['preview_id'],self.preview(2)['preview_id'])
        t=ET.parse(self.case/'materials.xml');t.getroot().find('material/density').set('value','0.8');t.write(self.case/'materials.xml')
        p=self.preview();self.assertEqual(p['parameter_changes'][0]['current'],.8)
        self.assertFalse(p['parameter_changes'][0]['matches_recorded_change']);self.assertTrue(p['warnings'])
    def test_stale_inputs_threads_configuration_and_external_data_refuse_without_job(self):
        for change in ('input','threads','config','external'):
            with self.subTest(change=change):
                expected=self.preview()['preview_id'];paths=[self.case/'settings.xml',self.cf,self.library];saved=[p.read_bytes() for p in paths]
                if change=='input':paths[0].write_bytes(saved[0]+b'\n')
                if change=='config':c=dict(self.cfg);c['job_timeout_seconds']=99;self.cf.write_text(json.dumps(c))
                if change=='external':self.library.write_bytes(b'changed external data')
                with patch.object(jobs,'spawn_worker') as launch,self.assertRaisesRegex(ValueError,'已过期'):
                    tools.submit_job('case',threads=2 if change=='threads' else 1,preview_id=expected)
                launch.assert_not_called();self.assertFalse((self.ws/'.mc-jobs').exists())
                for p,b in zip(paths,saved):p.write_bytes(b)
    def test_fresh_snapshot_binding_is_persisted_and_legacy_receipt_is_honest(self):
        expected=self.preview()['preview_id']
        with patch.object(jobs,'spawn_worker'):
            new=tools.submit_job('case',name='bound fixture',preview_id=expected)
            old=tools.submit_job('case',name='legacy fixture')
        self.assertTrue(new['execution_receipt']['binding_verified'])
        self.assertEqual(new['execution_receipt']['settings']['seed'],17)
        self.assertFalse(old['execution_receipt']['preview_bound'])
        record=json.loads((self.ws/'.mc-jobs'/new['job_id']/'job.json').read_text())
        self.assertEqual(record['execution_receipt']['preview_id'],expected)
    def test_change_during_copy_rejects_both_snapshot_and_external_race(self):
        original=jobs.shutil.copytree
        for kind in ('input','external'):
            with self.subTest(kind=kind):
                expected=self.preview()['preview_id']
                def racing(src,dst,*args,**kw):
                    result=original(src,dst,*args,**kw)
                    if kind=='input':(Path(dst)/'settings.xml').write_text('<settings/>')
                    else:self.library.write_bytes(b'external changed after initial comparison')
                    return result
                with patch.object(jobs.shutil,'copytree',side_effect=racing),patch.object(jobs,'spawn_worker') as launch,self.assertRaisesRegex(ValueError,'未启动|未提交'):
                    tools.submit_job('case',preview_id=expected)
                launch.assert_not_called();self.assertEqual(list((self.ws/'.mc-jobs').glob('*/job.json')),[])

    def test_copied_relative_nuclear_data_and_absolute_references_keep_distinct_semantics(self):
        local=self.case/'xs.h5';local.write_bytes(b'copied local nuclear fixture')
        materials=self.case/'materials.xml'
        for reference in ('xs.h5',str(local)):
            with self.subTest(reference=reference):
                tree=ET.parse(materials);tree.getroot().find('cross_sections').text=reference;tree.write(materials)
                expected=self.preview()['preview_id']
                with patch.object(jobs,'spawn_worker'):
                    result=tools.submit_job('case',preview_id=expected)
                self.assertTrue(result['execution_receipt']['binding_verified'])
        # A relative index may itself reference an absolute library inside the source.
        # That file remains external to the snapshot and must retain its identity.
        index=self.case/'cross_sections.xml'
        index.write_text(f'<cross_sections><library path="{local}" materials="X" type="neutron"/></cross_sections>')
        tree=ET.parse(materials);tree.getroot().find('cross_sections').text='cross_sections.xml';tree.write(materials)
        expected=self.preview()['preview_id']
        with patch.object(jobs,'spawn_worker'):
            result=tools.submit_job('case',preview_id=expected)
        self.assertTrue(result['execution_receipt']['binding_verified'])
        local.write_bytes(b'changed external identity')
        with patch.object(jobs,'spawn_worker') as launch,self.assertRaisesRegex(ValueError,'已过期'):
            tools.submit_job('case',preview_id=expected)
        launch.assert_not_called()
