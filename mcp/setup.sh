#!/usr/bin/env bash
# Shared dependency setup for the MCP server and the direct CLI.
set -euo pipefail
mc_setup_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$mc_setup_root"
mc_setup_python="${PYTHON:-python3}"
"$mc_setup_python" -c 'import sys; assert sys.version_info >= (3, 10), "MCP requires Python 3.10+; OpenMC API 0.16 requires 3.12+"'
if [[ ! -x .venv/bin/python ]]; then "$mc_setup_python" -m venv .venv; fi
mc_setup_hash="$(.venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
if [[ ! -f .venv/.requirements.sha256 ]] || [[ "$(cat .venv/.requirements.sha256)" != "$mc_setup_hash" ]]; then
  mc_setup_args=(--disable-pip-version-check --quiet install -r requirements.txt)
  if [[ -n "${WHEELHOUSE:-}" ]]; then mc_setup_args+=(--no-index --find-links "$WHEELHOUSE"); fi
  .venv/bin/python -m pip "${mc_setup_args[@]}"
  .venv/bin/python -m pip check
  printf '%s\n' "$mc_setup_hash" > .venv/.requirements.sha256
fi
