"""语义校验离线测试（v2）：统一 problems 定位清单——核素/S(a,b)可用性、密度/温度、tally filter 规则。
合法值集合与缺陷样本对照安装版 OpenMC 0.16.0 源码内省结果构造。"""
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


def materials_xml(nuclides, sab=(), macroscopic=False,
                  density='<density value="10.0" units="g/cc"/>', temperature=None):
    if macroscopic:
        body = '<material id="1"><macroscopic name="synthetic_material"/></material>'
    else:
        temp_attr = f' temperature="{temperature}"' if temperature is not None else ''
        entries = density + ''.join(f'<nuclide name="{n}" ao="1.0"/>' for n in nuclides)
        entries += ''.join(f'<sab name="{s}"/>' for s in sab)
        body = f'<material id="1" name="fuel"{temp_attr}>{entries}</material>'
    return '<materials>' + body + '</materials>'


def tallies_xml(definitions, references=''):
    """OpenMC 0.16.0 真实结构（引擎源码实证）：顶层 <filter id type> 定义；
    tally 内 <filters>id…</filters>（复数）为整数 id 引用。"""
    ref = f'<filters>{references}</filters>' if references else ''
    return ('<tallies>' + definitions +
            f'<tally id="7" name="t1">{ref}<scores>flux</scores></tally></tallies>')


