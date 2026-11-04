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

    # ---- A5 后续：build_dagmc_model（patch api，不依赖引擎） ----

    def make_converted(self, name='converted', tags=('steel',), implicit=None):
        directory = business.WORKSPACE / name
        directory.mkdir()
        (directory / 'dagmc.h5m').write_bytes(b'h5m')
        marker = {'data_kind': 'cad_conversion_unverified', 'generator': 'cad_to_dagmc',
                  'source_step': {'path': 'model.step', 'sha256': '1' * 64},
                  'material_tags': list(tags), 'scale_factor': 0.1,
                  'implicit_complement_material_tag': implicit,
                  'professional_acceptance': False}
        (directory / 'mc-case.json').write_text(json.dumps(marker))
        return name

    def fake_build(self):
        def fake(action, directory, payload, cfg=None):
            self.assertEqual(action, 'build-dagmc-model')
            warnings = (['材料' + payload['materials'][0]['name'] + '未给核素组成；OpenMC输运前须补全。']
                        if not payload['materials'][0].get('nuclides') else [])
            return {'files': ['model.xml'], 'dagmc_filename': 'dagmc.h5m',
                    'materials': [item['name'] for item in payload['materials']],
                    'settings': {'particles': payload['particles'],
                                 'batches': payload['batches'], 'inactive': payload['inactive']},
                    'source_point': payload['source_point'] or [0.0, 0.0, 0.0],
                    'engine_parse': {'ok': True, 'materials': len(payload['materials'])},
                    'warnings': warnings, 'note': 'stub'}
        return fake

    def test_build_dagmc_model_writes_marker_and_warns(self):
        case = self.make_converted()
        with patch.object(cad, 'api', self.fake_build()):
            value = cad.build_dagmc_model(case, [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}])
        self.assertEqual(value['files'], ['model.xml'])
        self.assertEqual(value['dagmc_filename'], 'dagmc.h5m')
        self.assertTrue(value['warnings'])
        self.assertIn('输运前须补全', value['warnings'][0])
        marker = json.loads((business.WORKSPACE / case / 'mc-case.json').read_text())
        self.assertIn('model_built', marker)
        self.assertFalse(marker['model_built']['professional_acceptance'])
        self.assertFalse(value['professional_acceptance'])

    def test_build_dagmc_model_material_validation(self):
        case = self.make_converted()
        with patch.object(cad, 'api', self.fake_build()):
            for materials in (
                [{'name': 'steel', 'density': 7.8, 'units': 'sum'}],
                [{'name': 'steel', 'density': 0, 'units': 'g/cm3'}],
                [{'name': 'steel', 'density': -1, 'units': 'g/cm3'}],
                [{'name': 'steel', 'density': True, 'units': 'g/cm3'}],
                [{'name': 'aluminum', 'density': 7.8, 'units': 'g/cm3'}],
                [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'},
                 {'name': 'steel', 'density': 2.0, 'units': 'g/cm3'}],
                [],
            ):
                with self.assertRaises(business.MCInputError):
                    cad.build_dagmc_model(case, materials)
            with self.assertRaises(business.MCInputError):
                cad.build_dagmc_model(case, [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3',
                                              'nuclides': [{'name': 'Fe56', 'fraction': 0}]}])

    def test_build_dagmc_model_requires_all_tags_including_complement(self):
        case = self.make_converted(tags=('steel', 'water'), implicit='graveyard')
        with patch.object(cad, 'api', self.fake_build()):
            with self.assertRaises(business.MCInputError) as caught:
                cad.build_dagmc_model(case, [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}])
            self.assertIn('water', str(caught.exception))
            value = cad.build_dagmc_model(case, [
                {'name': 'steel', 'density': 7.8, 'units': 'g/cm3'},
                {'name': 'water', 'density': 1.0, 'units': 'g/cm3'},
                {'name': 'graveyard', 'density': 0.001, 'units': 'atom/b-cm'}])
        self.assertEqual(len(value['materials']), 3)

    def test_build_dagmc_model_settings_validation(self):
        case = self.make_converted()
        with patch.object(cad, 'api', self.fake_build()):
            for kwargs in ({'particles': 0}, {'batches': 1}, {'inactive': 10},
                           {'source_point': [0, 0]}, {'source_point': [0, 0, 'x']},
                           {'seed': 0}):
                with self.assertRaises(business.MCInputError):
                    cad.build_dagmc_model(
                        case, [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}], **kwargs)
            value = cad.build_dagmc_model(
                case, [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}],
                source_point=[0.5, 0.0, 0.0], seed=7)
        self.assertEqual(value['source_point'], [0.5, 0.0, 0.0])

    def test_build_dagmc_model_refuses_missing_products_and_overwrite(self):
        with patch.object(cad, 'api', self.fake_build()):
            with self.assertRaises(business.MCInputError):
                cad.build_dagmc_model('nope', [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}])
            bare = business.WORKSPACE / 'bare'
            bare.mkdir()
            with self.assertRaises(business.MCInputError):
                cad.build_dagmc_model('bare', [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}])
        case = self.make_converted()
        (business.WORKSPACE / case / 'model.xml').write_text('<model/>')
        with patch.object(cad, 'api', self.fake_build()):
            with self.assertRaises(business.MCInputError) as caught:
                cad.build_dagmc_model(case, [{'name': 'steel', 'density': 7.8, 'units': 'g/cm3'}])
        self.assertIn('不覆盖', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
