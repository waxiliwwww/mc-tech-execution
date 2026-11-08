#!/usr/bin/env bash
# Own the test service; never attach to an unrelated running endpoint.
set -euo pipefail
mc_test_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$mc_test_root/mcp/.venv/bin/python" "$mc_test_root/scripts/mcp_test_session.py" "$@"
