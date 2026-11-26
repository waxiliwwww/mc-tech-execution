"""User-facing Slurm discovery/recovery boundaries. Scheduler is explicitly controlled."""
import asyncio,hashlib,json,os,shutil,shlex,unittest
from pathlib import Path
from unittest.mock import patch
import test_slurm_workflow as fixture
import openmc_slurm as slurm
import openmc_jobs as jobs
from openmc_runtime import Error

class SlurmContext(unittest.TestCase):
    setUp=fixture.SlurmWorkflow.setUp
    write_profile=fixture.SlurmWorkflow.write_profile
    hashes=staticmethod(fixture.SlurmWorkflow.hashes)
    prepare=fixture.SlurmWorkflow.prepare
    load=fixture.SlurmWorkflow.load
    rows=fixture.SlurmWorkflow.rows
    set_state=fixture.SlurmWorkflow.set_state

    def sinfo(self):
        self.p['commands']['sinfo']=[fixture.sys.executable,str(fixture.ROOT/'tests/fixtures/slurm_scheduler.py'),'sinfo'];self.write_profile()

    def test_personal_slurm_defaults_and_original_receipt_survive_site_edit(self):
        import personal_config
        self.sinfo()
        with patch.dict(os.environ,{'MC_PERSONAL_CONFIG':str(self.root/'personal.json')}):
            personal_config.update({'slurm':self.p},seed=False)
            original=os.environ.pop('MC_SLURM_CONFIG')
            try:
                self.assertTrue(slurm.site_context()['configured'])
                self.assertEqual(slurm.site_context(True)['queue_discovery']['status'],'available')
                os.environ['SLURM_FIXTURE_MODE']='hold'
                sid=self.prepare()['submission_id'];slurm.submit_slurm(sid)
                personal_config.update({'slurm':{'remote_root':str(self.root/'different site')}})
                self.assertEqual(slurm.status_slurm(sid)['state'],'queued')
                self.assertEqual(len(self.rows()),1)
            finally:os.environ['MC_SLURM_CONFIG']=original

    def test_default_context_is_offline_and_explicit_discovery_reuses_profile(self):
        self.sinfo();before=self.profile.read_bytes()
        with patch.object(slurm,'_remote',side_effect=AssertionError('default must not connect')):
            value=slurm.site_context();self.assertFalse(value['network_used']);self.assertTrue(value['configured'])
        actual=slurm.site_context(True)
        self.assertEqual([q['partition'] for q in actual['queue_discovery']['queues']],['CPU','short'])
        self.assertTrue(actual['queue_discovery']['queues'][0]['is_default'])
        self.assertEqual(actual['queue_discovery']['source'],'sinfo');self.assertTrue(actual['network_used'])
        self.assertFalse((self.work/'.mc-slurm').exists());self.assertFalse(Path(self.p['remote_root']).exists())
        self.assertEqual(self.profile.read_bytes(),before)

    def test_public_environment_and_schema_keep_default_offline(self):
        import openmc_tools
        from tools import TOOLS
        from types import SimpleNamespace
        found={t['name']:t for t in TOOLS};self.assertEqual(len(found),77)
        self.assertTrue(found['openmc_environment']['annotations']['openWorldHint'])
        with patch.object(openmc_tools,'api',return_value={'api_version':'0.16.0'}),patch.object(openmc_tools.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='OpenMC 0.16.0',stderr='')),patch.object(slurm,'_remote',side_effect=AssertionError('offline')):
            result=openmc_tools.environment();self.assertFalse(result['slurm']['network_used'])
            with self.assertRaisesRegex(Error,'布尔'):openmc_tools.environment(check_slurm='false')

    def test_reference_script_resources_are_read_not_executed_or_applied(self):
        marker=self.root/'must-not-execute';path=self.case/'in.slurm'
        raw=('\ufeff#!/bin/bash\n#SBATCH -J existing\n#SBATCH --nodes=2\n#SBATCH --ntasks-per-node=4\n#SBATCH -c 1\n#SBATCH -p CPU\n#SBATCH --time=1000:57:57\n#SBATCH --mem=2G\n#SBATCH --output=%j.out\nexport OPENMC_CROSS_SECTIONS=/site/library.xml\ntouch '+shlex.quote(str(marker))+'\n').encode()
        path.write_bytes(raw);before=self.hashes(self.case);config=self.profile.read_bytes()
        actual=slurm.site_context(reference='cases/my input')['reference_materials']['scripts'][0]
        self.assertEqual(actual['suggested_resources'],{'nodes':2,'ntasks':8,'cpus_per_task':1,'partition':'CPU','time':'1000:57:57','memory_mb':2048})
        self.assertEqual(actual['sha256'],hashlib.sha256(raw).hexdigest());self.assertEqual(actual['suggested_name'],'existing')
        self.assertIn('export OPENMC_CROSS_SECTIONS',actual['content']);self.assertFalse(actual['executed']);self.assertFalse(actual['environment_applied'])
        self.assertFalse(marker.exists());self.assertEqual(self.hashes(self.case),before);self.assertEqual(self.profile.read_bytes(),config)

    def test_late_unsupported_or_variable_directives_are_not_silently_borrowed(self):
        path=self.case/'in.slurm';path.write_text('#SBATCH --nodes=1\necho start\n#SBATCH --ntasks=32\n')
        actual=slurm.site_context(reference='cases/my input')['reference_materials']['scripts'][0]
        self.assertEqual(actual['suggested_resources'],{'nodes':1});self.assertIn('#SBATCH --ntasks=32',actual['content']);self.assertTrue(actual['unhandled_directives'])
        path.write_text('#SBATCH --partition=$QUEUE\n#SBATCH --exclusive\n')
        actual=slurm.site_context(reference='cases/my input')['reference_materials']['scripts'][0]
        self.assertEqual(actual['suggested_resources'],{});self.assertTrue(actual['unhandled_directives'])

    def test_reference_boundaries_and_unreadable_query_preserve_defaults(self):
        self.sinfo();self.p['resource_defaults']={'partition':'known'};self.write_profile()
        for mode in ['sinfo_error','sinfo_bad']:
            os.environ['SLURM_FIXTURE_MODE']=mode;value=slurm.site_context(True)
            self.assertEqual(value['queue_discovery']['status'],'unavailable');self.assertEqual(value['resource_defaults']['partition'],'known')
        self.assertEqual(slurm.site_context(reference='../outside')['reference_materials']['status'],'unavailable')
        (self.case/'in.slurm').symlink_to(self.root/'outside')
        self.assertEqual(slurm.site_context(reference='cases/my input')['reference_materials']['status'],'unavailable')

    def test_invalid_current_profile_does_not_break_local_environment_or_frozen_job(self):
        os.environ['SLURM_FIXTURE_MODE']='hold';sid=self.prepare()['submission_id'];slurm.submit_slurm(sid)
        for invalid in [[],{**self.p,'commands':[]}]:
            self.profile.write_text(json.dumps(invalid));value=slurm.site_context(True)
            self.assertEqual(value['status'],'configuration_error');self.assertFalse(value['network_used'])
            self.assertEqual(slurm.status_slurm(sid)['state'],'queued')
        self.assertEqual(len(self.rows()),1)

    def test_original_slurm_lookup_survives_bad_records_without_changing_old_jobs(self):
        sid1=self.prepare(name='same')['submission_id'];sid2=self.prepare(name='same')['submission_id']
        bad='a'*32;directory=self.work/'.mc-slurm'/bad;directory.mkdir();(directory/'job.json').write_text('[]')
        badtime='b'*32;directory=self.work/'.mc-slurm'/badtime;directory.mkdir();(directory/'job.json').write_text(json.dumps({'submission_id':badtime,'created_at':'bad'}))
        with patch.object(slurm,'_remote',side_effect=AssertionError('lookup must not connect')):
            old=jobs.list_jobs();self.assertNotIn('slurm_submissions',old)
            value=jobs.list_jobs(include_slurm=True,name='same',limit=1)
        self.assertEqual(value['jobs'],old['jobs']);self.assertEqual(value['slurm_total_matches'],2);self.assertTrue(value['slurm_truncated'])
        self.assertEqual(value['slurm_submissions'][0]['state_source'],'last_recorded_not_live')
        self.assertEqual(set(value['unreadable_slurm_submission_ids']),{bad,badtime})
        self.assertIn(value['slurm_submissions'][0]['submission_id'],(sid1,sid2))

    def test_controller_only_can_confirm_complete_without_accounting(self):
        os.environ['SLURM_FIXTURE_MODE']='controller_only';sid=self.prepare()['submission_id'];slurm.submit_slurm(sid)
        actual=slurm.status_slurm(sid);self.assertEqual(actual['state'],'completed');self.assertEqual(actual['scheduler_state_source'],'scontrol')
        calls=[json.loads(x)[0] for x in (self.root/'scheduler/calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls,['sbatch','scontrol'])
        collected=slurm.collect_slurm(sid);self.assertEqual(collected['state'],'completed')

    def test_controller_completed_without_exit_code_does_not_claim_success(self):
        os.environ['SLURM_FIXTURE_MODE']='controller_missing_exit';sid=self.prepare()['submission_id'];slurm.submit_slurm(sid)
        self.assertEqual(slurm.status_slurm(sid)['state'],'unknown')
        with self.assertRaisesRegex(Error,'终态'):slurm.collect_slurm(sid)

    def test_missing_local_collection_fetches_original_results_again(self):
        sid=self.prepare()['submission_id'];slurm.submit_slurm(sid);first=slurm.collect_slurm(sid)
        target=self.work/'.mc-jobs'/sid;before=self.hashes(target/'work');self.assertTrue(target.resolve().is_relative_to(self.work.resolve()))
        shutil.rmtree(target)
        again=slurm.collect_slurm(sid);self.assertTrue(again['recovered_missing_collection']);self.assertEqual(again['job_id'],first['job_id']);self.assertEqual(self.hashes(target/'work'),before)
        repeated=slurm.collect_slurm(sid);self.assertTrue(repeated['already_collected']);self.assertEqual(repeated['directory'],str(target));self.assertEqual(len(self.rows()),1)

if __name__=='__main__':unittest.main()
