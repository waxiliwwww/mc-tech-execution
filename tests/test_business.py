"""Behavior tests use synthetic files, never physical reference results."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mc_business", ROOT / "mcp" / "business.py")
business = importlib.util.module_from_spec(spec)
spec.loader.exec_module(business)


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = business.WORKSPACE
        business.WORKSPACE = Path(self.temp.name)
        self.path = business.WORKSPACE / "input.txt"
        self.path.write_text("particles = -1\n", encoding="utf-8")

    def tearDown(self):
        business.WORKSPACE = self.old
        self.temp.cleanup()

    def test_preview_does_not_change_file_and_apply_backs_up_once(self):
        before = self.path.read_bytes()
        plan = business.preview_repair("input.txt", "-1", "100")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertIn("-particles = -1", plan["diff"])
        result = business.apply_repair(plan["plan_id"])
        self.assertEqual(self.path.read_text(), "particles = 100\n")
        self.assertEqual((business.WORKSPACE / result["backup"]).read_bytes(), before)
        with self.assertRaisesRegex(ValueError, "已经执行"):
            business.apply_repair(plan["plan_id"])

    def test_stale_file_refuses_apply(self):
        plan = business.preview_repair("input.txt", "-1", "100")
        self.path.write_text("a different input\n")
        with self.assertRaisesRegex(ValueError, "发生变化"):
            business.apply_repair(plan["plan_id"])
        self.assertEqual(self.path.read_text(), "a different input\n")

    def test_ambiguous_match_and_path_escape_rejected(self):
        self.path.write_text("xx xx")
        with self.assertRaises(ValueError):
            business.preview_repair("input.txt", "xx", "y")
        with self.assertRaises(ValueError):
            business.preview_repair("../input.txt", "xx", "y")
        (business.WORKSPACE / "link.txt").symlink_to(self.path)
        with self.assertRaises(ValueError):
            business.preview_repair("link.txt", "xx", "y")

    def test_noop_invalid_plan_and_internal_paths_rejected(self):
        with self.assertRaises(ValueError):
            business.preview_repair("input.txt", "-1", "-1")
        with self.assertRaises(ValueError):
            business.read_repair_plan("../../bad")
        with self.assertRaises(ValueError):
            business.preview_repair(".mc-plans/x.json", "x", "y")

    def test_unknown_errors_not_reported_as_valid(self):
        self.path.write_text("unclassified application output")
        result = business.diagnose_log("input.txt")
        self.assertEqual(result["findings"], [])
        self.assertIn("不代表输入正确", result["note"])

    def test_clear_s_is_explicitly_pending(self):
        result = business.get_case("CLEAR-S")
        self.assertFalse(result["runnable"])
        self.assertEqual(result["case"]["status"], "pending_material")


class JobLogReadBoundary(unittest.TestCase):
    def test_only_standard_job_log_routes_to_the_existing_diagnostic(self):
        import sys, os, json, hashlib
        from unittest.mock import patch
        sys.path.insert(0, str(ROOT/'mcp'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); identifier = 'ab'*16
            folder = root/'.mc-jobs'/identifier; (folder/'work').mkdir(parents=True)
            text = b'<settings><run_mode>eigenvalue</run_mode></settings>'
            (folder/'work/settings.xml').write_bytes(text)
            (folder/'solver.log').write_text('ERROR: No fission sites banked on MPI rank 0\n')
            record = {'job_id':identifier, 'state':'failed', 'mode':'run', 'case_dir':'case',
                      'input_files':{'settings.xml':hashlib.sha256(text).hexdigest()},
                      'provenance':{'data_kind':'log_fixture'}}
            (folder/'job.json').write_text(json.dumps(record))
            old = business.WORKSPACE
            try:
                business.WORKSPACE = root
                with patch.dict(os.environ, {'MC_WORKSPACE':str(root)}):
                    relative = '.mc-jobs/'+identifier+'/solver.log'
                    for path in [relative, str(root/relative)]:
                        value = business.diagnose_log(path)
                        self.assertEqual(value['job_id'], identifier)
                        self.assertIn('no_fission_sites', {f['category'] for f in value['findings']})
                        self.assertEqual(value['proposed_repairs'], [])
                    custom = '.mc-jobs/'+identifier+'/work/custom.log'
                    (root/custom).write_text('ERROR: No fission sites banked on MPI rank 0\n')
                    self.assertEqual(business.diagnose_log(custom)['findings'][0]['category'], 'no_fission_sites')
                    for path in ['../outside.log', '.mc-backups/missing', relative+'/../runtime.json']:
                        with self.assertRaises(business.MCInputError):business.diagnose_log(path)
                    for state, mutate in [('running', False), ('failed', True)]:
                        record['state'] = state; (folder/'job.json').write_text(json.dumps(record))
                        if mutate:(folder/'work/settings.xml').write_bytes(text+b'\n')
                        readable = business.diagnose_log(relative)
                        self.assertEqual(readable['findings'][0]['category'], 'no_fission_sites')
                        self.assertTrue(readable['diagnostic_limitations'])
                        self.assertEqual(readable['proposed_repairs'], [])
                    (folder/'solver.log').unlink(); (folder/'solver.log').symlink_to(folder/'work/settings.xml')
                    with self.assertRaisesRegex(ValueError, '符号链接'):business.diagnose_log(relative)
            finally:business.WORKSPACE = old


class FissionDiagnosticRule(unittest.TestCase):
    def test_real_engine_symptom_has_an_actionable_nonautomatic_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = business.WORKSPACE
            try:
                business.WORKSPACE = Path(tmp)
                (business.WORKSPACE/'solver.log').write_bytes((ROOT/'fixtures/faults/engine-logs/s18-no-fission-sites.log.txt').read_bytes())
                value = business.diagnose_log('solver.log')
            finally:business.WORKSPACE = old
        finding = value['findings'][0]
        self.assertEqual(finding['category'], 'no_fission_sites')
        self.assertIn('多种原因', finding['suggestion'])
        self.assertIn('用户确认', finding['suggestion'])


if __name__ == "__main__":
    unittest.main()
