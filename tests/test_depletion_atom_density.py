"""Regression: atom-density materials must yield a usable heavy-metal estimate.

Test function
-------------
`openmc_tools._heavy_mass_estimate` / `openmc_configure_depletion` for a
material whose density is declared in atom/b-cm (not g/cc).

Category
--------
Regression (burnup guard; unit conversion).

Preconditions
-------------
Temporary workspace with a UO2 materials fixture and a minimal chain file; no
OpenMC transport.

Input / operation
-----------------
0.072 atom/b-cm, volume 1000 cm3, U235/U238/O16 atom fractions 0.03/0.97/2,
then configure depletion at 1000 W for one day.

Expected
--------
The estimate is available and lies in the physical range for ~10 g/cc UO2
(roughly 8–11 kg heavy metal); the guard reports a very small one-day burnup
and does not reject the configuration.

Not proven
----------
The estimate is not a geometry volume audit and does not validate the chain or
the user's intended power history.
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


class AtomDensityMass(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc atom density ')
        self.root = Path(self.temp.name)
        self.previous = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        cfg.update(data_kind='mock_process_fixture')
        self.cfg = {**cfg, 'depletion_chain': str(self.root / 'chain.xml')}
        (self.root / 'chain.xml').write_text('<depletion_chain/>')
        self.env = patch.dict(os.environ, {'MC_WORKSPACE': str(business.WORKSPACE),
                                           'MC_OPENMC_CONFIG': str(self.root / 'runtime.json')})
        (self.root / 'runtime.json').write_text(json.dumps(self.cfg))
        self.env.start()
        self.case = business.WORKSPACE / 'atom-case'
        self.case.mkdir()
        (self.case / 'settings.xml').write_text('<settings><particles>10</particles></settings>')
        (self.case / 'geometry.xml').write_text('<geometry/>')
        (self.case / 'materials.xml').write_text(
            '<materials><material id="1" volume="1000">'
            '<density value="0.072" units="atom/b-cm"/>'
            '<nuclide name="U235" ao="0.03"/><nuclide name="U238" ao="0.97"/>'
            '<nuclide name="O16" ao="2.0"/></material></materials>')

    def tearDown(self):
        self.env.stop()
        business.WORKSPACE = self.previous
        self.temp.cleanup()

    def test_atom_density_mass_is_estimated(self):
        estimate = tools._heavy_mass_estimate(self.case)
        self.assertTrue(estimate['available'], estimate)
        self.assertGreater(estimate['heavy_metal_g'], 8000)
        self.assertLess(estimate['heavy_metal_g'], 11000)
        with patch.object(tools, 'config', lambda: self.cfg):
            value = tools.configure_depletion('atom-case', 1000, [1], 'd',
                                              acknowledge_extreme_burnup=True)
        guard = value['depletion_guard']
        self.assertEqual(guard['status'], 'ok')
        self.assertLess(guard['estimated_burnup_mwd_kg'], 0.01)


if __name__ == '__main__':
    unittest.main()