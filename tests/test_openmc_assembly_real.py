"""真实门控：A1 组件/栅格模板——生成→openmc -g 几何调试无报错→17×17短实跑出keff。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')
# 与基准值比较属专业判定，不在本测试：只做合理性下限与数值有限性检查。
PLAUSIBLE_MIN = 0.5


@unittest.skipUnless(RUNTIME, '设置MC_TEST_OPENMC_CONFIG启用真实组件模板测试')
class AssemblyTemplatesReal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='mc assembly ')
        self.work = Path(self.tmp.name)
        config = json.loads(Path(RUNTIME).read_text())
        self.openmc = config['openmc_command'][0]
        key = config.get('nuclear_data_library', 'endfb-vii.1')
        index = ROOT / '.runtime/nuclear-data' / key / 'cross_sections.xml'
        self.env = {**os.environ, 'MC_OPENMC_CONFIG': RUNTIME, 'MC_WORKSPACE': str(self.work),
                    'OPENMC_CROSS_SECTIONS': str(index)}

    def tearDown(self):
        self.tmp.cleanup()

    def call(self, name, arguments, timeout=600):
        done = subprocess.run([sys.executable, str(ROOT / 'mcp/cli.py'), 'call', name,
                               json.dumps(arguments)],
                              capture_output=True, text=True, timeout=timeout, env=self.env)
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        return json.loads(done.stdout)

    def geometry_debug(self, case_dir):
        done = subprocess.run([self.openmc, '-g'], cwd=self.work / case_dir,
                              capture_output=True, text=True, timeout=900, env=self.env)
        return done.returncode, done.stdout + done.stderr

    def test_templates_generate_and_pass_geometry_debug(self):
        created = self.call('openmc_create_model', {'case_dir': 'assembly-17x17',
                                                    'template': 'assembly-17x17',
                                                    'particles': 1000, 'batches': 6, 'inactive': 2})
        self.assertEqual(created['data_kind'], 'public_benchmark_example')
        self.assertIn('RectLattice', [item['type'] for item in created.get('lattices') or []])
        self.assertEqual(created['benchmark']['name'], 'BEAVRS 17x17 assembly')
        code, log = self.geometry_debug('assembly-17x17')
        self.assertEqual(code, 0, log[-1500:])
        self.assertNotIn('lost particles', log.lower())
        for template in ('control-rod', 'hex-lattice'):
            created = self.call('openmc_create_model', {'case_dir': template, 'template': template,
                                                        'particles': 1000, 'batches': 6, 'inactive': 2})
            self.assertEqual(created['data_kind'], 'public_parameters_self_test')
            code, log = self.geometry_debug(template)
            self.assertEqual(code, 0, template + log[-1200:])
            self.assertNotIn('lost particles', log.lower())
        core = self.call('openmc_create_model', {'case_dir': 'core-lattice', 'template': 'core-lattice',
                                                 'particles': 1000, 'batches': 6, 'inactive': 2})
        self.assertEqual(len(core.get('lattices') or []), 4)
        self.assertEqual(core['data_kind'], 'public_benchmark_example')

    def test_assembly_short_run_produces_finite_keff(self):
        self.call('openmc_create_model', {'case_dir': 'asm-run', 'template': 'assembly-17x17',
                                          'particles': 2000, 'batches': 20, 'inactive': 5})
        checked = self.call('openmc_validate_inputs', {'case_dir': 'asm-run', 'api_check': True})
        self.assertTrue(checked['structure_valid'], checked)
        self.assertTrue(checked['openmc_api']['api_parsed'], checked)
        job = self.call('openmc_submit_job', {'case_dir': 'asm-run'})
        deadline = time.monotonic() + 600
        while True:
            status = self.call('openmc_job_status', {'job_id': job['job_id']})
            if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
                break
            self.assertLess(time.monotonic(), deadline)
            time.sleep(1)
        self.assertEqual(status['state'], 'completed', status)
        result = self.call('openmc_read_results', {'job_id': job['job_id'], 'max_values': 10})
        keff = result['keff']
        self.assertTrue(keff and keff['mean'] is not None, result)
        self.assertGreater(keff['mean'], PLAUSIBLE_MIN)
        self.assertTrue(keff['std_dev'] is not None and keff['std_dev'] >= 0)


if __name__ == '__main__':
    unittest.main()
