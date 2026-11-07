"""MV-MCP-04：边界：外边界 transmission 与专家覆盖 tag

测试功能
--------
根宇宙外边界 anti-hang 硬约束，以及 `validation.overrides` 的 `outer_boundary_transmission` 例外和 tag 持久化。

类别
----
边界 + 专家覆盖

前置
----
真实 MCP 服务（每条用例由测试基类新启动一次），设置 `MC_TEST_OPENMC_CONFIG`；OpenMC 0.16.0 运行时可用；测试只构建，不提交求解。

操作
----
把最小球根边界改为 `transmission`；先无覆盖提交，再加带 `reason`/`scope` 的覆盖提交；读回结果和 `mc-case.json`。

预期
----
无覆盖被拒绝并指出 transmission；有覆盖构建成功；结果 `validation.overrides` 和 `mc-case.json` 都保留同一覆盖 tag，无 warning。

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
class TestBoundaryOuterTransmissionOverride(RealMCPCase):
    def assert_rejected_without_target(self, result, case_dir, *needles):
        self.assertTrue(result.isError, self.error_text(result))
        text = self.error_text(result)
        self.assertTrue(text.strip())
        for needle in needles:
            self.assertIn(needle, text)
        self.assertFalse((self.workspace / case_dir).exists())

    async def test_outer_transmission_requires_explicit_override(self):
        spec = self.example("fixed-source-water.json")
        spec["surfaces"][0]["boundary"] = "transmission"
        rejected = await self.call("openmc_build_model",
                                   {"case_dir": "cases/outer-rejected", "spec": spec})
        self.assert_rejected_without_target(rejected, "cases/outer-rejected",
                                            "model-spec校验失败", "transmission")
        spec["validation"] = {"overrides": [{
            "code": "outer_boundary_transmission",
            "reason": "fixed-source escape boundary requested by test",
            "scope": "run",
        }]}
        built = self.data(await self.call("openmc_build_model",
                                          {"case_dir": "cases/outer-overridden", "spec": spec}))
        self.assertTrue(built["api_parsed"])
        self.assertEqual(built["validation"]["overrides"], [{
            "code": "outer_boundary_transmission",
            "reason": "fixed-source escape boundary requested by test",
            "scope": "run",
        }])
        self.assertEqual(built["validation"]["warnings"], [])
        marker = json.loads((self.workspace / "cases/outer-overridden/mc-case.json")
                            .read_text(encoding="utf-8"))
        self.assertEqual(marker["validation"]["overrides"][0]["code"],
                         "outer_boundary_transmission")
