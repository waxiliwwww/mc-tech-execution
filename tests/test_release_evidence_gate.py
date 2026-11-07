"""发布门禁回归：关键真实用例 skip、缺失或 FAILED 时必须拒绝放行。

功能：`scripts/check-release-evidence.py` 的日志判定。
类别：反例/治理（旧问题：`OK (skipped=...)` 被当成全通过）。
前置：无。只用构造的 unittest 日志，不运行真实 OpenMC。
输入：四条日志：全部执行、关键用例 skip、关键用例缺失、包含 FAILED。
预期：只有全部执行通过；其余三种都返回具体错误。
不证明：不证明真实环境可用、测试本身正确或专业验收。
"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = spec_from_file_location('release_evidence', ROOT / 'scripts/check-release-evidence.py')
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REQUIRED = ['test_accuracy_baseline.AccuracyReal']


class ReleaseEvidenceGate(unittest.TestCase):
    def test_all_required_tests_ran_and_passed(self):
        log = ('test_real (test_accuracy_baseline.AccuracyReal.test_real) ... ok\n'
               '----------------------------------------------------------------------\n'
               'OK\n')
        self.assertEqual(MODULE.evaluate(log, REQUIRED), [])

    def test_skipped_critical_test_is_a_failure(self):
        log = ('test_real (test_accuracy_baseline.AccuracyReal.test_real) ... '
               'skipped \'MC_TEST_OPENMC_CONFIG未设置\'\nOK (skipped=1)\n')
        errors = MODULE.evaluate(log, REQUIRED)
        self.assertEqual(len(errors), 1)
        self.assertIn('跳过', errors[0])

    def test_missing_critical_test_is_a_failure(self):
        errors = MODULE.evaluate('OK\n', REQUIRED)
        self.assertEqual(len(errors), 1)
        self.assertIn('未执行', errors[0])

    def test_failed_log_is_a_failure(self):
        log = ('test_real (test_accuracy_baseline.AccuracyReal.test_real) ... FAIL\n'
               'FAILED (failures=1)\n')
        errors = MODULE.evaluate(log, REQUIRED)
        self.assertTrue(any('FAILED' in error for error in errors))


if __name__ == '__main__':
    unittest.main()