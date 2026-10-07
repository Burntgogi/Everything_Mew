"""Contracts that save agents a model round trip."""

from __future__ import annotations

import pytest

from everything_mcp import query, server
from everything_mcp.contracts import AdapterStatus, SearchBatch, SearchHit, SortName
from everything_mcp.policy import SearchPolicy
from everything_mcp.tool_specs import tool_definitions

SCOPE = r"C:\Work"
POLICY = SearchPolicy(allowed_roots=(SCOPE,))


class Adapter:
    name = "fake"

    def __init__(self, batch: SearchBatch, count: int = 0) -> None:
        self.batch = batch
        self.total = count

    def status(self) -> AdapterStatus:  # pragma: no cover - not used
        raise AssertionError

    def count(self, query: str, scope: str | None = None) -> int:
        return self.total

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit] | SearchBatch:
        return self.batch


def test_only_the_search_tool_is_loaded_up_front() -> None:
    definitions = {tool["name"]: tool for tool in tool_definitions()}

    # Claude Code defers MCP tools behind a tool-search turn unless alwaysLoad is set.
    assert definitions["everything_search"]["_meta"] == {"anthropic/alwaysLoad": True}
    for name in ("everything_status", "everything_count", "everything_syntax_help"):
        meta = definitions[name]["_meta"]
        assert "anthropic/alwaysLoad" not in meta
        assert isinstance(meta["anthropic/searchHint"], str)


def test_search_description_teaches_the_1_4_syntax_agents_guess_wrong() -> None:
    description = next(tool for tool in tool_definitions() if tool["name"] == "everything_search")["description"]

    assert "wfn:pyproject.toml" in description
    assert "There is no name: function" in description
    assert "dm:today" in description


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("file: name:pyproject.toml", ("name",)),
        ("filename:a.py ext:py", ("filename",)),
        ("ext:py !foo:bar", ("foo",)),
        ("ext:toml wfn:pyproject.toml dm:today size:>1mb", ()),
        ("regex:^a ext:py", ()),
        (r"C:\Work\a.py", ()),
        ('"name:literal"', ()),
        ("nowholeword:case:lite", ()),
        ("content:needle ext:txt", ()),
        ("unterminated:\"", ()),
    ],
)
def test_unknown_search_functions(text: str, expected: tuple[str, ...]) -> None:
    assert query.unknown_search_functions(text) == expected


def test_empty_search_with_an_unknown_function_explains_why() -> None:
    result = server.everything_search(
        "file: name:pyproject.toml", SCOPE, adapter=Adapter(SearchBatch(hits=(), total_count=0)), policy=POLICY
    )

    assert result["countReturned"] == 0
    assert "error" not in result
    assert "name: is not an Everything 1.4 search function" in result["notes"][0]


def test_empty_count_with_an_unknown_function_explains_why() -> None:
    result = server.everything_count("name:x ext:py", SCOPE, adapter=Adapter(SearchBatch(hits=()), count=0), policy=POLICY)

    assert result["count"] == 0
    assert "name: is not an Everything 1.4 search function" in result["notes"][0]


def test_matches_and_plain_empty_results_get_no_hint() -> None:
    found = server.everything_search(
        "name:x", SCOPE, adapter=Adapter(SearchBatch(hits=(SearchHit(rf"{SCOPE}\name:x"),), total_count=1)), policy=POLICY
    )
    empty = server.everything_search("ext:py", SCOPE, adapter=Adapter(SearchBatch(hits=(), total_count=0)), policy=POLICY)

    assert "notes" not in found
    assert "notes" not in empty
