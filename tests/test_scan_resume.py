"""Explicit continuation: preserve successful work; reject unknown or changed conditions.

No solver mocks are claimed as scientific evidence. This file checks persisted
batch decisions/idempotence; the real complete journey is gated separately.
"""
import json,os,sys,tempfile,unittest,uuid,copy
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'mcp'))
import business,openmc_scan as scan,openmc_jobs as jobs
from openmc_runtime import atomic_json
from openmc_model_context import model_context

class ScanResume(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.ws=Path(self.temp.name);old=business.WORKSPACE;business.WORKSPACE=self.ws
        self.addCleanup(setattr,business,'WORKSPACE',old)
        self.cfg=json.loads((ROOT/'config/openmc.json').read_text());self.cfg.update(python_command=[sys.executable],openmc_command=[sys.executable])
        self.cf=self.ws/'runtime.json';self.cf.write_text(json.dumps(self.cfg))
        env=patch.dict(os.environ,MC_WORKSPACE=str(self.ws),MC_OPENMC_CONFIG=str(self.cf));env.start();self.addCleanup(env.stop)
        self.id=uuid.uuid4().hex;self.folder=self.ws/'.mc-scans'/self.id;self.base=self.folder/'baseline';self.base.mkdir(parents=True)
        (self.base/'geometry.xml').write_text('<geometry/>');(self.base/'settings.xml').write_text('<settings><energy_mode>multi-group</energy_mode></settings>')
        (self.base/'materials.xml').write_text('<materials><cross_sections>mg.h5</cross_sections></materials>');(self.base/'mg.h5').write_bytes(b'identity only')
        self.record={'scan_id':self.id,'case_dir':'models/user','state':'partial','axes':{'settings.seed':[1,2,3]},'runtime':self.cfg,'parameter_specs':[],
                     'threads':1,'stop_on_error':False,'name':'test','model_context':model_context(self.base,self.cfg),
                     'cases':[{'index':0,'parameters':{'settings.seed':1},'state':'completed','job_id':'a'*32,'keff':{'mean':1,'std_dev':.1}},
                              {'index':1,'parameters':{'settings.seed':2},'state':'failed','job_id':'b'*32},
                              {'index':2,'parameters':{'settings.seed':3},'state':'not_run'}]}
        atomic_json(self.folder/'job.json',self.record)
        self.status=patch.object(jobs,'status',side_effect=lambda i:{'state':'completed' if i=='a'*32 else 'failed','process_cleanup':{'verified_empty':True}});self.status.start();self.addCleanup(self.status.stop)
        self.verify=patch('openmc_tools.verified_result',return_value=None);self.verify.start();self.addCleanup(self.verify.stop)

    def test_resume_keeps_success_new_jobs_only_for_pending_and_is_idempotent(self):
        before=(self.folder/'job.json').read_bytes()
        with patch.object(jobs,'spawn_worker') as launch:
            new=scan.resume_scan(self.id);again=scan.resume_scan(self.id)
        self.assertEqual(new['scan_id'],again['scan_id']);self.assertTrue(again['existing']);self.assertEqual(launch.call_count,1)
        record=json.loads((self.ws/'.mc-scans'/new['scan_id']/'job.json').read_text())
        self.assertEqual(record['cases'][0]['job_id'],'a'*32);self.assertEqual(record['cases'][0]['state'],'completed')
        self.assertEqual([c['state'] for c in record['cases'][1:]],['planned','planned'])
        self.assertNotIn('job_id',record['cases'][1]);self.assertEqual(record['case_dir'],'models/user')
        self.assertEqual((self.folder/'job.json').read_bytes(),before)

    def test_changed_input_dependency_configuration_or_active_job_never_spawns(self):
        for change in ['input','library','config','active','unknown_cleanup','legacy']:
            with self.subTest(change=change):
                original=copy.deepcopy(self.record);atomic_json(self.folder/'job.json',original)
                paths=[self.base/'geometry.xml',self.base/'mg.h5',self.cf];saved=[p.read_bytes() for p in paths]
                if change=='input':paths[0].write_text('<geometry changed="yes"/>')
                if change=='library':paths[1].write_bytes(b'changed')
                if change=='config':v={**self.cfg,'max_scan_cases':2};self.cf.write_text(json.dumps(v))
                if change=='legacy':original.pop('model_context');atomic_json(self.folder/'job.json',original)
                if change=='active':jobs.status.side_effect=lambda _:{'state':'running'}
                if change=='unknown_cleanup':jobs.status.side_effect=lambda _:{'state':'interrupted','process_cleanup':{'verified_empty':False}}
                with patch.object(jobs,'spawn_worker') as launch, self.assertRaises(ValueError):scan.resume_scan(self.id)
                launch.assert_not_called();self.assertFalse((self.folder/'continuation.json').exists())
                for p,b in zip(paths,saved):p.write_bytes(b)
                # Restore metadata too: external library is local input and tracked by hash.
                self.record['model_context']=model_context(self.base,self.cfg)
                jobs.status.side_effect=lambda i:{'state':'completed' if i=='a'*32 else 'failed','process_cleanup':{'verified_empty':True}}
