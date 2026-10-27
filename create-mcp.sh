#!/usr/bin/env bash
# Create an editable independent service from a bundled, pinned source template.
set -euo pipefail
mc_create_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$mc_create_root/scripts/create_mcp.py" "$@"
