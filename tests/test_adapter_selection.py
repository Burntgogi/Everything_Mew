from importlib import import_module
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

selection = import_module("everything_mcp.adapters.selection")
config_module = import_module("everything_mcp.config")
contracts = import_module("everything_mcp.contracts")

SDK_DLL = Path(r"C:\Tools\Everything64.dll")


class FakeSdkAdapter:
    name = "sdk-ipc"

    def __init__(self, config: Any) -> None:
        self.config = config

    def status(self) -> Any:
        return contracts.AdapterStatus(True, True, "sdk-ipc", False, notes=("sdk ready",))


class FakeNativeAdapter:
    name = "native-ipc"
    reachable = True

    def __init__(self, config: Any) -> None:
        self.config = config

    def is_reachable(self) -> bool:
        return self.reachable


class UnreachableNative(FakeNativeAdapter):
    reachable = False


def _config(**overrides: Any) -> Any:
    return config_module.EverythingConfig(everything_exe=Path(r"C:\missing\Everything.exe"), **overrides)


def test_select_adapter_prefers_reachable_native_ipc(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(selection, "NativeIpcAdapter", FakeNativeAdapter)
    monkeypatch.setattr(selection, "SdkIpcAdapter", FakeSdkAdapter)

    adapter = selection.select_adapter(_config(sdk_dll=SDK_DLL))

    assert adapter.name == "native-ipc"


def test_select_adapter_uses_configured_sdk_when_native_ipc_is_unreachable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(selection, "NativeIpcAdapter", UnreachableNative)
    monkeypatch.setattr(selection, "SdkIpcAdapter", FakeSdkAdapter)
    monkeypatch.setattr(selection, "find_es_cli", lambda configured=None: Path(r"C:\Tools\es.exe"))

    adapter = selection.select_adapter(_config(sdk_dll=SDK_DLL))

    assert adapter.name == "sdk-ipc"


def test_select_adapter_skips_sdk_dll_when_none_is_configured(monkeypatch: MonkeyPatch) -> None:
    def unexpected_sdk(config: Any) -> Any:
        raise AssertionError("the SDK DLL adapter must stay unloaded without a configured DLL")

    monkeypatch.setattr(selection, "NativeIpcAdapter", UnreachableNative)
    monkeypatch.setattr(selection, "SdkIpcAdapter", unexpected_sdk)
    monkeypatch.setattr(selection, "find_es_cli", lambda configured=None: None)

    adapter = selection.select_adapter(_config())

    assert isinstance(adapter, selection.NullAdapter)


def test_select_adapter_uses_es_cli_when_sdk_unavailable(monkeypatch: MonkeyPatch) -> None:
    class UnavailableSdk(FakeSdkAdapter):
        def status(self) -> Any:
            return contracts.AdapterStatus(False, False, "none", False, notes=("sdk unavailable",))

    monkeypatch.setattr(selection, "NativeIpcAdapter", UnreachableNative)
    monkeypatch.setattr(selection, "SdkIpcAdapter", UnavailableSdk)
    monkeypatch.setattr(selection, "find_es_cli", lambda configured=None: Path(r"C:\Tools\es.exe"))

    adapter = selection.select_adapter(_config(sdk_dll=SDK_DLL))

    assert adapter.name == "es-cli"
    assert "sdk unavailable" in adapter.sdk_notes


def test_select_adapter_returns_actionable_null_when_no_backend(monkeypatch: MonkeyPatch) -> None:
    class UnavailableSdk(FakeSdkAdapter):
        def status(self) -> Any:
            return contracts.AdapterStatus(False, False, "none", False, notes=("sdk unavailable",))

    monkeypatch.setattr(selection, "NativeIpcAdapter", UnreachableNative)
    monkeypatch.setattr(selection, "SdkIpcAdapter", UnavailableSdk)
    monkeypatch.setattr(selection, "find_es_cli", lambda configured=None: None)

    adapter = selection.select_adapter(_config(sdk_dll=SDK_DLL))

    assert isinstance(adapter, selection.NullAdapter)
    status = adapter.status()
    assert status.backend == "none"
    assert any("IPC window" in note for note in status.notes)
    assert any("ES CLI" in note for note in status.notes)
    assert any("HTTP" in note for note in status.notes)


@pytest.mark.parametrize(
    ("backend", "expected"),
    [("native", "native-ipc"), ("sdk", "sdk-ipc"), ("es", "es-cli")],
)
def test_forced_backend_skips_automatic_probing(monkeypatch: MonkeyPatch, backend: str, expected: str) -> None:
    monkeypatch.setattr(selection, "NativeIpcAdapter", UnreachableNative)
    monkeypatch.setattr(selection, "SdkIpcAdapter", FakeSdkAdapter)
    monkeypatch.setattr(selection, "find_es_cli", lambda configured=None: Path(r"C:\Tools\es.exe"))

    adapter = selection.select_adapter(_config(backend=backend))

    assert adapter.name == expected


def test_config_rejects_unknown_backend(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_BACKEND", "http")

    with pytest.raises(ValueError, match="EVERYTHING_MCP_BACKEND"):
        config_module.EverythingConfig.from_env()


def test_config_reads_named_instance(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EVERYTHING_INSTANCE", " 1.5a ")
    monkeypatch.delenv("EVERYTHING_MCP_BACKEND", raising=False)

    assert config_module.EverythingConfig.from_env().instance == "1.5a"