# energyfunction 的子元素标签用拼接构造，避免源码里出现完整字面量（工具链转义问题）
FUNCTION_FILTER = ('<filter id="5" type="energyfunction">'
                   '<func' 'tion>1.0 2.0</func' 'tion>'
                   '</filter>')


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

    def kinds(self, value, severity='error'):
        return [p['kind'] for p in value['problems'] if p['severity'] == severity]

    # ---- 核素/S(a,b) 可用性（第一刀能力，v2 形态） ----

    def test_known_nuclide_and_sab_pass_with_index_source(self):
        sab = (KNOWN_SAB,) if KNOWN_SAB else ()
        self.write_materials([KNOWN_NUCLIDE, 'U235'], sab=sab)
        value = self.semantic()
        self.assertTrue(value['checked'])
        self.assertTrue(value['ok'], value)
        self.assertTrue(value['complete'])
        self.assertEqual(value['index_source'], 'deployment_config_cross_sections')
        self.assertEqual(value['library_key'], 'official-endfb-vii.1')
        self.assertEqual(value['missing_nuclides'], [])
        self.assertEqual(value['problems'], [])

    def test_missing_nuclide_reported_in_unified_problems(self):
        self.write_materials([KNOWN_NUCLIDE, 'Zz999'])
        value = self.semantic()
        self.assertFalse(value['ok'])
        self.assertEqual(value['missing_nuclides'], ['Zz999'])
        problem = next(p for p in value['problems'] if p['kind'] == 'missing_nuclide')
        self.assertEqual(problem['severity'], 'error')
        self.assertIn('fuel', problem['location'])
        self.assertIn('Zz999', problem['detail'])
        # 语义缺失不进structure problems：两层结论分开呈现
        self.assertTrue(tools.validate_inputs('case')['structure_valid'])

    def test_missing_sab_is_reported(self):
        self.write_materials([KNOWN_NUCLIDE], sab=('c_Imaginary_In_Unobtainium',))
        value = self.semantic()
        self.assertFalse(value['ok'])
        self.assertEqual(value['missing_sab'], ['c_Imaginary_In_Unobtainium'])

    def test_no_index_skips_nuclide_check_but_static_checks_run(self):
        with patch.object(tools, 'ROOT', self.root / 'empty'), \
             patch.object(nuclear_data, 'ROOT', self.root / 'empty'):
            self.cfg.pop('cross_sections')
            self.config.write_text(json.dumps(self.cfg))
            self.write_materials(['Zz999'], density='<density value="-1.0" units="g/cc"/>')
            value = self.semantic()
        self.assertTrue(value['checked'])
        self.assertFalse(value['complete'])
        self.assertIn('未找到截面索引', value['nuclide_availability']['skipped_reason'])
        self.assertNotIn('missing_nuclides', value)
        # 静态检查照常执行：负密度仍被报出
        self.assertIn('density_value', self.kinds(value))

    # ---- 密度/温度（第二刀） ----

    def test_negative_density_reported_with_engine_evidence_note(self):
        self.write_materials([KNOWN_NUCLIDE], density='<density value="-1.0" units="g/cc"/>')
        value = self.semantic()
        self.assertFalse(value['ok'])
        problem = next(p for p in value['problems'] if p['kind'] == 'density_value')
        self.assertEqual(problem['location'], 'material fuel density')
        self.assertIn('positive density', problem['detail'])

    def test_zero_and_unparseable_density_reported(self):
        self.write_materials([KNOWN_NUCLIDE], density='<density value="0" units="g/cc"/>')
        self.assertIn('density_value', self.kinds(self.semantic()))
        self.write_materials([KNOWN_NUCLIDE], density='<density value="abc" units="g/cc"/>')
        self.assertIn('density_value', self.kinds(self.semantic()))
        self.write_materials([KNOWN_NUCLIDE], density='')
        self.assertIn('density_value', self.kinds(self.semantic()))

    def test_bad_density_units_reported(self):
        self.write_materials([KNOWN_NUCLIDE], density='<density value="1.0" units="lb/ft3"/>')
        value = self.semantic()
        self.assertIn('density_units', self.kinds(value))

    def test_sum_units_with_value_is_warning_not_error(self):
        self.write_materials([KNOWN_NUCLIDE], density='<density value="1.0" units="sum"/>')
        value = self.semantic()
        self.assertTrue(value['ok'])
        self.assertIn('density_value', self.kinds(value, severity='warning'))

    def test_negative_temperature_reported(self):
        self.write_materials([KNOWN_NUCLIDE], temperature='-300')
        value = self.semantic()
        self.assertIn('temperature', self.kinds(value))
        self.assertIn('fuel', value['problems'][0]['location'])

    # ---- tally filter（第二刀：定义 + 引用两层） ----

    def write_tallies(self, definitions, references=''):
        (self.case / 'tallies.xml').write_text(tallies_xml(definitions, references))

    def test_valid_tally_filters_pass_with_counts(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies(
            '<filter id="1" type="energy"><bins>0.0 1.0 10.0e6</bins></filter>'
            '<filter id="2" type="cell"><bins>1 2</bins></filter>'
            '<filter id="3" type="particle"><bins>neutron</bins></filter>',
            references='1 2 3')
        value = self.semantic()
        self.assertTrue(value['ok'], value['problems'])
        self.assertEqual(value['tally_section'],
                         {'performed': True, 'tallies': 1,
                          'filter_definitions': 3, 'filter_references': 3})

    def test_unknown_filter_type_reported(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies('<filter id="1" type="energ"><bins>1.0</bins></filter>')
        value = self.semantic()
        self.assertIn('filter_type', self.kinds(value))
        problem = value['problems'][0]
        self.assertIn('filter id=1', problem['location'])
        self.assertIn('api_check', problem['detail'])

    def test_missing_type_and_empty_bins_reported(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies('<filter id="1"><bins>1</bins></filter>')
        self.assertIn('filter_type', self.kinds(self.semantic()))
        self.write_tallies('<filter id="1" type="cell"><bins>  </bins></filter>')
        self.assertIn('filter_bins', self.kinds(self.semantic()))

    def test_energy_bins_must_increase(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies('<filter id="1" type="energy"><bins>10.0 1.0</bins></filter>')
        value = self.semantic()
        self.assertIn('filter_bins_values', self.kinds(value))
        self.assertIn('递增', value['problems'][0]['detail'])

    def test_mu_bounds_and_id_integer_rules(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies('<filter id="1" type="mu"><bins>-2.0 0.5</bins></filter>')
        self.assertIn('filter_bins_values', self.kinds(self.semantic()))
        self.write_tallies('<filter id="1" type="cell"><bins>1 abc</bins></filter>')
        value = self.semantic()
        self.assertIn('filter_bins_values', self.kinds(value))
        self.assertIn('abc', value['problems'][0]['detail'])

    def test_energyfunction_filter_needs_no_bins(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies(FUNCTION_FILTER)
        value = self.semantic()
        self.assertTrue(value['ok'], value['problems'])
        self.assertEqual(value['tally_section']['filter_definitions'], 1)

    def test_dangling_filter_reference_reported(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies('<filter id="1" type="energy"><bins>0.0 1.0</bins></filter>',
                           references='1 42')
        value = self.semantic()
        self.assertIn('filter_reference', self.kinds(value))
        problem = next(p for p in value['problems'] if p['kind'] == 'filter_reference')
        self.assertIn('42', problem['detail'])
        self.assertIn('t1', problem['location'])

    def test_non_integer_filter_reference_reported(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies('<filter id="1" type="energy"><bins>0.0 1.0</bins></filter>',
                           references='abc')
        value = self.semantic()
        self.assertIn('filter_reference', self.kinds(value))
        self.assertIn('整数', value['problems'][0]['detail'])

    def test_stray_singular_filter_in_tally_reported(self):
        # 引擎只读复数 <filters>；tally 内单数 <filter> 会被静默忽略——须报出
        self.write_materials([KNOWN_NUCLIDE])
        (self.case / 'tallies.xml').write_text(
            '<tallies><tally id="7" name="t1">'
            '<filter type="energy"><bins>0.0 1.0</bins></filter>'
            '<scores>flux</scores></tally></tallies>')
        value = self.semantic()
        self.assertIn('filter_placement', self.kinds(value))
        self.assertIn('忽略', value['problems'][0]['detail'])

    def test_filter_definition_missing_id_is_warning(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.write_tallies('<filter type="energy"><bins>0.0 1.0</bins></filter>')
        value = self.semantic()
        self.assertTrue(value['ok'])
        self.assertIn('filter_id', self.kinds(value, severity='warning'))

    # ---- 跳过与形态 ----

    def test_multi_group_runs_tally_checks_but_skips_material_checks(self):
        (self.case / 'settings.xml').write_text(
            '<settings><energy_mode>multi-group</energy_mode></settings>')
        self.write_materials(['U235'], density='')
        self.write_tallies('<filter type="energ"><bins>1</bins></filter>')
        value = self.semantic()
        self.assertTrue(value['checked'])
        self.assertFalse(value['complete'])
        self.assertFalse(value['material_section']['performed'])
        self.assertIn('多群', value['material_section']['skipped_reason'])
        self.assertIn('多群', value['nuclide_availability']['skipped_reason'])
        # 材料级缺陷不报（体系不同），但tally filter照常检查
        self.assertNotIn('density_value', self.kinds(value))
        self.assertIn('filter_type', self.kinds(value))
        self.assertEqual(value['tally_section']['performed'], True)

    def test_macroscopic_material_skips_material_checks(self):
        self.write_materials([], macroscopic=True)
        value = self.semantic()
        self.assertTrue(value['checked'])
        self.assertFalse(value['material_section']['performed'])
        self.assertIn('macroscopic', value['material_section']['skipped_reason'])

    def test_missing_materials_file_skips_semantic(self):
        (self.case / 'geometry.xml').write_text('<geometry/>')
        result = tools.validate_inputs('case', semantic_check=True)
        self.assertFalse(result['structure_valid'])
        self.assertFalse(result['semantic']['checked'])

    def test_semantic_absent_unless_requested(self):
        self.write_materials([KNOWN_NUCLIDE])
        self.assertNotIn('semantic', tools.validate_inputs('case'))

    def test_env_var_index_source_is_used(self):
        self.cfg.pop('cross_sections')
        self.config.write_text(json.dumps(self.cfg))
        self.write_materials([KNOWN_NUCLIDE])
        with patch.dict(os.environ, {'OPENMC_CROSS_SECTIONS': str(PINNED)}):
            value = self.semantic()
        self.assertEqual(value['index_source'], 'env_OPENMC_CROSS_SECTIONS')
        self.assertTrue(value['ok'])


if __name__ == '__main__':
    unittest.main()
