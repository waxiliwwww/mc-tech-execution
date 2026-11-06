"""几何检查离线测试：stub引擎（bash脚本）按MOCK_GEOM_MODE返回固定输出与退出码，
输出格式复刻引擎0.16.0实测样本（重叠报错/CELL OVERLAP CHECK SUMMARY）。"""
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

PINNED = ROOT / 'config/data-index/endfb-vii.1.xml'

STUB = r"""#!/bin/bash
if [ -n "$MOCK_ARGS_FILE" ]; then echo "$@" >> "$MOCK_ARGS_FILE"; fi
for a in "$@"; do
  if [ "$a" = "--version" ]; then echo "OpenMC 0.16.0 (stub)"; exit 0; fi
done
case "$MOCK_GEOM_MODE" in
  overlap)
    echo " WARNING: Cell overlap checking is ON." >&2
    echo " ERROR: Overlapping cells detected: 2, 1 on universe 0" >&2
    exit 255;;
  clean)
    echo "==================>     CELL OVERLAP CHECK SUMMARY     <==================="
    echo " Cell ID   No. Overlap Checks"
    echo " -------   ------------------"
    echo "        1                  1234"
    echo "        2                  1188"
    echo " There were 0 cells with less than 10 overlap checks"
    exit 0;;
  crash)
    echo " ERROR: Cross sections not found" >&2
    exit 1;;
  timeout)
    sleep 8
    exit 0;;
esac
exit 0
"""

MATERIALS = ('<materials><material id="1" name="m">'
             '<density value="1.0" units="g/cc"/><nuclide name="U235" ao="1.0"/>'
             '</material></materials>')
GEOM = ('<geometry><surface id="1" type="sphere" coeffs="0.0 0.0 0.0 1.0" boundary="vacuum"/>'
        '<cell id="1" material="1" region="-1"/></geometry>')
SETTINGS = ('<settings><run_mode>eigenvalue</run_mode><particles>100</particles>'
            '<batches>10</batches><inactive>2</inactive></settings>')
MODEL = ('<model><settings><run_mode>eigenvalue</run_mode><particles>50</particles>'
         '<batches>10</batches><inactive>2</inactive></settings>'
         '<materials><material id="1" name="m"><density value="1.0" units="g/cc"/>'
         '<nuclide name="U235" ao="1.0"/></material></materials>'
         '<geometry><surface id="1" type="sphere" coeffs="0.0 0.0 0.0 1.0" boundary="vacuum"/>'
         '<cell id="1" material="1" region="-1"/></geometry></model>')


class GeometryCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc geomcheck ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.stub = self.root / 'stub-openmc.sh'
        self.stub.write_text(STUB, encoding='utf-8')
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        self.cfg.update(data_kind='mock_process_fixture',
                        openmc_command=['/bin/bash', str(self.stub)],
                        cross_sections=str(PINNED))
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.cfg))
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE), 'MC_OPENMC_CONFIG': str(self.config)})
        self.env_patch.start()
        self.case = business.WORKSPACE / 'case'
        self.case.mkdir()
        (self.case / 'materials.xml').write_text(MATERIALS, encoding='utf-8')
        (self.case / 'geometry.xml').write_text(GEOM, encoding='utf-8')
        (self.case / 'settings.xml').write_text(SETTINGS, encoding='utf-8')

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def run_check(self, mode, **flags):
        with patch.dict(os.environ, {'MOCK_GEOM_MODE': mode}):
            return tools.validate_inputs('case', geometry_check=True, **flags)

    def test_overlap_detected_with_cells_and_universe(self):
        value = self.run_check('overlap')
        geometry = value['geometry']
        self.assertTrue(geometry['checked'])
        self.assertTrue(geometry['completed'])
        self.assertFalse(geometry['ok'])
        self.assertEqual(geometry['returncode'], 255)
        self.assertEqual(geometry['overlapping_cells'], [{'cells': [2, 1], 'universe': 0}])
        self.assertIn('+openmc_geometry_debug', value['validation_level'])
        self.assertIn('概率', geometry['note'])
        self.assertTrue(geometry['probabilistic'])
        self.assertTrue(geometry['does_not_prove_no_overlap'])
        self.assertIn('Overlapping cells', geometry['log_tail'])

    def test_clean_run_parses_summary_and_adjusts_settings(self):
        value = self.run_check('clean')
        geometry = value['geometry']
        self.assertTrue(geometry['ok'], geometry['log_tail'])
        self.assertTrue(geometry['probabilistic'])
        self.assertTrue(geometry['does_not_prove_no_overlap'])
        self.assertEqual(geometry['overlap_check_summary']['cells'],
                         [{'cell': 1, 'checks': 1234}, {'cell': 2, 'checks': 1188}])
        self.assertEqual(geometry['overlap_check_summary']['low_check'],
                         {'cells': 0, 'min_checks': 10})
        self.assertTrue(geometry['settings_adjusted']['batches'].endswith('→2'))
        self.assertIn('inactive', geometry['settings_adjusted'])

    def test_case_files_are_not_modified_and_temp_dir_removed(self):
        self.run_check('clean')
        self.assertEqual((self.case / 'settings.xml').read_text(encoding='utf-8'), SETTINGS)
        leftovers = [p.name for p in business.WORKSPACE.iterdir()
                     if p.name.startswith('mc-geomcheck-')]
        self.assertEqual(leftovers, [])

    def test_crash_without_overlap_detail_is_reported_not_hidden(self):
        geometry = self.run_check('crash')['geometry']
        self.assertFalse(geometry['ok'])
        self.assertEqual(geometry['returncode'], 1)
        self.assertEqual(geometry['overlapping_cells'], [])
        self.assertIn('log_tail', geometry['reason'])
        self.assertIn('Cross sections not found', geometry['log_tail'])

    def test_timeout_reported_as_incomplete(self):
        self.cfg['geometry_check_timeout_seconds'] = 1
        self.config.write_text(json.dumps(self.cfg))
        value = self.run_check('timeout')
        geometry = value['geometry']
        self.assertTrue(geometry['checked'])
        self.assertFalse(geometry['completed'])
        self.assertIsNone(geometry['ok'])
        self.assertIn('超时', geometry['reason'])
        self.assertNotIn('+openmc_geometry_debug', value['validation_level'])

    def test_engine_unavailable_skips_honestly(self):
        self.cfg['openmc_command'] = ['/nonexistent/openmc']
        self.config.write_text(json.dumps(self.cfg))
        geometry = self.run_check('clean')['geometry']
        self.assertFalse(geometry['checked'])
        self.assertIn('探测失败', geometry['skipped_reason'])

    def test_structure_problem_skips_geometry(self):
        (self.case / 'settings.xml').write_text(
            '<settings><batches>0</batches></settings>', encoding='utf-8')
        value = self.run_check('clean')
        self.assertFalse(value['structure_valid'])
        self.assertFalse(value['geometry']['checked'])
        self.assertIn('结构检查未通过', value['geometry']['skipped_reason'])

    def test_plot_mode_skipped(self):
        (self.case / 'settings.xml').write_text(
            '<settings><run_mode>plot</run_mode></settings>', encoding='utf-8')
        geometry = self.run_check('clean')['geometry']
        self.assertFalse(geometry['checked'])
        self.assertIn('不适用', geometry['skipped_reason'])

    def test_model_xml_layout_and_arguments(self):
        for name in ('materials.xml', 'geometry.xml', 'settings.xml'):
            (self.case / name).unlink()
        (self.case / 'model.xml').write_text(MODEL, encoding='utf-8')
        args_file = self.root / 'args.txt'
        with patch.dict(os.environ, {'MOCK_GEOM_MODE': 'clean',
                                     'MOCK_ARGS_FILE': str(args_file)}):
            value = tools.validate_inputs('case', geometry_check=True)
        self.assertTrue(value['geometry']['ok'], value['geometry'])
        recorded = args_file.read_text(encoding='utf-8')
        self.assertIn('-g model.xml', recorded)

    def test_validation_level_composition_with_semantic(self):
        value = self.run_check('clean', semantic_check=True)
        self.assertEqual(value['validation_level'],
                         'xml_structure_and_basic_settings+static_semantics+openmc_geometry_debug')
        self.assertTrue(value['semantic']['ok'])


if __name__ == '__main__':
    unittest.main()
