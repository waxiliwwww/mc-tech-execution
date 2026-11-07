"""Regression: mesh heat maps flag low-statistics bins, not only zeros.

Test function
-------------
`openmc_visuals.mesh_statistics` summarizes finite mesh cells by zero count and
relative standard deviation so `openmc_plot_mesh` can warn before a heat map is
read as a physical map.

Category
--------
Regression (D7, low-statistics mesh warning)

Preconditions
-------------
No OpenMC runtime, matplotlib or MCP service required.

Input / operation
-----------------
Mixed mesh arrays with zero, well-estimated, high-relative-error and
non-finite-error cells.

Expected
--------
The helper counts each class separately, reports a maximum relative std and
does not turn a high relative error into a physical conclusion.

Not proven
----------
Does not prove the rendered PNG contains a particular annotation; the real
mesh/report tests cover rendering. This only locks the warning data that the
renderer and JSON/CSV expose.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from openmc_visuals import mesh_statistics


class MeshStatistics(unittest.TestCase):
    def test_zero_low_and_missing_error_cells_are_separated(self):
        values = [[0.0, 10.0, 10.0, 10.0, float('nan')]]
        errors = [[2.0, 1.0, 8.0, float('nan'), 5.0]]
        result = mesh_statistics(values, errors)
        self.assertEqual(result['total_cells'], 5)
        self.assertEqual(result['zero_bins'], 1)
        self.assertEqual(result['low_statistics_bins'], 2)  # 80% relative and missing error
        self.assertEqual(result['missing_error_bins'], 1)
        self.assertEqual(result['relative_std_threshold'], 0.1)
        self.assertEqual(result['max_relative_std'], 0.8)

    def test_all_zero_mesh_has_no_relative_std_claim(self):
        result = mesh_statistics([[0.0, 0.0]], [[0.0, 0.0]])
        self.assertEqual(result['zero_bins'], 2)
        self.assertEqual(result['low_statistics_bins'], 0)
        self.assertIsNone(result['max_relative_std'])

    def test_well_estimated_cells_do_not_warn(self):
        result = mesh_statistics([[100.0, 50.0]], [[1.0, 2.0]])
        self.assertEqual(result['low_statistics_bins'], 0)
        self.assertEqual(result['max_relative_std'], 0.04)


if __name__ == '__main__':
    unittest.main()
