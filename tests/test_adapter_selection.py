from importlib import import_module
from pathlib import Path

selection = import_module("everything_mcp.adapters.selection")
config_module = import_module("everything_mcp.config")
contracts = import_module("everything_mcp.contracts")


class FakeSdkAdapter:
    name = "sdk-ipc"

    def __init__(self, config) -> None:
        self.config = config

    def status(self):
        return contracts.AdapterStatus(True, True, "sdk-ipc", False, notes=("sdk ready",))


def test_select_adapter_prefers_ready_sdk(monkeypatch) -> None:
    monkeypatch.setattr("everything_mcp.adapters.selection.SdkIpcAdapter", FakeSdkAdapter)
    monkeypatch.setattr("everything_mcp.adapters.selection.find_es_cli", lambda configured=None: Path(r"C:\Tools\es.exe"))

    adapter = selection.select_adapter(config_module.EverythingConfig(everything_exe=Path(r"C:\missing\Everything.exe")))

    assert adapter.name == "sdk-ipc"


def test_select_adapter_uses_es_cli_when_sdk_unavailable(monkeypatch) -> None:
    class UnavailableSdk(FakeSdkAdapter):
        def status(self):
            return contracts.AdapterStatus(False, False, "none", False, notes=("sdk unavailable",))

    monkeypatch.setattr("everything_mcp.adapters.selection.SdkIpcAdapter", UnavailableSdk)
    monkeypatch.setattr("everything_mcp.adapters.selection.find_es_cli", lambda configured=None: Path(r"C:\Tools\es.exe"))

    adapter = selection.select_adapter(config_module.EverythingConfig(everything_exe=Path(r"C:\missing\Everything.exe")))

    assert adapter.name == "es-cli"
    assert adapter.status().backend == "es-cli"


def test_select_adapter_returns_actionable_null_when_no_backend(monkeypatch) -> None:
    class UnavailableSdk(FakeSdkAdapter):
        def status(self):
            return contracts.AdapterStatus(False, False, "none", False, notes=("sdk unavailable",))

    monkeypatch.setattr("everything_mcp.adapters.selection.SdkIpcAdapter", UnavailableSdk)
    monkeypatch.setattr("everything_mcp.adapters.selection.find_es_cli", lambda configured=None: None)

    adapter = selection.select_adapter(config_module.EverythingConfig(everything_exe=Path(r"C:\missing\Everything.exe")))

    assert isinstance(adapter, selection.NullAdapter)
    status = adapter.status()
    assert status.backend == "none"
    assert any("ES CLI" in note for note in status.notes)
    assert any("HTTP" in note for note in status.notes)
