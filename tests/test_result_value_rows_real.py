"""Official SDK oracle; paired facts survive multiple filter/score/nuclide axes."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
CONFIG = os.environ.get('MC_TEST_OPENMC_CONFIG')


@unittest.skipUnless(CONFIG, 'MC_TEST_OPENMC_CONFIG指定真实SDK运行时')
class ResultValueRowsReal(unittest.TestCase):
    def test_native_rows_and_coordinate_swap_negative_control(self):
        runtime = json.loads(Path(CONFIG).read_text())
        result = subprocess.run(runtime['python_command'] + [str(ROOT/'tests/fixtures/result_value_rows_check.py')],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('all 96 paired rows', result.stdout)
