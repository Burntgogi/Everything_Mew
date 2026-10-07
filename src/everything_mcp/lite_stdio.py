"""Minimal stateful stdio MCP server for low standby memory.

This module intentionally avoids importing FastMCP or the MCP SDK. It handles
the JSON-RPC lifecycle and schemas needed for Everything_Mew tools.

By default the resident process is only a protocol broker: each validated
Everything tool call runs in a short-lived worker process (the one-shot
runner) that exits as soon as it has answered. The worker owns every ctypes,
IPC, and adapter allocation, so its memory returns to Windows after each call
and a stuck call is stopped by killing the worker.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable, Iterator
from enum import Enum
from importlib import import_module
from math import isfinite
from typing import Any, BinaryIO, cast

from .tool_specs import TOOL_SPEC_BY_NAME, tool_definitions
from .validation import ToolValidationError, validate_tool_arguments

SERVER_NAME = "Everything_Mew_Lite"
JSONRPC_VERSION = "2.0"
TOOL_EXECUTION_ERROR = "Tool execution failed."
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_INSTRUCTIONS = (
    "Everything_Mew is a read-only Windows file and folder discovery server backed by Everything. "
    "Pass a scope inside host-configured allowed roots to search or count, then add path/extension/date/size filters "
    "for large result sets. everything_search also reports totalCount, so a separate count is rarely needed; "
    "lead with ext:, size:, or dm: filters for the fastest queries. "
    "Use normal filesystem tools to read or modify files after locating paths."
)
EXECUTION_ENV = "EVERYTHING_MCP_EXECUTION"
WORKER_TIMEOUT_ENV = "EVERYTHING_MCP_WORKER_TIMEOUT"
IDLE_EXIT_ENV = "EVERYTHING_MCP_IDLE_EXIT_SECONDS"
DEFAULT_WORKER_TIMEOUT_SECONDS = 30.0
# Answered from static data in the broker; every other tool talks to Everything.
BROKER_TOOLS = frozenset({"everything_syntax_help"})
CREATE_NO_WINDOW = 0x08000000
ToolPayload = dict[str, Any] | str
ToolFunc = Callable[..., ToolPayload]
ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


class SessionState(Enum):
    NEW = "new"
    INITIALIZING = "initializing"
    READY = "ready"


def call_tool_result(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Validate and execute one tool call in this process."""
    spec = TOOL_SPEC_BY_NAME[name]
    try:
        validated_arguments = validate_tool_arguments(spec, arguments)
    except ToolValidationError as exc:
        return _tool_error(str(exc))

    try:
        payload = _call_tool(name, validated_arguments)
    except Exception:
        return _tool_error(TOOL_EXECUTION_ERROR)
    return _tool_success(payload)


class LiteSession:
    __slots__ = ("state", "executor")

    def __init__(self, state: SessionState = SessionState.NEW, executor: ToolExecutor = call_tool_result) -> None:
        self.state = state
        self.executor = executor


