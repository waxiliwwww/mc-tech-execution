#!/usr/bin/env python3
"""Generic MCP host, 2026-10-13. Start with ./start.sh; customize tools.py."""
import asyncio
import base64
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import sys
import time
from urllib.parse import urlsplit

from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings, TransportSecurityMiddleware
from mcp.types import CallToolResult, ImageContent, Resource, TextContent, Tool
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
import uvicorn

from adapters import ToolExecutionError, execute_tool, public_tool, validate_registry
import resources
from tools import TOOLS

ROOT = Path(__file__).resolve().parent
HEARTBEAT_SECONDS = 300


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def address(host, port):
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


def resolve_path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


@dataclass(frozen=True)
class Config:
    host: str
    port: int
    path: str
    name: str
    version: str
    client_name: str
    url: str
    token: str = field(repr=False)
    connection_file: Path
    config_file: Path

    @property
    def connection(self):
        entry = {"name": self.client_name, "transport": "streamable-http", "url": self.url}
        if self.token:
            entry["headers"] = {"Authorization": "Bearer ${MCP_AUTH_TOKEN}"}
        return [entry]

    @property
    def authentication(self):
        return ({"implemented": True, "type": "bearer", "tokenEnv": "MCP_AUTH_TOKEN"}
                if self.token else {"implemented": False, "type": "none"})


def load_config(env=None, defaults=None):
    env = os.environ if env is None else env
    config_file = resolve_path(env.get("MCP_CONFIG_FILE", "service.config.json"))
    if defaults is None:
        try:
            defaults = json.loads(config_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Cannot read service configuration: {config_file} ({type(error).__name__}).") from error
    if not isinstance(defaults, dict) or not {"host", "port", "path", "name", "clientName"} <= defaults.keys():
        raise ValueError("Configuration requires host, port, path, name, clientName and version/versionFile.")
    defaults = dict(defaults)
    if "versionFile" in defaults:
        if not isinstance(defaults["versionFile"], str) or not defaults["versionFile"].strip():
            raise ValueError("versionFile must be a nonempty path.")
        version_path = Path(defaults["versionFile"])
        if not version_path.is_absolute():
            version_path = config_file.parent / version_path
        try:
            defaults["version"] = version_path.read_text(encoding="utf-8").strip()
        except OSError as error:
            # A wheel has no repository root. Its distribution version is built
            # by setuptools from the same VERSION, never a config literal.
            if defaults["versionFile"] != "../VERSION" or config_file.resolve() != (ROOT / 'service.config.json').resolve():
                raise ValueError("Cannot read versionFile: " + str(version_path)) from error
            from importlib.metadata import PackageNotFoundError, distribution
            try:
                installed = distribution("mc-agent-tools")
                owner = Path(installed.locate_file('mc_agent_tools/server.py')).resolve()
                if owner != (ROOT / 'server.py').resolve():
                    raise ValueError("Installed package metadata does not belong to this server; restore VERSION.") from error
                defaults["version"] = installed.version
            except PackageNotFoundError:
                raise ValueError("Cannot read VERSION or installed package version.") from error
    if "version" not in defaults:
        raise ValueError("Configuration requires version or versionFile.")
    host = env.get("MCP_HOST", defaults["host"])
    if not isinstance(host, str) or not host or host.strip() != host:
        raise ValueError("MCP_HOST must be an IP address or localhost.")
    if host != "localhost":
        try:
            ipaddress.ip_address(host)
        except ValueError as error:
            raise ValueError("MCP_HOST must be an IP address or localhost (public DNS belongs in publicUrl).") from error
    raw_port = env.get("MCP_PORT", defaults["port"])
    if isinstance(raw_port, bool) or not re.fullmatch(r"[0-9]+", str(raw_port)) or not 1 <= int(raw_port) <= 65535:
        raise ValueError("MCP_PORT must be an integer between 1 and 65535.")
    port = int(raw_port)
    path = env.get("MCP_PATH", defaults["path"])
    if (not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*", path)
            or path.lower() in ("/health", "/info") or any(v in (".", "..") for v in path.split("/"))):
        raise ValueError("MCP_PATH must be an absolute literal path, excluding /health, /info and dot segments.")
    for key in ("name", "version", "clientName"):
        if not isinstance(defaults[key], str) or not defaults[key].strip() or any(ord(c) < 32 for c in defaults[key]):
            raise ValueError(f"Configuration {key} must be a nonempty single-line string.")
    token = env.get("MCP_AUTH_TOKEN", "")
    if token and (not token.isascii() or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in token)):
        raise ValueError("MCP_AUTH_TOKEN must contain visible ASCII characters without whitespace.")
    public_url = env.get("MCP_PUBLIC_URL", defaults.get("publicUrl"))
    if not loopback(host) and (not public_url or not token):
        raise ValueError("Non-loopback listening requires publicUrl/MCP_PUBLIC_URL and MCP_AUTH_TOKEN.")
    url = public_url or f"http://{address(host, port)}{path}"
    if not isinstance(url, str) or any(c.isspace() for c in url):
        raise ValueError("publicUrl must be a complete http(s) MCP URL.")
    try:
        parsed = urlsplit(url)
        _ = parsed.port
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != path or parsed.hostname in ("0.0.0.0", "::")):
            raise ValueError
        if not loopback(host) and loopback(parsed.hostname):
            raise ValueError
    except ValueError as error:
        raise ValueError("publicUrl must use a reachable host, match path, and exclude credentials/query/fragment.") from error
    return Config(host, port, path, defaults["name"], defaults["version"], defaults["clientName"], url, token,
                  resolve_path(env.get("MCP_CONNECTION_FILE", "connection.json")), config_file)


