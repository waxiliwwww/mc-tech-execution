"""D2 离线测试：openmc_job_compare 输入指纹、状态与 Combined k 对比。日志样本按真实格式构造，无物理含义。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
import uuid
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

    # ---- D2 第二刀：tally 逐 bin 对比（patch read_results，不依赖引擎） ----

    def results_payload(self, job_id, tallies, warnings=()):
        return {'job_id': job_id, 'openmc_version': [0, 16, 0], 'run_mode': 'eigenvalue',
                'particles': 100, 'realizations': 15, 'keff': {'mean': 1.16, 'std_dev': 0.003},
                'tallies': tallies, 'warnings': list(warnings),
                'provenance': {'data_kind': 'mock_process_fixture',
                               'execution_kind': 'mock_process_fixture'},
                'statepoint': 'statepoint.20.h5', 'statepoint_sha256': 'x',
                'professional_acceptance': False}

    def tally(self, tally_id, mean, std, scores=('flux',), nuclides=('total',),
              estimator='analog', filters=(), shape=None, truncated=False):
        return {'id': tally_id, 'name': f'tally-{tally_id}', 'scores': list(scores),
                'nuclides': list(nuclides), 'estimator': estimator,
                'filters': [{'type': kind, 'description': ''} for kind in filters],
                'shape': shape or [len(mean)], 'mean': mean, 'std_dev': std,
                'values_truncated': truncated, 'normalization': ''}

    def compare_with_tallies(self, tally_lists, warnings=((), ()), **kwargs):
        a = self.make_job(uuid.uuid4().hex)
        b = self.make_job(uuid.uuid4().hex)
        store = {a: self.results_payload(a, tally_lists[0], warnings[0]),
                 b: self.results_payload(b, tally_lists[1], warnings[1])}
        with patch.object(tools, 'read_results',
                          lambda job_id, max_values=1000: store[job_id]):
            return tools.compare_jobs(a, b, compare_tallies=True, **kwargs)['comparison']['tallies']

    def test_tally_off_by_default(self):
        a = self.make_job('aa' * 16, log=BATCH)
        b = self.make_job('bb' * 16, log=BATCH)
        self.assertNotIn('tallies', tools.compare_jobs(a, b)['comparison'])
        self.assertIn('compare_tallies=true可开启', tools.compare_jobs(a, b)['note'])

    def test_tally_within_and_outside_two_sigma(self):
        one = self.tally(1, [1.0, 2.0], [0.01, 0.01])
        two = self.tally(1, [1.005, 2.5], [0.01, 0.01])
        section = self.compare_with_tallies(([one], [two]))
        self.assertTrue(section['performed'])
        entry = section['by_id'][0]
        self.assertTrue(entry['aligned'])
        self.assertEqual(entry['bins_compared'], 2)
        self.assertEqual(entry['within_2sigma'], 1)
        self.assertEqual(entry['outside_2sigma'], 1)
        self.assertEqual(entry['problem_bins'][0]['index'], 1)
        self.assertAlmostEqual(entry['max_abs_diff'], 0.5)
        self.assertEqual(section['summary']['outside_2sigma'], 1)

    def test_tally_identical_values_all_within(self):
        one = self.tally(1, [1.0], [0.01])
        two = self.tally(1, [1.0], [0.01])
        entry = self.compare_with_tallies(([one], [two]))['by_id'][0]
        self.assertEqual(entry['bins_compared'], 1)
        self.assertEqual(entry['within_2sigma'], 1)
        self.assertEqual(entry['outside_2sigma'], 0)
        self.assertEqual(entry['problem_bins'], [])
        self.assertEqual(entry['max_abs_diff'], 0.0)

    def test_tally_zero_sigma_different_means_outside(self):
        one = self.tally(1, [1.0], [0.0])
        two = self.tally(1, [1.1], [0.0])
        entry = self.compare_with_tallies(([one], [two]))['by_id'][0]
        self.assertEqual(entry['outside_2sigma'], 1)
        self.assertEqual(entry['problem_bins'][0]['combined_std_dev'], 0.0)

    def test_tally_non_finite_not_zeroed(self):
        one = self.tally(1, [1.0, 2.0], [0.01, 0.01])
        two = self.tally(1, [1.0, None], [0.01, None])
        section = self.compare_with_tallies(
            ([one], [two]), warnings=((), ('统计量包含非有限值；保留为空，未替换为0；需检查统计条件。',)))
        entry = section['by_id'][0]
        self.assertEqual(entry['non_finite_bins'], 1)
        self.assertEqual(entry['bins_compared'], 1)
        self.assertTrue(entry['problem_bins'][0]['non_finite'])
        self.assertTrue(section['warnings'])
        self.assertIn('未替换为0', section['warnings'][0])

    def test_tally_structure_mismatch_not_compared(self):
        one = self.tally(1, [1.0, 2.0], [0.01, 0.01])
        two = self.tally(1, [1.0], [0.01])
        entry = self.compare_with_tallies(([one], [two]))['by_id'][0]
        self.assertFalse(entry['aligned'])
        self.assertEqual(entry['structure_mismatch'], ['shape'])
        self.assertNotIn('bins_compared', entry)
        self.assertIn('如实报告', entry['note'])

    def test_tally_scores_mismatch_listed(self):
        one = self.tally(1, [1.0], [0.01])
        two = self.tally(1, [1.0], [0.01], scores=('fission',))
        entry = self.compare_with_tallies(([one], [two]))['by_id'][0]
        self.assertFalse(entry['aligned'])
        self.assertEqual(entry['structure_mismatch'], ['scores'])

    def test_tally_only_in_one_side(self):
        section = self.compare_with_tallies(
            ([self.tally(1, [1.0], [0.01]), self.tally(2, [2.0], [0.01])],
             [self.tally(1, [1.0], [0.01]), self.tally(3, [3.0], [0.01])]))
        self.assertEqual(section['only_in_a'], [2])
        self.assertEqual(section['only_in_b'], [3])
        self.assertEqual([entry['id'] for entry in section['by_id']], [1])
        self.assertEqual(section['summary']['tallies'], 1)

    def test_tally_truncation_flagged(self):
        entry = self.compare_with_tallies(
            ([self.tally(1, [1.0], [0.01], truncated=True)],
             [self.tally(1, [1.0], [0.01])]))['by_id'][0]
        self.assertTrue(entry['truncated'])

    def test_tally_performed_false_when_statepoint_unreadable(self):
        a = self.make_job('aa' * 16)
        b = self.make_job('bb' * 16)

        def broken(job_id, max_values=1000):
            raise Error('作业没有可读取的StatePoint')

        with patch.object(tools, 'read_results', broken):
            section = tools.compare_jobs(a, b, compare_tallies=True)['comparison']['tallies']
        self.assertFalse(section['performed'])
        self.assertIn('StatePoint', section['reason'])
        self.assertIn('不猜测', section['note'])

    def test_different_complete_filter_identity_prevents_false_bin_alignment(self):
        first=self.tally(1,[1,2],[.1,.1],filters=('EnergyFilter',))
        second=self.tally(1,[1,2],[.1,.1],filters=('EnergyFilter',))
        first['filter_signatures']=['full-bounds-a'];second['filter_signatures']=['full-bounds-b']
        value=self.compare_with_tallies(([first],[second]))['by_id'][0]
        self.assertFalse(value['aligned']);self.assertIn('filters',value['structure_mismatch'])
        self.assertNotIn('bins_compared',value)
        second['filter_signatures']=first['filter_signatures']
        value=self.compare_with_tallies(([first],[second]))['by_id'][0]
        self.assertTrue(value['aligned']);self.assertEqual(value['bins_compared'],2)
        del second['filter_signatures']
        self.assertFalse(self.compare_with_tallies(([first],[second]))['by_id'][0]['aligned'])


if __name__ == '__main__':
    unittest.main()
