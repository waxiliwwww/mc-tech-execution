"""CAD转换工具离线测试：fixture只验证协议、路径与边界；真实网格化见MC_TEST_CAD_CONFIG实跑档。"""
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
import openmc_cad as cad


class CadConversion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc cad with spaces ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        # 离线环境无openmc：python_command指向系统解释器，engine_read预期失败并保留错误文本。
        self.cfg.update(python_command=[sys.executable], data_kind='mock_process_fixture',
                        cad_python_command=[sys.executable, str(ROOT / 'tests/fixtures/mock_cad.py')])
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.cfg))
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE), 'MC_OPENMC_CONFIG': str(self.config)})
        self.env_patch.start()
        self.step = business.WORKSPACE / 'model.step'
        self.step.write_bytes(b'ISO-10303-21;MOCK;END-ISO-10303-21;')

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def test_missing_cad_config_is_actionable(self):
        self.cfg.pop('cad_python_command')
        self.config.write_text(json.dumps(self.cfg))
        with self.assertRaises(business.MCInputError) as caught:
            cad.inspect_step('model.step')
        self.assertIn('cad_python_command', str(caught.exception))

    def test_invalid_cad_config_types_are_rejected(self):
        self.cfg.update(cad_python_command='not-a-list')
        self.config.write_text(json.dumps(self.cfg))
        with self.assertRaises(business.MCInputError):
            cad.inspect_step('model.step')
        self.cfg.update(cad_python_command=[sys.executable], cad_timeout_seconds=-1)
        self.config.write_text(json.dumps(self.cfg))
        with self.assertRaises(business.MCInputError):
            cad.inspect_step('model.step')

    def test_inspect_step_returns_solids_and_units_note(self):
        value = cad.inspect_step('model.step')
        self.assertEqual(value['solids'], 2)
        self.assertEqual(value['bounding_box']['xmax'], 10.0)
        self.assertIn('毫米', value['units_note'])
        self.assertEqual(value['location'], 'server')

    def test_inspect_rejects_bad_paths_and_suffixes(self):
        (business.WORKSPACE / 'model.txt').write_text('x')
        for bad in ('/abs/model.step', '../escape.step', 'missing.step', 'model.txt'):
            with self.assertRaises(business.MCInputError):
                cad.inspect_step(bad)

    def test_convert_success_writes_h5m_marker_and_keeps_engine_error(self):
        value = cad.convert_cad('model.step', 'converted', ['steel'])
        directory = business.WORKSPACE / 'converted'
        self.assertTrue((directory / 'dagmc.h5m').is_file())
        marker = json.loads((directory / 'mc-case.json').read_text())
        self.assertEqual(marker['data_kind'], 'cad_conversion_unverified')
        self.assertFalse(marker['professional_acceptance'])
        self.assertEqual(marker['material_tags'], ['steel'])
        self.assertEqual(marker['source_step']['sha256'], '1' * 64)
        self.assertEqual(value['case_dir'], 'converted')
        self.assertFalse(value['professional_acceptance'])
        # 离线环境无openmc：engine_read必须保留失败而不是冒充成功。
        self.assertFalse(value['engine_read']['ok'])
        self.assertIn('error', value['engine_read'])
        self.assertEqual(value['provenance']['data_kind'], 'cad_conversion_unverified')

    def test_convert_rejects_existing_target_directory(self):
        (business.WORKSPACE / 'converted').mkdir()
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'converted', ['steel'])

    def test_material_tags_validation(self):
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'a', [])
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'b', ['x', 'x'])
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'c', 'steel')
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'e', ['ok', 3])
        value = cad.convert_cad('model.step', 'd', 'assembly_names')
        self.assertTrue((business.WORKSPACE / 'd/dagmc.h5m').is_file())

    def test_scale_and_mesh_parameter_validation(self):
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'a', ['x'], scale_factor=0)
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'b', ['x'], scale_factor=True)
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'c', ['x'], min_mesh_size=-1)
        with self.assertRaises(business.MCInputError):
            cad.convert_cad('model.step', 'd', ['x'], implicit_complement_material_tag='  ')

    def test_conversion_failure_keeps_evidence(self):
        with patch.dict(os.environ, {'MOCK_CAD_MODE': 'fail'}):
            with self.assertRaises(business.MCInputError):
                cad.convert_cad('model.step', 'failed', ['steel'])
        note = json.loads((business.WORKSPACE / 'failed/conversion-failed.json').read_text())
        self.assertEqual(note['state'], 'failed')
        self.assertFalse((business.WORKSPACE / 'failed/dagmc.h5m').exists())

    def test_worker_crash_without_result_file_reports_stderr(self):
        with patch.dict(os.environ, {'MOCK_CAD_MODE': 'crash'}):
            with self.assertRaises(business.MCInputError) as caught:
                cad.convert_cad('model.step', 'crashed', ['steel'])
        self.assertIn('CAD转换失败', str(caught.exception))
        self.assertIn('crash detail', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
