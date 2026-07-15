from __future__ import annotations

import importlib
import json
import sys
import tomllib
from collections.abc import MutableMapping
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any
from typing import cast

import pytest
from pytest import CaptureFixture, MonkeyPatch

MISSING_PARAMS = object()


def request(
    method: str,
    params: dict[str, Any] | None = None,
    id_: str | int | float | None = 1,
) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        message["params"] = params
    return message


def valid_initialize_params(protocol_version: str = "2025-11-25") -> dict[str, Any]:
    return {
        "protocolVersion": protocol_version,
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0"},
    }


def initialized_session(lite_stdio: Any, protocol_version: str = "2025-11-25") -> Any:
    session = lite_stdio.LiteSession()
    response = lite_stdio.handle_message(
        request("initialize", valid_initialize_params(protocol_version)),
        session,
    )
    assert response["result"]["protocolVersion"] == protocol_version
    assert (
        lite_stdio.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session,
        )
        is None
    )
    return session


def test_package_version_prefers_installed_distribution_metadata(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    version_module = importlib.import_module("everything_mcp.version")

    def installed_version(distribution_name: str) -> str:
        assert distribution_name == "everything-mew"
        return "9.8.7"

    def unexpected_source_fallback(pyproject_path: Path) -> str:
        pytest.fail(f"source fallback should not be read: {pyproject_path}")

    monkeypatch.setattr(version_module.metadata, "version", installed_version)
    monkeypatch.setattr(version_module, "_source_tree_version", unexpected_source_fallback)

    assert version_module._resolve_version(tmp_path / "missing-pyproject.toml") == "9.8.7"


def test_package_version_falls_back_to_structurally_parsed_pyproject(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    version_module = importlib.import_module("everything_mcp.version")
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        '[tool.decoy]\nversion = "0.0.0"\n\n[project]\nname = "everything-mew"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )

    def missing_distribution(distribution_name: str) -> str:
        raise version_module.metadata.PackageNotFoundError(distribution_name)

    monkeypatch.setattr(version_module.metadata, "version", missing_distribution)

    assert version_module._resolve_version(pyproject_path) == "1.2.3"


def test_package_version_literal_exists_only_in_pyproject() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))
    project = cast(dict[str, object], pyproject["project"])
    package_version = project["version"]
    assert isinstance(package_version, str)

    for relative_path in ("src/everything_mcp/version.py", "src/everything_mcp/lite_stdio.py"):
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        assert package_version not in source


def test_release_manifest_and_ci_cover_public_sdist_and_fastmcp() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((repository_root / "pyproject.toml").read_text(encoding="utf-8"))
    sdist_config = cast(dict[str, object], pyproject["tool"]["hatch"]["build"]["targets"]["sdist"])
    included_paths = cast(list[str], sdist_config["include"])

    assert "/docs" not in included_paths
    assert "/docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md" in included_paths
    assert "/tests" in included_paths
    assert all("SDK_DESIGN_REVIEW" not in path for path in included_paths)

    workflow_path = repository_root / ".github/workflows/ci.yml"
    if not workflow_path.is_file():
        pytest.skip("Repository CI workflow is intentionally excluded from the source distribution.")

    workflow = workflow_path.read_text(encoding="utf-8")
    assert 'python -m pip install -e ".[dev,server]"' in workflow
    assert "create_mcp()" in workflow
    assert "server.list_tools()" in workflow
    for tool_name in (
        "everything_status",
        "everything_count",
        "everything_search",
        "everything_syntax_help",
    ):
        assert tool_name in workflow


def test_opencode_examples_use_host_environment_substitution() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    config = cast(
        dict[str, Any],
        json.loads((repository_root / "opencode.example.json").read_text(encoding="utf-8")),
    )
    environment = config["mcp"]["everything-mew"]["environment"]
    assert environment["EVERYTHING_SDK_DLL"] == "{env:EVERYTHING_SDK_DLL}"

    for relative_path in ("README.md", "README.ko.md", "docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md"):
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        sdk_environment_lines = [line for line in source.splitlines() if '"EVERYTHING_SDK_DLL"' in line]
        assert sdk_environment_lines
        assert all("{env:EVERYTHING_SDK_DLL}" in line for line in sdk_environment_lines)
        assert all("%USERPROFILE%" not in line for line in sdk_environment_lines)


