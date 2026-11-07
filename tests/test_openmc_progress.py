"""B6 离线测试：job_status 结构化进度解析。包含真实OpenMC 0.16日志回归及构造边界样本，不执行输运。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_jobs as jobs

# 与实机 2f9da135 作业日志同构的批次表（数值为样本，不作物理结论）。
BATCH_LINES = """====================>     K EIGENVALUE SIMULATION     <====================

  Bat./Gen.      k       Entropy         Average k 
  =========   ========   ========   ====================
        1/1    1.13842    6.30692
        2/1    1.21771    6.30699
       12/1    1.14854    6.30581    1.15639 +/- 0.00785
       15/1    1.18960    6.29015    1.17034 +/- 0.01721
"""
RESULTS_BLOCK = """
 ============================>     RESULTS     <============================

 k-effective (Collision)     = 1.16607 +/- 0.00407
 Combined k-effective        = 1.16262 +/- 0.00308
 Leakage Fraction            = 0.00000 +/- 0.00000
"""


class JobProgress(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc progress ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.env_patch = patch.dict(os.environ, {'MC_WORKSPACE': str(business.WORKSPACE)})
        self.env_patch.start()
        self.now = time.time()

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def make_job(self, job_id, state, log, run_mode='eigenvalue', batches=20, inactive=5,
                 started=True):
        directory = business.WORKSPACE / '.mc-jobs' / job_id
        (directory / 'work').mkdir(parents=True)
        record = {'job_id': job_id, 'state': state, 'mode': 'run', 'case_dir': 'case',
                  'submitted_at': self.now - 70, 'exit_code': 0 if state == 'completed' else None,
                  'provenance': {'data_kind': 'mock_process_fixture'}}
        if started:
            record['started_at'] = self.now - 60
        if state in jobs.TERMINAL:
            record['finished_at'] = self.now
        (directory / 'job.json').write_text(json.dumps(record))
        if run_mode is not None:
            settings = f'<settings><run_mode>{run_mode}</run_mode><particles>100</particles>'
            if run_mode == 'eigenvalue':
                settings += f'<batches>{batches}</batches><inactive>{inactive}</inactive>'
            (directory / 'work' / 'settings.xml').write_text(settings + '</settings>')
        if log is not None:
            (directory / 'solver.log').write_text(log)
        return directory

    def test_eigenvalue_running_progress_fields(self):
        self.make_job('ab' * 16, 'running', BATCH_LINES)
        value = jobs.status('ab' * 16)
        progress = value['progress']
        self.assertEqual(progress['run_mode'], 'eigenvalue')
        self.assertEqual(progress['latest_batch'], 15)
        self.assertEqual(progress['latest_generation'], 1)
        self.assertEqual(progress['keff_latest'], 1.18960)
        self.assertEqual(progress['entropy_latest'], 6.29015)
        self.assertEqual(progress['keff_average'], {'mean': 1.17034, 'std_dev': 0.01721})
        self.assertEqual(progress['batches_total'], 20)
        self.assertEqual(progress['inactive'], 5)
        self.assertEqual(progress['percent'], 75.0)
        self.assertIsNone(progress['keff_combined'])
        self.assertGreaterEqual(progress['elapsed_seconds'], 59)
        # 既有契约不受影响
        self.assertIn('log_tail', value)
        self.assertEqual(value['state'], 'running')

    def test_completed_results_combined_keff_and_full_percent(self):
        self.make_job('cd' * 16, 'completed', BATCH_LINES + RESULTS_BLOCK)
        progress = jobs.status('cd' * 16)['progress']
        self.assertEqual(progress['keff_combined'], {'mean': 1.16262, 'std_dev': 0.00308})
        self.assertEqual(progress['percent'], 100.0)

    def test_actual_no_entropy_log_preserves_last_batch_average_and_combined(self):
        log=(ROOT/'tests/fixtures/openmc-no-entropy.txt').read_text()
        self.make_job('02'*16, 'completed', log, batches=12, inactive=2)
        p=jobs.status('02'*16)['progress']
        self.assertEqual((p['latest_batch'],p['latest_generation']),(12,1))
        self.assertEqual(p['keff_latest'],1.16911)
        self.assertIsNone(p['entropy_latest'])
        self.assertEqual(p['keff_average'],{'mean':1.18114,'std_dev':.01027})
        self.assertEqual(p['keff_combined'],{'mean':1.17112,'std_dev':.00833})

    def test_inactive_and_scientific_rows_do_not_consume_the_next_line(self):
        directory=self.make_job('03'*16,'running','')
        for log,batch,keff,entropy in [
            ('  1/1   1.20202\n',1,1.20202,None),
            ('  2/1   1.2E+0  6.3D+0\n',2,1.2,6.3),
            (' 12/1   1.16911  1.18114 +/- 1.027E-2\n 13/1\n',12,1.16911,None)]:
            (directory/'solver.log').write_text(log)
            p=jobs.status('03'*16)['progress']
            self.assertEqual(p['latest_batch'],batch);self.assertEqual(p['keff_latest'],keff)
            self.assertEqual(p['entropy_latest'],entropy)

    def test_last_nonfinite_values_remain_null_with_batch_and_warning(self):
        self.make_job('04'*16,'running',' 1/1 1.2\n 2/1 nan 1.1 +/- inf\n')
        p=jobs.status('04'*16)['progress']
        self.assertEqual(p['latest_batch'],2);self.assertIsNone(p['keff_latest'])
        self.assertIsNone(p['keff_average']['std_dev']);self.assertIn('非有限',p['note'])
        json.dumps(p,allow_nan=False)

    def test_fixed_source_has_no_invented_percent(self):
        self.make_job('ef' * 16, 'running', ' Initializing source particles...\n batch 3 done\n',
                      run_mode='fixed source')
        progress = jobs.status('ef' * 16)['progress']
        self.assertEqual(progress['run_mode'], 'fixed source')
        self.assertIsNone(progress['latest_batch'])
        self.assertIsNone(progress['percent'])
        self.assertIn('不编造百分比', progress['note'])

    def test_missing_log_is_honest(self):
        self.make_job('01' * 16, 'queued', None, started=False)
        progress = jobs.status('01' * 16)['progress']
        self.assertIn('尚不存在', progress['note'])
        self.assertIsNone(progress['elapsed_seconds'])

    def test_binary_garbage_log_does_not_crash(self):
        self.make_job('23' * 16, 'running', '\x00\xff\xfe garbage 12/1 not-a-batch-line')
        progress = jobs.status('23' * 16)['progress']
        self.assertIsNone(progress['latest_batch'])

    def test_eigenvalue_without_batch_lines_yet(self):
        self.make_job('45' * 16, 'running', ' Loading cross sections...\n')
        progress = jobs.status('45' * 16)['progress']
        self.assertIsNone(progress['latest_batch'])
        self.assertIn('初始化', progress['note'])

    def test_completed_state_falls_back_to_full_percent(self):
        # 日志被截断只剩头部时，completed 的本征值作业仍按状态给 100%
        self.make_job('67' * 16, 'completed', ' Loading cross sections...\n')
        progress = jobs.status('67' * 16)['progress']
        self.assertEqual(progress['percent'], 100.0)
        self.assertIsNone(progress['keff_combined'])

    def test_nan_combined_keff_reported_not_parsed(self):
        # 真实历史作业（0ea869ea）出现过的退化输出：std为-nan；不得进入JSON结果
        log = BATCH_LINES + ("""
 ============================>     RESULTS     <============================

 Combined k-effective        = 1.66667 +/- -nan
 Leakage Fraction            = 0.00000 +/- 0.00000
""")
        self.make_job('89' * 16, 'completed', log)
        progress = jobs.status('89' * 16)['progress']
        self.assertIsNone(progress['keff_combined'])
        self.assertIn('非有限值', progress['keff_note'])
        self.assertIn('-nan', progress['keff_note']);self.assertIn('未收敛', progress['keff_note']);self.assertIn('方差为0', progress['keff_note'])


if __name__ == '__main__':
    unittest.main()
