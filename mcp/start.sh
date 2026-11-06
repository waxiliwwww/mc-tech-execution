#!/usr/bin/env bash
# MCP Python launcher, 2026-10-13. Run ./start.sh; first run needs network or WHEELHOUSE.
set -euo pipefail
MCP_TOOLKIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$MCP_TOOLKIT_DIR"
printf '准备 MCP 依赖并启动（首次运行可能需要几分钟）...\n' >&2
"$MCP_TOOLKIT_DIR/setup.sh" >&2
exec .venv/bin/python server.py "$@"
