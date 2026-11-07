"""k口径：k的来源可辨、非有限时回退、StatePoint上下文与统计分级。

功能：openmc_job_compare 的keff选择（StatePoint优先、日志fallback）、
      openmc_read_results 的 statepoint_context 与 statistical_adequacy
类别：回归（旧行为：compare只读solver.log的Combined行，日志里是 -nan 时
      两个“已完成”的作业被拒绝比较k；read_results不说明读到的是第几个StatePoint）
前置：无。离线：jobs层与StatePoint读取用mock替换；不跑OpenMC
输入：见各 test_ 方法
预期：keff带source；有StatePoint时差值可算且给出2σ工程判据；无k时如实说明；
      多StatePoint时标出序号并指向openmc_read_depletion；分级只按相对σ
不证明：不证明真实日志解析与真实StatePoint读取（分别由test_job_names/real用例覆盖）；
      不证明2σ判据属于统计显著性检验
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import openmc_tools as tools


def record(mode='run', files=None):
    return {'mode': mode, 'case_dir': 'case', 'threads': 2, 'runtime': {},
            'input_files': {'model.xml': 'hash'}, 'output_files': files or {}}


class KefSources(unittest.TestCase):
    def compare(self, keff_a, keff_b, progress_a=None, progress_b=None):
        directories = {'a': Path('/tmp/a'), 'b': Path('/tmp/b')}
        statuses = {name: {'state': 'completed', 'progress': progress or {}}
                    for name, progress in (('a', progress_a), ('b', progress_b))}
        records = {name: record() for name in ('a', 'b')}
        with patch.object(tools.jobs, 'status', side_effect=lambda job: statuses[job]), \
                patch.object(tools.jobs, 'read', side_effect=lambda directory: records[str(directory)[-1]]), \
                patch.object(tools.jobs, 'job_directory', side_effect=lambda job: directories[job]), \
                patch.object(tools, '_statepoint_keff', side_effect=lambda job: keff_a if job == 'a' else keff_b):
            return tools.compare_jobs('a', 'b')['comparison']['keff_combined']

    def test_statepoint_wins_over_missing_log_keff(self):
        value = self.compare({'mean': 1.11557, 'std_dev': 0.03066, 'source': 'statepoint:k_combined'},
                             {'mean': 1.03512, 'std_dev': 0.01223, 'source': 'statepoint:k_combined'})
        self.assertEqual(value['source'], {'a': 'statepoint:k_combined', 'b': 'statepoint:k_combined'})
        self.assertAlmostEqual(value['difference'], -0.08045, places=5)
        self.assertFalse(value['within_2sigma_engineering'])
        self.assertIn('StatePoint', value['note'])

    def test_falls_back_to_log_when_statepoint_missing(self):
        value = self.compare(None, {'mean': 1.0, 'std_dev': 0.0, 'source': 'statepoint:k_combined'},
                             progress_a={'keff_average': {'mean': 0.9, 'std_dev': 0.1}})
        self.assertEqual(value['source']['a'], 'log_average')
        self.assertAlmostEqual(value['difference'], 0.1, places=6)
        self.assertIn('StatePoint', value['note'])

    def test_missing_sigma_side_suppresses_two_sigma_verdict(self):
        value = self.compare(None, {'mean': 1.0, 'std_dev': 0.0, 'source': 'statepoint:k_combined'},
                             progress_a={'keff_average': {'mean': 0.9}})
        self.assertEqual(value['source']['a'], 'log_average')
        self.assertAlmostEqual(value['difference'], 0.1, places=6)
        self.assertNotIn('within_2sigma_engineering', value)
        self.assertIn('至少一方无标准差', value['note'])

    def test_no_usable_keff_is_reported_not_guessed(self):
        value = self.compare(None, None)
        self.assertIsNone(value['a'])
        self.assertIsNone(value.get('difference'))
        self.assertIn('无可用k', value['note'])


class StatepointContext(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc statepoint context ')
        self.directory = Path(self.temp.name)
        (self.directory / 'work').mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def touch(self, *names):
        for name in names:
            (self.directory / 'work' / name).write_bytes(b'')

    def test_depletion_last_step_is_labelled(self):
        self.touch('statepoint.10.h5', 'statepoint.20.h5')
        (self.directory / 'mc-case.json').write_text(json.dumps(
            {'depletion': {'timesteps': [1.0, 2.0]}}))
        point = self.directory / 'work' / 'statepoint.20.h5'
        value = tools._statepoint_context(self.directory, {'mode': 'deplete'}, point)
        self.assertEqual(value['statepoint_index'], 2)
        self.assertEqual(value['statepoints_present'], 2)
        self.assertTrue(value['depletion']['is_last_step'])
        self.assertIn('openmc_read_depletion', value['depletion']['note'])

    def test_single_statepoint_has_no_depletion_note(self):
        self.touch('statepoint.10.h5')
        point = self.directory / 'work' / 'statepoint.10.h5'
        value = tools._statepoint_context(self.directory, {'mode': 'run'}, point)
        self.assertEqual(value['statepoint_index'], 1)
        self.assertNotIn('depletion', value)
        self.assertNotIn('note', value)

    def test_multiple_statepoints_without_depletion_get_a_note(self):
        self.touch('statepoint.10.h5', 'statepoint.20.h5')
        point = self.directory / 'work' / 'statepoint.20.h5'
        value = tools._statepoint_context(self.directory, {'mode': 'run'}, point)
        self.assertIn('不是第一个', value['note'])


class StatisticalAdequacy(unittest.TestCase):
    def grade(self, keff):
        return tools.statistical_adequacy(keff, {'realizations': 8})['grade']

    def test_grades_follow_relative_sigma_only(self):
        self.assertEqual(self.grade({'mean': 1.0, 'std_dev': 0.02}), 'low_statistics_preview')
        self.assertEqual(self.grade({'mean': 1.0, 'std_dev': 0.005}), 'engineering_estimate')
        self.assertEqual(self.grade({'mean': 1.0, 'std_dev': 0.0005}), 'converged_indicator')

    def test_missing_sigma_is_unusable_not_passed(self):
        value = tools.statistical_adequacy({'mean': 1.0, 'std_dev': None}, {})
        self.assertEqual(value['grade'], 'unusable')
        self.assertIn('不能判断统计充分性', value['reason'])

    def test_fixed_source_has_no_adequacy_claim(self):
        self.assertEqual(tools.statistical_adequacy(None, {})['grade'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()