"""Regression: two-dimensional regular meshes must produce safe point previews.

Test function
-------------
`openmc_worker._mesh_descriptor` and `_mesh_points` for a 2-D `RegularMesh`
with only x/y bounds, as used by the official lattice examples.

Category
--------
Regression (real statepoint preview path; missing z bound).

Preconditions
-------------
Fake filter/tally objects exposing the public attributes used by the worker;
no OpenMC transport or statepoint file. numpy is available in the test venv.

Input / operation
-----------------
A 2x3 mesh with bounds [-1,-2] and [1,2]; inspect the descriptor and build
points for the first two flat bins.

Expected
--------
Bounds are padded to 3-D with z=0, `_mesh_points` does not raise, and each
point has a 0-based mesh index, a 1-based filter bin, and a z center of 0.

Not proven
----------
Does not prove the transport values or all 3-D mesh semantics; a real OpenMC
statepoint path is covered by the gated mesh tally test.
"""
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from openmc_worker import _mesh_descriptor, _mesh_points


class RegularMesh:
    id = 7
    dimension = [2, 3]
    lower_left = [-1.0, -2.0]
    upper_right = [1.0, 2.0]


class MeshFilter:
    num_bins = 6
    mesh = RegularMesh()


class FakeTally:
    filters = [MeshFilter()]
    mean = np.zeros(6)
    std_dev = np.zeros(6)


class MeshPoints2D(unittest.TestCase):
    def test_missing_z_bounds_are_padded(self):
        descriptor = _mesh_descriptor(FakeTally())
        self.assertEqual(descriptor['coordinate_status'], 'mappable')
        self.assertEqual(descriptor['lower_left'], [-1.0, -2.0, 0.0])
        self.assertEqual(descriptor['upper_right'], [1.0, 2.0, 0.0])
        points = _mesh_points(FakeTally.mean, FakeTally.std_dev, [0, 1], descriptor)
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]['mesh_index'], [0, 0, 0])
        self.assertEqual(points[0]['filter_bin'], [1, 1, 1])
        self.assertAlmostEqual(points[0]['center'][0], -0.5)
        self.assertAlmostEqual(points[0]['center'][1], -4.0 / 3.0)
        self.assertEqual(points[0]['center'][2], 0.0)
        self.assertEqual(points[1]['mesh_index'], [1, 0, 0])
        self.assertEqual(points[1]['filter_bin'], [2, 1, 1])
        self.assertAlmostEqual(points[1]['center'][0], 0.5)
        self.assertAlmostEqual(points[1]['center'][1], -4.0 / 3.0)
        self.assertEqual(points[1]['center'][2], 0.0)


if __name__ == '__main__':
    unittest.main()