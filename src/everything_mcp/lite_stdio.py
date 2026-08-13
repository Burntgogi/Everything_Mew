"""Minimal stateful stdio MCP server for low standby memory in Codex Desktop.

This module intentionally avoids importing FastMCP or the MCP SDK. It handles
the JSON-RPC lifecycle and schemas needed for Everything_Mew tools, importing
the actual tool implementations only after a tool call is fully validated.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from math import isfinite
from typing import Any, cast

from .contracts import SortName
from .tool_specs import TOOL_SPEC_BY_NAME, tool_definitions
from .validation import ToolValidationError, validate_tool_arguments
from .version import __version__

SERVER_NAME = "Everything_Mew_Lite"
JSONRPC_VERSION = "2.0"
TOOL_EXECUTION_ERROR = "Tool execution failed."
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18")
DEFAULT_PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[0]
SERVER_INSTRUCTIONS = (
    "Everything_Mew is a read-only Windows file and folder discovery server backed by Everything. "
    "Use everything_count before broad searches, add path/extension/date/size filters for large result sets, "
    "and use normal filesystem tools to read or modify files after locating paths."
)
ToolPayload = dict[str, Any] | str
ToolFunc = Callable[..., ToolPayload]


class SessionState(Enum):
    NEW = "new"
    INITIALIZING = "initializing"
    READY = "ready"


@dataclass(slots=True)
class LiteSession:
    state: SessionState = SessionState.NEW


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
        return _result(request_id, call_tool_result(name, cast(dict[str, Any], arguments)))
    return _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    session = LiteSession()
    for line in sys.stdin:
        if not line.strip():
            continue
        response = _handle_line(line, session)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def _handle_line(line: str, session: LiteSession | None = None) -> dict[str, Any] | None:
    try:
        message = json.loads(line)
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


def call_tool_result(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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
            sort=cast(SortName, arguments.get("sort", "name")),
            metadata=cast(bool, arguments.get("metadata", False)),
        )
    if name == "everything_syntax_help":
        return _server_tool("everything_syntax_help")(topic=cast(str | None, arguments.get("topic")))
    raise KeyError(name)


def _server_tool(name: str) -> ToolFunc:
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
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": False,
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
