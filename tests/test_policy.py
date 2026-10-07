"""Search policy must reject untrusted scopes before either backend is called."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import pytest
from pytest import MonkeyPatch

from everything_mcp import query, server
from everything_mcp.contracts import AdapterStatus, SearchHit, SortName
from everything_mcp.policy import SearchPolicy


class RecordingAdapter:
    name = "fake"

    def __init__(self, hits: list[SearchHit] | None = None) -> None:
        self.hits = hits or []
        self.count_calls: list[tuple[str, str | None]] = []
        self.search_calls: list[tuple[str, str | None, bool]] = []

    def count(self, query: str, scope: str | None = None) -> int:
        self.count_calls.append((query, scope))
        return 3

    def status(self) -> AdapterStatus:
        return AdapterStatus(True, True, "sdk-ipc", False)

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit]:
        self.search_calls.append((query, scope, metadata))
        return self.hits[:limit]


def test_default_policy_denies_scoped_and_unscoped_queries_before_backend(monkeypatch: MonkeyPatch) -> None:
    for name in ("EVERYTHING_MCP_ALLOWED_ROOTS", "EVERYTHING_MCP_ALLOW_UNSCOPED", "EVERYTHING_MCP_ALLOW_METADATA"):
        monkeypatch.delenv(name, raising=False)
    adapter = RecordingAdapter()

    count = server.everything_count("report ext:md", adapter=adapter)
    search = server.everything_search("report ext:md", scope=r"C:\Work", adapter=adapter)

    assert count["denied"] is True
    assert count["count"] is None
    assert search["denied"] is True
    assert search["items"] == []
    assert adapter.count_calls == []
    assert adapter.search_calls == []


def test_configured_root_allows_only_scopes_and_paths_inside_it(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_ALLOWED_ROOTS", json.dumps([r"C:\Work\project"]))
    monkeypatch.delenv("EVERYTHING_MCP_ALLOW_UNSCOPED", raising=False)
    monkeypatch.delenv("EVERYTHING_MCP_ALLOW_METADATA", raising=False)
    adapter = RecordingAdapter(
        [
            SearchHit(path=r"C:\Work\project\README.md"),
            SearchHit(path=r"C:\Work\project-backup\secret.md"),
            SearchHit(path=r"C:\Work\private\secret.md"),
        ]
    )

    allowed = server.everything_search("README ext:md", scope=r"C:\Work\project", adapter=adapter)
    denied_count = server.everything_count("README ext:md", scope=r"C:\Work\private", adapter=adapter)
    denied_parent = server.everything_search("README ext:md", scope=r"C:\Work", adapter=adapter)
    denied_traversal = server.everything_search(
        "README ext:md", scope=r"C:\Work\project\..\private", adapter=adapter
    )
    denied_unscoped = server.everything_count("README ext:md", adapter=adapter)

    assert allowed["items"] == [r"C:\Work\project\README.md"]
    assert adapter.search_calls == [("README ext:md", r"C:\Work\project", False)]
    assert all(result["denied"] is True for result in (denied_count, denied_parent, denied_traversal, denied_unscoped))
    assert adapter.count_calls == []


def test_count_and_metadata_use_the_same_policy(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_ALLOWED_ROOTS", json.dumps([r"C:\Work"]))
    monkeypatch.delenv("EVERYTHING_MCP_ALLOW_UNSCOPED", raising=False)
    monkeypatch.delenv("EVERYTHING_MCP_ALLOW_METADATA", raising=False)
    adapter = RecordingAdapter([SearchHit(path=r"C:\Work\README.md", size=10)])

    count = server.everything_count("README ext:md", scope=r"C:\Work", adapter=adapter)
    denied_metadata = server.everything_search(
        "README ext:md", scope=r"C:\Work", metadata=True, adapter=adapter
    )
    monkeypatch.setenv("EVERYTHING_MCP_ALLOW_METADATA", "1")
    allowed_metadata = server.everything_search(
        "README ext:md", scope=r"C:\Work", metadata=True, adapter=adapter
    )

    assert count["count"] == 3
    assert adapter.count_calls == [("README ext:md", r"C:\Work")]
    assert denied_metadata["denied"] is True
    assert adapter.search_calls == [("README ext:md", r"C:\Work", True)]
    assert allowed_metadata["items"] == [{"path": r"C:\Work\README.md", "size": 10}]


def test_query_groups_every_or_branch_under_the_scope() -> None:
    assert query.compose_query("ext:md | ext:txt", r"C:\Work") == '<ext:md | ext:txt> "C:\\Work\\"'
    assert query.compose_query("README | ext:txt", r"C:\Work") == '"C:\\Work\\" <README | ext:txt>'


def test_explicit_unscoped_mode_restores_index_search_without_allowed_roots(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("EVERYTHING_MCP_ALLOWED_ROOTS", raising=False)
    monkeypatch.setenv("EVERYTHING_MCP_ALLOW_UNSCOPED", "1")
    adapter = RecordingAdapter()

    result = server.everything_count("report ext:md", adapter=adapter)

    assert result["count"] == 3
    assert adapter.count_calls == [("report ext:md", None)]


@pytest.mark.parametrize("value", ("not-json", "{}", '["C:\\\\"]', '["relative\\\\folder"]'))
def test_invalid_allowed_roots_fail_closed(monkeypatch: MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_ALLOWED_ROOTS", value)
    with pytest.raises(ValueError):
        SearchPolicy.from_env()


@pytest.mark.parametrize("root", (r"\\?\C:\Work", r"\\.\C:\Work", "//?/C:/Work", "//./C:/Work"))
def test_device_namespace_allowed_roots_fail_closed(monkeypatch: MonkeyPatch, root: str) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_ALLOWED_ROOTS", json.dumps([root]))
    monkeypatch.delenv("EVERYTHING_MCP_ALLOW_UNSCOPED", raising=False)

    with pytest.raises(ValueError, match="device namespace"):
        SearchPolicy.from_env()


@pytest.mark.parametrize("root", (r"\\?\C:\Work", r"\\.\C:\Work"))
def test_device_namespace_scopes_are_denied_before_backend(root: str) -> None:
    policy = SearchPolicy(allowed_roots=(root,))
    adapter = RecordingAdapter()

    count = server.everything_count("README ext:md", scope=root, adapter=adapter, policy=policy)
    search = server.everything_search("README ext:md", scope=root, adapter=adapter, policy=policy)

    assert count.get("denied") is True
    assert search.get("denied") is True
    assert adapter.count_calls == []
    assert adapter.search_calls == []


@pytest.mark.parametrize("root", (r"\\?\C:\Work", r"\\.\C:\Work"))
def test_device_namespace_hits_are_rejected(root: str) -> None:
    policy = SearchPolicy(allowed_roots=(root,))

    assert policy.allows_path(root + r"\linked\secret.txt", root) is False


def test_unc_scope_uses_component_boundary() -> None:
    policy = SearchPolicy(allowed_roots=(r"\\server\share\project",))
    assert policy.denial_reason(r"\\server\share\project\docs") is None
    assert policy.denial_reason(r"\\server\share\project-old") is not None


@pytest.mark.skipif(os.name != "nt", reason="Windows reparse-point test")
@pytest.mark.parametrize("prefix", ("", "\\\\?\\", "\\\\.\\"))
def test_existing_link_to_outside_root_is_rejected(tmp_path: Path, prefix: str) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    linked = allowed / "linked"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError:
        created = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(linked), str(outside)],
            capture_output=True,
            text=True,
            check=False,
        )
        if created.returncode != 0:
            pytest.skip("directory links and junctions unavailable")

    scope = prefix + str(allowed)
    policy = SearchPolicy(allowed_roots=(scope,))
    assert policy.denial_reason(prefix + str(linked)) is not None
    assert policy.allows_path(prefix + str(linked / "secret.txt"), scope) is False
