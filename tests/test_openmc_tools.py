"""Local engineering tests: real CLI/worker process lifecycle, explicitly mocked solver."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_jobs as jobs
import openmc_tools as tools


class OpenMCTools(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc tools with spaces ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        self.cfg.update(openmc_command=[sys.executable, str(ROOT / 'tests/fixtures/mock_openmc.py')],
                        python_command=[sys.executable], data_kind='mock_process_fixture',
                        job_timeout_seconds=15, max_log_bytes=1024)
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.cfg))
        self.env = {'MC_WORKSPACE': str(business.WORKSPACE), 'MC_OPENMC_CONFIG': str(self.config)}
        self.env_patch = patch.dict(os.environ, self.env)
        self.env_patch.start()
        self.case = business.WORKSPACE / 'case'
        self.case.mkdir()
        for name in ('materials', 'geometry', 'settings'):
            (self.case / (name + '.xml')).write_text('<' + name + '/>')
        self.mode('success')
        self.job_ids = []

    def tearDown(self):
        for job_id in self.job_ids:
            try:
                jobs.cancel(job_id)
                self.wait(job_id)
            except Exception:
                pass
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def mode(self, mode):
        (self.case / 'mock-mode.json').write_text(json.dumps({'mode': mode}))

    def submit(self):
        value = jobs.submit('case')
        self.job_ids.append(value['job_id'])
        return value['job_id']

    def wait(self, job_id):
        for _ in range(150):
            result = jobs.status(job_id)
            if result['state'] in jobs.TERMINAL:
                return result
            time.sleep(0.05)
        self.fail('job did not terminate')

    def test_structure_check_rejects_missing_malformed_wrong_root_and_paths(self):
        self.assertTrue(tools.validate_inputs('case')['structure_valid'])
        (self.case / 'geometry.xml').write_text('<settings/>')
        self.assertFalse(tools.validate_inputs('case')['structure_valid'])
        (self.case / 'geometry.xml').write_text('<geometry>')
        self.assertFalse(tools.validate_inputs('case')['structure_valid'])
        (self.case / 'geometry.xml').unlink()
        self.assertFalse(tools.validate_inputs('case')['structure_valid'])
        with self.assertRaises(ValueError):tools.validate_inputs('../escape')
        (self.case / 'geometry.xml').symlink_to(self.case / 'settings.xml')
        with self.assertRaises(ValueError):tools.validate_inputs('case')

    def test_invalid_settings_and_entity_declaration_do_not_pass(self):
        (self.case / 'settings.xml').write_text('<settings><particles>0</particles></settings>')
        self.assertFalse(tools.validate_inputs('case')['structure_valid'])
        (self.case / 'settings.xml').write_text('<!DOCTYPE settings [<!ENTITY a "x">]><settings/>')
        self.assertFalse(tools.validate_inputs('case')['structure_valid'])

    def test_missing_runtime_is_visible_and_submission_refused(self):
        self.cfg['openmc_command'] = ['/no/such/openmc']
        self.config.write_text(json.dumps(self.cfg))
        self.assertFalse(tools.environment()['executable_available'])
        with self.assertRaisesRegex(ValueError, '不可用'):tools.submit_job('case')

    def test_completed_snapshot_and_restart_query_never_resubmit(self):
        job_id = self.submit()
        (self.case / 'geometry.xml').write_text('<changed/>')
        self.assertEqual(self.wait(job_id)['state'], 'completed')
        copied = jobs.job_directory(job_id) / 'work/geometry.xml'
        self.assertEqual(copied.read_text(), '<geometry/>')
        result = subprocess.run([sys.executable, str(ROOT / 'mcp/cli.py'), 'call', 'openmc_job_status', json.dumps({'job_id': job_id})],
                                env={**os.environ, **self.env}, text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['state'], 'completed')
        self.assertEqual(len(list((business.WORKSPACE / '.mc-jobs').glob('*/job.json'))), 1)
        self.assertEqual(jobs.status(job_id)['provenance']['execution_kind'], 'mock_process_fixture')
        with self.assertRaisesRegex(ValueError, '没有statepoint'):tools.read_results(job_id)

    def test_notebook_requires_explicit_stage_instead_of_silently_using_last_result(self):
        identifier=self.submit();self.assertEqual(self.wait(identifier)['state'],'completed')
        jobs.update(jobs.job_directory(identifier),example_workflow={'example_id':'notebook-mg-mode-part-i','step':'execute','parameters':{}})
        with self.assertRaisesRegex(ValueError,'不能默认取最后StatePoint'):
            tools.read_results(identifier)

    def test_nonzero_runner_preserves_exit_code_and_reaps_orphan(self):
        self.mode('parent-exits-nonzero');identifier=self.submit();status=self.wait(identifier)
        self.assertEqual(status['state'],'failed');self.assertEqual(status['exit_code'],7)
        self.assertIn('非零退出',status['error']);self.assertTrue(status['process_cleanup']['verified_empty'])
        child=int((jobs.job_directory(identifier)/'work/child.pid').read_text())
        self.assertFalse(Path(f'/proc/{child}').exists(),'后台子进程或僵尸未回收')

    def test_nonzero_exit_is_failed_and_results_unavailable(self):
        self.mode('fail'); job_id = self.submit(); status = self.wait(job_id)
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['exit_code'], 7)
        self.assertIn('intentional failure', status['log_tail'])
        with self.assertRaises(ValueError):tools.read_results(job_id)

    def test_wrong_thread_flag_counterexample_is_detected(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tests/fixtures/mock_openmc.py'), '-t', '1'],
                                cwd=self.case, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 9)
        self.assertIn('--threads', result.stdout)

    def test_old_high_batch_statepoint_not_relabelled_as_new_result(self):
        (self.case / 'statepoint.999.h5').write_bytes(b'old result must not be copied')
        job_id = self.submit(); self.wait(job_id)
        self.assertFalse((jobs.job_directory(job_id) / 'work/statepoint.999.h5').exists())
        with self.assertRaisesRegex(ValueError, '没有statepoint'):tools.read_results(job_id)
        jobs.update(jobs.job_directory(job_id), mode='plot')
        with self.assertRaisesRegex(ValueError, '绘图作业'):tools.read_results(job_id)

    def test_parent_exits_while_child_holds_stdout_is_bounded(self):
        self.mode('parent-exits'); job_id = self.submit()
        status = self.wait(job_id)
        # An exited wrapper with live background work is not a confirmed solve.
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(status['exit_code'], 0)
        self.assertIn('后台子进程', status['error'])
        self.assertTrue(status['process_cleanup']['verified_empty'])
        pid = int((jobs.job_directory(job_id) / 'work/child.pid').read_text())
        for _ in range(50):
            if jobs.identity(pid) is None:break
            time.sleep(0.05)
        self.assertIsNone(jobs.identity(pid))

    def test_csv_itself_discloses_truncation_and_simulation_source(self):
        job_id = self.submit(); self.wait(job_id)
        data = {'provenance': {'data_kind': 'synthetic_mgxs', 'execution_kind': 'mock_process_fixture', 'note': 'test'},
                'keff': None, 'tallies': [{'id': 1, 'scores': ['flux'], 'nuclides': ['total'],
                'mean': [3.0], 'std_dev': [0.2], 'shape': [2000, 1, 1],
                'values_truncated': True, 'normalization': 'raw OpenMC values'}]}
        with patch.object(tools, 'read_results', return_value=data):report = tools.export_report(job_id, 'csv')
        text = (business.WORKSPACE / report['path']).read_text()
        self.assertIn('values_truncated', text)
        self.assertIn('True', text)
        self.assertIn('2000', text)
        self.assertIn('mock_process_fixture', text)
        self.assertIn('results.json', text)

    def test_cancel_stops_solver_child_and_is_idempotent(self):
        self.mode('long'); job_id = self.submit()
        child_file = jobs.job_directory(job_id) / 'work/child.pid'
        for _ in range(100):
            if child_file.exists():break
            time.sleep(0.05)
        self.assertTrue(child_file.exists())
        pid = int(child_file.read_text())
        jobs.cancel(job_id)
        self.assertEqual(self.wait(job_id)['state'], 'cancelled')
        for _ in range(50):
            if jobs.identity(pid) is None:break
            time.sleep(0.05)
        self.assertIsNone(jobs.identity(pid), 'solver child left running')
        self.assertFalse(jobs.cancel(job_id)['changed'])

    def test_immediate_queued_cancel_does_not_lose_request(self):
        self.mode('long'); job_id = self.submit(); jobs.cancel(job_id)
        self.assertEqual(self.wait(job_id)['state'], 'cancelled')

    def test_timeout_and_log_limit_are_explicit(self):
        self.cfg['job_timeout_seconds'] = 1
        self.config.write_text(json.dumps(self.cfg))
        self.mode('timeout'); job_id = self.submit()
        self.assertEqual(self.wait(job_id)['state'], 'failed')
        self.assertIn('时限', jobs.status(job_id)['error'])
        self.mode('log-limit'); job_id = self.submit()
        self.assertTrue(self.wait(job_id)['log_truncated'])
        self.assertLessEqual((jobs.job_directory(job_id) / 'solver.log').stat().st_size, 1024)

    def test_reused_pid_not_signalled_or_reported_as_running(self):
        job_id = self.submit(); self.wait(job_id)
        directory = jobs.job_directory(job_id)
        jobs.update(directory, state='running', worker_pid=os.getpid(), worker_identity='wrong-identity')
        self.assertEqual(jobs.status(job_id)['state'], 'interrupted')
        self.assertFalse(jobs.cancel(job_id)['changed'])

    def test_workspace_root_and_symlinks_are_not_snapshotted(self):
        with self.assertRaises(ValueError):jobs.submit('.')
        (self.case / 'link').symlink_to(self.case / 'geometry.xml')
        with self.assertRaises(ValueError):jobs.submit('case')


if __name__ == '__main__':unittest.main()
