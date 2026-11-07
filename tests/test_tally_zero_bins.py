"""tally逐bin对比：零计数bin不参与2σ判决。

功能：openmc_job_compare(compare_tallies=true) 的 _tally_comparison 判决与计数
类别：边界 + 回归（旧行为把“一侧均值0且σ=0”直接代入2σ公式：σ_合成=另一侧的σ，
      任何非零差异都成了“多倍σ”，把抽样噪声写成超差）
前置：无。离线：用 mock 替换 read_results，喂合成 tally 数据；不跑OpenMC
输入：见各 test_ 方法（两作业同一tally的 mean/std_dev 组合）
预期：零计数bin记为 zero_count_bins 并从 bins_compared / outside_2sigma 中排除；
      真正的超差bin仍记 outside_2sigma=1 且问题bin标 within_2sigma=False
不证明：不证明两倍σ判据是统计显著性检验（工具自身只声明为工程参考）；不证明真实StatePoint读取路径
"""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import openmc_tools as tools


def tally(identifier=1, mean=None, std=None, scores=('flux',)):
    return {'id': identifier, 'name': 't' + str(identifier), 'scores': list(scores),
            'nuclides': [], 'estimator': 'analog', 'filters': [], 'shape': [len(mean)],
            'mean': mean, 'std_dev': std, 'values_truncated': False,
            'total_values': len(mean), 'returned_values': len(mean)}


def results(entry, warnings=()):
    return {'keff': {'mean': 1.0, 'std_dev': 0.01}, 'tallies': [entry],
            'warnings': list(warnings), 'max_values_per_tally': 1000}


class ZeroCountBins(unittest.TestCase):
    def compare(self, first, second):
        with patch.object(tools, 'read_results', side_effect=[results(first), results(second)]):
            return tools._tally_comparison('a', 'b', 1000)

    def test_zero_count_bin_excluded_from_two_sigma_verdict(self):
        value = self.compare(tally(mean=[0.0, 1.0], std=[0.0, 0.1]),
                             tally(mean=[3.0, 1.05], std=[0.5, 0.1]))
        entry = value['by_id'][0]
        self.assertEqual(entry['zero_count_bins'], 1)
        self.assertEqual(entry['bins_compared'], 1)
        self.assertEqual(entry['outside_2sigma'], 0)
        self.assertEqual(entry['within_2sigma'], 1)
        zero = next(b for b in entry['problem_bins'] if b.get('zero_count'))
        self.assertEqual(zero['index'], 0)
        self.assertNotIn('within_2sigma', zero)
        self.assertEqual(value['summary']['zero_count_bins'], 1)

    def test_real_outlier_still_reported(self):
        value = self.compare(tally(mean=[0.0, 1.0], std=[0.0, 0.1]),
                             tally(mean=[3.0, 3.0], std=[0.5, 0.1]))
        entry = value['by_id'][0]
        self.assertEqual(entry['zero_count_bins'], 1)
        self.assertEqual(entry['outside_2sigma'], 1)
        problem = next(b for b in entry['problem_bins'] if not b.get('zero_count'))
        self.assertEqual(problem['index'], 1)
        self.assertFalse(problem['within_2sigma'])
        self.assertEqual(value['summary']['outside_2sigma'], 1)

    def test_both_sides_zero_is_also_zero_count(self):
        value = self.compare(tally(mean=[0.0], std=[0.0]), tally(mean=[0.0], std=[0.0]))
        entry = value['by_id'][0]
        self.assertEqual(entry['zero_count_bins'], 1)
        self.assertEqual(entry['bins_compared'], 0)
        self.assertEqual(entry['outside_2sigma'], 0)


if __name__ == '__main__':
    unittest.main()