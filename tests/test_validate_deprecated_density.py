"""Regression: removed OpenMC density attribute must not crash validation.

Test function
-------------
`openmc_tools.validate_inputs` detects the OpenMC 0.16 removed
``<material density="...">`` spelling before calling the SDK, and reports the
file/element/field plus the replacement syntax.

Category
--------
Regression (external input deprecation; actionable validation).

Preconditions
------------
Temporary workspace, package config and a materials/geometry/settings fixture;
no OpenMC runtime or network. The API is patched to fail if called, proving the
static check runs first.

Input / operation
-----------------
A material with ``density="10.0"`` and no density child, then
``validate_inputs(..., api_check=True)``.

Expected
--------
``structure_valid`` is false; the problem names the material and field and
recommends ``<density value="..." units="..."/>``; no bare AttributeError.

Not proven
----------
Does not prove that every removed OpenMC syntax is covered, nor physical
correctness of a corrected model.
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


class DeprecatedDensity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc deprecated ')
        self.root = Path(self.temp.name)
        self.previous = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        cfg.update(data_kind='mock_process_fixture')
        config = self.root / 'runtime.json'; config.write_text(json.dumps(cfg))
        self.env = patch.dict(os.environ, {'MC_WORKSPACE': str(business.WORKSPACE),
                                           'MC_OPENMC_CONFIG': str(config)})
        self.env.start()
        self.case = business.WORKSPACE / 'case'; self.case.mkdir()
        (self.case / 'settings.xml').write_text('<settings><particles>10</particles></settings>')
        (self.case / 'geometry.xml').write_text('<geometry/>')
        (self.case / 'materials.xml').write_text(
            '<materials><material id="1" name="fuel" density="10.0">'
            '<nuclide name="U235" ao="1.0"/></material></materials>')

    def tearDown(self):
        self.env.stop(); business.WORKSPACE = self.previous; self.temp.cleanup()

    def test_density_attribute_is_rejected_with_replacement(self):
        with patch.object(tools, 'api', side_effect=AssertionError('API must not be called')):
            value = tools.validate_inputs('case', api_check=True)
        self.assertFalse(value['structure_valid'])
        text = '\n'.join(value['problems'])
        self.assertIn('density 属性', text)
        self.assertIn('material fuel', text)
        self.assertIn('<density value=', text)
        self.assertNotIn('openmc_api', value)


if __name__ == '__main__':
    unittest.main()
