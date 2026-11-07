"""Regression: an SDK parse failure must not erase static validation results.

Test function
-------------
`openmc_tools.validate_inputs(api_check=True)` catches an OpenMC API parsing
error, returns ``openmc_api.ok=false`` with context, and keeps the independent
XML/static-semantic sections.

Category
--------
Regression (error handling; degraded validation).

Preconditions
-------------
Temporary workspace, package config and valid XML; the API adapter is patched to
raise a representative opaque AttributeError; no transport.

Input / operation
-----------------
Validate a valid materials/geometry/settings model with api_check and
semantic_check while the API raises.

Expected
--------
No exception escapes. Structure is valid, ``openmc_api`` carries the error and
a suggestion, and the semantic section remains present when requested.

Not proven
----------
Does not prove the model is physically correct or that every SDK exception has
a bespoke diagnosis.
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


class ApiFailureDegrades(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mc api failure ')
        self.root = Path(self.temp.name)
        self.previous = business.WORKSPACE
        business.WORKSPACE = self.root / 'workspace'
        business.WORKSPACE.mkdir()
        cfg = json.loads((ROOT / 'config/openmc.json').read_text())
        cfg.update(data_kind='mock_process_fixture',
                   cross_sections=str(ROOT / 'config/data-index/endfb-vii.1.xml'))
        config = self.root / 'runtime.json'; config.write_text(json.dumps(cfg))
        self.env = patch.dict(os.environ, {'MC_WORKSPACE': str(business.WORKSPACE),
                                           'MC_OPENMC_CONFIG': str(config)})
        self.env.start()
        self.case = business.WORKSPACE / 'case'; self.case.mkdir()
        (self.case / 'settings.xml').write_text('<settings><particles>10</particles></settings>')
        (self.case / 'geometry.xml').write_text('<geometry/>')
        (self.case / 'materials.xml').write_text(
            '<materials><material id="1" name="fuel">'
            '<density value="10.0" units="g/cc"/><nuclide name="U235" ao="1.0"/>'
            '</material></materials>')

    def tearDown(self):
        self.env.stop(); business.WORKSPACE = self.previous; self.temp.cleanup()

    def test_api_failure_is_returned_and_semantics_remain(self):
        with patch.object(tools, 'api', side_effect=tools.Error(
                "OpenMC API失败：AttributeError: 'NoneType' object has no attribute 'attrib'")):
            value = tools.validate_inputs('case', api_check=True, semantic_check=True)
        self.assertTrue(value['structure_valid'])
        self.assertFalse(value['openmc_api']['ok'])
        self.assertIn('AttributeError', value['openmc_api']['error'])
        self.assertIn('suggestion', value['openmc_api']['context'])
        self.assertTrue(value['semantic']['checked'])
        self.assertIn('openmc_api_failed', value['validation_level'])


if __name__ == '__main__':
    unittest.main()
