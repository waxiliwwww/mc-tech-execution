"""真实门控：A3 自定义tally（MeshFilter+flux）定义→校验→运行→读取全链。"""
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


@unittest.skipUnless(RUNTIME, '设置MC_TEST_OPENMC_CONFIG启用真实tally测试')
class MeshTallyReal(unittest.TestCase):
    def test_mesh_tally_define_run_read(self):
        with tempfile.TemporaryDirectory(prefix='mc tally ') as tmp:
            work = Path(tmp)
            env = {**os.environ, 'MC_OPENMC_CONFIG': RUNTIME, 'MC_WORKSPACE': str(work)}

            def call(name, arguments, timeout=240):
                done = subprocess.run([sys.executable, str(ROOT / 'mcp/cli.py'), 'call', name,
                                       json.dumps(arguments)],
                                      capture_output=True, text=True, timeout=timeout, env=env)
                self.assertEqual(done.returncode, 0, done.stderr[-800:])
                return json.loads(done.stdout)

            call('openmc_create_model', {'case_dir': 'tally-case', 'template': 'synthetic-mgxs',
                                         'particles': 2000, 'batches': 6, 'inactive': 2})
            defined = call('openmc_define_tally', {'case_dir': 'tally-case', 'scores': ['flux'],
                                                   'name': '网格通量',
                                                   'filters': [{'type': 'mesh', 'dimension': [2, 2, 1],
                                                                'lower_left': [-10, -10, -10],
                                                                'upper_right': [10, 10, 10]}]})
            self.assertGreaterEqual(defined['tally_count'], 2)
            self.assertEqual(defined['filters'][0]['type'], 'MeshFilter')
            checked = call('openmc_validate_inputs', {'case_dir': 'tally-case', 'api_check': True})
            self.assertTrue(checked['structure_valid'], checked)
            self.assertTrue(checked['openmc_api']['api_parsed'], checked)
            job = call('openmc_submit_job', {'case_dir': 'tally-case'})
            deadline = time.monotonic() + 240
            while True:
                status = call('openmc_job_status', {'job_id': job['job_id']})
                if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
                    break
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.2)
            self.assertEqual(status['state'], 'completed', status)
            result = call('openmc_read_results', {'job_id': job['job_id']})
            target = next(t for t in result['tallies'] if t['id'] == defined['tally_id'])
            # OpenMC 把规则网格的(i,j,k)压成一个轴：2×2×1=4个分箱，shape为[4,1,1]。
            self.assertEqual(target['shape'], [4, 1, 1])
            self.assertEqual(target['scores'], ['flux'])
            self.assertEqual(len(target['mean']), 4)
            self.assertEqual(len(target['std_dev']), 4)
            self.assertTrue(all(v is not None and v >= 0 for v in target['mean']), target['mean'])
            self.assertEqual(target['filters'][0]['type'], 'MeshFilter')
            self.assertTrue(target['filters'][0]['description'])
            # 网格分箱序号→(i,j,k)坐标的显式映射仍在D3/F系列待补；当前扁平序号顺序为OpenMC默认。
            self.assertGreaterEqual(len(result['tallies']), 2)  # 原默认tally保留


if __name__ == '__main__':
    unittest.main()
