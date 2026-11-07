"""D1 回归：Combined k 不可用时，摘要不能选批次表 Average。

功能：`openmc_job_status.human_summary` 与 `progress.keff_source` 的 k 回退顺序。
类别：回归（旧行为：Combined std 为 -nan 时摘要选批次表 Average=Track-length，
      丢掉同一 RESULTS 中与解析值一致的 Absorption）。
前置：无。构造隔离工作区和日志；不执行输运。
输入：一份包含 Collision/Track-length/Absorption 和 Combined=-nan 的 solver.log。
预期：摘要和 `keff_source` 选 Absorption；只有 Absorption 不可用时才依次落到
      Collision、Track-length；批次表 Average 最后。
不证明：不证明真实物理收敛、StatePoint 数值、跨版本日志格式或专业验收。
"""
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


RESULTS_BLOCK = """
 ============================>     RESULTS     <============================

 k-effective (Collision)     = 1.66469 +/- 0.03160
 k-effective (Track-length)  = 1.68349 +/- 0.04545
 k-effective (Absorption)    = 1.66667 +/- 0.00000
 Combined k-effective        = 1.66667 +/- -nan
 Leakage Fraction            = 0.00000 +/- 0.00000
"""


class KefSummaryFallback(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc k summary ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.env_patch = patch.dict(os.environ, {'MC_WORKSPACE': str(business.WORKSPACE)})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def make_job(self, job_id, log):
        directory = business.WORKSPACE / '.mc-jobs' / job_id
        (directory / 'work').mkdir(parents=True)
        (directory / 'work' / 'settings.xml').write_text(
            '<settings><run_mode>eigenvalue</run_mode><batches>10</batches>'
            '<inactive>2</inactive></settings>')
        (directory / 'job.json').write_text(json.dumps({
            'job_id': job_id, 'state': 'completed', 'mode': 'run', 'case_dir': 'case',
            'submitted_at': time.time() - 10, 'started_at': time.time() - 9,
            'finished_at': time.time(), 'exit_code': 0,
            'provenance': {'data_kind': 'constructed_log_fixture'}}))
        (directory / 'solver.log').write_text(log)
        return directory

    def test_combined_nan_uses_absorption_not_batch_average(self):
        log = (' 1/10 1.6 1.0 1.6 +/- 0.1\n'
               ' 2/10 1.6 1.0 1.6 +/- 0.1\n' + RESULTS_BLOCK)
        self.make_job('aa' * 16, log)
        value = jobs.status('aa' * 16)
        progress = value['progress']
        self.assertEqual(progress['keff_combined'], None)
        self.assertEqual(progress['keff_estimates']['absorption'],
                         {'mean': 1.66667, 'std_dev': 0.0})
        self.assertIn('k=1.66667±0.00000', value['human_summary'])
        self.assertIn('Absorption', progress['keff_source'])
        self.assertNotIn('Track-length', progress['keff_source'])
        self.assertNotIn('Average', progress['keff_source'])
        json.dumps(value, allow_nan=False)


if __name__ == '__main__':
    unittest.main()