def snapshot_arguments(arguments):
    """Freeze received JSON before business code can mutate it; logging is best-effort."""
    try:
        def mask(record):
            return {key: "[redacted]" if re.fullmatch(r"password|passwd|secret|token|access_token|refresh_token|api[_-]?key|authorization|credentials?", key, re.I) else value
                    for key, value in record.items()}
        return json.loads(json.dumps(arguments, ensure_ascii=False, allow_nan=False), object_hook=mask)
    except Exception:
        # Unsupported values still go through the original adapter validation.
        # This marker keeps diagnostic JSON finite without pretending to coerce input.
        return {"_loggingError": "Parameters are not finite JSON data."}


class EventLog:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = directory / f"server-{datetime.now().strftime('%Y%m%dT%H%M%S%f')}-{os.getpid()}.jsonl"
        self.file = self.path.open("x", encoding="utf-8")
        self.path.chmod(0o600)
        self.warned = False

    def emit(self, level, event, **fields):
        if "arguments" in fields:
            fields["arguments"] = snapshot_arguments(fields["arguments"])
        now = datetime.now().astimezone()
        record = {"time": now.isoformat(), "level": level, "event": event, **fields}
        try:
            self.file.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            self.file.flush()
        except Exception:
            if not self.warned:
                print("事件日志写入失败；工具结果不受影响。", file=sys.stderr, flush=True)
                self.warned = True
        stamp = now.strftime("%Y-%m-%d %H:%M:%S %z")
        message = None
        if event == "tool.executed":
            parameters = json.dumps(snapshot_arguments(fields.get("arguments", {})), ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":"))
            message = f"执行 {fields['name']} 完成 · 参数：{parameters}"
        elif event == "tool.failed":
            parameters = json.dumps(snapshot_arguments(fields.get("arguments", {})), ensure_ascii=False,
                                    allow_nan=False, separators=(",", ":"))
            message = f"执行 {fields['name']} 失败 · 参数：{parameters}"
        elif event == "server.heartbeat":
            seconds = int(fields["uptimeMs"] / 1000)
            message = f"已运行 {seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
        elif event == "server.stopped":
            message = "MCP 已停止"
        elif level == "ERROR":
            message = f"{event}：{fields.get('message', fields.get('code', 'unknown'))}"
        if message:
            print(f"[{stamp}] {message}", file=sys.stderr if level in ("WARNING", "ERROR") else sys.stdout, flush=True)

    def close(self):
        self.file.close()


