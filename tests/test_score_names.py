"""score名单：提交前拒绝未知计分量，并在静态语义校验里定位。

功能：openmc_scores（config/score-names.json 名单）、openmc_define_tally 的score校验、
      openmc_validate_inputs(semantic_check=true) 对 tallies.xml <scores> 的检查
类别：反例为主 + 正例对照（回归：旧行为把未知score一路放行，运行期才以
      'Unknown reaction name' 终止，用户只看到算例跑挂）
前置：无。离线执行：不启动MCP服务、不跑OpenMC；名单取自随包 config/score-names.json
输入：见各 test_ 方法（'fluxx' / 'flux (n,gamma)' / valid score 对照）
预期：未知score在提交前抛Error并给出最接近的合法名；语义校验把 invalid_score 记为 error
      并定位到 tally；合法score无 invalid_score 问题
不证明：不证明名单覆盖引擎的全部别名写法；不证明计分定义物理正确，也不证明结果可验收
"""
import json
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
import openmc_scores
import openmc_tools as tools

PINNED = ROOT / 'config/data-index/endfb-vii.1.xml'


def index_nuclide():
    """取随包固定索引里真实存在的核素名，避免语义校验因子虚核素先报错。"""
    for entry in ET.parse(PINNED).getroot().iter('library'):
        names = (entry.get('materials') or '').split()
        if entry.get('type') == 'neutron' and names:
            return names[0]
    return 'H1'


KNOWN_NUCLIDE = index_nuclide()

MATERIALS = ('<materials><material id="1" name="fuel">'
             '<density value="10.0" units="g/cc"/>'
             f'<nuclide name="{KNOWN_NUCLIDE}" ao="1.0"/>'
             '</material></materials>')


class ScoreNames(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc score names ')
        self.root = Path(self.temp.name)
        self.previous_workspace = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
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
        (self.case / 'materials.xml').write_text(MATERIALS)
        (self.case / 'geometry.xml').write_text('<geometry/>')

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    def write_tallies(self, scores):
        (self.case / 'tallies.xml').write_text(
            '<tallies><tally id="7" name="t7"><scores>' + scores + '</scores></tally></tallies>')

    def semantic(self):
        return tools.validate_inputs('case', semantic_check=True)['semantic']

    # ---- 名单本身 ----

    def test_catalog_accepts_engine_names_and_reports_source(self):
        value = openmc_scores.describe()
        self.assertTrue(value['checked'], value)
        self.assertEqual(value['source'], 'config/score-names.json')
        known = openmc_scores.known()
        for name in ('flux', 'fission', 'absorption', '(n,gamma)', 'heating'):
            self.assertIn(name, known)
        self.assertGreater(len(known), 100)
        self.assertIsNone(openmc_scores.unavailable_reason())

    def test_close_match_is_suggested(self):
        unknown, hints = openmc_scores.check(['fluxx'])
        self.assertEqual(unknown, ['fluxx'])
        self.assertIn('flux', hints['fluxx'])

    # ---- define_tally：提交前拒绝 ----

    def test_define_tally_rejects_unknown_score_with_suggestion(self):
        with self.assertRaises(tools.Error) as caught:
            tools.define_tally('case', ['fluxx'])
        message = str(caught.exception)
        self.assertIn('fluxx', message)
        self.assertIn('flux', message)
        # 拒绝发生在写盘之前：算例里不留下半成品tally文件
        self.assertFalse((self.case / 'tallies.xml').exists())

    # ---- validate_inputs(semantic_check)：静态定位 ----

    def test_semantic_check_flags_unknown_score_in_tally(self):
        self.write_tallies('flux fluxx')
        value = self.semantic()
        self.assertFalse(value['ok'])
        problem = next(p for p in value['problems'] if p['kind'] == 'invalid_score')
        self.assertEqual(problem['severity'], 'error')
        self.assertIn('t7', problem['location'])
        self.assertIn('fluxx', problem['detail'])
        self.assertIn('flux', problem['detail'])
        self.assertEqual(value['tally_section']['scores_checked'], 2)
        self.assertTrue(value['tally_section']['score_catalog']['checked'])

    def test_semantic_check_passes_valid_scores(self):
        self.write_tallies('flux (n,gamma) heating')
        value = self.semantic()
        self.assertTrue(value['ok'], value['problems'])
        self.assertEqual([p for p in value['problems'] if p['kind'] == 'invalid_score'], [])
        self.assertEqual(value['tally_section']['scores_checked'], 3)


if __name__ == '__main__':
    unittest.main()