"""真实门控：D3 标准图型——网格分布热图（2D/3D层/错误分支）与k收敛±σ带。"""
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


@unittest.skipUnless(RUNTIME, '设置MC_TEST_OPENMC_CONFIG启用真实图型测试')
class ChartsReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='mc charts ')
        cls.work = Path(cls.tmp.name)
        cls.env = {**os.environ, 'MC_OPENMC_CONFIG': RUNTIME, 'MC_WORKSPACE': str(cls.work)}
        cls.call('openmc_create_model', {'case_dir': 'charts', 'template': 'synthetic-mgxs',
                                         'particles': 2000, 'batches': 8, 'inactive': 2})
        two_d = cls.call('openmc_define_tally', {'case_dir': 'charts', 'scores': ['flux'], 'name': '2d mesh',
                         'filters': [{'type': 'mesh', 'dimension': [2, 2, 1], 'lower_left': [-10, -10, -10],
                                      'upper_right': [10, 10, 10]}]})
        three_d = cls.call('openmc_define_tally', {'case_dir': 'charts', 'scores': ['flux'], 'name': '3d mesh',
                           'filters': [{'type': 'mesh', 'dimension': [2, 2, 2], 'lower_left': [-10, -10, -10],
                                        'upper_right': [10, 10, 10]}]})
        cls.two_d, cls.three_d = two_d['tally_id'], three_d['tally_id']
        cls.job = cls.call('openmc_submit_job', {'case_dir': 'charts'})['job_id']
        status = None
        for _ in range(300):
            status = cls.call('openmc_job_status', {'job_id': cls.job})
            if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
                break
            time.sleep(0.2)
        assert status['state'] == 'completed', status

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def call(name, arguments, expect_ok=True):
        done = subprocess.run([sys.executable, str(ROOT / 'mcp/cli.py'), 'call', name, json.dumps(arguments)],
                              capture_output=True, text=True, timeout=600,
                              env={**os.environ, 'MC_OPENMC_CONFIG': RUNTIME,
                                   'MC_WORKSPACE': str(ChartsReal.work)})
        if expect_ok:
            assert done.returncode == 0, done.stderr[-800:]
            return json.loads(done.stdout)
        assert done.returncode != 0, '预期失败但成功：' + done.stdout[-500:]
        return json.loads(done.stdout) if done.stdout.strip() else {'error': done.stderr}

    def test_mesh_plot_writes_png_json_csv(self):
        chart = self.call('openmc_plot_mesh', {'job_id': self.job, 'tally_id': self.two_d})
        self.assertEqual(chart['cells'], 4)
        self.assertEqual(chart['mesh']['dimension'], [2, 2, 1])
        for key in ('path', 'values_json', 'values_csv'):
            self.assertTrue(Path(chart['server_path'] if key == 'path'
                                 else str(self.work / chart[key])).is_file(), key)
        self.assertGreater(Path(chart['server_path']).stat().st_size, 1000)
        csv_lines = (self.work / chart['values_csv']).read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(csv_lines), 5)  # 表头+4格
        self.assertTrue(csv_lines[0].startswith('i,j,x_center,y_center'))
        payload = json.loads((self.work / chart['values_json']).read_text(encoding='utf-8'))
        self.assertEqual(payload['dimension'], [2, 2, 1])
        self.assertEqual(len(payload['values']), 2)
        self.assertEqual(len(payload['values'][0]), 2)
        self.assertFalse(payload['professional_acceptance'])

    def test_mesh_3d_slice_requires_level(self):
        error = self.call('openmc_plot_mesh', {'job_id': self.job, 'tally_id': self.three_d}, False)
        self.assertIn('level', str(error))
        chart = self.call('openmc_plot_mesh', {'job_id': self.job, 'tally_id': self.three_d, 'level': 1})
        self.assertEqual(chart['mesh']['level'], 1)
        self.assertEqual(chart['cells'], 4)
        self.assertTrue(Path(chart['server_path']).is_file())
        error = self.call('openmc_plot_mesh', {'job_id': self.job, 'tally_id': self.three_d, 'level': 5}, False)
        self.assertIn('level', str(error))

    def test_convergence_band(self):
        chart = self.call('openmc_plot_convergence', {'job_id': self.job})
        self.assertEqual(chart['batches'], 6)  # 8批-2非活跃
        self.assertAlmostEqual(chart['combined']['mean'], 1.6666666666666667, places=6)
        for key in ('path', 'series_json', 'series_csv'):
            self.assertTrue(Path(chart['server_path'] if key == 'path'
                                 else str(self.work / chart[key])).is_file(), key)
        rows = (self.work / chart['series_csv']).read_text(encoding='utf-8').strip().splitlines()
        self.assertEqual(len(rows), 7)  # 表头+6个活跃批
        self.assertEqual(rows[0].split(',')[0], 'batch')
        plain = self.call('openmc_plot_convergence', {'job_id': self.job, 'show_band': False})
        self.assertEqual(plain['batches'], 6)

    def test_invalid_arguments_and_unknown_job(self):
        self.assertIn('energy_bin', str(self.call('openmc_plot_mesh',
                    {'job_id': self.job, 'tally_id': self.two_d, 'energy_bin': -1}, False)))
        self.assertIn('level', str(self.call('openmc_plot_mesh',
                    {'job_id': self.job, 'tally_id': self.two_d, 'level': -2}, False)))
        error = self.call('openmc_plot_convergence', {'job_id': '4f'.ljust(32, '0')}, False)
        self.assertIn('error', str(error))


    def test_view_geometry_renders_slice_png(self):
        self.call('openmc_create_model', {'case_dir': 'look', 'template': 'synthetic-mgxs',
                                          'particles': 200, 'batches': 4, 'inactive': 1})
        value = self.call('openmc_view_geometry', {'case_dir': 'look', 'basis': 'xy', 'pixels': 200})
        image = Path(value['server_path'])
        self.assertTrue(image.is_file())
        self.assertGreater(image.stat().st_size, 400)
        self.assertEqual(image.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(value['basis'], 'xy')
        self.assertEqual(value['pixels'], 200)
        self.assertGreater(len(value['width']), 0)
        self.assertFalse(value['professional_acceptance'])


if __name__ == '__main__':
    unittest.main()
