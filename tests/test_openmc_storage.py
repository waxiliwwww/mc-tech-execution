"""存储只读报告离线测试：构造内部记录夹具，验证清点/榜单/可回收预览/引用保护/损坏如实报告。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_tools as tools
from openmc_runtime import Error


def job_record(identifier, state='completed', age_days=1.0):
    return {'job_id': identifier, 'state': state,
            'finished_at': time.time() - age_days * 86400,
            'worker_pid': 999999, 'worker_identity': 'boot:1'}


class StorageReport(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc storage ')
        self.root = Path(self.temp.name)
        self.previous = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        self.cfg.update(data_kind='mock_process_fixture')
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.cfg))
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE), 'MC_OPENMC_CONFIG': str(self.config)})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous
        self.temp.cleanup()

    def make_job(self, identifier, size=100, state='completed', age_days=1.0):
        target = business.WORKSPACE / '.mc-jobs' / identifier
        (target / 'work').mkdir(parents=True)
        (target / 'job.json').write_text(
            json.dumps(job_record(identifier, state, age_days)), encoding='utf-8')
        (target / 'work' / 'payload.bin').write_bytes(b'x' * size)
        return target

    def make_scan(self, identifier, children=(), state='completed', age_days=1.0):
        target = business.WORKSPACE / '.mc-scans' / identifier
        target.mkdir(parents=True)
        record = {'scan_id': identifier, 'state': state,
                  'finished_at': time.time() - age_days * 86400,
                  'worker_pid': 999999, 'worker_identity': 'boot:1',
                  'cases': [{'job_id': child, 'state': 'completed'} for child in children]}
        (target / 'job.json').write_text(json.dumps(record), encoding='utf-8')
        return target

    def test_totals_states_and_largest(self):
        self.make_job('a' * 32, size=100)
        self.make_job('b' * 32, size=300, state='failed')
        value = tools.storage_report()
        self.assertTrue(value['ok'])
        self.assertEqual(value['records'], 2)
        self.assertEqual(value['totals']['job']['count'], 2)
        self.assertEqual(value['all_bytes'], value['totals']['job']['bytes'])
        self.assertEqual(value['states'], {'completed': 1, 'failed': 1})
        self.assertEqual(value['largest'][0]['identifier'], 'b' * 32)
        self.assertIn('不删除', value['note'])
        disk = value['disk_usage']
        self.assertGreater(disk['total_bytes'], 0)
        self.assertGreaterEqual(disk['free_bytes'], 0)
        self.assertGreaterEqual(disk['total_bytes'], disk['used_bytes'] + disk['free_bytes'])
        self.assertEqual(disk['min_free_disk_bytes'], self.cfg['min_free_disk_bytes'])

    def test_top_limits_largest_list(self):
        for index in range(5):
            self.make_job(f'{index:032x}', size=10 * (index + 1))
        value = tools.storage_report(top=2)
        self.assertEqual(len(value['largest']), 2)
        self.assertEqual(value['largest'][0]['identifier'], f'{4:032x}')

    def test_disk_report_includes_explicit_additional_volumes(self):
        self.cfg['disk_space_check_paths']=[str(self.root/'host-volume')]
        self.config.write_text(json.dumps(self.cfg))
        with patch('shutil.disk_usage',side_effect=[(10000,1000,9000),(20000,19000,1000)]):
            value=tools.storage_report()
        self.assertEqual(value['disk_usage']['free_bytes'],9000)
        additional=value['disk_usage']['additional_volumes']
        self.assertEqual(additional,[{'path':str(self.root/'host-volume'),'total_bytes':20000,
                                     'used_bytes':19000,'free_bytes':1000}])

    def test_reclaimable_preview_by_age(self):
        self.make_job('c' * 32, size=500, age_days=10)
        self.make_job('d' * 32, size=700, age_days=0.2)
        value = tools.storage_report(older_than_days=5)
        preview = value['reclaimable']
        self.assertEqual(preview['records'], 1)
        # 仅老记录入选：500字节载荷+记录文件，且明显小于新记录的700字节载荷
        self.assertGreaterEqual(preview['bytes'], 500)
        self.assertLess(preview['bytes'], 700)

    def test_scan_reference_protection_in_preview(self):
        child = 'e' * 32
        self.make_job(child, size=400, age_days=10)
        self.make_scan('f' * 32, children=[child], age_days=0.2)
        value = tools.storage_report(older_than_days=5)
        preview = value['reclaimable']
        self.assertEqual(preview['records'], 0)
        self.assertTrue(any('保留仍被扫描引用' in item for item in preview['skipped']), preview['skipped'])

    def test_scan_selected_pulls_children(self):
        child = '1' * 32
        self.make_job(child, size=400, age_days=10)
        self.make_scan('2' * 32, children=[child], age_days=10)
        value = tools.storage_report(older_than_days=5)
        self.assertEqual(value['reclaimable']['records'], 2)

    def test_example_records_counted(self):
        target = business.WORKSPACE / '.mc-examples' / ('3' * 32)
        target.mkdir(parents=True)
        (target / 'example.json').write_text(json.dumps(
            {'instance_id': '3' * 32, 'state': 'completed',
             'finished_at': time.time() - 86400, 'steps': {}}), encoding='utf-8')
        (target / 'payload.bin').write_bytes(b'x' * 50)
        value = tools.storage_report()
        self.assertEqual(value['totals']['example']['count'], 1)

    def test_corrupt_record_reported_honestly(self):
        target = self.make_job('4' * 32)
        (target / 'job.json').write_text('{broken', encoding='utf-8')
        value = tools.storage_report()
        self.assertFalse(value['ok'])
        self.assertIn('清点内部记录失败', value['error'])
        self.assertIn('不删除', value['note'])

    def test_symlink_inside_record_goes_to_size_errors(self):
        target = self.make_job('5' * 32)
        os.symlink(self.config, target / 'work' / 'link.json')
        value = tools.storage_report()
        self.assertTrue(value['ok'])
        self.assertEqual(len(value['size_errors']), 1)
        self.assertIn('符号链接', value['size_errors'][0]['size_error'])

    def test_validation_errors(self):
        with self.assertRaises(Error):
            tools.storage_report(top=0)
        with self.assertRaises(Error):
            tools.storage_report(top=True)
        with self.assertRaises(Error):
            tools.storage_report(older_than_days=-1)
        with self.assertRaises(Error):
            tools.storage_report(older_than_days=float('nan'))

    def test_no_preview_without_parameter(self):
        self.make_job('6' * 32)
        self.assertNotIn('reclaimable', tools.storage_report())


if __name__ == '__main__':
    unittest.main()
