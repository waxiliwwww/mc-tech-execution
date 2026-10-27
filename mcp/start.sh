#!/usr/bin/env bash
# MCP Python launcher, 2026-10-13. Run ./start.sh; first run needs network or WHEELHOUSE.
set -euo pipefail
MCP_TOOLKIT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$MCP_TOOLKIT_DIR"
MCP_PYTHON_BIN="${PYTHON:-python3}"
"$MCP_PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 10), "Python 3.10+ required"'
if [[ ! -x .venv/bin/python ]]; then
  "$MCP_PYTHON_BIN" -m venv .venv
fi
MCP_REQUIREMENTS_HASH="$(.venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
if [[ ! -f .venv/.requirements.sha256 ]] || [[ "$(cat .venv/.requirements.sha256)" != "$MCP_REQUIREMENTS_HASH" ]]; then
  MCP_PIP_ARGS=(--disable-pip-version-check --quiet install -r requirements.txt)
  if [[ -n "${WHEELHOUSE:-}" ]]; then
    MCP_PIP_ARGS+=(--no-index --find-links "$WHEELHOUSE")
  fi
  .venv/bin/python -m pip "${MCP_PIP_ARGS[@]}"
  .venv/bin/python -m pip check >/dev/null
  printf '%s\n' "$MCP_REQUIREMENTS_HASH" > .venv/.requirements.sha256
fi
exec .venv/bin/python server.py "$@"
