from __future__ import annotations

import importlib
import json
import sys
from typing import Any

from pytest import MonkeyPatch


def request(method: str, params: dict[str, Any] | None = None, id_: int = 1) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def test_lite_stdio_does_not_import_heavy_mcp_runtimes_on_load() -> None:
    sys.modules.pop("everything_mcp", None)
    sys.modules.pop("everything_mcp.server", None)
    sys.modules.pop("everything_mcp.lite_stdio", None)
    sys.modules.pop("fastmcp", None)
    sys.modules.pop("mcp", None)

    importlib.import_module("everything_mcp.lite_stdio")

    assert "everything_mcp.server" not in sys.modules
    assert "fastmcp" not in sys.modules
    assert "mcp" not in sys.modules


def test_lite_stdio_initialize_advertises_tool_capability() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    response = lite_stdio.handle_message(
        request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "0"},
            },
        )
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Everything_Mew_Lite", "version": "0.1.0"},
            "instructions": (
                "Everything_Mew is a read-only Windows file and folder discovery server backed by Everything. "
                "Use everything_count before broad searches, add path/extension/date/size filters for large result "
                "sets, and use normal filesystem tools to read or modify files after locating paths."
            ),
        },
    }


def test_lite_stdio_tools_list_includes_everything_search_schema() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    response = lite_stdio.handle_message(request("tools/list"))
    tools = response["result"]["tools"]
    search = next(tool for tool in tools if tool["name"] == "everything_search")

    assert {tool["name"] for tool in tools} == {
        "everything_status",
        "everything_count",
        "everything_search",
        "everything_syntax_help",
    }
    assert search["annotations"] == {"readOnlyHint": True, "destructiveHint": False}
    assert search["inputSchema"]["required"] == ["query"]
    assert search["inputSchema"]["properties"]["sort"]["enum"] == ["name", "path", "size", "date_modified"]


def test_lite_stdio_call_tool_returns_structured_content_for_dict(monkeypatch: MonkeyPatch) -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    def fake_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"backend": "sdk-ipc"}

    monkeypatch.setattr(lite_stdio, "_call_tool", fake_call_tool)

    response = lite_stdio.handle_message(
        request("tools/call", {"name": "everything_status", "arguments": {}}, id_=7)
    )

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 7
    assert response["result"]["structuredContent"] == {"backend": "sdk-ipc"}
    assert json.loads(response["result"]["content"][0]["text"]) == {"backend": "sdk-ipc"}
    assert response["result"]["isError"] is False


def test_lite_stdio_rejects_invalid_search_argument_type_without_importing_server() -> None:
    sys.modules.pop("everything_mcp.server", None)
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    response = lite_stdio.handle_message(
        request("tools/call", {"name": "everything_search", "arguments": {"query": "ext:py", "metadata": "false"}})
    )

    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == "metadata must be a boolean."
    assert "everything_mcp.server" not in sys.modules


def test_lite_stdio_unknown_method_returns_jsonrpc_error() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    response = lite_stdio.handle_message(request("resources/list", id_=9))

    assert response == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32601, "message": "Method not found: resources/list"},
    }


def test_lite_stdio_rejects_non_object_method_params() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    response = lite_stdio.handle_message({"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": []})

    assert response == {
        "jsonrpc": "2.0",
        "id": 10,
        "error": {"code": -32602, "message": "Invalid params: expected object."},
    }
