"""Everything_Mew MCP package."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "everything_count",
    "everything_search",
    "everything_status",
    "everything_syntax_help",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        server = import_module("everything_mcp.server")
        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
