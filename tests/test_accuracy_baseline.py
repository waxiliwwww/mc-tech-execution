#!/usr/bin/env python3
"""D6 准确率比对harness测试：离线判定逻辑+真实门控（解析基准全链）。"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import accuracy_baseline as harness  # noqa: E402

RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


class Judge(unittest.TestCase):
    def test_within_sigma_passes(self):
        value = harness.judge('keff', {'mean': 1.001, 'std_dev': 0.002},
                              {'value': 1.0}, {'sigma': 3.0})
        self.assertEqual(value['verdict'], 'PASS')
        self.assertTrue(all(item['inside'] for item in value['criteria']))
        self.assertAlmostEqual(value['deviation'], 0.001)

    def test_outside_sigma_fails(self):
        value = harness.judge('keff', {'mean': 1.05, 'std_dev': 0.001},
                              {'value': 1.0}, {'sigma': 3.0})
        self.assertEqual(value['verdict'], 'FAIL')
        self.assertFalse(value['criteria'][0]['inside'])

    def test_relative_tolerance_enforced(self):
        ok = harness.judge('keff', {'mean': 1.01, 'std_dev': 0.001},
                           {'value': 1.0}, {'relative': 0.02})
        bad = harness.judge('keff', {'mean': 1.05, 'std_dev': 0.001},
                            {'value': 1.0}, {'relative': 0.02})
        self.assertEqual(ok['verdict'], 'PASS')
        self.assertEqual(bad['verdict'], 'FAIL')

    def test_reference_sigma_combined(self):
        value = harness.judge('keff', {'mean': 1.005, 'std_dev': 0.001},
                              {'value': 1.0, 'sigma': 0.001}, {'sigma': 3.0})
        self.assertAlmostEqual(value['sigma_combined'], math.hypot(0.001, 0.001))

    def test_missing_keff_inconclusive(self):
        value = harness.judge('keff', {'mean': None, 'std_dev': None}, {'value': 1.0}, {'sigma': 3.0})
        self.assertEqual(value['verdict'], 'INCONCLUSIVE')

    def test_non_finite_keff_inconclusive(self):
        for run in ({'mean': math.nan, 'std_dev': 0.001}, {'mean': 1.0, 'std_dev': -1.0}):
            self.assertEqual(harness.judge('keff', run, {'value': 1.0}, {'sigma': 3.0})['verdict'],
                             'INCONCLUSIVE')

    def test_no_tolerance_inconclusive(self):
        value = harness.judge('keff', {'mean': 1.0, 'std_dev': 0.001}, {'value': 1.0}, {})
        self.assertEqual(value['verdict'], 'INCONCLUSIVE')
        self.assertIn('容差未定义', value['reason'])

    def test_zero_sigma_requires_exact(self):
        same = harness.judge('keff', {'mean': 1.0, 'std_dev': 0.0}, {'value': 1.0}, {'sigma': 3.0})
        diff = harness.judge('keff', {'mean': 1.001, 'std_dev': 0.0}, {'value': 1.0}, {'sigma': 3.0})
        self.assertEqual(same['verdict'], 'PASS')
        self.assertEqual(diff['verdict'], 'FAIL')


class References(unittest.TestCase):
    def test_constant_null_is_refused(self):
        with self.assertRaises(harness.HarnessError):
            harness.resolve_reference(sys.executable, {'type': 'constant', 'value': None,
                                                       'source': '待取证'}, Path('/tmp'))

    def test_constant_valid(self):
        value = harness.resolve_reference(sys.executable,
                                          {'type': 'constant', 'value': 1.25, 'source': 'x'},
                                          Path('/tmp'))
        self.assertEqual(value['value'], 1.25)

    def test_constant_rejects_bool_and_nan(self):
        for bad in (True, float('nan'), '1.0'):
            with self.assertRaises(harness.HarnessError):
                harness.resolve_reference(sys.executable, {'type': 'constant', 'value': bad,
                                                           'source': 'x'}, Path('/tmp'))

    def test_analytic_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(harness.HarnessError):
                harness.resolve_reference(sys.executable,
                                          {'type': 'analytic_infinite_medium', 'xs_file': 'nope.h5'},
                                          Path(tmp))

    def test_unknown_type(self):
        with self.assertRaises(harness.HarnessError):
            harness.resolve_reference(sys.executable, {'type': 'mystery'}, Path('/tmp'))


class Reporting(unittest.TestCase):
    def test_markdown_renders_verdicts(self):
        text = harness.render_markdown([
            {'name': 'a', 'verdict': 'PASS', 'keff': {'mean': 1.0, 'std_dev': 0.001},
             'reference': {'value': 1.0}, 'relative_deviation': 0.0,
             'criteria': [{'criterion': '|Δk| ≤ 3σ', 'inside': True}]},
            {'name': 'b', 'verdict': 'INCONCLUSIVE', 'reason': '参考值未录入'}])
        self.assertIn('| a | PASS |', text)
        self.assertIn('| b | INCONCLUSIVE |', text)
        self.assertIn('参考值未录入', text)

    def test_manifest_shipped_is_valid(self):
        manifest = json.loads((ROOT / 'tests/fixtures/accuracy/manifest.json').read_text())
        names = [item['name'] for item in manifest['benchmarks']]
        self.assertIn('synthetic-mgxs-infinite-medium', names)
        self.assertIn('c5g7-pincell-multigroup', names)
        for item in manifest['benchmarks']:
            for key in ('name', 'template', 'case_dir', 'run', 'reference', 'tolerance'):
                self.assertIn(key, item)
        c5g7 = next(item for item in manifest['benchmarks'] if item['name'] == 'c5g7-pincell-multigroup')
        self.assertIsNone(c5g7['reference']['value'])
        self.assertTrue(c5g7['reference']['source'])


@unittest.skipUnless(RUNTIME, '设置MC_TEST_OPENMC_CONFIG启用真实准确率基线测试')
class AccuracyReal(unittest.TestCase):
    def test_synthetic_analytic_benchmark_passes(self):
        with tempfile.TemporaryDirectory(prefix='mc accuracy ') as tmp:
            work = Path(tmp)
            manifest = json.loads((ROOT / 'tests/fixtures/accuracy/manifest.json').read_text())
            entry = next(item for item in manifest['benchmarks']
                         if item['name'] == 'synthetic-mgxs-infinite-medium')
            entry['run'] = {'particles': 20000, 'batches': 20, 'inactive': 5}
            entry['case_dir'] = 'accuracy/synthetic-mgxs'
            manifest['benchmarks'] = [entry]
            manifest_path = work / 'manifest.json'
            manifest_path.write_text(json.dumps(manifest))
            env = {**os.environ, 'MC_OPENMC_CONFIG': RUNTIME, 'MC_WORKSPACE': str(work / 'ws')}
            run = subprocess.run(
                [sys.executable, str(ROOT / 'scripts/accuracy_baseline.py'),
                 '--manifest', str(manifest_path), '--workspace', str(work / 'ws'),
                 '--report', str(work / 'report.json')],
                capture_output=True, text=True, timeout=900, env=env)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            item = json.loads((work / 'report.json').read_text())['benchmarks'][0]
            self.assertEqual(item['verdict'], 'PASS', item)
            self.assertLess(abs(item['deviation_sigma']), 3.0)
            xs = item['reference']['xs']
            self.assertAlmostEqual(item['reference']['value'], xs['nu_fission'] / xs['absorption'],
                                   places=9)
            self.assertIn('cross_sections_sha256', item['nuclear_data_registry'])
            self.assertEqual(item['precheck']['ok'], True)


if __name__ == '__main__':
    unittest.main()