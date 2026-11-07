"""MV-MCP-02：正例：调用方自定义格架与输入预览

测试功能
--------
`openmc_build_model` 对调用方修改后的声明式模型（矩形格架排布、材料密度、seed）的构建；`openmc_preview_input` 对产物约束和 manifest 的只读检查。

类别
----
正例 + 新组合输入

前置
----
真实 MCP 服务（每条用例由测试基类新启动一次），设置 `MC_TEST_OPENMC_CONFIG`；OpenMC 0.16.0 运行时可用；测试只构建，不提交求解。

操作
----
读取 `rectangular-assembly.json`，改格架两行排布、材料密度和 seed，构建后读取保存的 spec，再调用预览。

预期
----
构建通过 API 读回；保存的 spec 保留调用方布局；预览 manifest 完整、无 problems，包含 XML 指纹和随包校验包。

不证明
------
不证明物理结果、统计收敛、几何无重叠、核数据适用性、异机部署或正式验收。
"""
import json
import unittest

try:
    from .support import RealMCPCase, RUNTIME, SKIP_REASON
except ImportError:  # direct discovery from this directory
    from support import RealMCPCase, RUNTIME, SKIP_REASON


@unittest.skipUnless(RUNTIME, SKIP_REASON)
class TestPositiveCustomLattice(RealMCPCase):
    async def test_custom_lattice_builds_and_previews(self):
        spec = self.example("rectangular-assembly.json")
        spec["name"] = "MCP caller custom lattice layout"
        spec["lattices"][0]["universes"] = [[2, 1, 2], [1, 1, 2]]
        spec["materials"][1]["density"] = 0.82
        spec["settings"]["seed"] = 71
        built = self.data(await self.call("openmc_build_model",
                                          {"case_dir": "cases/custom-lattice", "spec": spec}))
        self.assertTrue(built["api_parsed"])
        self.assertEqual(built["cells"], len(spec["cells"]))
        self.assertEqual(built["lattices"], len(spec["lattices"]))
        target = self.workspace / "cases/custom-lattice"
        saved = json.loads((target / "model-spec.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["lattices"][0]["universes"], [[2, 1, 2], [1, 1, 2]])
        preview = self.data(await self.call("openmc_preview_input",
                                           {"case_dir": "cases/custom-lattice"}))
        self.assertTrue(preview["manifest_complete"])
        self.assertEqual(preview["problems"], [])
        self.assertIn("geometry.xml", preview["input_hashes"])
        self.assertIn("model_validation/schema.json",
                      {item["name"] for item in preview["files"]})
