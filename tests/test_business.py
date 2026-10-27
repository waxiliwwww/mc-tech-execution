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


if __name__ == "__main__":
    unittest.main()
