"""实跑档：用已配置的CAD环境完成一次真实STEP→h5m→引擎读回；工程验证，不是专业验收。

启用：MC_TEST_CAD_CONFIG 指向含 cad_python_command/cad_library_path 与真实 python_command
（DAGMC版OpenMC环境）的配置文件，例如仓库的 config/openmc.local.json。
夹具 tests/fixtures/cube.step 为 cadquery 生成的 10 mm 立方体（来源与指纹见 docs/adr/0002）。
"""
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_cad as cad


@unittest.skipUnless(os.environ.get('MC_TEST_CAD_CONFIG'),
                     '真实CAD转换需MC_TEST_CAD_CONFIG指向含cad_python_command的配置')
class CadRealConversion(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc cad real ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        previous = {key: os.environ.get(key) for key in ('MC_WORKSPACE', 'MC_OPENMC_CONFIG')}

        def restore():
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        self.addCleanup(restore)
        os.environ['MC_WORKSPACE'] = str(business.WORKSPACE)
        os.environ['MC_OPENMC_CONFIG'] = os.environ['MC_TEST_CAD_CONFIG']
        shutil.copy(ROOT / 'tests/fixtures/cube.step', business.WORKSPACE / 'cube.step')

    def tearDown(self):
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def test_inspect_convert_and_engine_read(self):
        inspected = cad.inspect_step('cube.step')
        self.assertEqual(inspected['solids'], 1)
        self.assertIsNotNone(inspected['bounding_box'])
        # 10 mm 立方体按 0.1 缩放为 1 cm，OpenMC 读回厘米制包围盒 ±0.5。
        value = cad.convert_cad('cube.step', 'cube-dagmc', ['steel'], scale_factor=0.1)
        self.assertTrue(value['engine_read']['ok'], value['engine_read'])
        box = value['engine_read']['bounding_box']
        for axis in range(3):
            self.assertAlmostEqual(box['lower_left'][axis], -0.5, places=6)
            self.assertAlmostEqual(box['upper_right'][axis], 0.5, places=6)
        self.assertEqual(value['provenance']['data_kind'], 'cad_conversion_unverified')
        self.assertFalse(value['professional_acceptance'])


if __name__ == '__main__':
    unittest.main()
