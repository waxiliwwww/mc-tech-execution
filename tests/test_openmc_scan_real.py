"""User flows with the real solver. Synthetic MGXS proves engineering, not reactor accuracy."""
import asyncio
import base64
import csv
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(RUNTIME, '设置MC_TEST_OPENMC_CONFIG启用真实扫描、图表和报告测试')
class RealScanReports(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='mc real scan ')
        cls.work = Path(cls.temp.name)
        cls.env = {k: v for k, v in os.environ.items() if not k.startswith(('MC_', 'MCP_', 'HARNESS_'))}
        cls.runtime = json.loads(Path(RUNTIME).read_text())
        cls.runtime['job_timeout_seconds'] = 20
        cls.configuration = cls.work / 'runtime.json'
        cls.configuration.write_text(json.dumps(cls.runtime))
        cls.workspace = cls.work / 'workspace'
        cls.env.update(MC_WORKSPACE=str(cls.workspace), MC_OPENMC_CONFIG=str(cls.configuration))
        cls.call('openmc_create_model', {'case_dir': 'base'})
        parameters = cls.call('openmc_list_parameters', {'case_dir': 'base'})['parameters']
        cls.density = next(p['name'] for p in parameters if p['name'].endswith('.density'))
        cls.surface = next(p['name'] for p in parameters if p['name'].endswith('.x0') and p['current'] == -10)
        cls.scan = cls.call('openmc_submit_scan', {'case_dir': 'base', 'axes': {cls.density: [.8, 1.2], 'settings.seed': [1, 2]}})['scan_id']
        cls.completed = cls.wait(cls.scan)
        if cls.completed['state'] != 'completed':raise AssertionError(cls.completed)
        cls.job = cls.completed['cases'][0]['job_id']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def call(cls, name, arguments, success=True, env=None):
        result = subprocess.run([sys.executable, str(ROOT / 'mcp/cli.py'), 'call', name, json.dumps(arguments)],
                                env=env or cls.env, capture_output=True, text=True, timeout=180)
        if success and result.returncode:raise AssertionError(result.stderr)
        if not success:
            if not result.returncode:raise AssertionError('Expected tool failure: ' + name)
            return result.stderr
        return json.loads(result.stdout)

    @classmethod
    def wait(cls, identifier, kind='scan'):
        deadline = time.monotonic() + 100
        while time.monotonic() < deadline:
            result = cls.call('openmc_' + kind + '_status', {kind + '_id': identifier})
            if result['state'] in ('completed', 'partial', 'failed', 'cancelled', 'interrupted'):return result
            time.sleep(.1)
        raise AssertionError('Timeout waiting for ' + kind)

    def evidence(self, name, value):
        target = ROOT / 'artifacts/runs/20261027-scan-report'
        target.mkdir(parents=True, exist_ok=True)
        (target / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')

    def test_cartesian_scan_preserves_inputs_and_units(self):
        self.assertEqual(len(self.completed['cases']), 4)
        expected = {(d, s) for d in [.8, 1.2] for s in [1, 2]}
        self.assertEqual({(r['parameters'][self.density], r['parameters']['settings.seed']) for r in self.completed['cases']}, expected)
        original = ET.parse(self.workspace / 'base/materials.xml').getroot()
        self.assertEqual(original.find('material/density').attrib, {'value': '1.0', 'units': 'macro'})
        for row in self.completed['cases']:
            folder = self.workspace / '.mc-jobs' / row['job_id'] / 'work'
            material = ET.parse(folder / 'materials.xml').getroot().find('material')
            self.assertIsNotNone(material.find('macroscopic'), 'OpenMC XML roundtrip must not erase macro data')
            self.assertEqual(float(material.find('density').get('value')), row['parameters'][self.density])
            self.assertEqual(material.find('density').get('units'), 'macro')
            self.assertFalse(row.get('warnings') == [] and row['keff']['std_dev'] is None)
        self.evidence('real-cartesian-scan.json', self.completed)

    def test_prepare_case_preserves_unmodified_xml_and_original(self):
        prepared = self.call('openmc_prepare_case', {'case_dir': 'base', 'target_dir': 'modified', 'parameters': {self.density: .6, self.surface: -12}})
        diff = prepared['parameter_diff']
        self.assertTrue(diff['verified'], diff)
        by_name = {c['name']: c for c in diff['changes']}
        self.assertEqual(by_name[self.density]['before'], 1.0)
        self.assertEqual(by_name[self.density]['after'], .6)
        self.assertEqual(by_name[self.surface]['before'], -10)
        self.assertEqual(by_name[self.surface]['after'], -12)
        self.assertTrue(all(item['match'] for item in diff['readback']))
        material = ET.parse(self.workspace / 'modified/materials.xml').getroot().find('material')
        self.assertIsNotNone(material.find('macroscopic'))
        self.assertEqual(float(material.find('density').get('value')), .6)
        self.assertEqual(ET.parse(self.workspace / 'modified/geometry.xml').getroot().find("surface[@id='1']").get('coeffs'), '-12')
        self.assertEqual(ET.parse(self.workspace / 'base/geometry.xml').getroot().find("surface[@id='1']").get('coeffs'), '-10')
        self.assertIn('不能覆盖', self.call('openmc_prepare_case', {'case_dir': 'base', 'target_dir': 'modified', 'parameters': {self.density: 1}}, False))

    def test_invalid_combinations_rejected_before_submission(self):
        before = set((self.workspace / '.mc-scans').iterdir())
        for axes in ({'not.a.public.field': [1]}, {'settings.batches': [2, 10]}, {self.density: [1, 1.0]}, {'settings.seed': list(range(101))}):
            self.call('openmc_submit_scan', {'case_dir': 'base', 'axes': axes}, False)
        self.assertEqual(set((self.workspace / '.mc-scans').iterdir()), before)

    def test_complete_series_uses_selected_score_not_truncated_preview(self):
        preview = self.call('openmc_read_results', {'job_id': self.job, 'max_values': 1})
        self.assertTrue(preview['tallies'][0]['values_truncated'])
        chart = self.call('openmc_plot_results', {'job_id': self.job, 'score': 'fission', 'style': 'bar', 'format': 'svg'})
        series = json.loads((self.workspace / chart['series_json']).read_text())
        # Independent official API, not the plotting adapter under test.
        point = self.workspace / preview['statepoint']
        oracle = subprocess.check_output(self.runtime['python_command'] + ['-c',
            'import openmc,json,sys; p=openmc.StatePoint(sys.argv[1],autolink=False); t=next(iter(p.tallies.values())); print(json.dumps(t.get_values(scores=["fission"],nuclides=["total"]).ravel().tolist()))', str(point)], text=True)
        self.assertEqual(series['mean'], json.loads(oracle))
        self.assertEqual(series['score'], 'fission')
        self.assertIn('<svg', (self.workspace / chart['path']).read_text())
        self.assertIn('score或nuclide不存在', self.call('openmc_plot_results', {'job_id': self.job, 'score': 'not-a-score'}, False))
        self.assertIn('filter', self.call('openmc_plot_results', {'job_id': self.job, 'filter_selection': [{'type': 'EnergyFilter', 'bins': [[0, 1]]}]}, False))
        self.evidence('real-score-selection.json', {'chart': chart, 'series': series, 'oracle': json.loads(oracle)})

    def test_multidimensional_scan_requires_fixed_axes(self):
        self.assertIn('fixed', self.call('openmc_plot_scan', {'scan_id': self.scan, 'x_parameter': self.density}, False))
        chart = self.call('openmc_plot_scan', {'scan_id': self.scan, 'x_parameter': self.density, 'fixed': {'settings.seed': 1}, 'metric': 'tally', 'score': 'flux'})
        series = json.loads((self.workspace / chart['series_json']).read_text())
        self.assertEqual(series['x'], [.8, 1.2])
        self.assertEqual(len(series['sources']), 2)
        self.assertEqual(series['selection']['score'], 'flux')
        self.evidence('real-scan-chart.json', {'chart': chart, 'series': series})

    def test_cancel_stops_current_and_future_calculations(self):
        self.call('openmc_prepare_case', {'case_dir': 'base', 'target_dir': 'long', 'parameters': {'settings.particles': 3000000, 'settings.batches': 100}})
        scan = self.call('openmc_submit_scan', {'case_dir': 'long', 'axes': {self.density: [.7, .9, 1.1]}})['scan_id']
        for _ in range(100):
            status = self.call('openmc_scan_status', {'scan_id': scan})
            if status.get('active_job_id'):break
            time.sleep(.05)
        self.assertTrue(status.get('active_job_id'))
        self.call('openmc_cancel_scan', {'scan_id': scan})
        status = self.wait(scan)
        self.assertEqual(status['state'], 'cancelled')
        self.assertEqual(sum('job_id' in r for r in status['cases']), 1)
        self.assertEqual([r['state'] for r in status['cases']], ['cancelled', 'not_run', 'not_run'])
        self.assertFalse(self.call('openmc_cancel_scan', {'scan_id': scan})['changed'])
        self.evidence('real-scan-cancel.json', status)

    def test_failure_is_preserved_and_stop_on_error_prevents_later_work(self):
        scan = self.call('openmc_submit_scan', {'case_dir': 'base', 'axes': {self.surface: [11, -10]}, 'stop_on_error': True})['scan_id']
        status = self.wait(scan)
        self.assertEqual(status['state'], 'failed', status)
        self.assertEqual(status['cases'][0]['state'], 'failed')
        self.assertEqual(status['cases'][1]['state'], 'not_run')
        self.assertTrue(status['cases'][0]['error'])
        self.evidence('real-scan-stop-on-error.json', status)

    def test_partial_scan_keeps_failure_and_does_not_invent_zero(self):
        scan = self.call('openmc_submit_scan', {'case_dir': 'base', 'axes': {self.surface: [11, -10]}})['scan_id']
        status = self.wait(scan)
        self.assertEqual(status['state'], 'partial')
        self.assertEqual([r['state'] for r in status['cases']], ['failed', 'completed'])
        chart = self.call('openmc_plot_scan', {'scan_id': scan, 'x_parameter': self.surface})
        series = json.loads((self.workspace / chart['series_json']).read_text())
        self.assertEqual(series['x'], [-10, 11])
        self.assertIsNone(series['mean'][1])
        self.assertEqual(chart['missing'], 1)
        report = self.call('openmc_export_scan_report', {'scan_id': scan, 'format': 'csv'})
        with (self.workspace / report['path']).open() as stream:
            rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]['state'], 'failed')
            self.assertEqual(rows[0]['keff_mean'], '')
        self.evidence('real-scan-partial.json', {'scan': status, 'series': series})

    def test_interrupted_monitor_does_not_resubmit(self):
        self.call('openmc_prepare_case', {'case_dir': 'base', 'target_dir': 'interruption', 'parameters': {'settings.particles': 3000000, 'settings.batches': 100}})
        scan = self.call('openmc_submit_scan', {'case_dir': 'interruption', 'axes': {self.density: [.7, .9]}})['scan_id']
        record_path = self.workspace / '.mc-scans' / scan / 'job.json'
        for _ in range(100):
            record = json.loads(record_path.read_text())
            if record.get('active_job_id'):break
            time.sleep(.05)
        self.assertTrue(record.get('active_job_id'))
        os.kill(record['worker_pid'], signal.SIGKILL)
        time.sleep(.1)
        status = self.call('openmc_scan_status', {'scan_id': scan})
        self.assertEqual(status['state'], 'interrupted')
        self.call('openmc_cancel_scan', {'scan_id': scan})
        self.assertEqual(self.wait(record['active_job_id'], 'job')['state'], 'cancelled')
        again = self.call('openmc_scan_status', {'scan_id': scan})
        self.assertEqual(sum('job_id' in r for r in again['cases']), 1)
        self.evidence('real-scan-interrupted.json', again)

    def test_crash_after_child_launch_before_submit_returns_is_recoverable(self):
        self.call('openmc_prepare_case', {'case_dir': 'base', 'target_dir': 'crash-window', 'parameters': {'settings.particles': 3000000, 'settings.batches': 100}})
        driver = self.work / 'crash_driver.py'
        # Inject SIGKILL at the operation boundary. The original submit and actual
        # solver still execute; no simulated calculation or fake lifecycle record.
        driver.write_text('''import json,os,signal,sys
sys.path.insert(0,sys.argv[1])
import openmc_scan as scans, openmc_jobs as jobs
if len(sys.argv)>2:
    original=jobs.submit
    def crash(*a,**kw):
        result=original(*a,**kw)
        os.kill(os.getpid(),signal.SIGKILL)
        return result
    jobs.submit=crash
    scans.run(sys.argv[2])
else:
    original=jobs.spawn_worker
    # Reuse the normal worker launcher, adding this fault-injection entry point.
    def spawn(directory,script,identifier):
        import subprocess,threading
        with (directory/'worker.log').open('ab') as log:
            p=subprocess.Popen([sys.executable,__file__,sys.argv[1],identifier],stdout=log,stderr=log,start_new_session=True)
        jobs.update(directory,worker_pid=p.pid,worker_identity=jobs.identity(p.pid))
        threading.Thread(target=p.wait,daemon=True).start()
    jobs.spawn_worker=spawn
    print(json.dumps(scans.submit_scan('crash-window',{'settings.seed':[1,2]})))
''')
        result = subprocess.check_output([sys.executable, str(driver), str(ROOT / 'mcp')], env=self.env, text=True)
        scan = json.loads(result)['scan_id']
        status = self.wait(scan)
        self.assertEqual(status['state'], 'interrupted')
        self.assertTrue(status.get('active_job_id'), 'An already launched calculation must remain discoverable')
        self.call('openmc_cancel_scan', {'scan_id': scan})
        self.assertEqual(self.wait(status['active_job_id'], 'job')['state'], 'cancelled')
        self.assertEqual(sum('job_id' in r for r in self.call('openmc_scan_status', {'scan_id': scan})['cases']), 1)
        self.evidence('real-scan-launch-crash-window.json', status)

    def test_reports_are_real_formats_and_contain_results(self):
        from pypdf import PdfReader
        import olefile
        for kind, identifier in [('scan', self.scan), ('job', self.job)]:
            name = 'openmc_export_scan_report' if kind == 'scan' else 'openmc_export_report'
            outputs = {f: self.call(name, {kind + '_id': identifier, 'format': f}) for f in ('markdown', 'csv', 'docx', 'pdf', 'doc')}
            with zipfile.ZipFile(self.workspace / outputs['docx']['path']) as package:
                document = package.read('word/document.xml').decode()
                self.assertIn(identifier, document)
                self.assertIn('synthetic_mgxs', document)
                self.assertIn('专业验收', document)
            pdf = self.workspace / outputs['pdf']['path']
            text = '\n'.join(p.extract_text() for p in PdfReader(pdf).pages)
            self.assertIn(identifier, text)
            self.assertIn('蒙卡', text)
            self.assertIn('synthetic_mgxs', text)
            with olefile.OleFileIO(self.workspace / outputs['doc']['path']) as ole:self.assertTrue(ole.exists('WordDocument'))
            listing = self.call('openmc_list_artifacts', {'kind': kind, 'identifier': identifier})
            listed = {r['path']: r for r in listing['artifacts']}
            for output in outputs.values():self.assertIn(output['path'], listed)
            self.assertFalse(listing['download_supported'])
            self.assertFalse(any(Path(p).name == 'job.json' for p in listed))
            self.evidence('real-' + kind + '-reports.json', outputs)

    def test_real_filtered_chart_and_report_limits_are_explicit(self):
        self.call('openmc_prepare_case', {'case_dir': 'base', 'target_dir': 'filtered', 'parameters': {self.density: 1.0}})
        (self.workspace / 'filtered/tallies.xml').write_text('''<tallies>
  <filter id="10" type="energy"><bins>0 1000000 20000000</bins></filter>
  <tally id="10"><filters>10</filters><scores>flux fission</scores></tally>
  <tally id="11"><scores>flux</scores></tally>
</tallies>''')
        runtime = {**self.runtime, 'max_plot_points': 1, 'max_report_rows': 1}
        config = self.work / 'limited.json'; config.write_text(json.dumps(runtime))
        env = {**self.env, 'MC_OPENMC_CONFIG': str(config)}
        job = self.call('openmc_submit_job', {'case_dir': 'filtered'}, env=env)['job_id']
        self.assertEqual(self.wait(job, 'job')['state'], 'completed')
        self.assertIn('点过多', self.call('openmc_plot_results', {'job_id': job, 'score': 'flux'}, False))
        selection = [{'type': 'EnergyFilter', 'bins': [[0, 1000000]]}]
        chart = self.call('openmc_plot_results', {'job_id': job, 'score': 'flux', 'filter_selection': selection})
        series = json.loads((self.workspace / chart['series_json']).read_text())
        self.assertEqual(len(series['mean']), 1)
        self.assertEqual(series['filter_selection'], selection)
        with (self.workspace / chart['series_csv']).open() as source:
            row = next(csv.DictReader(source))
            self.assertIn('flux', row['y_label'])
            self.assertEqual(json.loads(row['selection'])['filter_selection'], selection)
            self.assertEqual(row['metadata_file'], Path(chart['series_json']).name)
        report = self.call('openmc_export_report', {'job_id': job, 'format': 'docx'})
        with zipfile.ZipFile(self.workspace / report['path']) as archive:
            text = archive.read('word/document.xml').decode()
            self.assertIn('默认图未生成', text)
            self.assertIn('未展示后续tally', text)
            self.assertIn('11', text)

    def test_polling_failure_stops_scan_and_keeps_active_calculation(self):
        self.call('openmc_prepare_case', {'case_dir': 'base', 'target_dir': 'polling-fault', 'parameters': {'settings.particles': 3000000, 'settings.batches': 100}})
        driver = self.work / 'polling_fault.py'
        driver.write_text('''import json,sys,subprocess,threading
sys.path.insert(0,sys.argv[1])
import openmc_scan as scans, openmc_jobs as jobs
if len(sys.argv)>2:
    def broken_poll(*a,**kw):raise RuntimeError('injected monitor polling failure')
    jobs.status=broken_poll
    original_cancel=jobs.cancel
    def wait_for_cancel(identifier):
        import time
        result=original_cancel(identifier)
        end=time.monotonic()+10
        while time.monotonic()<end:
            if jobs.read(jobs.job_directory(identifier))['state'] in jobs.TERMINAL:break
            time.sleep(.01)
        return result
    jobs.cancel=wait_for_cancel
    scans.run(sys.argv[2])
else:
    def spawn(directory,script,identifier):
        with (directory/'worker.log').open('ab') as log:
            p=subprocess.Popen([sys.executable,__file__,sys.argv[1],identifier],stdout=log,stderr=log,start_new_session=True)
        jobs.update(directory,worker_pid=p.pid,worker_identity=jobs.identity(p.pid))
        threading.Thread(target=p.wait,daemon=True).start()
    jobs.spawn_worker=spawn
    print(json.dumps(scans.submit_scan('polling-fault',{'settings.seed':[1,2]})))
''')
        scan = json.loads(subprocess.check_output([sys.executable, str(driver), str(ROOT / 'mcp')], env=self.env, text=True))['scan_id']
        status = self.wait(scan)
        self.assertEqual(status['state'], 'failed')
        self.assertEqual(sum('job_id' in r for r in status['cases']), 1)
        # If it was already cancelled before the monitor handled the error, no
        # outstanding calculation remains; otherwise its ID must stay accessible.
        job = next(r['job_id'] for r in status['cases'] if 'job_id' in r)
        self.assertEqual(self.wait(job, 'job')['state'], 'cancelled')
        self.assertEqual(status['cases'][1]['state'], 'not_run')
        self.evidence('real-scan-polling-failure.json', status)

    def test_conversion_failure_is_actionable_and_not_listed(self):
        bad = {**self.runtime, 'office_command': ['/no-such-office-converter']}
        path = self.work / 'bad-office.json'; path.write_text(json.dumps(bad))
        env = {**self.env, 'MC_OPENMC_CONFIG': str(path)}
        # Runtime is frozen on submission, so use a separate calculation with this config.
        job = self.call('openmc_submit_job', {'case_dir': 'base'}, env=env)['job_id']
        self.assertEqual(self.wait(job, 'job')['state'], 'completed')
        self.assertIn('转换组件', self.call('openmc_export_report', {'job_id': job, 'format': 'pdf'}, False))
        listed = self.call('openmc_list_artifacts', {'kind': 'job', 'identifier': job})['artifacts']
        self.assertFalse(any(a['path'].endswith('.pdf') for a in listed))

    def test_real_mcp_image_and_restart_query(self):
        with socket.socket() as sock:sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        config = self.work / 'service.json'
        config.write_text(json.dumps({'host': '127.0.0.1', 'port': port, 'path': '/mcp', 'name': 'scan-test', 'version': '0.3.0', 'clientName': 'mc'}))
        env = {**self.env, 'MCP_CONFIG_FILE': str(config), 'MCP_CONNECTION_FILE': str(self.work / 'connection.json'), 'MCP_AUTH_TOKEN': 'test-only-token'}
        log = (self.work / 'server.log').open('w+')
        def start():
            p = subprocess.Popen([sys.executable, str(ROOT / 'mcp/server.py')], env=env, stdout=log, stderr=log, cwd=ROOT / 'mcp')
            for _ in range(100):
                try:
                    urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=.2).close(); return p
                except OSError:time.sleep(.05)
            raise AssertionError('MCP server startup failed')
        async def flow():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamablehttp_client
            async with streamablehttp_client(f'http://127.0.0.1:{port}/mcp', headers={'Authorization': 'Bearer test-only-token'}) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    status = await session.call_tool('openmc_scan_status', {'scan_id': self.scan})
                    self.assertFalse(status.isError)
                    self.assertEqual(status.structuredContent['state'], 'completed')
                    chart = await session.call_tool('openmc_plot_results', {'job_id': self.job, 'score': 'flux'})
                    self.assertFalse(chart.isError)
                    images = [item for item in chart.content if item.type == 'image']
                    self.assertEqual(len(images), 1)
                    self.assertTrue(base64.b64decode(images[0].data).startswith(b'\x89PNG\r\n\x1a\n'))
                    listing = await session.call_tool('openmc_list_artifacts', {'kind': 'job', 'identifier': self.job})
                    self.assertFalse(listing.isError)
                    self.assertIn(chart.structuredContent['path'], [a['path'] for a in listing.structuredContent['artifacts']])
        server = start()
        try:
            asyncio.run(flow())
            server.terminate(); server.wait(timeout=10)
            server = start()
            asyncio.run(flow())
        finally:
            server.terminate(); server.wait(timeout=10); log.close()


if __name__ == '__main__':unittest.main()
