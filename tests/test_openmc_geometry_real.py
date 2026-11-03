"""真实引擎门控测试：openmc -g 几何重叠检查（需 MC_TEST_OPENMC_CONFIG 指向真实运行配置）。
重叠算例=两同心球同universe均填材料（引擎实测报 Overlapping cells detected: 2, 1）；
干净算例=单球真空边界。两者都用 U235 连续能材料（受管缓存索引覆盖）。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = os.environ.get('MC_TEST_OPENMC_CONFIG')

MATERIALS = ('<materials><material id="1" name="u">'
             '<density value="10.0" units="g/cc"/><nuclide name="U235" ao="1.0"/>'
             '</material></materials>')
# 引擎0.16.0实测：球面XML用coeffs="x y z r"（x/y/z/r简写属性会被拒绝）
OVERLAP_GEOMETRY = ('<geometry>'
                    '<surface id="1" type="sphere" coeffs="0.0 0.0 0.0 1.0"/>'
                    '<surface id="2" type="sphere" coeffs="0.0 0.0 0.0 2.0" boundary="vacuum"/>'
                    '<cell id="1" universe="0" material="1" region="-1"/>'
                    '<cell id="2" universe="0" material="1" region="-2"/>'
                    '</geometry>')
CLEAN_GEOMETRY = ('<geometry>'
                  '<surface id="1" type="sphere" coeffs="0.0 0.0 0.0 6.0" boundary="vacuum"/>'
                  '<cell id="1" universe="0" material="1" region="-1"/>'
                  '</geometry>')
SETTINGS = ('<settings><run_mode>eigenvalue</run_mode><particles>50</particles>'
            '<batches>10</batches><inactive>2</inactive></settings>')


@unittest.skipUnless(RUNTIME, '真实OpenMC测试未启用：设置MC_TEST_OPENMC_CONFIG为运行配置的绝对路径')
class RealGeometryCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='real geometry check ')
        self.root = Path(self.temp.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(('MCP_', 'MC_', 'HARNESS_'))}
        self.env.update(MC_OPENMC_CONFIG=str(Path(RUNTIME).resolve()),
                        MC_WORKSPACE=str(self.workspace))

    def tearDown(self):
        self.temp.cleanup()

    def validate(self, case, **flags):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'mcp/cli.py'), 'call', 'openmc_validate_inputs',
             json.dumps({'case_dir': case, **flags})],
            env=self.env, text=True, capture_output=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def write_case(self, name, geometry):
        case = self.workspace / name
        case.mkdir()
        (case / 'materials.xml').write_text(MATERIALS, encoding='utf-8')
        (case / 'geometry.xml').write_text(geometry, encoding='utf-8')
        (case / 'settings.xml').write_text(SETTINGS, encoding='utf-8')
        return case

    def test_overlap_case_is_rejected_with_cells(self):
        self.write_case('overlap', OVERLAP_GEOMETRY)
        value = self.validate('overlap', geometry_check=True)
        geometry = value['geometry']
        self.assertTrue(geometry['checked'])
        self.assertTrue(geometry['completed'])
        self.assertFalse(geometry['ok'])
        self.assertEqual(geometry['returncode'], 255)
        self.assertTrue(geometry['overlapping_cells'], geometry['log_tail'])
        first = geometry['overlapping_cells'][0]
        self.assertEqual(sorted(first['cells']), [1, 2])
        self.assertEqual(first['universe'], 0)
        self.assertIn('+openmc_geometry_debug', value['validation_level'])
        self.assertIn('概率', geometry['note'])

    def test_clean_case_passes_probabilistic_check(self):
        case = self.write_case('clean', CLEAN_GEOMETRY)
        before = (case / 'settings.xml').read_text(encoding='utf-8')
        value = self.validate('clean', geometry_check=True)
        geometry = value['geometry']
        self.assertTrue(geometry['ok'], geometry.get('log_tail'))
        self.assertEqual(geometry['returncode'], 0)
        summary = geometry['overlap_check_summary']
        self.assertTrue(summary and summary['cells'])
        self.assertTrue(all(row['checks'] > 0 for row in summary['cells']), summary)
        # 原算例文件必须原样不动（批次削减只发生在临时副本）
        self.assertEqual((case / 'settings.xml').read_text(encoding='utf-8'), before)
        self.assertTrue(geometry['settings_adjusted']['batches'].endswith('→2'))
        self.assertNotIn('reason', geometry)


if __name__ == '__main__':
    unittest.main()
