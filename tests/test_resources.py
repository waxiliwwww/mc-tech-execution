"""资源接口：列表、读取、以及"不泄露参数值"的边界。与工具测试分开，失败时定位清楚。"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))

import resources  # noqa: E402


class ResourceContract(unittest.TestCase):
    def test_existing_and_workflow_resources_are_declared(self):
        uris = [item["uri"] for item in resources.RESOURCES]
        self.assertEqual(uris[:3], ["mc://parameters/settings", "mc://examples/catalog", "mc://logs/tool-calls"])
        self.assertEqual(set(uris[3:]), {"skill://openmc-workflows/"+f for f in ["SKILL.md", "references/file-transfer.md", "references/extended-tasks.md"]})
        for item in resources.RESOURCES:
            self.assertTrue(item["name"] and item["description"])
            self.assertEqual(item["mimeType"], "text/markdown" if item["uri"].startswith("skill://") else "application/json")

    def test_parameter_resource_matches_module_constants(self):
        from openmc_parameters import SETTINGS, SURFACES
        data = json.loads(resources.read("mc://parameters/settings"))
        self.assertEqual(len(data["settings"]), len(SETTINGS))
        self.assertEqual(len(data["surface_types"]), len(SURFACES))
        first = data["settings"][0]
        self.assertEqual(first["name"], "particles")
        self.assertEqual(first["unit"], "particles/generation")
        self.assertIn("密度", data["note"])

    def test_catalog_resource_counts_real_file(self):
        file_items = json.loads((ROOT / "config/official-examples.json").read_text(encoding="utf-8"))
        items = file_items.get("examples", file_items)
        data = json.loads(resources.read("mc://examples/catalog"))
        self.assertEqual(data["total"], len(items))
        self.assertEqual(sum(data["by_group"].values()), data["total"])
        self.assertEqual(len(data["deferred"]), 2)

    def test_tool_logs_never_expose_argument_values(self):
        data = json.loads(resources.read("mc://logs/tool-calls"))
        self.assertIn("events", data)
        for event in data["events"]:
            self.assertEqual(set(event), {"time", "event", "tool", "ok", "argument_keys"})

    def test_unknown_uri_is_refused_with_hint(self):
        with self.assertRaises(ValueError) as caught:
            resources.read("mc://nope")
        self.assertIn("resources/list", str(caught.exception))


if __name__ == "__main__":
    unittest.main()