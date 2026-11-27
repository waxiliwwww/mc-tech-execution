import json
import os
from pathlib import Path
import subprocess
import unittest
ROOT=Path(__file__).resolve().parents[1]
class EnergySelection(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('MC_TEST_OPENMC_CONFIG'),'requires native OpenMC SDK')
    def test_native_multifilter_axes_and_values(self):
        python=json.loads(Path(os.environ['MC_TEST_OPENMC_CONFIG']).read_text())['python_command']
        done=subprocess.run(python+[str(ROOT/'tests/fixtures/energy_selection_check.py')],capture_output=True,text=True,timeout=30)
        self.assertEqual(done.returncode,0,done.stdout+done.stderr)
