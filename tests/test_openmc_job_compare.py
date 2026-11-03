"""D2 离线测试：openmc_job_compare 输入指纹、状态与 Combined k 对比。日志样本按真实格式构造，无物理含义。"""
import hashlib
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
import openmc_tools as tools
from openmc_runtime import Error

BATCH = """====================>     K EIGENVALUE SIMULATION     <====================

  Bat./Gen.      k       Entropy         Average k 
  =========   ========   ========   ====================
        1/1    1.13842    6.30692
"""
SETTINGS = ('<settings><run_mode>eigenvalue</run_mode><particles>100</particles>'
            '<batches>20</batches><inactive>5</inactive></settings>')


def results_block(mean, std):
    return (f"""
 ============================>     RESULTS     <============================

 Combined k-effective        = {mean} +/- {std}
""")


class JobCompare(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc compare ')
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

    def make_job(self, job_id, state='completed', settings=SETTINGS, log=None,
                 extra_input=None, threads=1):
        directory = business.WORKSPACE / '.mc-jobs' / job_id
        (directory / 'work').mkdir(parents=True)
        inputs = {'settings.xml': hashlib.sha256(settings.encode()).hexdigest()}
        (directory / 'work' / 'settings.xml').write_text(settings)
        if extra_input:
            (directory / 'work' / extra_input).write_text('x')
            inputs[extra_input] = hashlib.sha256(b'x').hexdigest()
        record = {'job_id': job_id, 'state': state, 'mode': 'run', 'case_dir': 'case',
                  'threads': threads, 'submitted_at': self.now - 70,
                  'started_at': self.now - 60, 'finished_at': self.now, 'exit_code': 0,
                  'input_files': inputs, 'output_files': {'statepoint.20.h5': 'sha-out'},
                  'provenance': {'data_kind': 'mock_process_fixture'}}
        (directory / 'job.json').write_text(json.dumps(record))
        if log is not None:
            (directory / 'solver.log').write_text(log)
        return job_id

    def test_identical_inputs_and_same_keff(self):
        log = BATCH + results_block(1.16262, 0.00308)
        a = self.make_job('aa' * 16, log=log)
        b = self.make_job('bb' * 16, log=log)
        value = tools.compare_jobs(a, b)
        inputs = value['comparison']['inputs']
        self.assertEqual(inputs['identical'], ['settings.xml'])
        self.assertEqual(inputs['differing'], [])
        keff = value['comparison']['keff_combined']
        self.assertEqual(keff['difference'], 0.0)
        self.assertTrue(value['comparison']['runtime']['state']['same'])
        self.assertFalse(value['professional_acceptance'])

    def test_differing_settings_and_only_in_side(self):
        other = SETTINGS.replace('<particles>100</particles>', '<particles>200</particles>')
        a = self.make_job('cc' * 16, log=BATCH + results_block(1.16262, 0.00308))
        b = self.make_job('dd' * 16, settings=other, extra_input='tallies.xml',
                          log=BATCH + results_block(1.15000, 0.00400), threads=4)
        value = tools.compare_jobs(a, b)
        comparison = value['comparison']
        self.assertEqual(comparison['inputs']['differing'], ['settings.xml'])
        self.assertEqual(comparison['inputs']['only_in_b'], ['tallies.xml'])
        self.assertEqual(comparison['inputs']['only_in_a'], [])
        keff = comparison['keff_combined']
        self.assertAlmostEqual(keff['difference'], 1.15000 - 1.16262, places=6)
        self.assertAlmostEqual(keff['combined_std_dev'],
                               (0.00308 ** 2 + 0.00400 ** 2) ** 0.5, places=6)
        self.assertIn('工程参考', keff['note'])
        self.assertFalse(comparison['runtime']['threads']['same'])
        self.assertEqual(value['sides']['b']['threads'], 4)

    def test_fixed_source_side_yields_no_difference(self):
        fixed = '<settings><run_mode>fixed source</run_mode><particles>1000</particles></settings>'
        a = self.make_job('ee' * 16, log=BATCH + results_block(1.16262, 0.00308))
        b = self.make_job('ff' * 16, settings=fixed, log='batch 1 done\n')
        keff = tools.compare_jobs(a, b)['comparison']['keff_combined']
        self.assertIsNone(keff['b'])
        self.assertNotIn('difference', keff)
        self.assertIn('不作差值', keff['note'])

    def test_same_job_rejected(self):
        a = self.make_job('11' * 16, log=BATCH)
        with self.assertRaises(Error):
            tools.compare_jobs(a, a)

    def test_missing_job_rejected(self):
        a = self.make_job('22' * 16, log=BATCH)
        with self.assertRaises(Error):
            tools.compare_jobs(a, '33' * 16)

    def test_running_job_compares_without_keff(self):
        a = self.make_job('44' * 16, log=BATCH + results_block(1.16262, 0.00308))
        b = self.make_job('55' * 16, state='running', log=BATCH)
        value = tools.compare_jobs(a, b)
        self.assertEqual(value['sides']['b']['state'], 'running')
        self.assertIsNone(value['comparison']['keff_combined']['b'])
        self.assertFalse(value['comparison']['runtime']['state']['same'])


if __name__ == '__main__':
    unittest.main()
