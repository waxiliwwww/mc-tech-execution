"""Regression: a two-dimensional RegularMesh must not crash filter_summary.

Test function
-------------
`openmc_worker.filter_summary` on an OpenMC `MeshFilter` whose `RegularMesh`
declares only two dimensions (x,y), as used by 2-D mesh tallies.

Category
--------
Regression (real OpenMC statepoint reader; dimension padding).

Preconditions
-------------
Fake filter/mesh objects with the same public attributes used by the worker; no
OpenMC transport or statepoint file.

Input / operation
-----------------
dimension=[2,3], six bins; call filter_summary.

Expected
--------
Returns bin_count=6 and a padded dimension [2,3,1], with a valid
filter-bin range; no IndexError.

Not proven
----------
Does not prove the mesh values or coordinates are physically correct; the real
3-D/2-D statepoint path is covered separately by the gated mesh test.
"""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from openmc_worker import filter_summary


class RegularMesh:
    id = 7
    dimension = [2, 3]
    lower_left = [-1.0, -2.0, 0.0]
    upper_right = [1.0, 2.0, 0.0]


class MeshFilter:
    num_bins = 6
    mesh = RegularMesh()


class MeshFilterSummary2D(unittest.TestCase):
    def test_two_dimensional_mesh_is_padded(self):
        value = filter_summary(MeshFilter())
        self.assertEqual(value['bin_count'], 6)
        self.assertEqual(value['parameters']['dimension'], [2, 3, 1])
        self.assertEqual(value['parameters']['unit'], 'cm')
        self.assertIn('[1..2,1..3,1..1]', value['parameters']['bin_range'])


if __name__ == '__main__':
    unittest.main()