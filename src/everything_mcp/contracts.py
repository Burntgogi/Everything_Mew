"""Small typed contracts shared by tools and adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

BackendName = Literal["sdk-ipc", "es-cli", "http", "none"]
SortName = Literal["name", "path", "size", "date_modified"]

DEFAULT_LIMIT = 25
HARD_LIMIT = 100
BROAD_RESULT_THRESHOLD = 1000


@dataclass(frozen=True)
class AdapterStatus:
    everything_installed: bool
    everything_running: bool
    backend: BackendName
    es_cli_available: bool
    http_available: bool = False
    notes: tuple[str, ...] = ()

    def to_tool_result(self) -> dict[str, Any]:
        return {
            "everythingInstalled": self.everything_installed,
            "everythingRunning": self.everything_running,
            "backend": self.backend,
            "esCliAvailable": self.es_cli_available,
            "httpAvailable": self.http_available,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SearchHit:
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


def clamp_limit(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), HARD_LIMIT))


def path_first_items(items: list[SearchHit], metadata: bool) -> list[str] | list[dict[str, Any]]:
    if metadata:
        return [item.to_metadata_result() for item in items]
    return [item.path for item in items]
