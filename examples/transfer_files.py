#!/usr/bin/env python3
"""Source-tree compatibility entry; installed command: mc-agent-tools-transfer."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'mcp'))
import host_transfer
if __name__=='__main__':raise SystemExit(host_transfer.main())
# Existing importers and patches share the canonical implementation and state.
sys.modules[__name__]=host_transfer
