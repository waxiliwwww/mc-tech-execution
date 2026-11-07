"""Regression: scan status must judge adjacent deltas against 2σ.

Test function
-------------
`openmc_scan.differences` pairs adjacent points on each scan axis and marks
whether the k difference exceeds the independent combined 2σ.

Category
--------
Regression (statistical interpretation; scan axis).

Preconditions
-------------
Synthetic scan record with no OpenMC runtime or filesystem.

Input / operation
-----------------
Four temperature points with σ≈350 pcm, one pair with a 100 pcm shift and one
with a 1200 pcm shift; a second axis with a zero-σ pair.

Expected
--------
The small shift is inside 2σ; the large shift is outside; each pair retains its
axis values and combined sigma. A zero-σ, zero-difference pair is inside, while
a zero-σ nonzero difference is outside.

Not proven
----------
Not a statistical significance test; does not prove convergence or a physical
temperature effect.
"""
import math
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from openmc_scan import differences


def point(index, temperature, mean, sigma):
    return {'index': index, 'state': 'completed', 'parameters': {'temperature': temperature},
            'keff': {'mean': mean, 'std_dev': sigma}}


class ScanDifferences(unittest.TestCase):
    def record(self, cases, axes=None):
        return {'axes': axes or {'temperature': [300, 600, 900, 1200]}, 'cases': cases}

    def test_small_delta_is_inside_and_large_delta_outside(self):
        cases = [point(0, 300, 1.0000, .035), point(1, 600, 1.0010, .035),
                 point(2, 900, 1.1010, .035)]
        result = differences(self.record(cases))
        self.assertEqual(len(result['pairs']), 2)
        first, second = result['pairs']
        self.assertTrue(first['within_2sigma_engineering'], first)
        self.assertAlmostEqual(first['combined_std_dev'], math.sqrt(2) * .035)
        self.assertFalse(second['within_2sigma_engineering'], second)
        self.assertEqual(result['outside_2sigma'], 1)

    def test_zero_sigma_needs_exact_zero_for_inside(self):
        cases = [point(0, 300, 1.0, 0.0), point(1, 600, 1.0, 0.0),
                 point(2, 900, 1.1, 0.0)]
        result = differences(self.record(cases))
        self.assertTrue(result['pairs'][0]['within_2sigma_engineering'])
        self.assertFalse(result['pairs'][1]['within_2sigma_engineering'])

    def test_missing_or_failed_points_are_not_compared(self):
        cases = [point(0, 300, 1.0, .1), {**point(1, 600, 1.1, .1), 'state': 'failed'}]
        result = differences(self.record(cases))
        self.assertEqual(result['pairs'], [])
        self.assertEqual(result['unavailable_points'], 1)

    def test_nonfinite_k_or_sigma_is_not_compared(self):
        cases = [point(0, 300, float('nan'), .1), point(1, 600, 1.1, float('inf'))]
        result = differences(self.record(cases))
        self.assertEqual(result['pairs'], [])
        self.assertEqual(result['unavailable_points'], 2)


if __name__ == '__main__':
    unittest.main()
