"""几何检查的数据依赖：引用文件要跟着进临时副本，缺文件不能当几何结论。

功能：openmc_validate_inputs(geometry_check=true) 对算例引用数据文件的处理，
      以及 openmc_tools._referenced_data_files 的引用识别
类别：回归 + 反例（旧行为：几何检查只把XML复制进临时目录，多群算例的h5不在副本里，
      引擎报 'Cross section HDF5 file ... does not exist'，结果被记成 ok=false，
      读起来像“几何有问题”）
前置：识别引用文件的用例无需环境；跑引擎的两个用例需设置MC_TEST_OPENMC_CONFIG（未设置skip）
输入：多群算例（materials.xml 以 <cross_sections>元素 引用h5）/ 引用不存在h5的算例 /
      以属性引用h5m的DAGMC片段
预期：引用文件被识别并复制；缺文件时 geometry 给 skipped_reason 与 missing_data_files，
      绝不给 ok=true；多群算例的检查能跑完
不证明：概率性重叠检查通过不等于无重叠；不证明数据文件内容正确
"""
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

RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')

MATERIALS_ELEMENT_REF = '''<materials>
  <cross_sections>absent.h5</cross_sections>
  <material id="1"><density value="1.0" units="macro"/><macroscopic name="m"/></material>
</materials>'''
GEOMETRY = '''<geometry>
  <cell id="1" material="1" region="-1"/>
  <surface id="1" type="sphere" coeffs="0 0 0 5"/>
</geometry>'''
SETTINGS = '''<settings>
  <run_mode>eigenvalue</run_mode>
  <particles>20</particles>
  <batches>4</batches>
  <inactive>2</inactive>
  <energy_mode>multi-group</energy_mode>
</settings>'''


class DataReferenceScan(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc data refs ')
        self.case = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_element_and_attribute_references_are_found(self):
        (self.case / 'materials.xml').write_text(MATERIALS_ELEMENT_REF)
        (self.case / 'geometry.xml').write_text(
            '<geometry><dagmc_universe id="1" filename="dagmc.h5m"/></geometry>')
        (self.case / 'present.h5').write_bytes(b'')
        found, missing = tools._referenced_data_files(self.case, ['materials.xml', 'geometry.xml'])
        self.assertIn('absent.h5', missing)
        self.assertIn('dagmc.h5m', missing)
        (self.case / 'geometry.xml').write_text(
            '<geometry><dagmc_universe id="1" filename="present.h5"/></geometry>')
        found, missing = tools._referenced_data_files(self.case, ['materials.xml', 'geometry.xml'])
        self.assertIn('present.h5', found)
        self.assertEqual(missing, ['absent.h5'])


@unittest.skipUnless(RUNTIME, 'MC_TEST_OPENMC_CONFIG启用真实OpenMC几何检查')
class GeometryCheckDataFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc geometry data ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE),
            'MC_OPENMC_CONFIG': str(Path(RUNTIME).resolve())})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def test_missing_data_file_is_reported_not_a_geometry_verdict(self):
        case = business.WORKSPACE / 'broken'
        case.mkdir()
        (case / 'materials.xml').write_text(MATERIALS_ELEMENT_REF)
        (case / 'geometry.xml').write_text(GEOMETRY)
        (case / 'settings.xml').write_text(SETTINGS)
        geometry = tools.validate_inputs('broken', geometry_check=True)['geometry']
        self.assertFalse(geometry['checked'])
        self.assertNotEqual(geometry.get('ok'), True)
        self.assertIn('absent.h5', geometry['skipped_reason'])
        self.assertEqual(geometry['missing_data_files'], ['absent.h5'])

    def test_multigroup_case_is_checked_with_its_data_file(self):
        tools.create_model('mg', 'synthetic-mgxs', 20, 4, 2)
        geometry = tools.validate_inputs('mg', geometry_check=True)['geometry']
        self.assertTrue(geometry['checked'], geometry)
        self.assertTrue(geometry['completed'], geometry)
        self.assertTrue(geometry['ok'], geometry)
        self.assertEqual(geometry['overlapping_cells'], [])
        self.assertTrue(geometry['probabilistic'])


if __name__ == '__main__':
    unittest.main()