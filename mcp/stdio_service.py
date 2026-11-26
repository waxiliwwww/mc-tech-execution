"""Host-owned stdio transport over the same handlers as the HTTP service."""
import asyncio
import contextlib
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

from mcp.server.stdio import stdio_server
import runtime_identity
from server import EventLog, create_mcp_server


async def serve():
    version = runtime_identity.version()
    if not version:
        raise ValueError('无法读取工具包版本；请恢复完整安装。')
    config = SimpleNamespace(name='openmc-forge', version=version,
                             version_file=runtime_identity.VERSION_FILE if runtime_identity.VERSION_FILE.is_file() else None)
    # Capture protocol streams first. All ordinary Python prints, including
    # legacy business diagnostics, then go to stderr rather than corrupt JSON.
    async with stdio_server() as (read, write):
        with contextlib.redirect_stdout(sys.stderr):
            from personal_config import logs
            from openmc_runtime import workspace
            log = EventLog(logs(Path(__file__).resolve().parent.parent,workspace()))
            try:
                server, _ = create_mcp_server(config, log)
                log.emit('INFO', 'server.started', transport='stdio', version=version)
                await server.run(read, write, server.create_initialization_options())
            finally:
                log.emit('INFO', 'server.stopped')
                log.close()


def main():
    logging.getLogger().setLevel(logging.CRITICAL)
    try:
        asyncio.run(serve())
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print('stdio启动失败：' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
