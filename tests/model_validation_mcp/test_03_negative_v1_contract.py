"""MV-MCP-03：反例：v1 schema 版本与字符串 region

测试功能
--------
MCP 工具输入 schema 对 `model-spec/v2` 契约的拒绝：`schema_version` 必须为 `"2"`，`region` 必须是类型树，不能是 v1 字符串。

类别
----
反例 + 契约边界

前置
----
真实 MCP 服务（每条用例由测试基类新启动一次），设置 `MC_TEST_OPENMC_CONFIG`；OpenMC 0.16.0 运行时可用；测试只构建，不提交求解。

操作
----
分别提交只改 `schema_version="1"` 的最小球，以及只把 region 改成 `"-1"` 的最小球；检查错误路径、目标目录和服务状态。

预期
----
两次都被 MCP 输入 schema 拒绝，错误路径分别指向 `spec.schema_version` 和 `spec.cells.0.region`；没有创建目标目录；随后能力发现仍正常。

不证明
------
不证明物理结果、统计收敛、几何无重叠、核数据适用性、异机部署或正式验收。
"""
import unittest

try:
    from .support import RealMCPCase, RUNTIME, SKIP_REASON
except ImportError:  # direct discovery from this directory
    from support import RealMCPCase, RUNTIME, SKIP_REASON


@unittest.skipUnless(RUNTIME, SKIP_REASON)
class TestNegativeV1Contract(RealMCPCase):
    def assert_rejected_without_target(self, result, case_dir, *needles):
        self.assertTrue(result.isError, self.error_text(result))
        text = self.error_text(result)
        self.assertTrue(text.strip())
        for needle in needles:
            self.assertIn(needle, text)
        self.assertFalse((self.workspace / case_dir).exists())

    async def test_v1_version_and_string_region_are_rejected(self):
        old_version = self.example("fixed-source-water.json")
        old_version["schema_version"] = "1"
        result = await self.call("openmc_build_model",
                                 {"case_dir": "cases/v1-version-rejected", "spec": old_version})
        self.assert_rejected_without_target(result, "cases/v1-version-rejected",
                                            "Input schema validation failed", "spec.schema_version")
        old_region = self.example("fixed-source-water.json")
        old_region["cells"][0]["region"] = "-1"
        result = await self.call("openmc_build_model",
                                 {"case_dir": "cases/v1-region-rejected", "spec": old_region})
        self.assert_rejected_without_target(result, "cases/v1-region-rejected",
                                            "Input schema validation failed", "spec.cells.0.region")
        capabilities = self.data(await self.call("openmc_model_capabilities", {}))
        self.assertEqual(capabilities["schema_version"], "2")
