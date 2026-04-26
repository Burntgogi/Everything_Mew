"""Safe backend selection."""

from __future__ import annotations

from dataclasses import dataclass

from pathlib import Path


from everything_mcp.config import EverythingConfig
from everything_mcp.contracts import AdapterStatus, SearchHit, SortName
from everything_mcp.errors import BackendUnavailableError

from .es_cli import EsCliAdapter, find_es_cli
from .sdk_ipc import SdkIpcAdapter


@dataclass
class NullAdapter:
    notes: tuple[str, ...]
    everything_installed: bool = False
    es_cli_available: bool = False
    name: str = "none"

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
        raise BackendUnavailableError("No Everything backend is available. Start Everything and configure SDK DLL or es.exe.")

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit]:
        raise BackendUnavailableError("No Everything backend is available. Start Everything and configure SDK DLL or es.exe.")


def select_adapter(config: EverythingConfig | None = None):
    cfg = config or EverythingConfig.from_env()
    everything_installed = Path(cfg.everything_exe).exists()

    sdk = SdkIpcAdapter(cfg)
    sdk_status = sdk.status()
    if sdk_status.backend == "sdk-ipc" and sdk_status.everything_running:
        return sdk

    es_path = find_es_cli(cfg.es_exe)
    es_available = es_path is not None
    if es_path is not None:
        return EsCliAdapter(es_path, everything_installed=everything_installed, sdk_notes=sdk_status.notes)

    notes = list(sdk_status.notes)
    if not everything_installed:
        notes.append(f"Everything.exe was not found at {cfg.everything_exe}.")
    notes.append("ES CLI fallback is unavailable; configure EVERYTHING_ES_EXE or install es.exe.")
    notes.append("HTTP fallback is deferred and will not be enabled automatically.")
    return NullAdapter(tuple(notes), everything_installed=everything_installed, es_cli_available=es_available)
