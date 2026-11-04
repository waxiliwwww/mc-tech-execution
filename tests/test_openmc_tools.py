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

    def test_markdown_report_head_carries_metadata_five_items(self):
        job_id = self.submit(); self.wait(job_id)
        directory = jobs.job_directory(job_id)
        (directory / 'work/settings.xml').write_text(
            '<settings><run_mode>eigenvalue</run_mode><particles>400</particles>'
            '<batches>20</batches><inactive>5</inactive><seed>7</seed></settings>')
        data = {'provenance': {'data_kind': 'synthetic_mgxs', 'execution_kind': 'mock_process_fixture',
                               'note': 'test'}, 'keff': None, 'tallies': []}
        with patch.object(tools, 'read_results', return_value=data):
            report = tools.export_report(job_id, 'markdown')
        text = (business.WORKSPACE / report['path']).read_text()
        for label in ('模板版本', '参数', 'OpenMC版本', '运行时间', '随机种子'):
            self.assertIn(label, text)
        self.assertEqual(report['metadata']['parameters']['particles'], '400')
        self.assertEqual(report['metadata']['seed'], '7')
        self.assertIn('particles=400', text)
        self.assertIsNotNone(report['metadata']['runtime']['duration_seconds'])
        self.assertIn('报告头元数据', text)

    def test_view_geometry_reports_unbounded_geometry_clearly(self):
        import openmc_tools
        from openmc_runtime import Error
        directory = self.root / 'workspace/unbounded'
        directory.mkdir(parents=True)
        for name, content in {
                'materials.xml': '<materials><material id="1"><density value="1.0" units="g/cm3"/>'
                                 '<nuclide name="U235" wo="1.0"/></material></materials>',
                'geometry.xml': '<geometry><cell id="1" material="1" region="-1"/></geometry>',
                'settings.xml': '<settings><run_mode>eigenvalue</run_mode><particles>10</particles>'
                                '<batches>2</batches></settings>',
        }.items():
            (directory / name).write_text(content, encoding='utf-8')
        with patch.object(openmc_tools, 'api',
                               side_effect=Error('OpenMC API失败：ValueError: The geometry does not '
                                                 'appear to be bounded in the xy plane')):
            with self.assertRaises(ValueError) as caught:
                openmc_tools.view_geometry('unbounded')
        self.assertIn('无界', str(caught.exception))
        self.assertIn('origin/width', str(caught.exception))

    def test_fault_sample_library_lists_and_self_checks(self):
        import openmc_faults
        listing = openmc_faults.list_samples()
        self.assertEqual(listing['count'], 17)
        self.assertEqual({item['kind'] for item in listing['samples']}, {'input', 'log'})
        result = openmc_faults.check_sample()
        self.assertEqual(result['total'], 17)
        self.assertEqual(result['failed'], [], result.get('results'))
        self.assertEqual(result['passed'] + len(result['skipped']), 17)
        single = openmc_faults.check_sample('s01-settings-syntax')
        self.assertEqual(single['total'], 1)
        self.assertTrue(single['results'][0]['passed'])
        with self.assertRaises(ValueError):
            openmc_faults.check_sample('no-such-sample')

    def test_view_geometry_validates_arguments_and_writes_png(self):
        case = self.root / 'workspace/look'
        case.mkdir(parents=True)
        captured = {}

        def fake_api(action, directory, params, cfg=None):
            captured.update(action=action, payload=params)
            (Path(directory) / params['filename']).parent.mkdir(parents=True, exist_ok=True)
            (Path(directory) / params['filename']).write_bytes(b'PNG')
            return {'image': params['filename'], 'basis': params['basis'], 'pixels': params['pixels']}

        with patch.object(tools, 'api', fake_api):
            value = tools.view_geometry('look', basis='xz', pixels=256, color_by='cell',
                                        width=[4.0, 8.0], origin=[1, 2, 3])
        self.assertEqual(captured['action'], 'view-geometry')
        self.assertEqual(captured['payload']['basis'], 'xz')
        self.assertEqual(captured['payload']['width'], [4.0, 8.0])
        self.assertEqual(captured['payload']['origin'], [1.0, 2.0, 3.0])
        self.assertIsInstance(value['path'], str, value)
        self.assertTrue(value['path'].endswith('.png'), value)
        self.assertNotIn('image', value, '统一返回结构：不重复给image键')
        for bad in ({'basis': 'ab'}, {'pixels': 10}, {'pixels': 5000}, {'color_by': 'zone'},
                    {'origin': [1, 2]}, {'width': [0]}, {'width': [1, 2, 3]}):
            arguments = {'basis': 'xy', 'pixels': 300, 'color_by': 'material', **bad}
            with self.assertRaises(ValueError):
                with patch.object(tools, 'api', fake_api):
                    tools.view_geometry('look', **arguments)

    def test_configure_depletion_validates_and_writes_marker(self):
        case = self.root / 'workspace/dep'
        case.mkdir(parents=True)
        (case / 'materials.xml').write_text(
            '<materials><material id="1" volume="1.0" density="10.0"></material></materials>', encoding='utf-8')
        (case / 'settings.xml').write_text('<settings><particles>10</particles><batches>2</batches>'
                                          '<inactive>0</inactive></settings>', encoding='utf-8')
        chain = self.root / 'chain.xml'
        chain.write_text('<depletion_chain/>', encoding='utf-8')
        cfg = {**self.cfg, 'depletion_chain': str(chain)}
        with patch.object(tools, 'config', lambda: cfg):
            value = tools.configure_depletion('dep', 1000, [0.5, 0.5], 'd')
        self.assertEqual(value['steps'], 2)
        self.assertEqual(value['chain_file'], str(chain))
        self.assertEqual(len(value['chain_sha256']), 64)
        marker = json.loads((case / 'mc-case.json').read_text(encoding='utf-8'))
        self.assertEqual(marker['depletion']['power_watts'], 1000.0)
        self.assertEqual(marker['depletion']['timesteps'], [0.5, 0.5])
        for bad in ({'power_watts': 0}, {'power_watts': -3}, {'timesteps': []},
                    {'timesteps': [0.1, 0]}, {'timesteps': '0.1'}, {'timestep_unit': 'd2'}):
            arguments = {'power_watts': 1000, 'timesteps': [0.1], 'timestep_unit': 'd', **bad}
            with self.assertRaises(ValueError):
                with patch.object(tools, 'config', lambda: cfg):
                    tools.configure_depletion('dep', **arguments)
        (case / 'materials.xml').write_text('<materials><material id="1"></material></materials>', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '体积'):
            with patch.object(tools, 'config', lambda: cfg):
                tools.configure_depletion('dep', 1000, [0.1])

    def test_concurrency_limits_by_cpu_threads_and_optional_job_count(self):
        cfg = {**self.cfg, 'threads': 1}
        (self.root / 'workspace/.mc-jobs').mkdir(parents=True, exist_ok=True)
        busy = self.root / ('workspace/.mc-jobs/' + 'a' * 32)
        busy.mkdir()
        (busy / 'job.json').write_text(json.dumps({'job_id': 'a' * 32, 'state': 'running',
                                                   'threads': 4}), encoding='utf-8')
        usage = jobs.running_usage()
        self.assertEqual(usage, {'running_jobs': 1, 'used_threads': 4})
        capacity = jobs.check_capacity({**cfg, 'cpu_count': 8}, 4)
        self.assertEqual(capacity['cores'], 8)
        self.assertEqual(capacity['used_threads'], 4)
        with self.assertRaises(business.MCInputError) as caught:
            jobs.check_capacity({**cfg, 'cpu_count': 8}, 5)
        self.assertIn('超核', str(caught.exception))
        with self.assertRaises(business.MCInputError) as caught:
            jobs.check_capacity({**cfg, 'max_concurrent_jobs': 1}, 1)
        self.assertIn('并发作业已达上限', str(caught.exception))
        finished = json.loads((busy / 'job.json').read_text())
        finished['state'] = 'completed'
        (busy / 'job.json').write_text(json.dumps(finished), encoding='utf-8')
        self.assertEqual(jobs.running_usage()['running_jobs'], 0)

    def test_limits_come_from_configuration(self):
        cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        self.assertGreaterEqual(cfg['max_log_bytes'], 50 * 1024 * 1024)
        self.assertEqual(cfg['max_input_bytes'], 10 * 1024 * 1024)
        case = self.root / 'workspace/big'
        (case).mkdir(parents=True)
        (case / 'geometry.xml').write_text('<geometry/>', encoding='utf-8')
        (case / 'materials.xml').write_text('<materials/>', encoding='utf-8')
        (case / 'settings.xml').write_text('<settings><particles>10</particles><batches>2</batches>'
                                          '<inactive>0</inactive></settings>', encoding='utf-8')
        with patch.object(tools, 'config', lambda: {**cfg, 'max_input_bytes': 8}):
            value = tools.validate_inputs('big')
        self.assertTrue(any('MiB结构检查上限' in item for item in value['problems']), value)

    def test_create_model_accepts_public_benchmark_templates_only(self):
        calls = []

        def fake(action, directory, payload, cfg=None):
            calls.append(payload['template'])
            return {'template': payload['template'], 'files': [], 'data_kind': 'stub'}

        for template in ('assembly-17x17', 'core-lattice', 'control-rod', 'hex-lattice'):
            with patch.object(tools, 'api', fake):
                value = tools.create_model('case-' + template, template=template)
            self.assertEqual(value['template'], template)
        with self.assertRaises(business.MCInputError) as caught:
            tools.create_model('bad-template', template='mystery')
        self.assertIn('hex-lattice', str(caught.exception))
        self.assertEqual(calls, ['assembly-17x17', 'core-lattice', 'control-rod', 'hex-lattice'])

    def test_define_tally_validates_and_sends_normalised_payload(self):
        calls = []

        def fake(action, directory, payload, cfg=None):
            calls.append((action, payload))
            return {'tally_id': payload.get('tally_id') or 7, 'name': payload.get('name') or '',
                    'scores': payload['scores'], 'filters': payload['filters'], 'tally_count': 2,
                    'note': ''}

        mesh = {'type': 'mesh', 'dimension': [2, 2, 1], 'lower_left': [-10, -10, -10],
                'upper_right': [10, 10, 10]}
        with patch.object(tools, 'api', fake):
            value = tools.define_tally('case', ['flux'], filters=[mesh], name='网格通量')
        self.assertEqual(value['tally_id'], 7)
        self.assertEqual(calls[0][0], 'add-tally')
        self.assertEqual(calls[0][1]['filters'][0]['type'], 'mesh')
        for bad in (
            {'scores': []},
            {'scores': ['']},
            {'scores': ['flux'], 'filters': [{'type': 'mystery'}]},
            {'scores': ['flux'], 'filters': [{'type': 'energy', 'bins': [1e7, 1e6]}]},
            {'scores': ['flux'], 'filters': [{'type': 'energy', 'bins': [0]}]},
            {'scores': ['flux'], 'filters': [{'type': 'material', 'bins': [0]}]},
            {'scores': ['flux'], 'filters': [{'type': 'particle', 'bins': ['muon']}]},
            {'scores': ['flux'], 'filters': [{'type': 'mesh', 'dimension': [2, 2],
                                              'lower_left': [0, 0, 0], 'upper_right': [1, 1, 1]}]},
            {'scores': ['flux'], 'filters': [{'type': 'mesh', 'dimension': [2, 2, 1],
                                              'lower_left': [0, 0, 0], 'upper_right': [0, 1, 1]}]},
            {'scores': ['flux'], 'filters': [{'type': 'mesh', 'dimension': [2, 2, 1],
                                              'lower_left': [0, 0, 0], 'upper_right': [1, 1, 1]},
                                             {'type': 'mesh', 'dimension': [2, 2, 1],
                                              'lower_left': [0, 0, 0], 'upper_right': [1, 1, 1]}]},
            {'scores': ['flux'], 'tally_id': 0},
            {'scores': ['flux'], 'name': 'x' * 81},
        ):
            with self.assertRaises(business.MCInputError):
                tools.define_tally('case', **bad)
        self.assertEqual(len(calls), 1)  # 非法请求不应触达引擎

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
