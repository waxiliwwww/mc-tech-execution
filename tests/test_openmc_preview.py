"""openmc_preview_input 离线测试：预览内容、指纹、截断、符号链接拒绝与结构摘要。"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_tools as tools

MATERIALS = ('<materials><material id="1" name="fuel"><nuclide name="U235" ao="1.0"/>'
             '<sab name="c_H_in_H2O"/></material></materials>')
GEOMETRY = ('<geometry><cell id="1" universe="0" material="1" region="-1"/>'
            '<surface id="1" type="sphere" coeffs="0 0 0 10" boundary="vacuum"/></geometry>')
SETTINGS = ('<settings><run_mode>eigenvalue</run_mode><particles>200</particles>'
            '<batches>10</batches><inactive>2</inactive></settings>')
TALLIES = '<tallies><tally id="1"><scores>flux fission</scores></tally></tallies>'


class PreviewInput(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc preview ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        self.cfg.update(data_kind='mock_process_fixture')
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.cfg))
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE), 'MC_OPENMC_CONFIG': str(self.config)})
        self.env_patch.start()
        self.case = business.WORKSPACE / 'case'
        self.case.mkdir()
        (self.case / 'materials.xml').write_text(MATERIALS)
        (self.case / 'geometry.xml').write_text(GEOMETRY)
        (self.case / 'settings.xml').write_text(SETTINGS)
        (self.case / 'tallies.xml').write_text(TALLIES)

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def entry(self, value, name):
        return next(item for item in value['files'] if item['name'] == name)

    def test_preview_returns_content_hashes_and_summary(self):
        value = tools.preview_input('case')
        entry = self.entry(value, 'materials.xml')
        self.assertEqual(entry['content'], MATERIALS)
        self.assertEqual(entry['bytes'], len(MATERIALS.encode()))
        self.assertEqual(entry['sha256'], hashlib.sha256(MATERIALS.encode()).hexdigest())
        self.assertFalse(entry['truncated'])
        summary = value['summary']
        self.assertEqual(summary['run_mode'], 'eigenvalue')
        self.assertEqual(summary['particles'], '200')
        self.assertEqual(summary['materials'][0]['nuclides'], ['U235'])
        self.assertEqual(summary['materials'][0]['sab'], ['c_H_in_H2O'])
        self.assertEqual(summary['cells'], 1)
        self.assertEqual(summary['surfaces'], 1)
        self.assertEqual(summary['tallies'][0]['scores'], ['flux', 'fission'])
        self.assertEqual(value['problems'], [])
        self.assertFalse(value['professional_acceptance'])
        self.assertEqual(value['location'], 'server')

    def test_missing_files_are_listed_as_problems(self):
        (self.case / 'geometry.xml').unlink()
        value = tools.preview_input('case')
        self.assertIn('geometry.xml缺失', value['problems'])
        self.assertFalse(self.entry(value, 'geometry.xml')['present'])

    def test_symlink_inside_case_is_refused_not_followed(self):
        (self.case / 'geometry.xml').unlink()
        (self.case / 'geometry.xml').symlink_to(self.case / 'settings.xml')
        value = tools.preview_input('case')
        entry = self.entry(value, 'geometry.xml')
        self.assertTrue(entry['symlink'])
        self.assertNotIn('content', entry)
        self.assertIn('符号链接', ''.join(value['problems']))

    def test_content_truncated_with_marker(self):
        long_text = '<settings><!-- ' + 'x' * 25000 + ' --></settings>'
        (self.case / 'settings.xml').write_text(long_text)
        value = tools.preview_input('case')
        entry = self.entry(value, 'settings.xml')
        self.assertTrue(entry['truncated'])
        self.assertEqual(len(entry['content']), 20000)
        self.assertIn('截断', entry['note'])

    def test_symlinked_provenance_marker_is_rejected_before_metadata_read(self):
        outside=self.root/'private.json'
        outside.write_text('{"data_kind":"private","note":"must not be read"}')
        (self.case/'mc-case.json').symlink_to(outside)
        with self.assertRaisesRegex(business.MCInputError,'符号链接'):
            tools.preview_input('case')

    def test_oversize_file_content_withheld_but_fingerprint_kept(self):
        big = b'<tallies>' + b' ' * (2 * 1024 * 1024 + 100) + b'</tallies>'
        (self.case / 'tallies.xml').write_bytes(big)
        value = tools.preview_input('case')
        entry = self.entry(value, 'tallies.xml')
        self.assertIsNone(entry['content'])
        self.assertTrue(entry['truncated'])
        self.assertEqual(entry['sha256'], hashlib.sha256(big).hexdigest())

    def test_model_xml_single_file_layout(self):
        for name in ('materials.xml', 'geometry.xml', 'settings.xml', 'tallies.xml'):
            (self.case / name).unlink()
        (self.case / 'model.xml').write_text(
            '<model>' + MATERIALS[11:-12] + GEOMETRY[10:-11] + SETTINGS[10:-11] + '</model>')
        value = tools.preview_input('case')
        self.assertEqual(value['summary']['layout'], 'model.xml单文件')
        self.assertEqual(value['summary']['run_mode'], 'eigenvalue')
        self.assertEqual([f['name'] for f in value['files']], ['model.xml'])

    def test_unparseable_file_keeps_preview_with_parse_error(self):
        (self.case / 'tallies.xml').write_text('<tallies><tally></tallies>')
        value = tools.preview_input('case')
        entry = self.entry(value, 'tallies.xml')
        self.assertIn('parse_error', entry)
        self.assertIn('tally', entry['content'])

    def test_manifest_matches_actual_mock_job_snapshot_including_markers_and_resources(self):
        import time
        import openmc_jobs as jobs
        marker={'data_kind':'mock_process_fixture','note':'input manifest fixture, no physics'}
        (self.case/'mc-case.json').write_text(json.dumps(marker))
        (self.case/'mock-mode.json').write_text('{"mode":"success"}')
        (self.case/'resources').mkdir();(self.case/'resources/dagmc.h5m').write_bytes(b'path/hash fixture')
        (self.case/'views').mkdir();(self.case/'views/old.png').write_bytes(b'not input')
        self.cfg.update(openmc_command=[sys.executable,str(ROOT/'tests/fixtures/mock_openmc.py')],
                        python_command=[sys.executable],job_timeout_seconds=15,max_log_bytes=4096)
        self.config.write_text(json.dumps(self.cfg))
        before={str(p.relative_to(self.case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.case.rglob('*') if p.is_file()}
        preview=tools.preview_input('case')
        job=jobs.submit('case');identifier=job['job_id']
        try:
            deadline=time.monotonic()+10
            while True:
                status=jobs.status(identifier)
                if status['state'] in jobs.TERMINAL:break
                if time.monotonic()>deadline:self.fail('mock worker timeout')
                time.sleep(.03)
            self.assertEqual(status['state'],'completed',status)
            self.assertTrue(preview['manifest_complete'])
            self.assertEqual(preview['input_hashes'],status['input_hashes'])
            self.assertEqual({e['name']:e['sha256'] for e in preview['files']},status['input_hashes'])
            self.assertIn('mc-case.json',preview['input_hashes'])
            self.assertIn('resources/dagmc.h5m',preview['input_hashes'])
            self.assertNotIn('views/old.png',preview['input_hashes'])
            self.assertNotIn('parse_error',self.entry(preview,'mc-case.json'))
            self.assertIsNone(self.entry(preview,'resources/dagmc.h5m')['content'])
            after={str(p.relative_to(self.case)):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.case.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
        finally:
            if jobs.status(identifier)['state'] not in jobs.TERMINAL:jobs.cancel(identifier)

    def test_large_binary_fingerprint_does_not_read_whole_file_into_memory(self):
        asset=self.case/'mesh.h5m';content=b'0'*(2*1024*1024+10);asset.write_bytes(content)
        read_bytes=Path.read_bytes
        def guarded(path):
            if path==asset:raise AssertionError('binary resource must be streamed')
            return read_bytes(path)
        with patch.object(Path,'read_bytes',guarded):value=tools.preview_input('case')
        entry=self.entry(value,'mesh.h5m')
        self.assertEqual(entry['sha256'],hashlib.sha256(content).hexdigest())
        self.assertIsNone(entry['content'])
        self.assertTrue(value['manifest_complete'])

    def test_path_rules_apply(self):
        for bad in ('/abs/case', '../escape', 'missing-dir'):
            with self.assertRaises(business.MCInputError):
                tools.preview_input(bad)


if __name__ == '__main__':
    unittest.main()
