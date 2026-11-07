"""hex-lattice 模板：六角边界覆盖整个7棒栅格，燃料按UO2化学计量。

功能：openmc_create_model(template='hex-lattice') 生成的 geometry/plots/materials
类别：回归（旧行为两处：①反射六角棱柱只有“一个栅距”大，内切圆半径0.75cm，
      7根棒里6根的栅元中心落在边界外，实际只算1根棒且不报错；②O16质量份额0.1184
      对应O/U=1.76，与材料名UO2不符）
前置：设置MC_TEST_OPENMC_CONFIG指向可用的OpenMC运行时配置；未设置时skip（skip不算通过）
输入：hex-lattice，粒子20、批4、非活跃2（仅为生成与几何检查，不做物理结论）
预期：六角边界内切圆半径≥栅格外径(环数×栅距=3.0cm)；O/U≈2；plot宽度覆盖栅格；
      几何调试检查能跑完且无重叠单元明细；旧值0.75cm与O/U=1.76不再出现
不证明：不证明该自测模型可用于工程验收或物理正确；概率性几何检查通过不等于无重叠
"""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_tools as tools

RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')


def plane_distance(coeffs):
    """OpenMC平面：d/|n| 为到原点的距离；x/y/z平面的coeffs只有常数一项。"""
    numbers = [float(v) for v in coeffs.split()]
    if len(numbers) == 1:
        return abs(numbers[0])
    a, b, c, d = numbers
    return abs(d) / math.sqrt(a * a + b * b + c * c)


@unittest.skipUnless(RUNTIME, 'MC_TEST_OPENMC_CONFIG启用真实OpenMC生成与几何检查')
class HexLatticeTemplate(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc hex lattice ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE),
            'MC_OPENMC_CONFIG': str(Path(RUNTIME).resolve())})
        self.env_patch.start()
        self.value = tools.create_model('hex', 'hex-lattice', 20, 4, 2)
        self.case = business.WORKSPACE / 'hex'

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def geometry(self):
        return ET.parse(self.case / 'geometry.xml').getroot()

    def lattice(self):
        lattice = self.geometry().find('hex_lattice')
        self.assertIsNotNone(lattice, 'geometry.xml缺少hex_lattice')
        return lattice

    def outer_radius(self):
        lattice = self.lattice()
        rings = int(lattice.get('n_rings'))
        pitch = float(lattice.find('pitch').text.split()[0])
        return rings * pitch

    def test_reflective_boundary_covers_whole_lattice(self):
        outer = self.outer_radius()
        self.assertAlmostEqual(outer, 3.0, places=6)
        reflective = [surface for surface in self.geometry().findall('surface')
                      if surface.get('boundary') == 'reflective']
        self.assertEqual(len(reflective), 6, [s.get('id') for s in reflective])
        inradius = min(plane_distance(surface.get('coeffs')) for surface in reflective)
        # 反射边界的内切圆半径必须不小于栅格外径，否则最外环栅元被裁掉
        self.assertGreaterEqual(inradius, outer - 1e-9,
                                f'边界内切圆半径{inradius}小于栅格外径{outer}')
        # 负对照：旧模板的0.75cm棱柱不该再出现
        self.assertGreater(inradius, 0.76)
        self.assertAlmostEqual(inradius, 3.0, places=6)

    def test_fuel_is_uo2_stoichiometry(self):
        material = ET.parse(self.case / 'materials.xml').getroot().find('material')
        fractions = {}
        for nuclide in material.findall('nuclide'):
            name = nuclide.get('name')
            if nuclide.get('wo') is not None:
                fractions[name] = float(nuclide.get('wo'))
        oxygen_moles = fractions['O16'] / 15.994915
        uranium_moles = fractions['U235'] / 235.043930 + fractions['U238'] / 238.050788
        ratio = oxygen_moles / uranium_moles
        self.assertAlmostEqual(ratio, 2.0, delta=0.01, msg='O/U=' + format(ratio, '.4f'))
        self.assertNotAlmostEqual(fractions['O16'], 0.1184, places=4)

    def test_plot_width_covers_lattice(self):
        width = float(ET.parse(self.case / 'plots.xml').getroot().find('plot').find('width').text.split()[0])
        self.assertGreaterEqual(width, 2 * self.outer_radius())

    def test_geometry_check_runs_on_generated_model(self):
        value = tools.validate_inputs('hex', geometry_check=True)
        geometry = value['geometry']
        self.assertTrue(geometry['checked'], geometry)
        self.assertTrue(geometry['completed'], geometry)
        self.assertEqual(geometry['overlapping_cells'], [])
        self.assertTrue(geometry['probabilistic'])
        self.assertTrue(geometry['does_not_prove_no_overlap'])


if __name__ == '__main__':
    unittest.main()