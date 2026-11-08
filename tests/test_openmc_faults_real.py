"""Real gate: registered fault library must all be located (nuclear data required)."""
import os
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))

CONFIG = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(CONFIG, 'set MC_TEST_OPENMC_CONFIG to run fault library gate')
class FaultLibraryReal(unittest.TestCase):
    def setUp(self):
        os.environ['MC_OPENMC_CONFIG'] = CONFIG

    def test_all_registered_samples_are_located(self):
        import openmc_faults
        value = openmc_faults.check_sample()
        expected = len(json.loads((ROOT/'config/fault-samples.json').read_text()))
        self.assertEqual(value['total'], expected)
        self.assertEqual(value['failed'], [], value.get('results'))
        self.assertEqual(value['skipped'], [], '核数据就绪时不应跳过任何样本')
        self.assertEqual(value['passed'], expected)
        for result in value['results']:
            self.assertTrue(result['found'], result['id'])


if __name__ == '__main__':
    unittest.main()
