"""Compact Everything query syntax help."""

from __future__ import annotations

SYNTAX_TOPICS: dict[str, str] = {
    "extension": "extension: ext:md or ext:ts;tsx",
    "path": r"path: path:C:\Work or C:\Work\ ext:md",
    "date": "date: dm:today, dm:thisweek, dc:2026, rc:thismonth",
    "size": "size: size:>10mb, size:<1gb, size:gigantic",
    "operators": "operators: space means AND, | means OR, ! excludes, quotes keep phrases",
    "regex": r"regex: regex:^report_.*\.md$; combine with a path or extension scope",
    "content": "content: avoid by default; contents are slow and not indexed, so prefilter by path/ext/date first",
    "exclude": "exclude: !node_modules !.git !dist !build !.venv !__pycache__ !reports",
}


def syntax_help(topic: str | None = None) -> str:
    key = (topic or "").strip().lower()
    if key in SYNTAX_TOPICS:
        return SYNTAX_TOPICS[key]
    return "\n".join(SYNTAX_TOPICS[name] for name in ("extension", "path", "date", "size", "exclude"))
