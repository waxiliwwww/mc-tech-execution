"""Slurm offline boundary: actual tools, scripts, subprocesses and file transfer.

Normal task: prepare existing model → scheduler-shaped boundary → collect →
verify immutable input and raw result identity. Faults: lost receipt, delayed
accounting, cancellation, mutation, transfer corruption and setup failures.
Fixture scheduler/solver are NOT real Slurm/OpenMC; no model calls, network or
existing service/config changes. Temporary directories are always removed.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'mcp'))
import business
import openmc_slurm as slurm
from openmc_runtime import Error
from adapters import execute_tool
from tools import TOOLS


class SlurmWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='forge slurm ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.work=self.root/'workspace';self.work.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(business,'WORKSPACE',self.work).start()
        self.runtime=self.root/'runtime.json'
        cfg=json.loads((ROOT/'config/openmc.json').read_text());cfg['min_free_disk_bytes']=1;cfg['max_log_bytes']=2048
        self.runtime.write_text(json.dumps(cfg))
        self.p={'transport':'local','remote_root':str(self.root/'remote scratch'),'python_command':[sys.executable],
                'openmc_command':[sys.executable,str(ROOT/'tests/fixtures/slurm_solver.py')],
                'commands':{key:[sys.executable,str(ROOT/'tests/fixtures/slurm_scheduler.py'),key]
                            for key in ('sbatch','squeue','sacct','scancel')},'environment_setup':[]}
        self.profile=self.root/'cluster.json';self.write_profile()
        patch.dict(os.environ,{'MC_OPENMC_CONFIG':str(self.runtime),'MC_SLURM_CONFIG':str(self.profile),
                              'SLURM_FIXTURE_ROOT':str(self.root/'scheduler')}).start()
        self.case=self.work/'cases'/'my input';self.case.mkdir(parents=True)
        (self.case/'geometry.xml').write_text('<geometry><cell id="1" material="1" region="-1"/><surface id="1" type="sphere" coeffs="0 0 0 10" boundary="vacuum"/></geometry>')
        (self.case/'materials.xml').write_text('<materials><material id="1"><density units="macro" value="1"/><macroscopic name="fixture"/></material></materials>')
        (self.case/'settings.xml').write_text('<settings><run_mode>eigenvalue</run_mode><energy_mode>multi-group</energy_mode><particles>100</particles><batches>6</batches><inactive>1</inactive></settings>')
        (self.case/'tallies.xml').write_text('<tallies/>')
        (self.case/'model-spec.json').write_text('{}')
        self.original=self.hashes(self.case)

    def write_profile(self):self.profile.write_text(json.dumps(self.p))
    @staticmethod
    def hashes(root):return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    def prepare(self,**kw):return slurm.prepare_slurm('cases/my input',**kw)
    def load(self,s):return json.loads((slurm.directory(s)/'job.json').read_text())
    def rows(self):return json.loads((self.root/'scheduler/scheduler.json').read_text())
    def set_state(self,identifier,state,code='0:0'):
        path=self.root/'scheduler/scheduler.json';rows=self.rows();rows[identifier].update(state=state,exit=code);path.write_text(json.dumps(rows))

    def test_offline_prepare_to_completed_collect_is_same_input_and_bytes(self):
        prepared=self.prepare(name='组件远程测试',resources={'cpus_per_task':3,'time':'00:05:00','partition':'cpu','memory_mb':512})
        s=prepared['submission_id'];self.assertFalse(prepared['network_used']);self.assertFalse((self.root/'remote scratch').exists())
        self.assertIn('#SBATCH --cpus-per-task=3',prepared['batch_script'])
        self.assertIn('#SBATCH --partition=cpu',prepared['batch_script'])
        result=slurm.submit_slurm(s);self.assertEqual(result['state'],'queued')
        self.assertEqual(slurm.status_slurm(s)['state'],'completed')
        collected=slurm.collect_slurm(s);self.assertEqual(collected['job_id'],s)
        d=self.work/'.mc-jobs'/s;record=json.loads((d/'job.json').read_text())
        for name,expected in prepared['input_hashes'].items():self.assertEqual(slurm.jobs.file_hash(d/'work'/name),expected)
        native=d/'work/statepoint.6.h5';self.assertTrue(native.read_bytes().startswith(b'CONTROLLED-TRANSPORT-FIXTURE'))
        self.assertEqual(record['output_files']['statepoint.6.h5'],slurm.jobs.file_hash(native))
        self.assertEqual(record['backend'],'slurm');self.assertEqual(record['cluster_job_id'],result['cluster_job_id'])
        self.assertEqual(record['provenance']['execution_kind'],'slurm_remote')
        self.assertEqual(slurm.collect_slurm(s)['already_collected'],True)
        self.assertEqual(slurm.submit_slurm(s)['cluster_job_id'],result['cluster_job_id'])
        calls=[json.loads(x)[0] for x in (self.root/'scheduler/calls.jsonl').read_text().splitlines()]
        self.assertEqual(calls.count('sbatch'),1)
        self.assertEqual(self.hashes(self.case),self.original)

    def test_prepared_snapshot_survives_source_edit_and_validates_actual_snapshot(self):
        prepared=self.prepare();s=prepared['submission_id'];(self.case/'settings.xml').write_text('<broken>')
        slurm.submit_slurm(s)
        remote=Path(prepared['remote_dir'])
        self.assertIn('<batches>6</batches>',(remote/'settings.xml').read_text())

    def test_snapshot_mutation_refuses_before_remote_effects(self):
        s=self.prepare()['submission_id'];(slurm.directory(s)/'input/settings.xml').write_text('changed')
        with self.assertRaisesRegex(Error,'输入已变化'):slurm.submit_slurm(s)
        self.assertFalse((self.root/'scheduler').exists())

    def test_lost_submit_receipt_recovers_same_id_without_second_sbatch(self):
        os.environ['SLURM_FIXTURE_MODE']='receipt_lost';s=self.prepare()['submission_id']
        with self.assertRaises(Error):slurm.submit_slurm(s)
        self.assertEqual(self.load(s)['state'],'submission_unknown')
        with self.assertRaisesRegex(Error,'禁止盲重交'):slurm.submit_slurm(s)
        status=slurm.status_slurm(s);self.assertEqual(status['state'],'queued');self.assertEqual(status['cluster_job_id'],'1000')
        slurm.submit_slurm(s);self.assertEqual(len(self.rows()),1)

    def test_queue_disappearance_and_accounting_delay_are_unknown_not_success(self):
        s=self.prepare()['submission_id'];slurm.submit_slurm(s);os.environ['SLURM_FIXTURE_MODE']='accounting_delay'
        self.assertEqual(slurm.status_slurm(s)['state'],'unknown')
        with self.assertRaisesRegex(Error,'终态未确认'):slurm.collect_slurm(s)
        del os.environ['SLURM_FIXTURE_MODE'];self.assertEqual(slurm.status_slurm(s)['state'],'completed')

    def test_pending_running_cancel_and_prepared_cancel_preserve_inputs(self):
        os.environ['SLURM_FIXTURE_MODE']='hold';s=self.prepare()['submission_id'];sub=slurm.submit_slurm(s)
        self.assertEqual(slurm.status_slurm(s)['state'],'queued')
        self.set_state(sub['cluster_job_id'],'RUNNING');self.assertEqual(slurm.status_slurm(s)['state'],'running')
        self.assertTrue(slurm.cancel_slurm(s)['cancel_requested']);self.assertEqual(slurm.status_slurm(s)['state'],'cancelled')
        self.assertFalse(slurm.cancel_slurm(s)['changed'])
        s2=self.prepare()['submission_id'];self.assertEqual(slurm.cancel_slurm(s2)['state'],'cancelled')
        with self.assertRaises(Error):slurm.submit_slurm(s2)
        self.assertEqual(self.hashes(self.case),self.original)

    def test_failed_solver_keeps_failure_and_collects_log(self):
        os.environ['SLURM_SOLVER_EXIT']='7';s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        self.assertEqual(slurm.status_slurm(s)['state'],'failed')
        result=slurm.collect_slurm(s);self.assertEqual(result['state'],'failed')
        r=json.loads((self.work/'.mc-jobs'/s/'job.json').read_text());self.assertEqual(r['exit_code'],7)
        self.assertIn('fixture log',(self.work/'.mc-jobs'/s/'solver.log').read_text())

    def test_setup_failure_collects_scheduler_log_without_fabricating_solver_receipt(self):
        self.p['environment_setup']=['echo module-not-found; exit 3'];self.write_profile()
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        self.assertEqual(slurm.collect_slurm(s)['state'],'failed')
        self.assertIn('module-not-found',(self.work/'.mc-jobs'/s/'work/slurm-1000.out').read_text())

    def test_zero_exit_not_enough_without_receipt_or_when_scheduler_signal_nonzero(self):
        s=self.prepare()['submission_id'];sub=slurm.submit_slurm(s)
        self.set_state(sub['cluster_job_id'],'COMPLETED','0:9');self.assertEqual(slurm.status_slurm(s)['state'],'failed')
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        (Path(self.load(s)['remote_dir'])/'.forge-result.json').unlink()
        with self.assertRaisesRegex(Error,'没有计算完成收据'):slurm.collect_slurm(s)

    def test_remote_changed_inputs_or_outputs_do_not_publish_results(self):
        for filename in ('settings.xml','statepoint.6.h5'):
            with self.subTest(filename=filename):
                s=self.prepare()['submission_id'];slurm.submit_slurm(s)
                (Path(self.load(s)['remote_dir'])/filename).write_text('changed')
                with self.assertRaisesRegex(Error,'已变化'):slurm.collect_slurm(s)
                self.assertFalse((self.work/'.mc-jobs'/s).exists())

    def test_solver_changing_input_is_not_a_valid_completed_result(self):
        os.environ['SLURM_SOLVER_CHANGE_INPUT']='1';s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        with self.assertRaisesRegex(Error,'输入已变化|修改了输入'):slurm.collect_slurm(s)

    def test_missing_config_has_actionable_message_and_no_side_effect(self):
        del os.environ['MC_SLURM_CONFIG']
        with self.assertRaisesRegex(Error,'MC_SLURM_CONFIG'):self.prepare()
        self.assertFalse((self.work/'.mc-slurm').exists())

    def test_resource_validation_covers_related_fields_before_preparation(self):
        for resources in ({'cpus_per_task':0},{'cpus_per_task':True},{'memory_mb':-1},{'time':'never'},
                          {'partition':'cpu\n#SBATCH --nodes=99'},{'account':'a b'},{'qos':'-q'},{'nodes':10}):
            with self.subTest(resources=resources),self.assertRaises(Error):self.prepare(resources=resources)
        self.assertFalse((self.work/'.mc-slurm').exists())

    def test_paths_and_dependencies_have_explicit_mapping_without_source_changes(self):
        external=self.root/'local-data.h5';external.write_bytes(b'data')
        (self.case/'materials.xml').write_text('<materials><cross_sections>'+str(external)+'</cross_sections><material id="1"><density units="macro" value="1"/><macroscopic name="fixture"/></material></materials>')
        before=self.hashes(self.case)
        with self.assertRaisesRegex(Error,'path_mappings'):self.prepare()
        self.p['path_mappings']={str(external):'/cluster/data/mgxs.h5'};self.write_profile()
        prepared=self.prepare();self.assertEqual(prepared['path_changes'][0]['to'],'/cluster/data/mgxs.h5')
        self.assertEqual(self.hashes(self.case),before)
        self.assertNotEqual(prepared['input_hashes']['materials.xml'],before['materials.xml'])

    def test_log_limit_is_applied_and_indicated_after_collect(self):
        os.environ['SLURM_SOLVER_LOG']='1';s=self.prepare()['submission_id'];slurm.submit_slurm(s);slurm.collect_slurm(s)
        self.assertEqual((self.work/'.mc-jobs'/s/'solver.log').stat().st_size,2048)
        self.assertTrue(json.loads((self.work/'.mc-jobs'/s/'job.json').read_text())['log_truncated'])

    def test_profile_is_bound_at_prepare_not_replaced_by_later_config(self):
        prepared=self.prepare();self.p['remote_root']=str(self.root/'wrong');self.write_profile()
        slurm.submit_slurm(prepared['submission_id']);self.assertTrue(Path(prepared['remote_dir']).exists())
        self.assertFalse((self.root/'wrong').exists())

    def test_no_real_host_is_contacted_by_prepare_even_with_ssh_profile(self):
        self.p.update(transport='ssh',host='never-connect.invalid');self.write_profile()
        with patch.object(slurm,'_remote',side_effect=AssertionError('prepare used network')):result=self.prepare()
        self.assertFalse(result['network_used'])

    def test_space_or_transfer_limits_keep_remote_and_no_partial_local_job(self):
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        with patch.object(slurm.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(10,9,1)):
            with self.assertRaisesRegex(Error,'空间不足'):slurm.collect_slurm(s)
        self.assertFalse((self.work/'.mc-jobs'/s).exists());self.assertTrue(Path(self.load(s)['remote_dir']).exists())
        slurm.collect_slurm(s)

    def test_ssh_transport_quotes_paths_and_transfers_streams_without_network(self):
        self.p.update(transport='ssh',host='controlled.invalid',
                      ssh_command=[sys.executable,str(ROOT/'tests/fixtures/slurm_ssh.py')])
        self.write_profile();s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        self.assertEqual(slurm.collect_slurm(s)['state'],'completed')
        self.assertEqual((self.root/'scheduler/ssh-host').read_text(),'controlled.invalid')

    def test_queue_failure_can_use_accounting_and_stale_accounting_does_not_hide_running(self):
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        os.environ['SLURM_FIXTURE_MODE']='queue_only_error'
        self.assertEqual(slurm.status_slurm(s)['state'],'completed')
        os.environ['SLURM_FIXTURE_MODE']='hold'
        s=self.prepare()['submission_id'];r=slurm.submit_slurm(s)
        self.set_state(r['cluster_job_id'],'RUNNING')
        os.environ['SLURM_FIXTURE_MODE']='stale_accounting'
        self.assertEqual(slurm.status_slurm(s)['state'],'running')

    def test_transfer_mutation_is_rejected_and_retry_collect_preserves_identity(self):
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        remote=slurm._remote
        def damaged(p,action,payload,**kw):
            value=remote(p,action,payload,**kw)
            if action=='inventory':
                target=Path(payload['directory'])/'statepoint.6.h5'
                target.write_bytes(b'X'*target.stat().st_size)
            return value
        with patch.object(slurm,'_remote',side_effect=damaged):
            with self.assertRaisesRegex(Error,'SHA256不一致'):slurm.collect_slurm(s)
        self.assertFalse((self.work/'.mc-jobs'/s).exists())
        self.assertTrue(Path(self.load(s)['remote_dir']).exists())

    def test_concurrent_submission_is_one_scheduler_job(self):
        from concurrent.futures import ThreadPoolExecutor
        s=self.prepare()['submission_id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:slurm.submit_slurm(s),range(2)))
        self.assertEqual(results[0]['cluster_job_id'],results[1]['cluster_job_id'])
        self.assertEqual(len(self.rows()),1)

    def test_collect_before_submit_is_offline_and_preserves_prepared_state(self):
        s=self.prepare()['submission_id']
        with patch.object(slurm,'_remote',side_effect=AssertionError('not submitted')):
            with self.assertRaisesRegex(Error,'尚未提交'):slurm.collect_slurm(s)
        self.assertEqual(self.load(s)['state'],'prepared')
        slurm.submit_slurm(s)

    def test_local_snapshot_changed_after_remote_submit_cannot_be_collected(self):
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        (slurm.directory(s)/'input/settings.xml').write_text('<settings/>')
        with self.assertRaisesRegex(Error,'本地准备快照'):slurm.collect_slurm(s)
        self.assertFalse((self.work/'.mc-jobs'/s).exists())

    def test_relative_hdf5_library_is_portable_but_xml_index_requires_mapping(self):
        path=self.case/'materials.xml';text=path.read_text().replace('<materials>','<materials><cross_sections>xs.h5</cross_sections>')
        path.write_text(text);(self.case/'xs.h5').write_bytes(b'\x89HDF\r\n\x1a\nDATA')
        prepared=self.prepare();self.assertIn('xs.h5',prepared['input_hashes'])
        path.write_text(text.replace('xs.h5','cross_sections.xml'));(self.case/'cross_sections.xml').write_text('<cross_sections/>')
        with self.assertRaisesRegex(Error,'索引须用path_mappings'):self.prepare()

    def test_live_transfer_limit_can_be_increased_without_changing_task_identity(self):
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        self.p['max_transfer_bytes']=1;self.write_profile()
        with self.assertRaisesRegex(Error,'超过max_transfer_bytes'):slurm.collect_slurm(s)
        self.p['max_transfer_bytes']=1024**2;self.write_profile()
        self.assertEqual(slurm.collect_slurm(s)['job_id'],s)

    def test_remote_wrong_version_fails_before_solver_and_collects_failure_log(self):
        os.environ['SLURM_SOLVER_VERSION']='0.15.2';s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        self.assertEqual(slurm.status_slurm(s)['state'],'failed')
        slurm.collect_slurm(s)
        self.assertFalse((self.work/'.mc-jobs'/s/'work/statepoint.6.h5').exists())
        self.assertIn('版本不符',(self.work/'.mc-jobs'/s/'solver.log').read_text())

    def test_upload_archive_space_failure_does_not_stage_or_submit(self):
        s=self.prepare()['submission_id']
        with patch.object(slurm.shutil,'disk_usage',return_value=shutil._ntuple_diskusage(10,9,1)):
            with self.assertRaisesRegex(Error,'上传打包空间不足'):slurm.submit_slurm(s)
        self.assertEqual(self.load(s)['state'],'prepared')
        self.assertFalse((self.root/'scheduler').exists())

    def test_old_solver_and_scheduler_logs_are_not_immutable_model_inputs(self):
        (self.case/'solver.log').write_text('old log')
        (self.case/'slurm-1000.out').write_text('old scheduler output')
        s=self.prepare()['submission_id'];r=self.load(s)
        self.assertNotIn('solver.log',r['input_hashes']);self.assertNotIn('slurm-1000.out',r['input_hashes'])
        slurm.submit_slurm(s);self.assertEqual(slurm.collect_slurm(s)['state'],'completed')
        self.assertEqual((self.case/'solver.log').read_text(),'old log')

    def test_operational_timeout_changes_apply_to_query_and_cancel(self):
        os.environ['SLURM_FIXTURE_MODE']='hold'
        s=self.prepare()['submission_id'];slurm.submit_slurm(s)
        self.p['command_timeout_seconds']=1;self.write_profile()
        os.environ['SLURM_FIXTURE_MODE']='slow_queue'
        self.assertIn('squeue失败',slurm.status_slurm(s)['last_error'])
        os.environ['SLURM_FIXTURE_MODE']='slow_cancel'
        with self.assertRaisesRegex(Error,'scancel失败'):slurm.cancel_slurm(s)
        self.assertFalse(self.load(s).get('cancel_requested',False))

    def test_example_resumes_interrupted_upload_using_original_submission(self):
        import asyncio
        from contextlib import asynccontextmanager
        from types import SimpleNamespace
        sys.path.insert(0,str(ROOT/'examples'))
        import slurm_workflow
        s=self.prepare()['submission_id']
        with patch.object(slurm,'_remote',side_effect=Error('controlled interrupted upload')):
            with self.assertRaises(Error):slurm.submit_slurm(s)
        self.assertEqual(self.load(s)['state'],'staging')
        found={t['name']:t for t in TOOLS}
        @asynccontextmanager
        async def calls(_url):
            async def call(name,args):return await execute_tool(found[name],args)
            yield call
        args=SimpleNamespace(submission_id=s,output=self.root/'example.json',mcp_url=None,submit=True,timeout=10,poll_seconds=.01)
        with patch.object(slurm_workflow,'client',calls):result=asyncio.run(slurm_workflow.workflow(args))
        self.assertEqual(result['submission_id'],s);self.assertEqual(len(self.rows()),1)
        self.assertEqual(result['job_id'],s)

    def test_new_registry_tools_validate_real_arguments_and_descriptions(self):
        found={t['name']:t for t in TOOLS}
        for name in ('prepare','submit','status','cancel','collect'):
            tool=found['openmc_slurm_'+name]
            self.assertTrue(tool['description'])
            for field in tool['inputSchema']['properties'].values():self.assertTrue(field.get('description'))
        self.assertTrue(found['openmc_slurm_status']['annotations']['readOnlyHint'])
        self.assertFalse(found['openmc_slurm_submit']['annotations']['readOnlyHint'])
        import asyncio
        with self.assertRaises(Exception):asyncio.run(execute_tool(found['openmc_slurm_prepare'],{'case_dir':'cases/my input','resources':{'cpus_per_task':0}}))
