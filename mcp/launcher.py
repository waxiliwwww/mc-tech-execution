"""Console entry points, 2026-11-03.

模块仍按平铺布局放在同一目录（cli.py、server.py 等），彼此以 `from tools import ...`
直接引用。安装成包后目录名变成 mc_agent_tools，这里把该目录加回 sys.path，
让原有引用照旧工作，不做大范围改名——后续若改成相对导入，只需要改这一处。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _prepare() -> None:
    here = str(HERE)
    if here not in sys.path:
        sys.path.insert(0, here)


def main(argv=None) -> int:
    """直接调用工具（等同 ./mc.sh）。"""
    _prepare()
    import cli
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    return cli.main()


def serve(argv=None) -> int:
    """启动标准 MCP 服务（等同 ./mcp/start.sh）。"""
    _prepare()
    if argv is not None:
        sys.argv = [sys.argv[0], *argv]
    runpy.run_path(str(HERE / "server.py"), run_name="__main__")
    return 0