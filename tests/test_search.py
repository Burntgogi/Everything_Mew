from importlib import import_module

contracts = import_module("everything_mcp.contracts")
server = import_module("everything_mcp.server")


class SearchAdapter:
    name = "fake"

    def __init__(self, hits: list[object]) -> None:
        self.hits = hits
        self.calls: list[dict[str, object]] = []

    def status(self):
        return contracts.AdapterStatus(True, True, "sdk-ipc", False)

    def search(self, query: str, scope: str | None = None, limit: int = 25, sort: str = "name", metadata: bool = False):
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


def test_search_hard_caps_limit_to_100() -> None:
    hits = [contracts.SearchHit(path=fr"C:\Work\file{i}.md") for i in range(150)]
    adapter = SearchAdapter(hits)

    result = server.everything_search("ext:md", scope=r"C:\Work", limit=500, adapter=adapter)

    assert result["countReturned"] == 100
    assert result["truncated"] is True
    assert adapter.calls[0]["limit"] == 101


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
    adapter = SearchAdapter([contracts.SearchHit(path=r"C:\Work\file.md")])

    broad = server.everything_search("content:password", scope=r"C:\Work", adapter=adapter)
    narrow = server.everything_search("content:needle ext:md", scope=r"C:\Work\project", adapter=adapter)

    assert broad["tooBroad"] is True
    assert narrow["countReturned"] == 1
    assert len(adapter.calls) == 1
