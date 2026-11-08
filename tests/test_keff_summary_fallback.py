"""Job status must not promote nonfinite/zero-uncertainty estimates into a conclusion.
Constructed log fixtures exercise public status and depletion block consistency.
No solver/physics acceptance is implied; real-log replay is retained separately.
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

    def test_combined_nan_does_not_announce_a_fallback_as_a_result(self):
        log = (' 1/10 1.6 1.0 1.6 +/- 0.1\n'
               ' 2/10 1.6 1.0 1.6 +/- 0.1\n' + RESULTS_BLOCK)
        self.make_job('aa' * 16, log)
        value = jobs.status('aa' * 16)
        progress = value['progress']
        self.assertEqual(progress['keff_combined'], None)
        self.assertEqual(progress['keff_estimates']['absorption'],
                         {'mean': 1.66667, 'std_dev': 0.0})
        self.assertNotIn('k=', value['human_summary'])
        self.assertIn('不可用', value['human_summary'])
        self.assertIn('非有限', value['human_summary'])
        self.assertIsNone(progress['keff_source'])
        json.dumps(value, allow_nan=False)

    def test_zero_combined_cannot_fall_back_to_a_positive_estimator(self):
        self.make_job('ee' * 16, RESULTS_BLOCK.replace('1.66667 +/- -nan', '1.66667 +/- 0.0'))
        value = jobs.status('ee' * 16)
        self.assertEqual(value['progress']['keff_combined']['std_dev'], 0.0)
        self.assertGreater(value['progress']['keff_estimates']['collision']['std_dev'], 0)
        self.assertNotIn('k=', value['human_summary'])
        self.assertIn('标准差为零', value['human_summary'])

    def test_depletion_uses_one_final_results_block_and_points_to_time_series(self):
        first = RESULTS_BLOCK.replace('1.66667 +/- -nan', '1.30 +/- 0.03')
        last = RESULTS_BLOCK.replace('1.66667 +/- -nan', '1.11 +/- 0.02').replace('1.66667 +/- 0.00000', '1.12 +/- 0.04')
        directory = self.make_job('bb' * 16, first + last)
        record = json.loads((directory/'job.json').read_text()); record['mode'] = 'deplete'
        (directory/'job.json').write_text(json.dumps(record))
        value = jobs.status('bb' * 16)
        self.assertEqual(value['progress']['keff_combined']['mean'], 1.11)
        self.assertEqual(value['progress']['keff_estimates']['absorption']['mean'], 1.12)
        self.assertNotIn('k=', value['human_summary'])
        self.assertIn('openmc_read_depletion', value['human_summary'])

    def test_incomplete_final_block_cannot_reuse_an_earlier_combined(self):
        first = RESULTS_BLOCK.replace('1.66667 +/- -nan', '1.30 +/- 0.03')
        self.make_job('cc' * 16, first + '\n ====>     RESULTS     <====\n')
        value = jobs.status('cc' * 16)
        self.assertIsNone(value['progress']['keff_combined'])
        self.assertEqual(value['progress']['keff_estimates'], {})
        self.assertNotIn('k=', value['human_summary'])

    def test_small_nonzero_uncertainty_is_not_formatted_as_zero(self):
        self.make_job('dd' * 16, RESULTS_BLOCK.replace('1.66667 +/- -nan', '1.25 +/- 0.00000001'))
        value = jobs.status('dd' * 16)
        self.assertNotIn('±0.00000', value['human_summary'])
        self.assertIn('1e-08', value['human_summary'])


if __name__ == '__main__':
    unittest.main()