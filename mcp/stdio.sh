#!/usr/bin/env bash
# No network or dependency installation during a host's MCP connection.
set -euo pipefail
mc_stdio_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$mc_stdio_root"
if [[ ! -x mcp/.venv/bin/python ]]; then
  printf '尚未准备本地环境。先运行：bash "%s/scripts/local-setup.sh"\n' "$mc_stdio_root" >&2
  exit 2
fi
# A fixed workspace makes jobs available across independent host sessions.
if [[ -z "${MC_WORKSPACE:-}" && -f config/launcher.local.json ]]; then
  MC_WORKSPACE="$(mcp/.venv/bin/python -c 'import json;print(json.load(open("config/launcher.local.json"))["workspace"])')"
  export MC_WORKSPACE
fi
exec mcp/.venv/bin/python mcp/stdio_service.py
