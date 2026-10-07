"""Safe backend selection."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from everything_mcp.config import EverythingConfig
from everything_mcp.contracts import AdapterStatus, SearchHit, SortName
from everything_mcp.errors import BackendUnavailableError

from .base import EverythingAdapter
from .native_ipc import NativeIpcAdapter

if TYPE_CHECKING:
    from .es_cli import EsCliAdapter as _EsCliAdapter
    from .sdk_ipc import SdkIpcAdapter as _SdkIpcAdapter


class NullAdapter:
    name = "none"

    def __init__(
        self, notes: tuple[str, ...], everything_installed: bool = False, es_cli_available: bool = False
    ) -> None:
        self.notes = notes
        self.everything_installed = everything_installed
        self.es_cli_available = es_cli_available

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            everything_installed=self.everything_installed,
            everything_running=False,
            backend="none",
            es_cli_available=self.es_cli_available,
            http_available=False,
            notes=self.notes,
        )

    def count(self, query: str, scope: str | None = None) -> int:
        raise BackendUnavailableError(_UNAVAILABLE)

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit]:
        raise BackendUnavailableError(_UNAVAILABLE)


_UNAVAILABLE = "No Everything backend is available. Start Everything, or configure es.exe for the ES CLI fallback."


def SdkIpcAdapter(config: EverythingConfig) -> _SdkIpcAdapter:
    """Load the optional SDK DLL adapter only when it is actually selected."""
    from .sdk_ipc import SdkIpcAdapter as adapter

    return adapter(config)


def EsCliAdapter(
    es_exe: Path, everything_installed: bool = True, sdk_notes: tuple[str, ...] = ()
) -> _EsCliAdapter:
    from .es_cli import EsCliAdapter as adapter

    return adapter(es_exe, everything_installed=everything_installed, sdk_notes=sdk_notes)


def find_es_cli(configured: Path | None = None) -> Path | None:
    from .es_cli import find_es_cli as find

    return find(configured)


def select_adapter(config: EverythingConfig | None = None) -> EverythingAdapter:
    """Pick the cheapest working backend: native IPC, then the SDK DLL, then es.exe.

    Native IPC needs no third-party binary. The SDK DLL is only tried when the
    host configured one (or forced it), because it speaks the same IPC protocol.
    """
    cfg = config or EverythingConfig.from_env()
    everything_installed = Path(cfg.everything_exe).exists()
    notes: list[str] = []

    if cfg.backend in ("auto", "native"):
        native = NativeIpcAdapter(cfg)
        if cfg.backend == "native" or native.is_reachable():
            return native
        notes.append("Everything IPC window was not found; start Everything and retry.")

    if cfg.backend == "sdk" or (cfg.backend == "auto" and cfg.sdk_dll is not None):
        sdk = SdkIpcAdapter(cfg)
        if cfg.backend == "sdk":
            return sdk
        sdk_status = sdk.status()
        if sdk_status.backend == "sdk-ipc" and sdk_status.everything_running:
            return sdk
        notes.extend(sdk_status.notes)

    es_path = find_es_cli(cfg.es_exe) if cfg.backend in ("auto", "es") else None
    if es_path is not None:
        return EsCliAdapter(es_path, everything_installed=everything_installed, sdk_notes=tuple(notes))

    if not everything_installed:
        notes.append(f"Everything.exe was not found at {cfg.everything_exe}.")
    notes.append("ES CLI fallback is unavailable; configure EVERYTHING_ES_EXE or install es.exe.")
    notes.append("HTTP fallback is deferred and will not be enabled automatically.")
    return NullAdapter(tuple(notes), everything_installed=everything_installed, es_cli_available=False)
