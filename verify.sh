#!/usr/bin/env bash
# Verify this project, 2026-10-13. Local protocol/model fixtures; no external model calls.
set -euo pipefail
mc_verify_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$mc_verify_root"
mkdir -p artifacts
printf '开始校验：检查依赖并运行工程测试。\n'
printf '[1/4] 检查 Python 依赖...\n'
if [[ ! -x mcp/.venv/bin/python ]]; then "${PYTHON:-python3}" -m venv mcp/.venv; fi
mcp/.venv/bin/python -m pip install --disable-pip-version-check --quiet -r mcp/requirements.txt
mcp/.venv/bin/python -m pip check > artifacts/python-dependencies.log
printf '[2/4] 安装锁定的 Harness 开发依赖...\n'
corepack pnpm@10.23.0 --dir harness install --frozen-lockfile --ignore-scripts > artifacts/node-dependencies.log
printf '[3/4] 运行业务逻辑与 MCP 集成测试...\n'
mcp/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v > artifacts/python-tests.log 2>&1
printf '[4/4] 运行 Harness 策略与终端测试...\n'
node --test tests/harness-*.mjs > artifacts/harness-tests.log 2>&1
printf '\n校验成功：已执行的工程检查均通过，未发现明显问题。\n'
printf '检查范围：依赖一致性、MCP 调用、修复确认、Harness 模型备用策略。\n'
printf '详细记录：%s/artifacts\n' "$mc_verify_root"