def handle_message(message: dict[str, Any], session: LiteSession | None = None) -> dict[str, Any] | None:
    active_session = session if session is not None else LiteSession()
    request_id = message.get("id") if "id" in message else None
    is_notification = "id" not in message

    if not is_notification and not _is_valid_request_id(request_id):
        return _error(None, -32600, "Invalid Request: id must be a string or number.")

    if message.get("jsonrpc") != JSONRPC_VERSION:
        return _error(request_id, -32600, "Invalid Request: jsonrpc must be exactly '2.0'.")

    method = message.get("method")
    if not isinstance(method, str):
        return _error(request_id, -32600, "Invalid Request: method must be a string.")

    if method == "notifications/initialized" and is_notification:
        if active_session.state is SessionState.INITIALIZING:
            active_session.state = SessionState.READY
        return None

    if is_notification:
        return None

    if method == "ping":
        return _result(request_id, {})

    if method == "initialize":
        if active_session.state is not SessionState.NEW:
            return _error(request_id, -32600, "Initialize request already received.")
        raw_params = message.get("params")
        if not isinstance(raw_params, dict):
            return _error(request_id, -32602, "Invalid params: expected object.")
        initialize_params = cast(dict[str, Any], raw_params)
        validation_error = _initialize_params_error(initialize_params)
        if validation_error is not None:
            return _error(request_id, -32602, validation_error)
        active_session.state = SessionState.INITIALIZING
        return _result(request_id, _initialize_result(initialize_params))

    if active_session.state is SessionState.NEW:
        return _error(request_id, -32002, "Server not initialized.")
    if active_session.state is SessionState.INITIALIZING:
        return _error(request_id, -32002, "Server initialization is not complete.")

    if method == "tools/list":
        return _result(request_id, {"tools": tool_definitions()})
    if method == "tools/call":
        raw_params = message.get("params")
        if not isinstance(raw_params, dict):
            return _error(request_id, -32602, "Invalid params: tools/call params must be an object.")
        params = cast(dict[str, Any], raw_params)
        name = params.get("name")
        if not isinstance(name, str) or not name:
            return _error(request_id, -32602, "Invalid params: tool name must be a non-empty string.")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "Invalid params: tool arguments must be an object.")
        if name not in TOOL_SPEC_BY_NAME:
            return _error(request_id, -32602, f"Unknown tool: {name}")
        return _result(request_id, active_session.executor(name, cast(dict[str, Any], arguments)))
    return _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    session = LiteSession(executor=executor_from_env())
    stdout = sys.stdout.buffer
    idle_seconds = _env_seconds(IDLE_EXIT_ENV, 0.0)
    # MCP stdio is UTF-8 regardless of the Windows ANSI code page, so use the binary streams.
    for line in _read_lines(sys.stdin.buffer, idle_seconds):
        if not line.strip():
            continue
        response = _handle_line(line, session)
        if response is not None:
            stdout.write(encode_message(response) + b"\n")
            stdout.flush()
    if idle_seconds > 0:
        stdout.flush()
        # The stdin reader thread may still be blocked in a read; skip interpreter teardown.
        os._exit(0)


def encode_message(message: dict[str, Any]) -> bytes:
    try:
        return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except UnicodeEncodeError:
        # Unpaired surrogates from Windows names are not UTF-8; escape them instead.
        return json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def _read_lines(stream: BinaryIO, idle_seconds: float) -> Iterator[bytes]:
    if idle_seconds <= 0:
        yield from iter(stream.readline, b"")
        return
    import queue
    import threading

    lines: queue.Queue[bytes | None] = queue.Queue()

    def pump() -> None:
        for line in iter(stream.readline, b""):
            lines.put(line)
        lines.put(None)

    threading.Thread(target=pump, name="everything-mew-stdin", daemon=True).start()
    while True:
        try:
            line = lines.get(timeout=idle_seconds)
        except queue.Empty:
            return
        if line is None:
            return
        yield line


