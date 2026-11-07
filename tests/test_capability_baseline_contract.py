"""测试体系守卫：能力基线必须可核对，限制必须留下出口或迁移。

功能：`config/capability-baseline.json` 的结构、证据文件存在性和限制登记。
类别：回归/治理（旧问题：删功能或收紧输入时没有单列；测试可被改写来迁就实现）。
前置：无。只读配置与测试文件路径，不执行OpenMC。
输入：能力基线与限制清单。
预期：每个 supported 能力有至少一个存在的证据文件；每条限制引用已登记能力，
      并带有 override、migration 或明确的 user_decision；未知结构直接失败。
不证明：不证明证据测试真的覆盖了全部行为、不证明物理正确或发布验收。
"""
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / 'config/capability-baseline.json'


class CapabilityBaselineContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.value = json.loads(BASELINE.read_text(encoding='utf-8'))

    def test_capabilities_have_existing_evidence(self):
        capabilities = self.value.get('capabilities')
        self.assertIsInstance(capabilities, list)
        self.assertGreaterEqual(len(capabilities), 10)
        ids = [item.get('id') for item in capabilities]
        self.assertEqual(len(ids), len(set(ids)))
        for item in capabilities:
            with self.subTest(capability=item.get('id')):
                self.assertEqual(item.get('status'), 'supported')
                self.assertTrue(item.get('surface'))
                self.assertTrue(item.get('input'))
                self.assertTrue(item.get('output'))
                evidence = item.get('evidence')
                self.assertTrue(evidence)
                for relative in evidence:
                    self.assertTrue((ROOT / relative).is_file(),
                                    '%s 的证据文件不存在：%s' % (item['id'], relative))
                self.assertIn(item.get('gate'),
                              {'offline', 'real_mcp', 'real_openmc', 'offline_and_real'})

    def test_supported_capabilities_are_not_dropped(self):
        required = {'declarative_model_build', 'fixed_source_and_eigenvalue',
                    'custom_rectangular_lattice', 'native_model_import', 'scan',
                    'selected_report_export', 'geometry_preview', 'dagmc', 'depletion',
                    'official_examples', 'job_k_summary'}
        actual = {item['id'] for item in self.value['capabilities']}
        self.assertTrue(required <= actual, '能力基线缺少已支持能力：' + repr(required - actual))

    def test_restrictions_have_an_explicit_exit_or_decision(self):
        capability_ids = {item['id'] for item in self.value['capabilities']}
        restrictions = self.value.get('restrictions')
        self.assertIsInstance(restrictions, list)
        self.assertTrue(restrictions)
        ids = [item.get('id') for item in restrictions]
        self.assertEqual(len(ids), len(set(ids)))
        for item in restrictions:
            with self.subTest(restriction=item.get('id')):
                self.assertIn(item.get('capability'), capability_ids)
                self.assertIn(item.get('kind'),
                              {'contract_removal', 'guarded', 'whitelist', 'required_field'})
                self.assertTrue(item.get('impact'))
                self.assertTrue(item.get('override') or item.get('migration')
                                or item.get('user_decision'))
                if item['kind'] in {'contract_removal', 'whitelist', 'required_field'}:
                    self.assertTrue(item.get('user_decision'))
                    self.assertTrue(item.get('migration') or item.get('override'))
