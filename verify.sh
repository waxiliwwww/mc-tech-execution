#!/usr/bin/env bash
# Verify this project, 2026-10-13. Local protocol/model fixtures; no external model calls.
set -euo pipefail
mc_verify_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$mc_verify_root"
mc_verify_tools_only=0
mc_verify_release=0
if [[ "${1:-}" == --tools-only ]]; then mc_verify_tools_only=1; export MC_VERIFY_TOOLS_ONLY=1; shift; fi
if [[ "${1:-}" == --release ]]; then mc_verify_release=1; export MC_VERIFY_RELEASE=1; shift; fi
if [[ $# -gt 0 || ( $mc_verify_tools_only == 1 && $mc_verify_release == 1 ) ]]; then
  printf '用法：./verify.sh [--tools-only|--release]\n' >&2; exit 2
fi
if [[ $mc_verify_release == 1 && -z "${MC_TEST_OPENMC_CONFIG:-}" ]]; then
  printf '发布门禁要求设置 MC_TEST_OPENMC_CONFIG；未设置时真实用例会 skip，不能当通过。\n' >&2
  exit 2
fi
mkdir -p artifacts
printf '开始校验：检查依赖并运行工程测试。\n'
printf '[1/4] 检查 Python 依赖...\n'
if [[ ! -x mcp/.venv/bin/python ]]; then "${PYTHON:-python3}" -m venv mcp/.venv; fi
mcp/.venv/bin/python -m pip install --disable-pip-version-check --quiet -r mcp/requirements.txt
if [[ -n "${MC_TEST_OPENMC_CONFIG:-}" ]]; then
  mcp/.venv/bin/python -m pip install --disable-pip-version-check --quiet -r tests/requirements-real.txt
fi
mcp/.venv/bin/python -m pip check > artifacts/python-dependencies.log
if [[ $mc_verify_tools_only == 0 ]]; then
printf '[2/4] 安装锁定的 Harness 开发依赖...\n'
corepack pnpm@10.23.0 --dir harness install --frozen-lockfile --ignore-scripts > artifacts/node-dependencies.log
else
  printf '[2/4] 工具包独立校验：不安装可选Harness依赖。\n'
fi
printf '[3/4] 运行业务逻辑与 MCP 集成测试...\n'
# A signal to this shell's PID must reach the owned supervisor too; terminal
# process-group delivery alone is insufficient for CI/desktop cancellation.
mc_verify_test_pid=''
mc_verify_stop_tests() {
  local mc_verify_sig="$1" mc_verify_exit="$2"
  trap '' INT TERM HUP
  if [[ -n "$mc_verify_test_pid" ]]; then
    kill -s "$mc_verify_sig" "$mc_verify_test_pid" 2>/dev/null || true
    wait "$mc_verify_test_pid" 2>/dev/null || true
  fi
  exit "$mc_verify_exit"
}
trap 'mc_verify_stop_tests INT 130' INT
trap 'mc_verify_stop_tests TERM 143' TERM
trap 'mc_verify_stop_tests HUP 129' HUP
./scripts/test-mcp.sh -- mcp/.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v > artifacts/python-tests.log 2>&1 &
mc_verify_test_pid=$!
wait "$mc_verify_test_pid"
mc_verify_test_pid=''
trap - INT TERM HUP
if [[ $mc_verify_release == 1 ]]; then
  printf '[3b/4] 检查发布门禁：关键真实用例不得 skip...\n'
  mcp/.venv/bin/python scripts/check-release-evidence.py artifacts/python-tests.log
fi
if [[ $mc_verify_tools_only == 0 ]]; then
printf '[4/4] 运行 Harness 策略与终端测试...\n'
node --test tests/harness-*.mjs > artifacts/harness-tests.log 2>&1
else
  printf '[4/4] 已按--tools-only排除可选Harness检查。\n'
fi
printf '\n校验成功：已执行的工程检查均通过，未发现明显问题。\n'
printf '检查范围：依赖一致性、OpenMC 接口与计算作业、MCP 调用、修复确认；是否包含Harness以本次选项及日志为准。\n'
if [[ -n "${MC_TEST_OPENMC_CONFIG:-}" ]]; then
  printf '真实 OpenMC 检查：已启用（含配置允许的真实运行；具体例题以日志为准，不作专业验收）。\n'
else
  printf '真实 OpenMC 检查：未启用，启用方法见 docs/测试与验收说明.md。\n'
fi
printf '详细记录：%s/artifacts\n' "$mc_verify_root"
if [[ $mc_verify_release == 1 ]]; then
  printf '发布门禁：关键真实用例已实际执行；仍需人工核对结果含义与未证明边界。\n'
fi
