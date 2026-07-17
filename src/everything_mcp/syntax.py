"""Compact Everything query syntax help."""

from __future__ import annotations

SYNTAX_TOPICS: dict[str, str] = {
    "compatibility": (
        "compatibility: Everything 1.4.1 core syntax is the default; call everything_status before using "
        "Everything 1.5-only features"
    ),
    "extension": "extension: ext:md or ext:ts;tsx",
    "scope": (
        'scope: pass an absolute scope such as C:\\Work\\project; the server composes "C:\\Work\\project\\" '
        "so prefix siblings are excluded"
    ),
    "path": (
        'path: use an absolute scope for recursive folder boundaries; path:"C:\\Work\\project" is only a '
        "full-path partial match"
    ),
    "date": "date: dm:today, dm:thisweek, dc:2026, rc:thismonth",
    "size": "size: size:>10mb, size:<1gb, size:gigantic",
    "operators": "operators: space means AND, | means OR, ! excludes, < > groups; OR has higher precedence than AND",
    "regex": (
        'regex: regex:"gr(a|e)y" ext:txt; quote patterns containing |, spaces, <, or > and add a separate indexed filter'
    ),
    "content": (
        "content: content aliases, content*: literal tails, nested content modifiers, byte/ADS content, and "
        "from-disk: are slow I/O; require a non-root scope and a separate indexed filter such as ext:txt"
    ),
    "sort": "sort: name ascending is the free default; path, size, and date_modified benefit from enabled fast sorts",
    "exclude": "exclude: !node_modules !.git !dist !build !.venv !__pycache__ !reports",
}


def syntax_help(topic: str | None = None) -> str:
    key = (topic or "").strip().lower()
    if key in SYNTAX_TOPICS:
        return SYNTAX_TOPICS[key]
    return "\n".join(
        SYNTAX_TOPICS[name]
        for name in ("compatibility", "scope", "extension", "date", "size", "operators", "content", "exclude")
    )
