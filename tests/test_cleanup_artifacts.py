"""Exercise real temporary directories, references, locks and removal failures."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('mc_cleanup',ROOT/'scripts/cleanup_artifacts.py')
cleanup=importlib.util.module_from_spec(spec);spec.loader.exec_module(cleanup)
J='1'*32;K='2'*32;S='3'*32;T='4'*32


class CleanupRecords(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='mc cleanup ')
        self.root=Path(self.temp.name)
        (self.root/'cases/input').mkdir(parents=True)
        self.original=self.root/'cases/input/materials.xml';self.original.write_text('original input')
        self.config=self.root/'configuration.json';self.config.write_text('keep config')

    def tearDown(self):self.temp.cleanup()

    def record(self,kind,identifier,**changes):
        target=self.root/('.mc-jobs' if kind=='job' else '.mc-scans')/identifier;target.mkdir(parents=True)
        value={kind+'_id':identifier,'state':'completed','finished_at':time.time()-86400,
               'worker_pid':999999999,'worker_identity':'old-boot:100','case_dir':'cases/input'}
        if kind=='scan':value['cases']=[]
        value.update(changes)
        (target/'job.json').write_text(json.dumps(value));(target/'output.bin').write_bytes(b'generated data')
        return target

    def plan(self,jobs=(),scans=(),days=None,allow=False):
        return cleanup.plan(self.root,list(jobs),list(scans),days,allow)

    def invoke(self,*args):
        return subprocess.run([str(ROOT/'cleanup.sh'),'--workspace',str(self.root),*args],capture_output=True,text=True)

    def test_shell_entry_runs_from_other_cwd_and_preserves_relative_workspace(self):
        source=self.record('job',J)
        package=self.root/'package with spaces';(package/'scripts').mkdir(parents=True)
        shutil.copy2(ROOT/'cleanup.sh',package/'cleanup.sh')
        shutil.copy2(ROOT/'scripts/cleanup_artifacts.py',package/'scripts/cleanup_artifacts.py')
        client=self.root/'client cwd';data=client/'data';data.mkdir(parents=True)
        shutil.copytree(source,data/'.mc-jobs'/J)
        run=subprocess.run([str(package/'cleanup.sh'),'--job-id',J,'--json'],cwd=client,
                           env={**os.environ,'MC_WORKSPACE':'data'},capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        result=json.loads(run.stdout)
        self.assertEqual(result['state'],'preview')
        self.assertEqual(result['plan']['workspace'],str(data))
        self.assertTrue((data/'.mc-jobs'/J).exists())

    def example(self,identifier,jobs,**changes):
        target=self.root/'.mc-examples'/identifier;target.mkdir(parents=True)
        value={'instance_id':identifier,'state':'completed','finished_at':time.time()-86400,
               'steps':{str(i):{'job_id':job} for i,job in enumerate(jobs)}}
        value.update(changes);(target/'example.json').write_text(json.dumps(value));return target

    def test_example_children_cannot_be_removed_alone_and_explicit_instance_removes_graph(self):
        child=self.record('job',J);instance=self.example(S,[J])
        with self.assertRaisesRegex(cleanup.CleanupError,'官方例题引用'):self.plan([J])
        run=self.invoke('--example-id',S,'--apply','--json')
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertFalse(child.exists());self.assertFalse(instance.exists());self.assertTrue(self.original.exists())

    def test_age_filter_preserves_job_referenced_by_recent_example(self):
        self.record('job',J,finished_at=time.time()-8*86400)
        self.example(S,[J],finished_at=time.time()-60)
        self.assertEqual(self.plan(days=7)['entries'],[])

    def test_default_preview_and_apply_require_explicit_selection(self):
        target=self.record('job',J)
        preview=self.invoke();self.assertEqual(preview.returncode,0,preview.stderr);self.assertIn('未删除',preview.stdout);self.assertTrue(target.exists())
        rejected=self.invoke('--apply');self.assertNotEqual(rejected.returncode,0);self.assertTrue(target.exists())

    def test_apply_removes_selected_record_but_preserves_inputs_and_config(self):
        target=self.record('job',J);keep=self.record('job',K)
        run=self.invoke('--job-id',J,'--apply','--json')
        self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(json.loads(run.stdout)['state'],'deleted')
        self.assertFalse(target.exists());self.assertTrue(keep.exists())
        self.assertEqual(self.original.read_text(),'original input');self.assertEqual(self.config.read_text(),'keep config')

    def test_scan_removes_children_and_rejects_standalone_child_removal(self):
        child=self.record('job',J);scan=self.record('scan',S,cases=[{'job_id':J,'state':'completed'}])
        with self.assertRaisesRegex(cleanup.CleanupError,'扫描引用'):self.plan([J])
        run=self.invoke('--scan-id',S,'--apply')
        self.assertEqual(run.returncode,0,run.stderr);self.assertFalse(child.exists());self.assertFalse(scan.exists());self.assertTrue(self.original.exists())

    def test_age_is_finished_time_not_modified_time_and_does_not_break_retained_scan(self):
        old=self.record('job',J,finished_at=time.time()-8*86400)
        recent=self.record('job',K,finished_at=time.time()-60)
        self.record('scan',S,cases=[{'job_id':J,'state':'completed'}],finished_at=time.time()-60)
        os.utime(recent,(1,1))
        value=self.plan(days=7);self.assertEqual(value['entries'],[])
        self.assertTrue(old.exists())

    def test_active_and_interrupted_records_are_refused(self):
        target=self.record('job',J,state='running')
        with self.assertRaisesRegex(cleanup.CleanupError,'未确认结束'):self.plan([J])
        value=json.loads((target/'job.json').read_text());value['state']='interrupted';(target/'job.json').write_text(json.dumps(value))
        with self.assertRaisesRegex(cleanup.CleanupError,'interrupted'):self.plan([J])

    def test_completed_record_with_live_worker_is_refused(self):
        self.record('job',J,worker_pid=os.getpid(),worker_identity=cleanup.process_identity(os.getpid()))
        with self.assertRaisesRegex(cleanup.CleanupError,'进程仍在运行'):self.plan([J])

    def test_missing_or_future_completion_and_process_evidence_are_refused(self):
        for identifier,changes in [(J,{'finished_at':None}),(K,{'finished_at':time.time()+86400}),(T,{'worker_identity':None})]:
            self.record('job',identifier,**changes)
            with self.assertRaises(cleanup.CleanupError):self.plan([identifier])

    def test_partial_state_is_only_valid_for_scan_not_job(self):
        self.record('job',J,state='partial')
        with self.assertRaises(cleanup.CleanupError):self.plan([J])

    def test_corrupt_scan_associations_and_symlinks_are_never_ignored(self):
        self.record('job',J);scan=self.record('scan',S,cases='broken')
        with self.assertRaisesRegex(cleanup.CleanupError,'关联记录'):self.plan([J])
        (scan/'job.json').write_text('invalid json')
        with self.assertRaisesRegex(cleanup.CleanupError,'记录损坏'):self.plan([J])

    def test_symlink_inside_record_preserves_external_data(self):
        target=self.record('job',J);(target/'link').symlink_to(self.original)
        with self.assertRaisesRegex(cleanup.CleanupError,'符号链接'):self.plan([J])
        self.assertEqual(self.original.read_text(),'original input')

    def test_scan_shared_child_cannot_be_removed_with_only_one_parent(self):
        self.record('job',J)
        for identifier in [S,T]:self.record('scan',identifier,cases=[{'job_id':J}])
        with self.assertRaisesRegex(cleanup.CleanupError,'扫描引用'):self.plan(scans=[S])

    def test_repair_history_requires_explicit_choice_and_keeps_plan_and_backup(self):
        target=self.record('job',J);plans=self.root/'.mc-plans';plans.mkdir()
        plan=plans/('a'*32+'.json');plan.write_text(json.dumps({'plan_id':'a'*32,'state':'applied','context':{'origin_job_id':J}}))
        backup=self.root/'.mc-backups/b';backup.parent.mkdir();backup.write_text('original backup')
        with self.assertRaisesRegex(cleanup.CleanupError,'修复计划引用'):self.plan([J])
        run=self.invoke('--job-id',J,'--allow-repair-references','--apply')
        self.assertEqual(run.returncode,0,run.stderr);self.assertFalse(target.exists());self.assertTrue(plan.exists());self.assertTrue(backup.exists())

    def test_malformed_repair_context_is_refused(self):
        self.record('job',J);plans=self.root/'.mc-plans';plans.mkdir()
        for context in [['invalid'],[],{'origin_job_id':[]},{'rerun_job_id':123}]:
            with self.subTest(context=context):
                (plans/'p.json').write_text(json.dumps({'context':context}))
                with self.assertRaises(cleanup.CleanupError):self.plan([J])

    def test_execution_rechecks_changed_record_before_deleting(self):
        target=self.record('job',J);value=self.plan([J])
        (target/'new-output').write_text('added after preview')
        with self.assertRaisesRegex(cleanup.CleanupError,'改变'):cleanup.apply_plan(value,lambda:self.plan([J]))
        self.assertTrue(target.exists())

    def test_another_running_record_prevents_apply(self):
        target=self.record('job',J);self.record('job',K,state='queued')
        run=self.invoke('--job-id',J,'--apply')
        self.assertNotEqual(run.returncode,0);self.assertTrue(target.exists());self.assertIn('还有运行',run.stderr)

    def test_two_cleanup_processes_do_not_remove_same_record(self):
        target=self.record('job',J)
        with (self.root/'.mc-cleanup.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            run=self.invoke('--job-id',J,'--apply')
        self.assertNotEqual(run.returncode,0);self.assertIn('已有清理进程',run.stderr);self.assertTrue(target.exists())

    def test_real_filesystem_failure_reports_partial_result(self):
        if os.geteuid()==0:self.skipTest('权限失败用例需要普通用户执行')
        for machine in [True,False]:
            with self.subTest(json=machine):
                first=self.record('job',J);second=self.record('job',K)
                protected=second/'protected';protected.mkdir();(protected/'file').write_text('cannot unlink');protected.chmod(0o500)
                try:
                    run=self.invoke('--job-id',J,'--job-id',K,'--apply',*(['--json'] if machine else []))
                    self.assertNotEqual(run.returncode,0,run.stdout)
                    if machine:
                        result=json.loads(run.stdout);self.assertEqual(result['state'],'partial_failure')
                        self.assertIn(str(first),result['removed']);self.assertIn(str(second),result['not_removed_or_partial'])
                    else:
                        self.assertIn('已删除：'+json.dumps([str(first)],ensure_ascii=False),run.stdout)
                        self.assertIn('未删或可能部分删除：'+json.dumps([str(second)],ensure_ascii=False),run.stdout)
                    self.assertFalse(first.exists());self.assertTrue(self.original.exists())
                finally:
                    if protected.exists():protected.chmod(0o700)
                    if second.exists():shutil.rmtree(second)

    def test_invalid_id_never_escapes_workspace(self):
        self.record('job',J)
        with self.assertRaises(cleanup.CleanupError):self.plan(['../cases/input'])
        self.assertTrue(self.original.exists())
