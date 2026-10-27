"""Generic adapters for trusted Python functions, CLI programs, and HTTP JSON APIs."""
import asyncio
import inspect
import json
import math
import os
import signal
from urllib.parse import urlsplit

import httpx
from jsonschema import Draft202012Validator


class ToolExecutionError(Exception):
    """Safe actionable message for a tool caller; never include raw program output."""


def strict_json(value):
    """Reject non-JSON values, including NaN and Infinity."""
    try:
        return json.dumps(value, allow_nan=False, ensure_ascii=False)
    except (TypeError, ValueError) as error:
        raise ToolExecutionError("Value must be finite, JSON-serializable data.") from error


def decode_json(data):
    try:
        return json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite number")))
    except (ValueError, UnicodeError) as error:
        raise ToolExecutionError("Adapter returned invalid JSON; stdout/response must contain one JSON value.") from error


def validate_value(value, schema, direction):
    strict_json(value)
    errors = sorted(Draft202012Validator(schema).iter_errors(value), key=lambda e: str(list(e.path)))
    if errors:
        # jsonschema's default message may contain submitted secrets. Report paths only.
        first = errors[0]
        path = ".".join(map(str, first.path)) or "$"
        raise ToolExecutionError(f"{direction} schema validation failed at {path} ({first.validator}).")


def public_tool(definition):
    return {key: definition[key] for key in ("name", "description", "inputSchema", "outputSchema", "annotations") if key in definition}


def validate_registry(tools):
    names = set()
    for tool in tools:
        if not isinstance(tool.get("name"), str) or not tool["name"] or tool["name"] in names:
            raise ValueError("Each tool needs a unique nonempty name.")
        names.add(tool["name"])
        if not isinstance(tool.get("description"), str) or not tool["description"]:
            raise ValueError(f"Tool {tool['name']} needs a description.")
        for key in ("inputSchema", "outputSchema"):
            Draft202012Validator.check_schema(tool[key])
            if tool[key].get("type") != "object":
                raise ValueError(f"Tool {tool['name']} {key} must describe an object.")
        if sum(key in tool for key in ("function", "program", "http")) != 1:
            raise ValueError(f"Tool {tool['name']} needs exactly one execution adapter.")
        if "function" in tool and not callable(tool["function"]):
            raise ValueError("Function adapter must be callable.")


def limits(spec):
    timeout = spec.get("timeoutSeconds", 30)
    cap = spec.get("maxOutputBytes", 1024 * 1024)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ToolExecutionError("Adapter timeoutSeconds must be a positive finite number.")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ToolExecutionError("Adapter maxOutputBytes must be a positive integer.")
    return timeout, cap


async def run_program(spec, arguments):
    """No shell interpolation. Bounds combined stdout/stderr; kills POSIX child group."""
    timeout, cap = limits(spec)
    argv = spec.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v or "\0" in v for v in argv):
        raise ToolExecutionError("Program argv must be a nonempty list of strings.")
    try:
        process = await asyncio.create_subprocess_exec(
            *argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, cwd=spec.get("cwd"), start_new_session=os.name == "posix",
        )
    except OSError as error:
        raise ToolExecutionError(f"Program could not start ({type(error).__name__}); check argv and runtime installation.") from error
    total = 0

    async def read_stream(stream):
        nonlocal total
        chunks = []
        while chunk := await stream.read(8192):
            total += len(chunk)
            if total > cap:
                raise ToolExecutionError(f"Program output exceeds {cap} bytes (stdout and stderr combined).")
            chunks.append(chunk)
        return b"".join(chunks)

    async def send_input():
        try:
            process.stdin.write((strict_json(arguments) + "\n").encode())
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    tasks = [asyncio.create_task(read_stream(process.stdout)), asyncio.create_task(read_stream(process.stderr)),
             asyncio.create_task(send_input()), asyncio.create_task(process.wait())]
    try:
        stdout, _stderr, _, code = await asyncio.wait_for(asyncio.gather(*tasks), timeout)
        if code != 0:
            raise ToolExecutionError(f"Program exited with code {code}; run the configured CLI directly to inspect its stderr.")
        result = decode_json(stdout)
        if not isinstance(result, dict):
            raise ToolExecutionError("Program output must be a JSON object.")
        return result
    except asyncio.TimeoutError as error:
        raise ToolExecutionError(f"Program timed out after {timeout:g} seconds.") from error
    finally:
        # Kill the owned process group even if the parent exited but descendants kept pipes open.
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.returncode is None:
            process.kill()
        await process.wait()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def post_json(spec, arguments):
    """POST to a developer-fixed URL; no redirects, environment credentials, bounded body."""
    timeout, cap = limits(spec)
    endpoint = spec.get("url", "")
    parsed = urlsplit(endpoint)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ToolExecutionError("HTTP adapter requires a fixed http(s) URL without embedded credentials or fragment.")
    headers = {"Accept": "application/json"}
    token_env = spec.get("tokenEnv")
    if token_env:
        token = os.environ.get(token_env)
        if not token:
            raise ToolExecutionError(f"HTTP adapter credential environment variable {token_env} is missing.")
        headers["Authorization"] = f"Bearer {token}"
    try:
        # wait_for bounds the total duration, not only the individual socket operations.
        async def request():
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                async with client.stream("POST", endpoint, json=arguments, headers=headers) as response:
                    if not 200 <= response.status_code < 300:
                        raise ToolExecutionError(f"HTTP adapter returned status {response.status_code}.")
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > cap:
                            raise ToolExecutionError(f"HTTP response exceeds {cap} bytes.")
                    return decode_json(bytes(body))
        return await asyncio.wait_for(request(), timeout)
    except (asyncio.TimeoutError, httpx.TimeoutException) as error:
        raise ToolExecutionError(f"HTTP adapter timed out after {timeout:g} seconds.") from error
    except httpx.HTTPError as error:
        raise ToolExecutionError(f"HTTP adapter connection failed ({type(error).__name__}).") from error


async def execute_tool(tool, arguments):
    validate_value(arguments, tool["inputSchema"], "Input")
    if "program" in tool:
        result = await run_program(tool["program"], arguments)
    elif "http" in tool:
        result = await post_json(tool["http"], arguments)
    elif inspect.iscoroutinefunction(tool["function"]):
        result = await tool["function"](**arguments)
    else:
        # Prevent ordinary synchronous business code from blocking the HTTP event loop.
        # Python threads cannot be forcibly killed; use program adapter for hard timeouts.
        result = await asyncio.to_thread(tool["function"], **arguments)
    validate_value(result, tool["outputSchema"], "Output")
    return result
