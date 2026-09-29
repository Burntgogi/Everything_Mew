from importlib import import_module
from pathlib import Path
import subprocess
from typing import Any

import pytest
from pytest import MonkeyPatch

from everything_mcp.adapters.es_cli import EsCliAdapter

contracts = import_module("everything_mcp.contracts")
server = import_module("everything_mcp.server")


@pytest.fixture(autouse=True)
def unrestricted_policy_for_search_contract_tests(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("EVERYTHING_MCP_ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("EVERYTHING_MCP_ALLOW_UNSCOPED", "1")
    monkeypatch.setenv("EVERYTHING_MCP_ALLOW_METADATA", "1")


class SearchAdapter:
    name = "fake"

    def __init__(self, hits: list[object], backend: str = "sdk-ipc") -> None:
        self.hits = hits
        self.backend = backend
        self.calls: list[dict[str, object]] = []

    def status(self) -> Any:
        return contracts.AdapterStatus(True, True, self.backend, self.backend == "es-cli")

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: str = "name",
        metadata: bool = False,
    ) -> list[object]:
        self.calls.append({"query": query, "scope": scope, "limit": limit, "sort": sort, "metadata": metadata})
        return self.hits[:limit]


class SdkFixtureAdapter:
    name = "sdk-ipc"

    def __init__(self, hits: list[object]) -> None:
        self.hits = hits
        self.calls: list[dict[str, object]] = []

    def status(self) -> Any:
        return contracts.AdapterStatus(True, True, "sdk-ipc", False)

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: str = "name",
        metadata: bool = False,
    ) -> list[object]:
        self.calls.append({"query": query, "scope": scope, "limit": limit, "sort": sort, "metadata": metadata})
        return self.hits[:limit]


def test_search_defaults_to_path_first_and_limit_25() -> None:
    hits = [contracts.SearchHit(path=fr"C:\Work\file{i}.md") for i in range(30)]
    adapter = SearchAdapter(hits)

    result = server.everything_search("ext:md", scope=r"C:\Work", adapter=adapter)

    assert result["countReturned"] == 25
    assert result["truncated"] is True
    assert result["items"][0] == r"C:\Work\file0.md"
    assert isinstance(result["items"][0], str)
    assert adapter.calls[0]["limit"] == 26


def test_search_metadata_is_opt_in() -> None:
    adapter = SearchAdapter([contracts.SearchHit(path=r"C:\Work\README.md", size=10, date_modified="2026-04-26", attributes="A")])

    result = server.everything_search("README ext:md", scope=r"C:\Work", metadata=True, adapter=adapter)

    assert result["items"] == [{"path": r"C:\Work\README.md", "size": 10, "dateModified": "2026-04-26", "attributes": "A"}]


def test_search_batch_adds_diagnostic_note_without_changing_truncation() -> None:
    hits = [contracts.SearchHit(path=fr"C:\Work\file{i}.md") for i in range(30)]
    adapter = SearchAdapter(hits)

    def search_with_note(**_: object) -> object:
        return contracts.SearchBatch(hits=hits[:26], notes=("SDK returned different result capabilities.",))

    adapter.search = search_with_note  # type: ignore[assignment]

    result = server.everything_search("ext:md", scope=r"C:\Work", adapter=adapter)

    assert result["countReturned"] == 25
    assert result["truncated"] is True
    assert result["notes"] == ["SDK returned different result capabilities."]
    assert result["items"][-1] == r"C:\Work\file24.md"


def test_search_batch_converts_hits_to_an_immutable_tuple() -> None:
    batch = contracts.SearchBatch(hits=[contracts.SearchHit(path=r"C:\Work\file.md")])

    assert batch.hits == (contracts.SearchHit(path=r"C:\Work\file.md"),)
    with pytest.raises(AttributeError):
        getattr(batch.hits, "append")(contracts.SearchHit(path=r"C:\Work\other.md"))


def test_search_hard_caps_limit_to_100() -> None:
    hits = [contracts.SearchHit(path=fr"C:\Work\file{i}.md") for i in range(150)]
    adapter = SearchAdapter(hits)

    result = server.everything_search("ext:md", scope=r"C:\Work", limit=500, adapter=adapter)

    assert result["countReturned"] == 100
    assert result["truncated"] is True
    assert adapter.calls[0]["limit"] == 101


def test_sdk_and_real_es_adapter_have_the_same_public_limit_sort_and_truncation_contract(monkeypatch: MonkeyPatch) -> None:
    hits = [contracts.SearchHit(path=fr"C:\Work\file{i}.md") for i in range(101)]
    sdk_adapter = SdkFixtureAdapter(hits)
    es_calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        es_calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="\n".join(hit.path for hit in hits), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    es_adapter = EsCliAdapter(Path(r"C:\Tools\es.exe"))

    sdk_result = server.everything_search("ext:md", scope=r"C:\Work", limit=100, sort="date_modified", adapter=sdk_adapter)
    es_result = server.everything_search("ext:md", scope=r"C:\Work", limit=100, sort="date_modified", adapter=es_adapter)

    assert sdk_result == es_result
    assert sdk_result["countReturned"] == 100
    assert sdk_result["truncated"] is True
    assert sdk_adapter.calls[0]["limit"] == 101
    assert sdk_adapter.calls[0]["sort"] == "date_modified"
    assert es_calls[0][1:5] == ["-n", "101", "-sort", "date-modified-ascending"]


def test_broad_search_does_not_dump_results() -> None:
    adapter = SearchAdapter([contracts.SearchHit(path=r"C:\anywhere")])

    result = server.everything_search("*", adapter=adapter)

    assert result["items"] == []
    assert result["tooBroad"] is True
    assert adapter.calls == []


def test_scoped_star_search_still_counts_as_broad() -> None:
    adapter = SearchAdapter([contracts.SearchHit(path=r"C:\Work\file.txt")])

    result = server.everything_search("*", scope=r"C:\Work", adapter=adapter)

    assert result["items"] == []
    assert result["tooBroad"] is True
    assert adapter.calls == []


def test_drive_root_scope_counts_as_broad_even_with_extension() -> None:
    adapter = SearchAdapter([contracts.SearchHit(path=r"C:\file.md")])

    result = server.everything_search("ext:md", scope="C:\\\\", adapter=adapter)

    assert result["items"] == []
    assert result["tooBroad"] is True
    assert adapter.calls == []


def test_content_search_requires_strong_scope_and_filter() -> None:
    adapter = SearchAdapter([contracts.SearchHit(path=r"C:\Work\project\file.md")])

    broad = server.everything_search("content:password", scope=r"C:\Work", adapter=adapter)
    narrow = server.everything_search("content:needle ext:md", scope=r"C:\Work\project", adapter=adapter)

    assert broad["tooBroad"] is True
    assert narrow["countReturned"] == 1
    assert len(adapter.calls) == 1
