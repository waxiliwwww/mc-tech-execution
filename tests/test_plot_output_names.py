"""Regression: identical plot requests reuse the same artifact name.

Test function
-------------
`openmc_plots.output_name` is the naming boundary used by result, mesh,
convergence and scan plots. Repeated report exports must not create a new
image merely because the same request ran twice.

Category
--------
Regression (D6, repeated report export)

Preconditions
-------------
No OpenMC runtime or MCP service required.

Input / operation
-----------------
Build the same payload in different key orders and compare names; then change
one selection, title or format.

Expected
--------
Canonical payloads produce the same name; meaningful changes produce a
different name; the name stays inside the documented prefix/format shape.

Not proven
----------
Does not prove image content correctness or that every plot backend writes
atomically; those are covered by the real report tests.
"""
import re
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from openmc_plots import output_name


class PlotOutputName(unittest.TestCase):
    def test_identical_selection_is_stable_across_key_order(self):
        first = {'job_id': 'job-1', 'tally_id': 2, 'score': 'flux', 'format': 'png'}
        second = {'format': 'png', 'score': 'flux', 'tally_id': 2, 'job_id': 'job-1'}
        self.assertEqual(output_name('plot', first, 'png'), output_name('plot', second, 'png'))

    def test_meaningful_changes_get_a_new_name(self):
        base = {'job_id': 'job-1', 'tally_id': 2, 'score': 'flux', 'format': 'png'}
        self.assertNotEqual(output_name('plot', base, 'png'),
                            output_name('plot', {**base, 'tally_id': 3}, 'png'))
        self.assertNotEqual(output_name('plot', base, 'png'),
                            output_name('plot', {**base, 'title': 'other'}, 'png'))
        self.assertNotEqual(output_name('plot', base, 'png'),
                            output_name('plot', base, 'svg'))

    def test_name_shape_is_bounded(self):
        name = output_name('mesh', {'job_id': 'job-1'}, 'png')
        self.assertRegex(name, r'^mesh-[0-9a-f]{16}\.png$')
        self.assertLess(len(name), 80)
        with self.assertRaisesRegex(ValueError, 'format'):
            output_name('mesh', {}, 'pdf')


if __name__ == '__main__':
    unittest.main()
