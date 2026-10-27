#!/usr/bin/env bash
# Independent direct tool entry; setup messages stay off the JSON output stream.
set -euo pipefail
mc_cli_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$mc_cli_root/mcp/setup.sh" >&2
exec "$mc_cli_root/mcp/.venv/bin/python" "$mc_cli_root/mcp/cli.py" "$@"