def test_sdk_guide_selects_dll_from_python_pointer_width_without_overwrite() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    source = (repository_root / "docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md").read_text(encoding="utf-8")

    assert "struct.calcsize('P') * 8" in source
    assert '$dllName = "Everything64.dll"' in source
    assert '$dllName = "Everything32.dll"' in source
    assert 'Join-Path $sdkRoot "dll\\$dllName"' in source
    assert 'Join-Path $destDir $dllName' in source
    assert "Copy-Item -LiteralPath $source -Destination $dest" in source
    assert "Refusing to overwrite existing SDK DLL" in source


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


def test_lite_stdio_tool_specs_are_shared_and_immutable() -> None:
    tool_specs = importlib.import_module("everything_mcp.tool_specs")
    search_spec = tool_specs.TOOL_SPEC_BY_NAME["everything_search"]

    with pytest.raises(FrozenInstanceError):
        setattr(search_spec, "name", "changed")
    with pytest.raises(TypeError):
        cast(MutableMapping[str, Any], search_spec.properties)["extra"] = object()
    with pytest.raises(TypeError):
        cast(MutableMapping[str, Any], tool_specs.TOOL_SPEC_BY_NAME)["extra"] = search_spec


def test_lite_stdio_supports_current_and_previous_protocol_versions() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    version_module = importlib.import_module("everything_mcp.version")

    assert lite_stdio.SUPPORTED_PROTOCOL_VERSIONS == ("2025-11-25", "2025-06-18")
    for protocol_version in lite_stdio.SUPPORTED_PROTOCOL_VERSIONS:
        session = lite_stdio.LiteSession()
        response = lite_stdio.handle_message(
            request(
                "initialize",
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "0"},
                },
            ),
            session,
        )

        assert response == {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "Everything_Mew_Lite", "version": version_module.__version__},
                "instructions": (
                    "Everything_Mew is a read-only Windows file and folder discovery server backed by Everything. "
                    "Use everything_count before broad searches, add path/extension/date/size filters for large "
                    "result sets, and use normal filesystem tools to read or modify files after locating paths."
                ),
            },
        }


def test_lite_stdio_selects_newest_version_when_client_requests_unsupported_version() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = lite_stdio.LiteSession()

    response = lite_stdio.handle_message(
        request("initialize", valid_initialize_params("2099-01-01")),
        session,
    )

    assert response["result"]["protocolVersion"] == "2025-11-25"


@pytest.mark.parametrize(
    ("params", "error_message"),
    [
        pytest.param(MISSING_PARAMS, "Invalid params: expected object.", id="missing-params"),
        pytest.param(None, "Invalid params: expected object.", id="null-params"),
        pytest.param([], "Invalid params: expected object.", id="non-object-params"),
        pytest.param(
            {"capabilities": {}, "clientInfo": {"name": "client", "version": "1"}},
            "Invalid params: protocolVersion must be a string.",
            id="missing-protocol-version",
        ),
        pytest.param(
            {
                "protocolVersion": 20251125,
                "capabilities": {},
                "clientInfo": {"name": "client", "version": "1"},
            },
            "Invalid params: protocolVersion must be a string.",
            id="wrong-protocol-version-type",
        ),
        pytest.param(
            {"protocolVersion": "2025-11-25", "clientInfo": {"name": "client", "version": "1"}},
            "Invalid params: capabilities must be an object.",
            id="missing-capabilities",
        ),
        pytest.param(
            {
                "protocolVersion": "2025-11-25",
                "capabilities": [],
                "clientInfo": {"name": "client", "version": "1"},
            },
            "Invalid params: capabilities must be an object.",
            id="wrong-capabilities-type",
        ),
        pytest.param(
            {"protocolVersion": "2025-11-25", "capabilities": {}},
            "Invalid params: clientInfo must be an object.",
            id="missing-client-info",
        ),
        pytest.param(
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": "client"},
            "Invalid params: clientInfo must be an object.",
            id="wrong-client-info-type",
        ),
        pytest.param(
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"version": "1"}},
            "Invalid params: clientInfo.name must be a string.",
            id="missing-client-name",
        ),
        pytest.param(
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": 1, "version": "1"},
            },
            "Invalid params: clientInfo.name must be a string.",
            id="wrong-client-name-type",
        ),
        pytest.param(
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "client"}},
            "Invalid params: clientInfo.version must be a string.",
            id="missing-client-version",
        ),
        pytest.param(
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "client", "version": 1},
            },
            "Invalid params: clientInfo.version must be a string.",
            id="wrong-client-version-type",
        ),
    ],
)
def test_lite_stdio_rejects_invalid_initialize_params_without_advancing_session(
    params: object,
    error_message: str,
) -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = lite_stdio.LiteSession()
    initialize_request = request("initialize", id_="invalid-initialize")
    if params is not MISSING_PARAMS:
        initialize_request["params"] = params

    response = lite_stdio.handle_message(initialize_request, session)

    assert response == {
        "jsonrpc": "2.0",
        "id": "invalid-initialize",
        "error": {"code": -32602, "message": error_message},
    }
    assert session.state is lite_stdio.SessionState.NEW

    retry = lite_stdio.handle_message(
        request("initialize", valid_initialize_params(), id_="valid-retry"),
        session,
    )

    assert retry["id"] == "valid-retry"
    assert retry["result"]["protocolVersion"] == "2025-11-25"
    assert session.state is lite_stdio.SessionState.INITIALIZING


