"""Everything query composition and broadness helpers."""

from __future__ import annotations

import re
from pathlib import PureWindowsPath

STRONG_FILTER_PATTERN = re.compile(r"(?:^|\s)(?:path:|ext:|dm:|dc:|rc:|size:|regex:|!)", re.IGNORECASE)
PATH_SIGNAL_PATTERN = re.compile(r"(?:^|\s)(?:path:|[a-z]:[\\/])", re.IGNORECASE)
DRIVE_ROOT_PATTERN = re.compile(r"^[a-z]:[\\/]?$", re.IGNORECASE)


def compose_query(query: str, scope: str | None = None) -> str:
    text = query.strip()
    if not scope or not scope.strip():
        return text
    return f"path:{quote_everything_phrase(normalize_scope(scope))} {text}".strip()


def quote_everything_phrase(value: str) -> str:
    escaped = value.replace('"', '""')
    return f'"{escaped}"'


def normalize_scope(scope: str) -> str:
    text = scope.strip().strip('"')
    if not text:
        return text
    normalized = str(PureWindowsPath(text))
    if DRIVE_ROOT_PATTERN.match(normalized):
        return normalized if normalized.endswith("\\") else f"{normalized}\\"
    return normalized.rstrip("\\/")


def is_drive_root(value: str | None) -> bool:
    if value is None:
        return False
    return bool(DRIVE_ROOT_PATTERN.match(normalize_scope(value)))


def has_strong_filter(query: str) -> bool:
    return bool(STRONG_FILTER_PATTERN.search(query))


def has_path_signal(query: str) -> bool:
    return bool(PATH_SIGNAL_PATTERN.search(query))
