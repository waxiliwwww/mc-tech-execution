#!/usr/bin/env bash
# MCP Python launcher, 2026-10-13. Run ./start.sh; first run needs network or WHEELHOUSE.
set -euo pipefail
MCP_TOOLKIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$MCP_TOOLKIT_DIR"
"$MCP_TOOLKIT_DIR/setup.sh" >&2
exec .venv/bin/python server.py "$@"
