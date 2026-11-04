"""prepare_case 逐参数差异离线测试：stub API 调用，验证落盘核对与失败/并发下目标发布原子性。"""
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
import openmc_scan as scan


class PrepareCaseDiff(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc preparediff ')
        self.root = Path(self.temp.name)
        self.previous = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        self.cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        self.cfg.update(data_kind='mock_process_fixture')
        self.config = self.root / 'runtime.json'
        self.config.write_text(json.dumps(self.cfg))
        self.env_patch = patch.dict(os.environ, {
            'MC_WORKSPACE': str(business.WORKSPACE), 'MC_OPENMC_CONFIG': str(self.config)})
        self.env_patch.start()
        self.case = business.WORKSPACE / 'case'
        self.case.mkdir()
        (self.case / 'model.xml').write_text('<model/>', encoding='utf-8')
        self.actions = []
        self.targets = 0

    def tearDown(self):
        self.env_patch.stop()
        business.WORKSPACE = self.previous
        self.temp.cleanup()

    def stub_api(self, readback_values):
        def fake(action, directory, payload, cfg=None):
            self.actions.append(action)
            if action == 'check-parameters':
                return {'valid': True, 'combinations': len(payload['combinations']),
                        'parameters': []}
            if action == 'update-parameters':
                changes = [{'name': name, 'before': 100, 'after': value,
                            'unit': None, 'category': 'settings'}
                           for name, value in payload['parameters'].items()]
                return {'changes': changes, 'professional_acceptance': False}
            if action == 'parameters':
                return {'parameters': [{'name': name, 'current': value}
                                       for name, value in readback_values.items()]}
            raise AssertionError('未预期的action：' + action)
        return fake

    def prepare(self, readback_values, parameters, target=None):
        self.targets += 1
        with patch.object(scan, 'api', self.stub_api(readback_values)):
            return scan.prepare_case('case', target or f'modified{self.targets}', parameters)

    def test_verified_diff_with_readback(self):
        result = self.prepare({'settings.particles': 400}, {'settings.particles': 400})
        diff = result['parameter_diff']
        self.assertTrue(diff['verified'], diff)
        self.assertEqual(diff['changes'][0]['after'], 400)
        self.assertEqual(diff['readback'], [{'name': 'settings.particles', 'expected': 400,
                                             'found': 400, 'match': True}])
        self.assertEqual(self.actions, ['check-parameters', 'update-parameters', 'parameters'])
        # 顶层changes兼容保留
        self.assertEqual(result['changes'], diff['changes'])

    def test_float_roundtrip_tolerance(self):
        result = self.prepare({'materials.1.density': 0.6000000000000001},
                              {'materials.1.density': 0.6})
        self.assertTrue(result['parameter_diff']['verified'])

    def test_failed_update_removes_partial_target_and_preserves_source(self):
        before=(self.case/'model.xml').read_bytes()
        def fail_update(action,directory,payload,cfg=None):
            if action=='check-parameters':return {'valid':True}
            (directory/'model.xml').write_text('<partially-written/>')
            raise business.MCInputError('injected update failure')
        with patch.object(scan,'api',fail_update):
            with self.assertRaisesRegex(ValueError,'injected update failure'):
                scan.prepare_case('case','broken',{'settings.particles':400})
        self.assertFalse((business.WORKSPACE/'broken').exists())
        self.assertEqual((self.case/'model.xml').read_bytes(),before)
        self.assertFalse(any(p.name.startswith('.prepare-') for p in business.WORKSPACE.iterdir()))

    def test_racing_existing_target_is_not_overwritten(self):
        original=self.stub_api({'settings.particles':400})
        def concurrent(action,directory,payload,cfg=None):
            result=original(action,directory,payload,cfg)
            if action=='parameters':
                target=business.WORKSPACE/'racing';target.mkdir()
                (target/'keep').write_text('other writer')
            return result
        with patch.object(scan,'api',concurrent):
            with self.assertRaisesRegex(ValueError,'拒绝覆盖'):
                scan.prepare_case('case','racing',{'settings.particles':400})
        self.assertEqual((business.WORKSPACE/'racing/keep').read_text(),'other writer')
        self.assertFalse((business.WORKSPACE/'racing/model.xml').exists())

    def test_mismatch_is_refused_with_details_and_no_target(self):
        with self.assertRaisesRegex(ValueError,'settings.particles.*100'):
            self.prepare({'settings.particles':100},{'settings.particles':400},'mismatch')
        self.assertFalse((business.WORKSPACE/'mismatch').exists())

    def test_missing_readback_entry_refuses_publication(self):
        with self.assertRaisesRegex(ValueError,'读回'):
            self.prepare({}, {'settings.particles':400},'missing')
        self.assertFalse((business.WORKSPACE/'missing').exists())

    def test_empty_parameters_are_refused_before_copy(self):
        with self.assertRaisesRegex(ValueError,'非空'):
            self.prepare({}, {},'empty')
        self.assertFalse((business.WORKSPACE/'empty').exists())

    def test_multiple_parameters_all_must_match(self):
        with self.assertRaisesRegex(ValueError,'settings.batches'):
            self.prepare({'settings.particles':400,'settings.batches':10},
                         {'settings.particles':400,'settings.batches':20},'multi')
        self.assertFalse((business.WORKSPACE/'multi').exists())


if __name__ == '__main__':
    unittest.main()
