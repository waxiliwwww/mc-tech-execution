"""Regression: report preview prefers non-zero bins over leading zeros.

Test function
-------------
`openmc_visuals.selected_report_rows` builds the human-readable table preview.
Full values remain in CSV/JSON; the preview must not be all leading zero bins
when later bins contain usable estimates.

Category
--------
Regression (N3, selected report preview usability)

Preconditions
-------------
No OpenMC runtime or renderer required.

Input / operation
-----------------
A series with two leading zero bins and usable values later; an all-zero
series; and a small preview limit.

Expected
--------
Non-zero bins appear first while preserving their original bin index, and an
all-zero series still previews its first bins without inventing values.

Not proven
----------
Does not prove DOCX/PDF layout or that a zero bin is physically zero; real
report tests cover rendering and the CSV keeps every bin.
"""
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
from openmc_visuals import selected_report_rows


def series(mean):
    return {'tally_id': 1, 'score': 'flux', 'nuclide': 'total',
            'mean': mean, 'std_dev': [0.1] * len(mean), 'filter_selection': []}


class ReportPreviewRows(unittest.TestCase):
    def test_nonzero_bins_come_first_with_original_indices(self):
        rows = selected_report_rows(series([0.0, 0.0, 3.0, 4.0, 0.0]), 3)
        self.assertEqual([row[1].split(' / ')[-1] for row in rows], ['bin 2', 'bin 3', 'bin 0'])
        self.assertEqual([row[2] for row in rows], ['3', '4', '0'])

    def test_all_zero_series_keeps_leading_order(self):
        rows = selected_report_rows(series([0.0, 0.0, 0.0]), 2)
        self.assertEqual([row[1].split(' / ')[-1] for row in rows], ['bin 0', 'bin 1'])

    def test_limit_is_honoured(self):
        rows = selected_report_rows(series([1.0, 2.0, 3.0]), 2)
        self.assertEqual(len(rows), 2)


if __name__ == '__main__':
    unittest.main()
