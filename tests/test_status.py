from importlib import import_module

contracts = import_module("everything_mcp.contracts")
server = import_module("everything_mcp.server")


class StatusAdapter:
    name = "fake"

    def status(self):
        return contracts.AdapterStatus(
            everything_installed=True,
            everything_running=True,
            backend="sdk-ipc",
            es_cli_available=False,
            notes=("ready",),
        )


def test_status_contract_uses_camel_case_keys() -> None:
    assert server.everything_status(adapter=StatusAdapter()) == {
        "everythingInstalled": True,
        "everythingRunning": True,
        "backend": "sdk-ipc",
        "esCliAvailable": False,
        "httpAvailable": False,
        "notes": ["ready"],
    }
