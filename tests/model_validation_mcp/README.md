# model-spec/v2 真实 MCP 校验用例

这里一个用例一个文件；文件头写明测试功能、类别、前置、操作、预期和不证明的内容。`support.py` 只负责启动/停止独立 MCP 服务、连接和通用断言，不藏场景。

| 文件 | 用例 | 类别 |
| --- | --- | --- |
| `test_01_positive_minimal_sphere.py` | 最小球构建、能力发现、settings 下界 | 正例 + 下界 |
| `test_02_positive_custom_lattice.py` | 调用方自定义格架构建与预览 | 正例 |
| `test_03_negative_v1_contract.py` | v1 schema_version / 字符串 region 被输入 schema 拒绝 | 反例 |
| `test_04_negative_semantic_and_numeric_bounds.py` | 悬空面语义拒绝、零粒子数下界、失败后恢复 | 反例 |
| `test_05_boundary_outer_transmission_override.py` | 根宇宙 transmission 拒绝与专家覆盖 tag | 边界 |

每条测试都会启动一个新的 `mcp/server.py`，通过标准 streamable HTTP MCP 客户端调用公开工具；只构建，不提交求解。

```bash
MC_TEST_OPENMC_CONFIG="$PWD/config/openmc.local.json" \
  mcp/.venv/bin/python -m unittest discover \
  -s tests/model_validation_mcp -t tests/model_validation_mcp -p 'test_*.py' -v
```

未设置 `MC_TEST_OPENMC_CONFIG` 时全部 skip，不能计为通过。该组用例不证明物理结果、统计收敛、几何无重叠、核数据适用性、异机部署或正式验收。
