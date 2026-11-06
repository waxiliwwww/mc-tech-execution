"""MV-MCP-04：反例：悬空面引用与零粒子数

测试功能
--------
`openmc_build_model` 的跨字段语义校验（region 面引用必须存在）和输入 schema 的数值下界（`particles>=1`）；失败后的服务恢复能力。

类别
----
反例 + 语义边界

前置
----
真实 MCP 服务（每条用例由测试基类新启动一次），设置 `MC_TEST_OPENMC_CONFIG`；OpenMC 0.16.0 运行时可用；测试只构建，不提交求解。

操作
----
先提交 region 面编号 99 的最小球，再提交 `particles=0` 的最小球，最后提交合法模型。

预期
----
悬空面被语义校验拒绝并返回面引用错误；零粒子数在 `spec.settings.particles` 被 schema 拒绝；两次都不创建目标目录；随后合法构建成功。

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
class TestNegativeSemanticAndNumericBounds(RealMCPCase):
    def assert_rejected_without_target(self, result, case_dir, *needles):
        self.assertTrue(result.isError, self.error_text(result))
        text = self.error_text(result)
        self.assertTrue(text.strip())
        for needle in needles:
            self.assertIn(needle, text)
        self.assertFalse((self.workspace / case_dir).exists())

    async def test_dangling_surface_and_zero_particles_are_rejected(self):
        spec = self.example("fixed-source-water.json")
        spec["cells"][0]["region"]["surface"] = 99
        result = await self.call("openmc_build_model",
                                 {"case_dir": "cases/dangling-surface", "spec": spec})
        self.assert_rejected_without_target(result, "cases/dangling-surface",
                                            "model-spec校验失败", "面引用不存在")
        zero = self.example("fixed-source-water.json")
        zero["settings"]["particles"] = 0
        result = await self.call("openmc_build_model",
                                 {"case_dir": "cases/zero-particles", "spec": zero})
        self.assert_rejected_without_target(result, "cases/zero-particles",
                                            "spec.settings.particles")
        fixed = self.example("fixed-source-water.json")
        built = self.data(await self.call("openmc_build_model",
                                          {"case_dir": "cases/after-reject", "spec": fixed}))
        self.assertTrue(built["api_parsed"])