def executor_from_env() -> ToolExecutor:
    mode = os.environ.get(EXECUTION_ENV, "").strip().lower() or "worker"
    if mode == "inprocess" or not sys.executable:
        return call_tool_result
    timeout = _env_seconds(WORKER_TIMEOUT_ENV, DEFAULT_WORKER_TIMEOUT_SECONDS) or DEFAULT_WORKER_TIMEOUT_SECONDS

    def run_in_worker(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return worker_tool_result(name, arguments, timeout)

    return run_in_worker


def worker_tool_result(name: str, arguments: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    """Run one validated tool call in a fresh one-shot process and return its CallToolResult."""
    spec = TOOL_SPEC_BY_NAME[name]
    try:
        validated_arguments = validate_tool_arguments(spec, arguments)
    except ToolValidationError as exc:
        return _tool_error(str(exc))
    if name in BROKER_TOOLS:
        return call_tool_result(name, validated_arguments)

    import subprocess

    request = json.dumps({"schemaVersion": 1, "tool": name, "arguments": validated_arguments}, ensure_ascii=True)
    try:
        completed = subprocess.run(
            worker_command(),
            input=request.encode("ascii"),
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
            creationflags=CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except subprocess.TimeoutExpired:
        return _tool_error(f"Everything_Mew worker exceeded {timeout_seconds:g} seconds and was stopped.")
    except OSError:
        return _tool_error("Everything_Mew worker could not be started.")
    if completed.returncode not in (0, 1):
        return _tool_error(TOOL_EXECUTION_ERROR)
    try:
        result = json.loads(completed.stdout)
    except ValueError:
        return _tool_error(TOOL_EXECUTION_ERROR)
    if not isinstance(result, dict) or not isinstance(result.get("content"), list):
        return _tool_error(TOOL_EXECUTION_ERROR)
    if type(result.get("isError")) is not bool:
        return _tool_error(TOOL_EXECUTION_ERROR)
    return cast(dict[str, Any], result)


# Appended, not prepended: the package parent may be site-packages, and nothing there may shadow
# the standard library.
_WORKER_BOOTSTRAP = "import sys; sys.path.append(sys.argv[1]); from everything_mcp.oneshot import main; main()"


def worker_command() -> list[str]:
    """Return the one-shot worker command line.

    The worker runs the base interpreter in isolated mode without site
    processing: the package has no dependencies, so only its own parent
    directory is put on sys.path. This skips the venv redirector process and
    .pth processing, and ignores PYTHON* variables and the working directory.
    """
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base = getattr(sys, "_base_executable", "") or ""
    interpreter = base if base and os.path.isfile(base) else sys.executable
    return [interpreter, "-I", "-S", "-c", _WORKER_BOOTSTRAP, package_parent]


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if isfinite(value) and value > 0 else 0.0


def _handle_line(line: str | bytes, session: LiteSession | None = None) -> dict[str, Any] | None:
    try:
        text = line.decode("utf-8") if isinstance(line, bytes) else line
    except UnicodeDecodeError:
        return _error(None, -32700, "Parse error: request is not valid UTF-8")
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        return _error(None, -32700, f"Parse error: {exc.msg}")
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    try:
        return handle_message(message, session)
    except Exception:  # pragma: no cover - defensive server boundary
        raw_request_id = message.get("id") if "id" in message else None
        request_id = raw_request_id if _is_valid_request_id(raw_request_id) else None
        return _error(request_id, -32603, "Internal error.")


def _initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    from .version import __version__

    requested_protocol_version = params.get("protocolVersion")
    protocol_version = (
        requested_protocol_version
        if requested_protocol_version in SUPPORTED_PROTOCOL_VERSIONS
        else DEFAULT_PROTOCOL_VERSION
    )
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": __version__},
        "instructions": SERVER_INSTRUCTIONS,
    }


def _initialize_params_error(params: dict[str, Any]) -> str | None:
    if not isinstance(params.get("protocolVersion"), str):
        return "Invalid params: protocolVersion must be a string."
    if not isinstance(params.get("capabilities"), dict):
        return "Invalid params: capabilities must be an object."

    client_info = params.get("clientInfo")
    if not isinstance(client_info, dict):
        return "Invalid params: clientInfo must be an object."
    if not isinstance(client_info.get("name"), str):
        return "Invalid params: clientInfo.name must be a string."
    if not isinstance(client_info.get("version"), str):
        return "Invalid params: clientInfo.version must be a string."
    return None


def _call_tool(name: str, arguments: dict[str, Any]) -> ToolPayload:
    if name == "everything_status":
        return _server_tool("everything_status")()
    if name == "everything_count":
        return _server_tool("everything_count")(
            query=cast(str, arguments["query"]),
            scope=cast(str | None, arguments.get("scope")),
        )
    if name == "everything_search":
        return _server_tool("everything_search")(
            query=cast(str, arguments["query"]),
            scope=cast(str | None, arguments.get("scope")),
            limit=cast(int | None, arguments.get("limit")),
            sort=cast(str, arguments.get("sort", "name")),
            metadata=cast(bool, arguments.get("metadata", False)),
        )
    if name == "everything_syntax_help":
        return _server_tool("everything_syntax_help")(topic=cast(str | None, arguments.get("topic")))
    raise KeyError(name)


def _server_tool(name: str) -> ToolFunc:
    if name == "everything_syntax_help":
        # Static text: avoid importing the adapter stack in the broker.
        return cast(ToolFunc, import_module("everything_mcp.syntax").syntax_help)
    server = import_module("everything_mcp.server")
    return cast(ToolFunc, getattr(server, name))


def _is_valid_request_id(value: Any) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and isfinite(value)


def _tool_success(payload: ToolPayload) -> dict[str, Any]:
    if isinstance(payload, dict):
        # Compact JSON: the text block is what most hosts show the model, so whitespace costs tokens.
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            # Backend, query, policy, and configuration failures carry an "error" object.
            "isError": "error" in payload,
        }
    return {"content": [{"type": "text", "text": payload}], "isError": False}


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