def test_lite_stdio_rejects_messages_without_exact_jsonrpc_version() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    for jsonrpc in (None, "1.0", 2.0):
        message = request("ping")
        if jsonrpc is None:
            message.pop("jsonrpc")
        else:
            message["jsonrpc"] = jsonrpc

        response = lite_stdio.handle_message(message, lite_stdio.LiteSession())

        assert response == {
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32600, "message": "Invalid Request: jsonrpc must be exactly '2.0'."},
        }


@pytest.mark.parametrize("request_id", ["request-1", 1, 1.5, None])
def test_lite_stdio_accepts_string_number_and_null_request_ids(request_id: str | int | float | None) -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    response = lite_stdio.handle_message(request("ping", id_=request_id), lite_stdio.LiteSession())

    assert response == {"jsonrpc": "2.0", "id": request_id, "result": {}}


@pytest.mark.parametrize(
    "invalid_id",
    [[1], {"nested": "id"}, True, float("nan"), float("inf"), float("-inf")],
    ids=["list", "object", "bool", "nan", "positive-infinity", "negative-infinity"],
)
def test_lite_stdio_rejects_invalid_initialize_ids_without_advancing_session(invalid_id: object) -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = lite_stdio.LiteSession()
    params = valid_initialize_params()

    response = lite_stdio.handle_message(
        {"jsonrpc": "2.0", "id": invalid_id, "method": "initialize", "params": params},
        session,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": None,
        "error": {"code": -32600, "message": "Invalid Request: id must be a string, number, or null."},
    }
    assert session.state is lite_stdio.SessionState.NEW

    retry = lite_stdio.handle_message(request("initialize", params, id_="retry-1"), session)

    assert retry["id"] == "retry-1"
    assert retry["result"]["protocolVersion"] == "2025-11-25"
    assert session.state is lite_stdio.SessionState.INITIALIZING


def test_lite_stdio_allows_ping_before_initialize() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")

    response = lite_stdio.handle_message(request("ping", id_=3), lite_stdio.LiteSession())

    assert response == {"jsonrpc": "2.0", "id": 3, "result": {}}


def test_lite_stdio_requires_initialize_then_initialized_notification() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = lite_stdio.LiteSession()

    before_initialize = lite_stdio.handle_message(request("tools/list", id_=2), session)
    initialize_response = lite_stdio.handle_message(
        request("initialize", valid_initialize_params(), id_=3),
        session,
    )
    before_notification = lite_stdio.handle_message(request("tools/list", id_=4), session)
    notification_response = lite_stdio.handle_message(
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        session,
    )
    after_notification = lite_stdio.handle_message(request("tools/list", id_=5), session)

    assert before_initialize == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32002, "message": "Server not initialized."},
    }
    assert initialize_response["result"]["protocolVersion"] == "2025-11-25"
    assert before_notification == {
        "jsonrpc": "2.0",
        "id": 4,
        "error": {"code": -32002, "message": "Server initialization is not complete."},
    }
    assert notification_response is None
    assert after_notification["result"]["tools"]


def test_lite_stdio_does_not_accept_initialized_notification_before_initialize() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = lite_stdio.LiteSession()

    assert (
        lite_stdio.handle_message(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session,
        )
        is None
    )
    assert lite_stdio.handle_message(request("tools/list"), session)["error"]["code"] == -32002


def test_lite_stdio_tools_list_includes_everything_search_schema() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)

    response = lite_stdio.handle_message(request("tools/list"), session)
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
    assert search["inputSchema"]["additionalProperties"] is False
    assert search["inputSchema"]["properties"]["sort"]["enum"] == [
        "name",
        "path",
        "size",
        "date_modified",
    ]


