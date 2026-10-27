#!/usr/bin/env bash
# Local maintenance entry; preserve the caller's cwd and all explicit options.
set -euo pipefail
mc_cleanup_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${PYTHON:-python3}" "$mc_cleanup_root/scripts/cleanup_artifacts.py" "$@"
