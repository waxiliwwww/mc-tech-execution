"""MV-MCP-01：正例：最小球模型与 settings 下界

测试功能
--------
`openmc_model_capabilities` 的 schema/校验后端发现；`openmc_build_model` 的 model-spec/v2 输入门禁、OpenMC Python API XML 导出与 `validation` tag 持久化。

类别
----
正例 + 参数下界

前置
----
真实 MCP 服务（每条用例由测试基类新启动一次），设置 `MC_TEST_OPENMC_CONFIG`；OpenMC 0.16.0 运行时可用；测试只构建，不提交求解。

操作
----
用 `fixed-source-water.json` 构造最小球模型，设置 `particles=1`、`batches=2`、`inactive=0`；通过 MCP 调用能力发现和构建，再读回算例文件。

预期
----
能力发现返回 schema v2、服务端校验后端和构建器后端策略；构建结果分别列出服务端校验后端与构建器实际后端，二者不混用；构建成功并通过 API 读回；`files` 等于落盘重建集（含 `build_model.py`、`model-spec.json`、`model_validation/`），`xml_files` 为四个 XML；`mc-case.json` 带 schema v2 与 validation。

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
class TestPositiveMinimalSphere(RealMCPCase):
    async def test_minimal_sphere_builds_through_mcp(self):
        capabilities = self.data(await self.call("openmc_model_capabilities", {}))
        self.assertEqual(capabilities["schema_version"], "2")
        self.assertIn(capabilities["validation_backend"], ("pydantic", "fallback"))
        self.assertEqual(capabilities["builder_validation_backend_policy"],
                         "pydantic_if_available_else_fallback")
        self.assertTrue(capabilities["validation_backend_scope"])
        spec = self.example("fixed-source-water.json")
        spec["settings"]["particles"] = 1
        spec["settings"]["batches"] = 2
        built = self.data(await self.call("openmc_build_model",
                                          {"case_dir": "cases/minimal-sphere", "spec": spec}))
        self.assertTrue(built["api_parsed"])
        self.assertEqual(built["validation"]["schema_version"], "2")
        # MCP server and OpenMC Python may have different importable backends;
        # the builder must report the one it actually used.
        self.assertIn(built["validation"]["backend"], ("pydantic", "fallback"))
        self.assertEqual(built["validation_backend"], capabilities["validation_backend"])
        self.assertEqual(built["builder_validation_backend"], built["validation"]["backend"])
        self.assertTrue(built["validation_backend_scope"])
        target = self.workspace / "cases/minimal-sphere"
        on_disk = {p.relative_to(target).as_posix() for p in target.rglob("*")
                   if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"}
        self.assertEqual(set(built["files"]), on_disk)
        self.assertEqual(set(built["xml_files"]),
                         {"materials.xml", "geometry.xml", "settings.xml", "tallies.xml"})
        self.assertIn("build_model.py", built["files"])
        self.assertIn("model-spec.json", built["files"])
        self.assertIn("model_validation/schema_v2.json", built["files"])
        self.assertTrue((target / "build_model.py").is_file())
        self.assertTrue((target / "model_validation/schema_v2.json").is_file())
        marker = json.loads((target / "mc-case.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["schema_version"], "2")
        self.assertEqual(marker["validation"]["backend"], built["validation"]["backend"])
