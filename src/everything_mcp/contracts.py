"""Small typed contracts shared by tools and adapters."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal, NamedTuple

BackendName = Literal["native-ipc", "sdk-ipc", "es-cli", "http", "none"]
SortName = Literal["name", "path", "size", "date_modified"]
TargetMachineName = Literal["x86", "x64", "ARM", "ARM64"]

DEFAULT_LIMIT = 25
HARD_LIMIT = 100
BROAD_RESULT_THRESHOLD = 1000


class AdapterStatus(NamedTuple):
    everything_installed: bool
    everything_running: bool
    backend: BackendName
    es_cli_available: bool
    http_available: bool = False
    notes: tuple[str, ...] = ()
    db_loaded: bool | None = None
    version: str | None = None
    target_machine: TargetMachineName | None = None
    fast_sorts: tuple[str, ...] | None = None

    def to_tool_result(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "everythingInstalled": self.everything_installed,
            "everythingRunning": self.everything_running,
            "backend": self.backend,
            "esCliAvailable": self.es_cli_available,
            "httpAvailable": self.http_available,
            "notes": list(self.notes),
        }
        if self.db_loaded is not None:
            result["dbLoaded"] = self.db_loaded
        if self.version is not None:
            result["version"] = self.version
        if self.target_machine is not None:
            result["targetMachine"] = self.target_machine
        if self.fast_sorts is not None:
            result["fastSorts"] = list(self.fast_sorts)
        return result


class SearchHit(NamedTuple):
    path: str
    size: int | None = None
    date_modified: str | None = None
    attributes: str | None = None

    def to_metadata_result(self) -> dict[str, Any]:
        item: dict[str, Any] = {"path": self.path}
        if self.size is not None:
            item["size"] = self.size
        if self.date_modified is not None:
            item["dateModified"] = self.date_modified
        if self.attributes is not None:
            item["attributes"] = self.attributes
        return item


class _SearchBatchFields(NamedTuple):
    hits: tuple[SearchHit, ...]
    notes: tuple[str, ...] = ()
    total_count: int | None = None


class SearchBatch(_SearchBatchFields):
    """Immutable search hits plus diagnostics and Everything's total match count."""

    __slots__ = ()

    def __new__(
        cls, hits: Sequence[SearchHit], notes: Sequence[str] = (), total_count: int | None = None
    ) -> "SearchBatch":
        return super().__new__(cls, tuple(hits), tuple(notes), total_count)


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), HARD_LIMIT))


def path_first_items(items: Sequence[SearchHit], metadata: bool) -> list[str] | list[dict[str, Any]]:
    if metadata:
        return [item.to_metadata_result() for item in items]
    return [item.path for item in items]