def test_lite_stdio_call_tool_returns_structured_content_for_dict(monkeypatch: MonkeyPatch) -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)

    def fake_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert name == "everything_status"
        assert arguments == {}
        return {"backend": "sdk-ipc"}

    monkeypatch.setattr(lite_stdio, "_call_tool", fake_call_tool)

    response = lite_stdio.handle_message(
        request("tools/call", {"name": "everything_status", "arguments": {}}, id_=7),
        session,
    )

    assert response["jsonrpc"] == "2.0"
    assert response["id"] == 7
    assert response["result"]["structuredContent"] == {"backend": "sdk-ipc"}
    assert json.loads(response["result"]["content"][0]["text"]) == {"backend": "sdk-ipc"}
    assert response["result"]["isError"] is False


def test_lite_stdio_hides_unexpected_tool_exception_details(
    monkeypatch: MonkeyPatch,
    capsys: CaptureFixture[str],
) -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)
    sensitive_path = r"X:\fixtures\private\.env"
    sensitive_token = "sensitive-value-do-not-disclose"

    def failing_call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError(f"failed at {sensitive_path}: {sensitive_token}")

    monkeypatch.setattr(lite_stdio, "_call_tool", failing_call_tool)

    response = lite_stdio.handle_message(
        request("tools/call", {"name": "everything_status", "arguments": {}}, id_=8),
        session,
    )
    serialized_response = json.dumps(response)
    captured = capsys.readouterr()

    assert response["result"] == {
        "content": [{"type": "text", "text": "Tool execution failed."}],
        "isError": True,
    }
    assert sensitive_path not in serialized_response
    assert sensitive_token not in serialized_response
    assert sensitive_path not in captured.out + captured.err
    assert sensitive_token not in captured.out + captured.err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({}, "Missing required argument: query."),
        ({"query": "ext:py", "extra": True}, "Unexpected argument: extra."),
        ({"query": "ext:py", "sort": "newest"}, "sort must be one of: name, path, size, date_modified."),
        ({"query": "ext:py", "metadata": "false"}, "metadata must be a boolean."),
        ({"query": "ext:py", "limit": True}, "limit must be an integer."),
        ({"query": "ext:py", "limit": 1.5}, "limit must be an integer."),
        ({"query": "ext:py", "limit": 0}, "limit must be between 1 and 100."),
        ({"query": "ext:py", "limit": 101}, "limit must be between 1 and 100."),
    ],
)
def test_lite_stdio_validates_search_schema_before_importing_server(
    arguments: dict[str, Any],
    message: str,
) -> None:
    sys.modules.pop("everything_mcp.server", None)
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)

    response = lite_stdio.handle_message(
        request("tools/call", {"name": "everything_search", "arguments": arguments}),
        session,
    )

    assert response["result"]["isError"] is True
    assert response["result"]["content"][0]["text"] == message
    assert "everything_mcp.server" not in sys.modules


def test_lite_stdio_validates_count_required_string_before_importing_server() -> None:
    sys.modules.pop("everything_mcp.server", None)
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)

    response = lite_stdio.handle_message(
        request("tools/call", {"name": "everything_count", "arguments": {"query": 42}}),
        session,
    )

    assert response["result"]["content"][0]["text"] == "query must be a string."
    assert "everything_mcp.server" not in sys.modules


def test_lite_stdio_rejects_unknown_tool_without_importing_server() -> None:
    sys.modules.pop("everything_mcp.server", None)
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)

    response = lite_stdio.handle_message(
        request("tools/call", {"name": "everything_delete", "arguments": {}}),
        session,
    )

    assert response["result"]["content"][0]["text"] == "Unknown tool: everything_delete"
    assert "everything_mcp.server" not in sys.modules


def test_lite_stdio_unknown_method_returns_jsonrpc_error_after_initialization() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)

    response = lite_stdio.handle_message(request("resources/list", id_=9), session)

    assert response == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32601, "message": "Method not found: resources/list"},
    }


def test_lite_stdio_rejects_non_object_method_params() -> None:
    lite_stdio = importlib.import_module("everything_mcp.lite_stdio")
    session = initialized_session(lite_stdio)

    response = lite_stdio.handle_message(
        {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": []},
        session,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 10,
        "error": {"code": -32602, "message": "Invalid params: expected object."},
    }
