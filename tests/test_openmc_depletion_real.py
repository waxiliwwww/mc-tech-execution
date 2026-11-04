"""Real gate: depletion chain end-to-end (create -> configure -> deplete job -> read)."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import business
import openmc_jobs as jobs
import openmc_tools as tools

CONFIG = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(CONFIG, 'set MC_TEST_OPENMC_CONFIG to run real OpenMC depletion gate')
class DepletionReal(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc depletion real ')
        self.root = Path(self.temp.name)
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        os.environ['MC_OPENMC_CONFIG'] = CONFIG
        self.cfg = json.loads(Path(CONFIG).read_text(encoding='utf-8'))
        library = self.cfg['nuclear_data_library']
        index = ROOT / '.runtime/nuclear-data' / library / 'cross_sections.xml'
        os.environ['OPENMC_CROSS_SECTIONS'] = str(index)

    def tearDown(self):
        self.temp.cleanup()

    def test_deplete_job_returns_stepwise_keff_and_nuclide_mass(self):
        tools.create_model('burn', 'pincell-depletion', particles=300, batches=6, inactive=2)
        value = tools.configure_depletion('burn', 1000.0, [0.05, 0.05], 'd')
        self.assertEqual(value['steps'], 2)
        record = tools.submit_job('burn', mode='deplete', threads=1)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = jobs.status(record['job_id'])
            if status['state'] in ('completed', 'failed', 'cancelled', 'interrupted'):
                break
            time.sleep(2)
        self.assertEqual(status['state'], 'completed', status.get('error'))
        result = tools.read_depletion(record['job_id'])
        self.assertEqual(len(result['steps']), 3)
        self.assertEqual([step['time_days'] for step in result['steps']], [0.0, 0.05, 0.1])
        self.assertGreater(result['heavy_metal_total_g'], 50.0)
        for step in result['steps']:
            self.assertTrue(0.0 < step['keff']['mean'] < 2.0, step)
            self.assertGreaterEqual(step['keff']['std_dev'], 0.0)
        u235 = result['nuclide_mass_g']['U235']['1']
        self.assertLess(u235[-1], u235[0])
        pu239 = result['nuclide_mass_g'].get('Pu239', {}).get('1', [0.0, 0.0, 0.0])
        self.assertGreater(pu239[-1], pu239[0])
        self.assertGreater(result['steps'][-1]['burnup_mwd_kg'], 0.0)
        self.assertFalse(result['professional_acceptance'])


if __name__ == '__main__':
    unittest.main()
