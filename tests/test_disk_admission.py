"""Disk sensor is substituted; input files, copies and durable job records are real."""
import json
import os
from pathlib import Path
import sys
import signal
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'mcp'))
import business
import openmc_jobs as jobs
from openmc_runtime import config


class DiskAdmission(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mc disk admission ')
        self.root = Path(self.tmp.name)
        self.ws = self.root/'workspace';self.ws.mkdir()
        self.addCleanup(self.tmp.cleanup)
        previous = business.WORKSPACE;business.WORKSPACE = self.ws
        self.addCleanup(setattr,business,'WORKSPACE',previous)
        self.cfg = json.loads((ROOT/'config/openmc.json').read_text())
        self.cfg.update(openmc_command=[sys.executable,str(ROOT/'tests/fixtures/mock_openmc.py')],
                        python_command=[sys.executable],data_kind='mock_process_fixture',
                        min_free_disk_bytes=100,job_timeout_seconds=10)
        self.file=self.root/'runtime.json';self.write_config()
        env=patch.dict(os.environ,{'MC_WORKSPACE':str(self.ws),'MC_OPENMC_CONFIG':str(self.file)})
        env.start();self.addCleanup(env.stop)
        self.case=self.ws/'case';self.case.mkdir()
        for name in ['geometry','materials','settings']:(self.case/(name+'.xml')).write_text('<'+name+'/>')
        (self.case/'mock-mode.json').write_text('{"mode":"success"}')
        self.ids=[]
        self.addCleanup(self.finish)

    def write_config(self):self.file.write_text(json.dumps(self.cfg))

    def finish(self):
        for identifier in self.ids:
            jobs.cancel(identifier)
            for _ in range(100):
                if jobs.status(identifier)['state'] in jobs.TERMINAL:break
                time.sleep(.02)

    def test_low_space_rejected_before_job_record_or_worker_creation(self):
        with patch('shutil.disk_usage',return_value=(1000,950,50)),patch.object(jobs,'spawn_worker') as spawn:
            with self.assertRaisesRegex(ValueError,'磁盘.*cleanup.sh'):
                jobs.submit('case')
        self.assertFalse((self.ws/'.mc-jobs').exists())
        spawn.assert_not_called()
        self.assertTrue((self.case/'settings.xml').is_file())

    def test_old_output_filter_and_preserve_outputs_match_actual_copy(self):
        (self.case/'statepoint.999.h5').write_bytes(b'x'*2000)
        (self.case/'old.png').mkdir();(self.case/'old.png'/'large').write_bytes(b'x'*2000)
        with patch('shutil.disk_usage',return_value=(10000,9000,1000)):
            submitted=jobs.submit('case');self.ids.append(submitted['job_id'])
            copied=jobs.job_directory(submitted['job_id'])/'work'
            self.assertFalse((copied/'statepoint.999.h5').exists())
            self.assertFalse((copied/'old.png').exists())
            self.assertEqual((copied/'geometry.xml').read_bytes(),(self.case/'geometry.xml').read_bytes())
            before=set((self.ws/'.mc-jobs').iterdir())
            with self.assertRaisesRegex(ValueError,'磁盘'):
                jobs.submit('case',_preserve_outputs=True)
            self.assertEqual(set((self.ws/'.mc-jobs').iterdir()),before)

    def test_zero_disables_reserve_but_still_requires_input_copy_space(self):
        self.cfg['min_free_disk_bytes']=0;self.write_config()
        with patch('shutil.disk_usage',return_value=(100,99,1)):
            with self.assertRaisesRegex(ValueError,'磁盘'):jobs.submit('case')
        self.assertFalse((self.ws/'.mc-jobs').exists())

    def test_additional_volume_low_space_refuses_even_when_workspace_has_room(self):
        self.cfg['disk_space_check_paths']=[str(self.root/'host-volume')];self.write_config()
        with patch('shutil.disk_usage',side_effect=[(10000,1000,9000),(10000,9999,1)]), \
             patch.object(jobs,'spawn_worker') as spawn:
            with self.assertRaisesRegex(ValueError,'host-volume.*free_bytes=1'):
                jobs.submit('case')
        self.assertFalse((self.ws/'.mc-jobs').exists());spawn.assert_not_called()

    def test_additional_volume_probe_failure_refuses_without_side_effects(self):
        self.cfg['disk_space_check_paths']=[str(self.root/'host-volume')];self.write_config()
        with patch('shutil.disk_usage',side_effect=[(10000,1000,9000),OSError('unavailable')]), \
             patch.object(jobs,'spawn_worker') as spawn:
            with self.assertRaisesRegex(ValueError,'host-volume'):jobs.submit('case')
        self.assertFalse((self.ws/'.mc-jobs').exists());spawn.assert_not_called()

    def test_additional_paths_configuration_and_empty_default(self):
        for value in ['/mnt/c',None,[True],[''],['relative/path'],['/x','/x/'],['/x\x00bad']]:
            with self.subTest(value=value):
                self.cfg['disk_space_check_paths']=value;self.write_config()
                with self.assertRaisesRegex(ValueError,'disk_space_check_paths'):config()
        self.cfg.pop('disk_space_check_paths');self.write_config()
        self.assertEqual(config()['disk_space_check_paths'],[])

    def test_reserve_configuration_rejects_negative_bool_fraction_and_string(self):
        for value in [-1,True,1.5,'100']:
            with self.subTest(value=value):
                self.cfg['min_free_disk_bytes']=value;self.write_config()
                with self.assertRaisesRegex(ValueError,'min_free_disk_bytes'):config()
        self.cfg.pop('min_free_disk_bytes');self.write_config()
        self.assertEqual(config()['min_free_disk_bytes'],1024**3)

    def test_scan_later_disk_rejection_keeps_completed_child_and_stops_future_submissions(self):
        import openmc_scan as scan
        identifier='a'*32;target=self.ws/'.mc-scans'/identifier;target.mkdir(parents=True)
        shutil.copytree(self.case,target/'baseline')
        rows=[{'index':i,'state':'planned','parameters':{'settings.seed':i+1}} for i in range(3)]
        scan.atomic_json(target/'job.json',{'scan_id':identifier,'state':'queued','runtime':self.cfg,
                         'threads':1,'stop_on_error':False,'axes':{'settings.seed':[1,2,3]},
                         'provenance':{'data_kind':'mock_process_fixture'},'cases':rows})
        # Parameter adaptation and physics interpretation have separate native coverage;
        # this case exercises real scan/job persistence and worker process admission.
        handlers={s:signal.getsignal(s) for s in (signal.SIGTERM,signal.SIGINT)}
        try:
            with patch('shutil.disk_usage',side_effect=[(10000,1000,9000),(10000,9999,1)]), \
                 patch.object(scan,'api',return_value={}), \
                 patch('openmc_tools.read_results',return_value={'keff':None,'warnings':['mock process; no physics']}):
                scan.run(identifier)
        finally:
            for s,handler in handlers.items():signal.signal(s,handler)
        record=jobs.read(target)
        self.assertEqual([row['state'] for row in record['cases']],['completed','failed','not_run'])
        first=record['cases'][0]['job_id'];self.ids.append(first)
        self.assertEqual(jobs.status(first)['state'],'completed')
        self.assertIn('MOCK COMPLETED',(jobs.job_directory(first)/'solver.log').read_text())
        second=record['cases'][1]['job_id']
        self.assertFalse((self.ws/'.mc-jobs'/second).exists())
        self.assertIn('磁盘',record['cases'][1]['error'])
        summary=json.loads((target/'summary.json').read_text())
        self.assertEqual(summary['cases'][0]['job_id'],first)


if __name__=='__main__':unittest.main()
