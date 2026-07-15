"""Everything query composition and broadness helpers."""

from __future__ import annotations

import re
from pathlib import PureWindowsPath

POSITIVE_FILTER_PATTERN = re.compile(r"(?:^|\s)(?:path:|ext:|dm:|dc:|rc:|size:|regex:)", re.IGNORECASE)
DRIVE_ROOT_PATTERN = re.compile(r"^[a-z]:[\\/]*$", re.IGNORECASE)
UNC_ROOT_PATTERN = re.compile(r"^[\\/]{2}[^\\/]+(?:[\\/]+[^\\/]+)?[\\/]*$")


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
    return bool(DRIVE_ROOT_PATTERN.match(_unquote_path(value)))


def is_unc_root(value: str | None) -> bool:
    if value is None:
        return False
    return bool(UNC_ROOT_PATTERN.match(_unquote_path(value)))


def is_root_scope(value: str | None) -> bool:
    return is_drive_root(value) or is_unc_root(value)


def has_root_path_expression(query: str) -> bool:
    for term in _query_terms(query):
        value = term[5:] if term.lower().startswith("path:") else term
        if is_root_scope(value):
            return True
    return False


def has_positive_narrowing_filter(query: str) -> bool:
    return bool(POSITIVE_FILTER_PATTERN.search(query))


def has_exclusion(query: str) -> bool:
    return any(_is_exclusion_term(term) for term in _query_terms(query))


def is_exclusion_only_query(query: str) -> bool:
    terms = _query_terms(query)
    return bool(terms) and has_exclusion(query) and all(_is_exclusion_term(term) for term in terms)


def is_extension_only_query(query: str) -> bool:
    positive_terms = _positive_terms(query)
    return len(positive_terms) == 1 and positive_terms[0].lower().startswith("ext:")


def has_strong_filter(query: str) -> bool:
    """Compatibility name for positive narrowing filters only."""
    return has_positive_narrowing_filter(query)


def has_path_signal(query: str) -> bool:
    for term in _query_terms(query):
        if term.lower().startswith("path:"):
            return True
        value = _unquote_path(term)
        if re.match(r"(?:[a-z]:[\\/]|[\\/]{2})", value, re.IGNORECASE):
            return True
    return False


def _query_terms(query: str) -> tuple[str, ...]:
    """Return whitespace-separated terms while keeping quoted phrases intact."""
    terms: list[str] = []
    position = 0
    while position < len(query):
        while position < len(query) and query[position].isspace():
            position += 1
        if position == len(query):
            break

        start = position
        if query[position] == "!":
            position += 1
        if query[position : position + 5].lower() == "path:":
            position += 5

        if position < len(query) and query[position] == '"':
            position += 1
            while position < len(query):
                if query[position] != '"':
                    position += 1
                elif position + 1 < len(query) and query[position + 1] == '"':
                    position += 2
                else:
                    position += 1
                    break
        else:
            while position < len(query) and not query[position].isspace():
                position += 1

        terms.append(query[start:position])
    return tuple(terms)


def _is_exclusion_term(term: str) -> bool:
    return term.startswith("!")


def _positive_terms(query: str) -> tuple[str, ...]:
    return tuple(term for term in _query_terms(query) if not _is_exclusion_term(term))


def _unquote_path(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1].replace('""', '"')
    return text