def security_settings(config):
    parsed = urlsplit(config.url)
    # Exact host + port; support both the public reverse-proxy Host and local backend Host.
    hosts = {parsed.netloc, address(config.host, config.port)}
    origins = {f"{parsed.scheme}://{parsed.netloc}"}
    if loopback(config.host) or config.host in ("0.0.0.0", "::"):
        hosts.update(address(h, config.port) for h in ("127.0.0.1", "localhost", "::1"))
        origins.update(f"http://{address(h, config.port)}" for h in ("127.0.0.1", "localhost", "::1"))
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=sorted(hosts), allowed_origins=sorted(origins))


def create_app(config, log, tools=None):
    definitions = TOOLS if tools is None else tools
    validate_registry(definitions)
    registry = {tool["name"]: tool for tool in definitions}
    server = Server(config.name, version=config.version)

    @server.list_tools()
    async def list_tools():
        tools = [Tool(**public_tool(tool)) for tool in definitions]
        # A real host's discovery is different evidence from /info or startup.
        # Stateless HTTP does not retain initialize.clientInfo across requests.
        # Record the transport peer, never headers, credentials or capabilities.
        with contextlib.suppress(Exception):
            request = server.request_context.request
            peer = request.client if request else None
            log.emit("INFO", "tools.listed", count=len(tools), names=[tool.name for tool in tools],
                     peer={"host": peer.host, "port": peer.port} if peer else None)
        return tools

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        parameters = snapshot_arguments(arguments)
        try:
            if name not in registry:
                raise ToolExecutionError("Unknown tool; call tools/list to discover supported tools.")
            result = await execute_tool(registry[name], arguments)
            if name == 'openmc_environment':
                # Bind the MCP observation to this server's startup identity, not
                # a VERSION file that may have changed while it was running.
                result = {**result, "service": {"name": config.name, "version": config.version,
                                                "version_source": "mcp_server_startup"}}
            with contextlib.suppress(Exception):
                log.emit("INFO", "tool.executed", name=name, arguments=parameters)
            if name in ('openmc_plot_results', 'openmc_plot_scan') and result.get('path', '').endswith('.png'):
                from openmc_runtime import path_in_workspace
                preview = path_in_workspace(result['path'], internal=True)
                if preview.stat().st_size <= 2 * 1024 * 1024:
                    data = preview.read_bytes()
                    if data.startswith(b'\x89PNG\r\n\x1a\n'):
                        return CallToolResult(structuredContent=result, content=[TextContent(type='text', text=json.dumps(result, ensure_ascii=False)),
                                              ImageContent(type='image', data=base64.b64encode(data).decode(), mimeType='image/png')])
            return result
        except Exception as error:
            # The user requested submitted arguments. Auth headers, environment
            # credentials and raw business output remain outside the event fields.
            with contextlib.suppress(Exception):
                log.emit("WARNING", "tool.failed", name=name if name in registry else "unknown",
                         code=type(error).__name__, arguments=parameters)
            message = str(error) if isinstance(error, ToolExecutionError) else "Business function failed; inspect the business implementation."
            return CallToolResult(isError=True, content=[TextContent(type="text", text=message)])

    @server.list_resources()
    async def list_resources():
        return [Resource(**item) for item in resources.RESOURCES]

    @server.read_resource()
    async def read_resource(uri):
        return resources.read(str(uri))

    security = security_settings(config)
    checker = TransportSecurityMiddleware(security)
    manager = StreamableHTTPSessionManager(server, stateless=True, json_response=True, security_settings=security)

    @contextlib.asynccontextmanager
    async def lifespan(_):
        async with manager.run():
            yield

    life = Starlette(lifespan=lifespan)
    info = {"schemaVersion": 1, "service": {"name": config.name, "version": config.version},
            "transport": "streamable-http", "url": config.url, "authentication": config.authentication,
            "connection": config.connection, "tools": [public_tool(tool) for tool in definitions], "resources": list(resources.RESOURCES)}

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return await life(scope, receive, send)
        request = Request(scope, receive)
        path = scope["path"]
        if path == "/health":
            response = JSONResponse({"status": "ok", "service": config.name, "version": config.version}) if request.method == "GET" else JSONResponse({"error": "Method not allowed"}, 405)
            return await response(scope, receive, send)
        if path not in (config.path, "/info"):
            return await JSONResponse({"error": "Not found"}, 404)(scope, receive, send)
        if config.token:
            supplied = request.headers.get("authorization", "")
            if not secrets.compare_digest(supplied.encode(), f"Bearer {config.token}".encode()):
                return await JSONResponse({"error": "Unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        if path == "/info":
            rejected = await checker.validate_request(request)
            if rejected:
                return await rejected(scope, receive, send)
            response = JSONResponse(info) if request.method == "GET" else JSONResponse({"error": "Method not allowed"}, 405)
            return await response(scope, receive, send)
        try:
            await manager.handle_request(scope, receive, send)
        except Exception as error:
            log.emit("ERROR", "mcp.request_failed", code=type(error).__name__)
            raise

    return app


def write_connection(config):
    target = config.connection_file
    temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            temporary.chmod(0o600)
            json.dump(config.connection, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


async def heartbeat(log, started, interval=HEARTBEAT_SECONDS):
    while True:
        await asyncio.sleep(interval)
        log.emit("INFO", "server.heartbeat", uptimeMs=round((time.monotonic() - started) * 1000), intervalMs=round(interval * 1000))


class ToolkitServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # Uvicorn normally re-raises SIGTERM after cleanup, bypassing our final log.
        previous = {sig: signal.signal(sig, self.handle_exit) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            yield
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)


async def serve(config, log):
    # Bind before reporting success or writing connection metadata.
    family = socket.AF_INET6 if ":" in config.host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    server_task = beat = None
    started = False
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((config.host, config.port))
        sock.listen(128)
        app = create_app(config, log)
        uv = ToolkitServer(uvicorn.Config(app, host=config.host, port=config.port, log_config=None,
                            access_log=False, log_level="critical", timeout_graceful_shutdown=3))
        server_task = asyncio.create_task(uv.serve(sockets=[sock]))
        while not uv.started and not server_task.done():
            await asyncio.sleep(0.01)
        if not uv.started:
            await server_task
            raise RuntimeError("ASGI startup did not complete.")
        write_connection(config)
        started = True
        log.emit("INFO", "server.started", url=config.url, transport="streamable-http", authentication=config.authentication,
                 connectionFile=str(config.connection_file), eventLogFile=str(log.path), heartbeatIntervalMs=HEARTBEAT_SECONDS * 1000)
        print(f"{config.name} {config.version} · MCP 已就绪", flush=True)
        print("─" * 44, flush=True)
        print(f"地址    {config.url}", flush=True)
        print("传输    streamable-http", flush=True)
        print(f"鉴权    {'Bearer（Authorization 头）' if config.token else '未启用'}", flush=True)
        print(f"工具    {len(TOOLS)}", flush=True)
        print(f"事件日志：{log.path}", flush=True)
        beat = asyncio.create_task(heartbeat(log, time.monotonic()))
        await server_task
    finally:
        if beat:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
        if server_task and not server_task.done():
            uv.should_exit = True
            await server_task
        sock.close()
        if started:
            log.emit("INFO", "server.stopped")


def main():
    logging.getLogger().setLevel(logging.CRITICAL)
    try:
        config = load_config()
    except Exception as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    log = None
    try:
        log = EventLog(ROOT / "logs")
        asyncio.run(serve(config, log))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        message = f"{type(error).__name__} (errno={getattr(error, 'errno', None)}); check port, configuration and writable connection/log paths."
        if log:
            log.emit("ERROR", "server.failed", message=message)
        else:
            print(f"服务启动失败：{message}", file=sys.stderr)
        return 1
    finally:
        if log:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
