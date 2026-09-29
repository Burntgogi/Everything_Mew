from importlib import import_module
from typing import Any

import pytest
from pytest import MonkeyPatch

contracts = import_module("everything_mcp.contracts")
server = import_module("everything_mcp.server")


@pytest.fixture(autouse=True)
def unrestricted_policy_for_count_contract_tests(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("EVERYTHING_MCP_ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("EVERYTHING_MCP_ALLOW_UNSCOPED", "1")


class CountingAdapter:
    name = "fake"

    def __init__(self, count: int = 42) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._count = count

    def status(self) -> Any:
        return contracts.AdapterStatus(True, True, "sdk-ipc", False)

    def count(self, query: str, scope: str | None = None) -> int:
        self.calls.append((query, scope))
        return self._count


def test_broad_unscoped_count_returns_refine_without_backend_call() -> None:
    adapter = CountingAdapter()

    result = server.everything_count("readme", adapter=adapter)

    assert result["count"] is None
    assert result["tooBroad"] is True
    assert "refine" in result["recommendation"]
    assert adapter.calls == []


def test_scoped_count_calls_adapter_and_allows_search() -> None:
    adapter = CountingAdapter(42)

    result = server.everything_count("ext:md", scope=r"C:\Work", adapter=adapter)

    assert result == {"count": 42, "tooBroad": False, "recommendation": "search"}
    assert adapter.calls == [("ext:md", r"C:\Work")]


def test_large_count_recommends_refinement() -> None:
    result = server.everything_count("ext:md", scope=r"C:\Work", adapter=CountingAdapter(5000))

    assert result["count"] == 5000
    assert result["tooBroad"] is True
    assert "refine" in result["recommendation"]
