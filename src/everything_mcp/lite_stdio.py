"""Minimal stdio MCP server for low standby memory in Codex Desktop.

This module intentionally avoids importing FastMCP or the MCP SDK. It handles
the small JSON-RPC surface needed for Everything_Mew tools and imports the
actual tool implementations only when a tool is called.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from importlib import import_module
from typing import Any, cast

from .contracts import SortName

SERVER_NAME = "Everything_Mew_Lite"
SERVER_VERSION = "0.1.0"
JSONRPC_VERSION = "2.0"
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_INSTRUCTIONS = (
    "Everything_Mew is a read-only Windows file and folder discovery server backed by Everything. "
    "Use everything_count before broad searches, add path/extension/date/size filters for large result sets, "
    "and use normal filesystem tools to read or modify files after locating paths."
)
VALID_SORTS: tuple[SortName, ...] = ("name", "path", "size", "date_modified")
ToolPayload = dict[str, Any] | str
ToolFunc = Callable[..., ToolPayload]

TOOL_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "name": "everything_status",
        "description": "Report whether Everything and the selected read-only backend are available.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "everything_count",
        "description": "Count matching Everything results before searching broad queries.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "everything_search",
        "description": "Return path-first Everything search candidates with optional metadata.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "scope": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "sort": {"type": "string", "enum": ["name", "path", "size", "date_modified"]},
                "metadata": {"type": "boolean"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
    {
        "name": "everything_syntax_help",
        "description": "Return concise Everything query syntax help.",
        "inputSchema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
)


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        params = _params_object(message)
        if params is None:
            return _error(request_id, -32602, "Invalid params: expected object.")
        return _result(request_id, _initialize_result(params))
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": [dict(tool) for tool in TOOL_DEFINITIONS]})
    if method == "tools/call":
        params = _params_object(message)
        if params is None:
            return _error(request_id, -32602, "Invalid params: expected object.")
        return _result(request_id, _call_tool_result(params))
    return _error(request_id, -32601, f"Method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        response = _handle_line(line)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def _handle_line(line: str) -> dict[str, Any] | None:
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error(None, -32700, f"Parse error: {exc.msg}")
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    try:
        return handle_message(message)
    except Exception as exc:  # pragma: no cover - defensive server boundary
        request_id = message.get("id")
        return _error(request_id, -32603, f"Internal error: {exc}")


def _initialize_result(params: dict[str, Any]) -> dict[str, Any]:
    requested_protocol_version = params.get("protocolVersion")
    protocol_version = requested_protocol_version if isinstance(requested_protocol_version, str) else DEFAULT_PROTOCOL_VERSION
    return {
        "protocolVersion": protocol_version,
        "capabilities": {"tools": {}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": SERVER_INSTRUCTIONS,
    }


def _params_object(message: dict[str, Any]) -> dict[str, Any] | None:
    params = message.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        return None
    return params


def _call_tool_result(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(name, str):
        return _tool_error("Missing tool name.")
    if not isinstance(arguments, dict):
        return _tool_error("Tool arguments must be an object.")
    try:
        payload = _call_tool(name, arguments)
    except KeyError:
        return _tool_error(f"Unknown tool: {name}")
    except Exception as exc:
        return _tool_error(str(exc))
    return _tool_success(payload)


def _call_tool(name: str, arguments: dict[str, Any]) -> ToolPayload:
    if name == "everything_status":
        return _server_tool("everything_status")()
    if name == "everything_count":
        query = _string_arg(arguments, "query")
        scope = _optional_string_arg(arguments, "scope")
        return _server_tool("everything_count")(
            query=query,
            scope=scope,
        )
    if name == "everything_search":
        query = _string_arg(arguments, "query")
        scope = _optional_string_arg(arguments, "scope")
        limit = _optional_int_arg(arguments, "limit")
        sort = _sort_arg(arguments)
        metadata = _bool_arg(arguments, "metadata")
        return _server_tool("everything_search")(
            query=query,
            scope=scope,
            limit=limit,
            sort=sort,
            metadata=metadata,
        )
    if name == "everything_syntax_help":
        topic = _optional_string_arg(arguments, "topic")
        return _server_tool("everything_syntax_help")(topic=topic)
    raise KeyError(name)


def _server_tool(name: str) -> ToolFunc:
    server = import_module("everything_mcp.server")
    return cast(ToolFunc, getattr(server, name))


def _string_arg(arguments: dict[str, Any], name: str) -> str:
    value = arguments.get(name, "")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    return value


def _optional_string_arg(arguments: dict[str, Any], name: str) -> str | None:
    value = arguments.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    return value


def _optional_int_arg(arguments: dict[str, Any], name: str) -> int | None:
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    return value


def _bool_arg(arguments: dict[str, Any], name: str) -> bool:
    value = arguments.get(name, False)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean.")
    return value


def _sort_arg(arguments: dict[str, Any]) -> SortName:
    value = arguments.get("sort", "name")
    if value not in VALID_SORTS:
        joined = ", ".join(VALID_SORTS)
        raise ValueError(f"sort must be one of: {joined}.")
    return cast(SortName, value)


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
