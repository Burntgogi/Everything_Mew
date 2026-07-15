from importlib import import_module
from typing import Any

contracts = import_module("everything_mcp.contracts")
server = import_module("everything_mcp.server")


class StatusAdapter:
    name = "fake"

    def status(self) -> Any:
        return contracts.AdapterStatus(
            everything_installed=True,
            everything_running=True,
            backend="sdk-ipc",
            es_cli_available=False,
            db_loaded=True,
            version="1.4.1.1032",
            target_machine="x64",
            notes=("ready",),
        )


def test_status_contract_uses_camel_case_keys() -> None:
    assert server.everything_status(adapter=StatusAdapter()) == {
        "everythingInstalled": True,
        "everythingRunning": True,
        "backend": "sdk-ipc",
        "esCliAvailable": False,
        "httpAvailable": False,
        "dbLoaded": True,
        "version": "1.4.1.1032",
        "targetMachine": "x64",
        "notes": ["ready"],
    }


def test_status_contract_omits_unavailable_optional_sdk_fields() -> None:
    result = contracts.AdapterStatus(False, False, "none", False).to_tool_result()

    assert "dbLoaded" not in result
    assert "version" not in result
    assert "targetMachine" not in result
