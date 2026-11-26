#!/usr/bin/env bash
# No network or dependency installation during a host's MCP connection.
set -euo pipefail
mc_stdio_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$mc_stdio_root"
if [[ ! -x mcp/.venv/bin/python ]]; then
  printf '尚未准备本地环境。先运行：bash "%s/scripts/local-setup.sh"\n' "$mc_stdio_root" >&2
  exit 2
fi
# Python resolves the explicit, saved or original default workspace for all transports.
exec mcp/.venv/bin/python mcp/stdio_service.py
