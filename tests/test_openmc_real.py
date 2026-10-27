"""Opt-in tests use the real OpenMC runtime and synthetic MGXS, never private nuclear data."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME, '真实OpenMC测试未启用：设置MC_TEST_OPENMC_CONFIG为运行配置的绝对路径')
class RealOpenMC(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='real openmc interface ')
        self.root = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('MCP_', 'MC_', 'HARNESS_'))}
        self.env.update(MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()), MC_WORKSPACE=str(self.root / 'workspace'))

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, name, arguments, success=True):
        result = subprocess.run([sys.executable, str(ROOT / 'mcp/cli.py'), 'call', name, json.dumps(arguments)],
                                env=self.env, text=True, capture_output=True, timeout=180)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        return result.stderr

    def wait(self, job_id):
        for _ in range(100):
            status = self.invoke('openmc_job_status', {'job_id': job_id})
            if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):return status
            time.sleep(.1)
        self.fail('real OpenMC job did not finish')

    def save_evidence(self, name, value):
        target = ROOT / 'artifacts/runs/20261027-openmc-interfaces'
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def test_direct_demo_plot_and_changed_output_refusal(self):
        result = subprocess.run([sys.executable, str(ROOT / 'examples/openmc_demo.py')],
                                env=self.env, text=True, capture_output=True, timeout=180)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['job']['state'], 'completed')
        self.assertEqual(data['results']['provenance']['data_kind'], 'synthetic_mgxs')
        self.assertFalse(data['professional_acceptance'])
        self.assertIsInstance(data['results']['keff']['mean'], float)
        if data['results']['keff']['std_dev'] is None:
            self.assertTrue(data['results']['warnings'], 'missing uncertainty must be disclosed')
        self.assertTrue(data['results']['tallies'])
        limited = self.invoke('openmc_read_results', {'job_id': data['job']['job_id'], 'max_values': 1})
        self.assertEqual(len(limited['tallies'][0]['mean']), 1)
        self.assertTrue(limited['tallies'][0]['values_truncated'])
        self.assertIn('std_dev', limited['keff'])
        self.assertEqual(data['results']['openmc_version'], [0, 16, 0])
        workspace = Path(self.env['MC_WORKSPACE'])
        for report in data['reports']:
            self.assertTrue((workspace / report['path']).is_file())
        self.save_evidence('real-openmc-cli-test.json', data)
        case = workspace / data['case']['case_dir']
        (case / 'statepoint.999.h5').write_bytes(b'old statepoint')
        submitted = self.invoke('openmc_submit_job', {'case_dir': data['case']['case_dir'], 'mode': 'plot'})
        plotted = self.wait(submitted['job_id'])
        self.assertEqual(plotted['state'], 'completed', plotted)
        images = [name for name in plotted['files'] if name.endswith(('.png', '.ppm'))]
        self.assertTrue(images, plotted)
        self.assertNotIn('statepoint.999.h5', plotted['files'])
        self.assertIn('绘图作业', self.invoke('openmc_read_results', {'job_id': submitted['job_id']}, success=False))
        point = workspace / data['results']['statepoint']
        point.write_bytes(b'changed')
        self.assertIn('改变', self.invoke('openmc_read_results', {'job_id': data['job']['job_id']}, success=False))
        self.save_evidence('real-openmc-plot-test.json', plotted)

    def test_complete_demo_over_standard_mcp(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); port = listener.getsockname()[1]
        config = self.root / 'service.json'
        config.write_text(json.dumps({'host': '127.0.0.1', 'port': port, 'path': '/mcp',
                                     'name': 'real-openmc-test', 'version': '0.2.0', 'clientName': 'mc'}))
        self.env.update(MCP_CONFIG_FILE=str(config), MCP_CONNECTION_FILE=str(self.root / 'connection.json'))
        with (self.root / 'server.log').open('w+') as log:
            server = subprocess.Popen([sys.executable, str(ROOT / 'mcp/server.py')], env=self.env,
                                      stdout=log, stderr=log, cwd=ROOT / 'mcp')
            try:
                for _ in range(100):
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=.2):break
                    except OSError:
                        if server.poll() is not None:
                            log.seek(0); self.fail(log.read())
                        time.sleep(.05)
                result = subprocess.run([sys.executable, str(ROOT / 'examples/openmc_demo.py'), '--mcp-url', f'http://127.0.0.1:{port}/mcp'],
                                        env=self.env, text=True, capture_output=True, timeout=180)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data['job']['state'], 'completed')
                self.assertEqual(data['results']['provenance']['execution_kind'], 'openmc_runtime')
                self.assertFalse(data['results']['professional_acceptance'])
                self.save_evidence('real-openmc-mcp-test.json', data)
            finally:
                server.terminate()
                try:server.wait(timeout=10)
                except subprocess.TimeoutExpired:server.kill(); server.wait()


if __name__ == '__main__':unittest.main()
