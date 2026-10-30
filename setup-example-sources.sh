#!/usr/bin/env bash
# Initial setup only. Normal CLI/MCP calls use the fixed local source cache.
set -euo pipefail
mc_sources_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
"$mc_sources_root/mcp/setup.sh" >&2
exec "$mc_sources_root/mcp/.venv/bin/python" - "$mc_sources_root" "$@" <<'PY'
from pathlib import Path
import sys,argparse,json
root=Path(sys.argv[1]);sys.path.insert(0,str(root/'mcp'))
p=argparse.ArgumentParser(description='初次准备固定官方Notebook原文和配套输入')
p.add_argument('--asset-cache',help='可选已有官方输入缓存目录；仍核对固定SHA256')
p.add_argument('--source-cache',help='可选已有原始文件目录；仍按固定上游Git对象身份核对')
a=p.parse_args(sys.argv[2:])
from official_sources import prepare_notebook_sources
from official_assets import prepare_assets
print(json.dumps({'sources':prepare_notebook_sources(a.source_cache),'inputs':prepare_assets(a.asset_cache)},ensure_ascii=False,indent=2))
PY
