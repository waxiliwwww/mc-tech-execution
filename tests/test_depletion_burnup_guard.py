"""Regression: implausible depletion power/mass/time must not pass silently.

Test function
-------------
`openmc_tools.configure_depletion` estimates initial heavy-metal mass from
explicit volume/density/nuclide fractions and rejects an estimated burnup above
200 MWd/kgHM unless the caller explicitly acknowledges it.

Category
--------
Regression (safety guard; input plausibility).

Preconditions
-------------
Temporary workspace, package config, a UO2 materials fixture and a minimal
chain file; no OpenMC transport.

Input / operation
-----------------
1000 W for 20 days on 0.001 cm3 of 10 g/cc UO2 (≈8.8 mg heavy metal), first
without and then with acknowledge_extreme_burnup=true.

Expected
--------
The first call raises with the estimated mass, burnup, the 200 MWd/kgHM limit and
the explicit acknowledgement option; the second writes the marker and returns a
guard with status extreme. No value is silently accepted as physically valid.

Not proven
----------
The mass estimate is not a geometry/volume audit and does not decide whether a
real high-burnup design is acceptable.
"""
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


class BurnupGuard(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc burnup ')
        self.root = Path(self.temp.name)
        self.previous = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        cfg.update(data_kind='mock_process_fixture')
        self.config = self.root / 'runtime.json'; self.config.write_text(json.dumps(cfg))
        self.env = patch.dict(os.environ, {'MC_WORKSPACE': str(business.WORKSPACE),
                                           'MC_OPENMC_CONFIG': str(self.config)})
        self.env.start()
        self.case = business.WORKSPACE / 'dep'; self.case.mkdir()
        (self.case / 'settings.xml').write_text('<settings><particles>10</particles></settings>')
        (self.case / 'geometry.xml').write_text('<geometry/>')
        (self.case / 'materials.xml').write_text(
            '<materials><material id="1" volume="0.001">'
            '<density value="10.0" units="g/cc"/>'
            '<nuclide name="U235" ao="0.03"/><nuclide name="U238" ao="0.97"/>'
            '<nuclide name="O16" ao="2.0"/></material></materials>')
        self.chain = self.root / 'chain.xml'
        self.chain.write_text('<depletion_chain/>')
        self.cfg = {**cfg, 'depletion_chain': str(self.chain)}

    def tearDown(self):
        self.env.stop(); business.WORKSPACE = self.previous; self.temp.cleanup()

    def test_extreme_burnup_requires_explicit_acknowledgement(self):
        with patch.object(tools, 'config', lambda: self.cfg):
            with self.assertRaises(tools.Error) as caught:
                tools.configure_depletion('dep', 1000, [20], 'd')
        text = str(caught.exception)
        self.assertIn('MWd/kgHM', text)
        self.assertIn('acknowledge_extreme_burnup', text)
        self.assertIn('200.0', text)
        with patch.object(tools, 'config', lambda: self.cfg):
            value = tools.configure_depletion('dep', 1000, [20], 'd',
                                              acknowledge_extreme_burnup=True)
        self.assertEqual(value['depletion_guard']['status'], 'extreme')
        self.assertGreater(value['depletion_guard']['estimated_burnup_mwd_kg'], 200)
        marker = json.loads((self.case / 'mc-case.json').read_text())
        self.assertEqual(marker['depletion']['guard']['status'], 'extreme')


if __name__ == '__main__':
    unittest.main()
