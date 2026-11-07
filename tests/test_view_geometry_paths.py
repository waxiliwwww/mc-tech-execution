"""几何切片图：多群算例可出图，且不修改算例输入。

功能：openmc_view_geometry 的多群(多群截面h5)路径与连续能路径
类别：回归（旧行为：多群算例在SDK导出后由引擎中止，
      报 'No macroscopic data or nuclides specified on material 1'，用户拿到的信息无法行动）
前置：设置MC_TEST_OPENMC_CONFIG指向可用的OpenMC运行时；未设置时skip（skip不算通过）
输入：synthetic-mgxs 模板（多群，单材料盒）与 control-rod 模板（连续能）各建一个算例
预期：多群走 render_method=engine_native_plot_xml 并真的产出PNG；连续能仍走 python_api_export；
      两种路径都不改动算例输入文件（materials/geometry/settings 摘要不变）
不证明：不证明图像内容与几何一一对应（只验证能出图）；不证明算例本身物理正确
"""
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

RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')
INPUTS = ('materials.xml', 'geometry.xml', 'settings.xml')


def digest(directory):
    return {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in INPUTS if (directory / name).is_file()}


@unittest.skipUnless(RUNTIME, 'MC_TEST_OPENMC_CONFIG启用真实OpenMC几何绘图')
class ViewGeometryPaths(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc view geometry ')
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

    def case(self, name, template):
        tools.create_model(name, template, 20, 4, 2)
        return business.WORKSPACE / name

    def test_multigroup_case_renders_through_engine(self):
        directory = self.case('mg', 'synthetic-mgxs')
        before = digest(directory)
        value = tools.view_geometry('mg', 'xy', pixels=100)
        self.assertEqual(value['render_method'], 'engine_native_plot_xml', value.get('note'))
        image = business.WORKSPACE / value['path']
        self.assertTrue(image.is_file())
        self.assertEqual(image.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
        self.assertFalse(value['input_files_modified'])
        self.assertEqual(digest(directory), before)

    def test_continuous_energy_case_keeps_api_path(self):
        directory = self.case('ce', 'control-rod')
        before = digest(directory)
        value = tools.view_geometry('ce', 'xy', pixels=100)
        self.assertEqual(value['render_method'], 'python_api_export', value.get('note'))
        self.assertTrue((business.WORKSPACE / value['path']).is_file())
        self.assertEqual(digest(directory), before)


if __name__ == '__main__':
    unittest.main()