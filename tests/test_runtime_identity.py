"""Regression: a running MCP process must expose disk/version drift.

Test function
-------------
`runtime_identity` snapshots the Python modules and VERSION at service startup,
then `check` compares that snapshot with the package currently on disk.

Category
--------
Regression (deployment drift detection; stale tool schema vs disk validator).

Preconditions
-------------
Temporary directory with a minimal ``mcp/`` package and ``VERSION`` file; no
MCP process, OpenMC runtime or network.

Input / operation
-----------------
Take a snapshot, then rewrite VERSION and a tool module; compare again. Also
change only a non-Python file and confirm it does not count as code drift.

Expected
--------
Same files report no restart. VERSION or Python changes report
``restart_required``, matching flags, and the changed file name; unrelated
files do not create a false alarm.

Not proven
----------
Does not prove that an already-running stale process can load this detector;
it proves the detector and policy after a process starts with this code.
"""
import sys
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'mcp'))
import runtime_identity


def fixture(root):
    (root / 'mcp').mkdir(parents=True)
    (root / 'mcp/tool.py').write_text('TOOL = 1\n', encoding='utf-8')
    (root / 'VERSION').write_text('0.12.0\n', encoding='utf-8')


class RuntimeIdentity(unittest.TestCase):
    def test_stable_snapshot_has_no_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            start = runtime_identity.startup(root)
            drift = runtime_identity.check(start, root)
        self.assertFalse(drift['restart_required'])
        self.assertTrue(drift['version_matches'])
        self.assertTrue(drift['code_matches'])
        self.assertEqual(drift['changed_files'], [])

    def test_version_change_reports_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            start = runtime_identity.startup(root)
            (root / 'VERSION').write_text('0.13.0\n', encoding='utf-8')
            drift = runtime_identity.check(start, root)
        self.assertTrue(drift['restart_required'])
        self.assertFalse(drift['version_matches'])
        self.assertIn('0.12.0', drift['message'])
        self.assertIn('0.13.0', drift['message'])

    def test_python_change_names_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            start = runtime_identity.startup(root)
            (root / 'mcp/tool.py').write_text('TOOL = 2\n', encoding='utf-8')
            drift = runtime_identity.check(start, root)
        self.assertTrue(drift['restart_required'])
        self.assertFalse(drift['code_matches'])
        self.assertEqual(drift['changed_files'], ['mcp/tool.py'])

    def test_unrelated_file_is_not_code_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); fixture(root)
            start = runtime_identity.startup(root)
            (root / 'notes.txt').write_text('not a module\n', encoding='utf-8')
            drift = runtime_identity.check(start, root)
        self.assertFalse(drift['restart_required'])


if __name__ == '__main__':
    unittest.main()
