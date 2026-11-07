"""D2 回归：相对σ小但源批次不足时，统计分级必须降为预览。

功能：`openmc_read_results.statistical_adequacy` 对批次设置的源收敛提示。
类别：回归（旧行为：只看相对σ，200×10/inactive 2 被判 engineering_estimate，
      与 4000×80/inactive 20 结果差 21σ 仍不提示）。
前置：无。直接调用分级函数，使用构造的批次设置；不执行输运。
输入：同一 k 相对σ下，低活跃批次与充足批次两组 progress。
预期：低批次组 grade 降为 low_statistics_preview 且保留 downgraded_from；
      充足组不降级；无批次设置时明确 not_available，不编造收敛结论。
不证明：不证明 entropy 源收敛、真实物理偏差、跨模型阈值或专业验收。
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import openmc_tools as tools


class SourceConvergenceAdvisory(unittest.TestCase):
    def test_low_active_batches_downgrades_sigma_only_grade(self):
        keff = {'mean': 1.358892, 'std_dev': 0.011699}
        low = tools.statistical_adequacy(
            keff, {'realizations': 8},
            {'run_mode': 'eigenvalue', 'batches_total': 10, 'inactive': 2})
        self.assertEqual(low['grade'], 'low_statistics_preview')
        self.assertEqual(low['downgraded_from'], 'engineering_estimate')
        self.assertEqual(low['source_convergence']['active_batches'], 8)
        self.assertIn('源未收敛', low['reason'])

    def test_enough_active_batches_keep_sigma_grade(self):
        keff = {'mean': 1.324349, 'std_dev': 0.001500}
        enough = tools.statistical_adequacy(
            keff, {'realizations': 60},
            {'run_mode': 'eigenvalue', 'batches_total': 80, 'inactive': 20})
        self.assertEqual(enough['grade'], 'engineering_estimate')
        self.assertNotIn('downgraded_from', enough)
        self.assertEqual(enough['source_convergence']['status'], 'heuristic_ok')

    def test_missing_batches_is_not_a_convergence_claim(self):
        value = tools.statistical_adequacy(
            {'mean': 1.0, 'std_dev': 0.0005}, {},
            {'run_mode': 'eigenvalue'})
        self.assertEqual(value['source_convergence']['status'], 'not_available')
        self.assertEqual(value['grade'], 'converged_indicator')


if __name__ == '__main__':
    unittest.main()