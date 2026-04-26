"""Read-only Everything_Mew MCP tools and optional FastMCP registration."""

from __future__ import annotations

import re
from importlib import import_module
from typing import Any

from .adapters import EverythingAdapter, select_adapter
from .contracts import BROAD_RESULT_THRESHOLD, HARD_LIMIT, AdapterStatus, SortName, clamp_limit, path_first_items
from .errors import BackendUnavailableError, EverythingMcpError
from .query import has_path_signal, has_strong_filter, is_drive_root
from .syntax import syntax_help

TOOL_NAMES = ("everything_status", "everything_count", "everything_search", "everything_syntax_help")
TOOL_ANNOTATIONS: dict[str, dict[str, bool]] = {
    name: {"readOnlyHint": True, "destructiveHint": False} for name in TOOL_NAMES
}


def everything_status(adapter: EverythingAdapter | None = None) -> dict[str, Any]:
    selected = adapter or select_adapter()
    return selected.status().to_tool_result()


def everything_count(query: str, scope: str | None = None, adapter: EverythingAdapter | None = None) -> dict[str, Any]:
    if is_broad_query(query, scope):
        return {
            "count": None,
            "tooBroad": True,
            "recommendation": "refine with a path, extension, date, size, or exclusion before searching",
        }
    selected = adapter or select_adapter()
    try:
        count = selected.count(query=query, scope=scope)
    except EverythingMcpError as exc:
        return {"count": None, "tooBroad": False, "recommendation": str(exc)}
    too_broad = count > BROAD_RESULT_THRESHOLD
    return {
        "count": count,
        "tooBroad": too_broad,
        "recommendation": "refine query before search" if too_broad else "search",
    }


def everything_search(
    query: str,
    scope: str | None = None,
    limit: int | None = None,
    sort: SortName = "name",
    metadata: bool = False,
    adapter: EverythingAdapter | None = None,
) -> dict[str, Any]:
    safe_limit = clamp_limit(limit)
    if is_broad_query(query, scope):
        return {
            "countReturned": 0,
            "truncated": False,
            "tooBroad": True,
            "recommendation": "call everything_count after adding path, extension, date, size, or exclusion filters",
            "items": [],
        }
    selected = adapter or select_adapter()
    try:
        hits = selected.search(query=query, scope=scope, limit=safe_limit + 1, sort=sort, metadata=metadata)
    except BackendUnavailableError as exc:
        return {"countReturned": 0, "truncated": False, "items": [], "notes": [str(exc)]}
    except EverythingMcpError as exc:
        return {"countReturned": 0, "truncated": False, "items": [], "notes": [str(exc), syntax_help()]}
    truncated = len(hits) > safe_limit
    visible = hits[:safe_limit]
    result: dict[str, Any] = {
        "countReturned": len(visible),
        "truncated": truncated,
        "items": path_first_items(visible, metadata),
    }
    if safe_limit == HARD_LIMIT and truncated:
        result["recommendation"] = "result hard cap reached; refine by path, extension, date, size, or exclusions"
    return result


def everything_syntax_help(topic: str | None = None) -> str:
    return syntax_help(topic)


def is_broad_query(query: str | None, scope: str | None = None) -> bool:
    text = (query or "").strip()
    if not text:
        return True
    lowered = text.lower()
    if lowered in {"*", "*.*", "file:", "folder:", "c:\\", "c:/"}:
        return True
    if is_drive_root(scope) or (scope is None and is_drive_root(text)):
        return True
    if lowered.startswith("content:") and not (scope and not is_drive_root(scope) and has_strong_filter(lowered)):
        return True
    if scope and scope.strip():
        return False
    has_path = has_path_signal(lowered)
    has_narrowing_token = has_strong_filter(lowered)
    if not has_path and not has_narrowing_token:
        return True
    if lowered.startswith("ext:") and not has_path and " " not in lowered:
        return True
    return False


def create_mcp() -> Any:
    try:
        fastmcp_module = import_module("fastmcp")
    except ImportError as exc:
        raise RuntimeError("FastMCP is not installed. Install everything-mew[server] to run the MCP server.") from exc

    FastMCP = getattr(fastmcp_module, "FastMCP")
    mcp = FastMCP("Everything_Mew")
    _register_tool(mcp, _mcp_everything_status, "everything_status")
    _register_tool(mcp, _mcp_everything_count, "everything_count")
    _register_tool(mcp, _mcp_everything_search, "everything_search")
    _register_tool(mcp, _mcp_everything_syntax_help, "everything_syntax_help")
    return mcp


def _mcp_everything_status() -> dict[str, Any]:
    return everything_status()


def _mcp_everything_count(query: str, scope: str | None = None) -> dict[str, Any]:
    return everything_count(query=query, scope=scope)


def _mcp_everything_search(
    query: str,
    scope: str | None = None,
    limit: int | None = None,
    sort: SortName = "name",
    metadata: bool = False,
) -> dict[str, Any]:
    return everything_search(query=query, scope=scope, limit=limit, sort=sort, metadata=metadata)


def _mcp_everything_syntax_help(topic: str | None = None) -> str:
    return everything_syntax_help(topic=topic)


def _register_tool(mcp: Any, func: Any, name: str) -> None:
    annotations = TOOL_ANNOTATIONS[name]
    try:
        mcp.tool(name=name, annotations=annotations)(func)
    except TypeError:
        try:
            mcp.tool(name=name)(func)
        except TypeError as exc:
            raise RuntimeError("FastMCP tool registration requires support for explicit tool names.") from exc


def main() -> None:
    create_mcp().run()


if __name__ == "__main__":
    main()
