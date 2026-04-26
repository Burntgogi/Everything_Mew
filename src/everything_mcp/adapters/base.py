"""Adapter protocol used by the read-only tool layer."""

from __future__ import annotations

from typing import Protocol

from everything_mcp.contracts import AdapterStatus, SearchHit, SortName


class EverythingAdapter(Protocol):
    name: str

    def status(self) -> AdapterStatus:
        """Return backend readiness without raising for missing local services."""
        ...

    def count(self, query: str, scope: str | None = None) -> int:
        """Return the number of indexed results for a read-only query."""
        ...

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit]:
        """Return indexed path candidates for a read-only query."""
        ...
