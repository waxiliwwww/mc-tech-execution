"""Plot/export/replay contracts across templates; no solver or paid model.

Uses the configured plotting Python, or the current Python when Matplotlib is
installed. Data are deliberate fixtures, not a physics reference. Every exported
script is relocated with only its JSON, run outside the toolkit with OpenMC
blocked, and compared with the server-side rendering. Temporary data are removed.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PlotReproduction(unittest.TestCase):
    def test_templates_relocate_and_replay_without_openmc(self):
        config = os.environ.get('MC_TEST_OPENMC_CONFIG')
        if config:
            python = json.loads(Path(config).read_text())['python_command']
        elif importlib.util.find_spec('matplotlib'):
            python = [sys.executable]
        else:
            self.skipTest('需要绘图Python；可用MC_TEST_OPENMC_CONFIG指定，不运行求解器')
        done = subprocess.run(python + [str(ROOT/'tests/fixtures/plot_reproduction_check.py')],
                              cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn('All plot templates replayed', done.stdout)
