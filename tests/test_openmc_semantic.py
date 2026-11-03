"""语义校验第一刀离线测试：核素/S(a,b)名对照生效截面索引；全部使用固定离线索引与临时工作区。"""
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
import nuclear_data
import openmc_tools as tools
import xml.etree.ElementTree as ET

PINNED = ROOT / 'config/data-index/endfb-vii.1.xml'


def index_names():
    """从随包固定索引取真实存在的核素与S(a,b)名，避免测试硬编码猜测。"""
    neutron, thermal = [], []
    for entry in ET.parse(PINNED).getroot().iter('library'):
        names = (entry.get('materials') or '').split()
        if entry.get('type') == 'neutron' and names:
            neutron.extend(names)
        elif entry.get('type') == 'thermal' and names:
            thermal.extend(names)
    return neutron[0], (thermal[0] if thermal else None)


KNOWN_NUCLIDE, KNOWN_SAB = index_names()


def materials_xml(nuclides, sab=(), macroscopic=False):
    body = []
    if macroscopic:
        body.append('<material id="1"><macroscopic name="synthetic_material"/></material>')
    else:
        entries = ''.join(f'<nuclide name="{n}" ao="1.0"/>' for n in nuclides)
        entries += ''.join(f'<sab name="{s}"/>' for s in sab)
        body.append(f'<material id="1" name="fuel">{entries}</material>')
    return '<materials>' + ''.join(body) + '</materials>'


class SemanticValidation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc semantic ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        # 显式cross_sections指向随包固定索引：跨机器确定，不依赖受管缓存是否已初始化。
        self.cfg.update(data_kind='mock_process_fixture', cross_sections=str(PINNED))
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.cfg))
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE), 'MC_OPENMC_CONFIG': str(self.config)})
        self.env_patch.start()
        os.environ.pop('OPENMC_CROSS_SECTIONS', None)
        self.case = business.WORKSPACE / 'case'
        self.case.mkdir()
        (self.case / 'settings.xml').write_text('<settings><run_mode>eigenvalue</run_mode></settings>')

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def write_materials(self, *args, **kwargs):
        (self.case / 'materials.xml').write_text(materials_xml(*args, **kwargs))
        (self.case / 'geometry.xml').write_text('<geometry/>')

    def semantic(self, **flags):
        return tools.validate_inputs('case', semantic_check=True, **flags)['semantic']

    def test_known_nuclide_and_sab_pass_with_index_source(self):
        sab = (KNOWN_SAB,) if KNOWN_SAB else ()
        self.write_materials([KNOWN_NUCLIDE, 'U235'], sab=sab)
        value = self.semantic()
        self.assertTrue(value['checked'])
        self.assertTrue(value['ok'], value)
        self.assertEqual(value['index_source'], 'deployment_config_cross_sections')
        self.assertEqual(value['library_key'], 'official-endfb-vii.1')
        self.assertEqual(value['missing_nuclides'], [])

    def test_missing_nuclide_is_reported_with_material_label(self):
        self.write_materials([KNOWN_NUCLIDE, 'Zz999'])
        value = self.semantic()
        self.assertTrue(value['checked'])
        self.assertFalse(value['ok'])
        self.assertEqual(value['missing_nuclides'], ['Zz999'])
        self.assertEqual(value['missing_detail'][0]['material'], 'fuel')
        # 语义缺失不进structure problems：两层结论分开呈现
        self.assertTrue(tools.validate_inputs('case')['structure_valid'])

    def test_missing_sab_is_reported(self):
        self.write_materials([KNOWN_NUCLIDE], sab=('c_Imaginary_In_Unobtainium',))
        value = self.semantic()
        self.assertFalse(value['ok'])
        self.assertEqual(value['missing_sab'], ['c_Imaginary_In_Unobtainium'])

    def test_multi_group_mode_skips_with_reason(self):
        (self.case / 'settings.xml').write_text(
            '<settings><energy_mode>multi-group</energy_mode></settings>')
        self.write_materials(['U235'])
        value = self.semantic()
        self.assertFalse(value['checked'])
        self.assertIn('多群', value['skipped_reason'])

    def test_macroscopic_material_skips_with_reason(self):
        self.write_materials([], macroscopic=True)
        value = self.semantic()
        self.assertFalse(value['checked'])
        self.assertIn('macroscopic', value['skipped_reason'])

    def test_no_index_is_honest_skip_not_silent_pass(self):
        with patch.object(tools, 'ROOT', self.root / 'empty'), \
             patch.object(nuclear_data, 'ROOT', self.root / 'empty'):
            self.cfg.pop('cross_sections')
            self.config.write_text(json.dumps(self.cfg))
            self.write_materials(['Zz999'])
            value = self.semantic()
        self.assertFalse(value['checked'])
        self.assertNotIn('ok', value)
        self.assertIn('未找到截面索引', value['skipped_reason'])

    def test_env_var_index_source_is_used(self):
        self.cfg.pop('cross_sections')
        self.config.write_text(json.dumps(self.cfg))
        self.write_materials([KNOWN_NUCLIDE])
        with patch.dict(os.environ, {'OPENMC_CROSS_SECTIONS': str(PINNED)}):
            value = self.semantic()
        self.assertEqual(value['index_source'], 'env_OPENMC_CROSS_SECTIONS')
        self.assertTrue(value['ok'])

    def test_missing_materials_file_skips_semantic(self):
        (self.case / 'geometry.xml').write_text('<geometry/>')
        result = tools.validate_inputs('case', semantic_check=True)
        self.assertFalse(result['structure_valid'])
        self.assertFalse(result['semantic']['checked'])

    def test_semantic_absent_unless_requested(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.assertNotIn('semantic', tools.validate_inputs('case'))


if __name__ == '__main__':
    unittest.main()
