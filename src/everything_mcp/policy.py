"""Trusted, process-level limits for indexed path searches."""

from __future__ import annotations

import json
import os
from typing import NamedTuple

from .query import is_absolute_scope, is_path_within_scope, is_root_scope, normalize_scope

ALLOWED_ROOTS_ENV = "EVERYTHING_MCP_ALLOWED_ROOTS"
ALLOW_UNSCOPED_ENV = "EVERYTHING_MCP_ALLOW_UNSCOPED"
ALLOW_METADATA_ENV = "EVERYTHING_MCP_ALLOW_METADATA"


def _canonical_path(value: str) -> str:
    normalized = normalize_scope(value)
    # Device namespaces share the UNC prefix but can address local reparse
    # points. Reject them rather than bypassing local canonicalization below.
    if normalized.startswith(("\\\\?\\", "\\\\.\\")):
        raise ValueError("device namespace paths are not supported by server policy")
    if not is_absolute_scope(normalized):
        raise ValueError("scope must be an absolute Windows path")
    # Resolve local reparse points when they exist. UNC paths retain their
    # normalized indexed-path boundary without probing a remote share.
    if os.name == "nt" and not normalized.startswith("\\\\"):
        normalized = normalize_scope(os.path.realpath(normalized))
    return normalized


def _enabled(value: str | None, name: str) -> bool:
    if value is None or value == "":
        return False
    if value == "1":
        return True
    if value == "0":
        return False
    raise ValueError(f"{name} must be 0 or 1")


class SearchPolicy(NamedTuple):
    allowed_roots: tuple[str, ...] = ()
    allow_unscoped: bool = False
    allow_metadata: bool = False

    @classmethod
    def from_env(cls) -> "SearchPolicy":
        raw_roots = os.environ.get(ALLOWED_ROOTS_ENV, "")
        roots: tuple[str, ...] = ()
        if raw_roots:
            try:
                parsed: object = json.loads(raw_roots)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{ALLOWED_ROOTS_ENV} must be a JSON array") from exc
            if not isinstance(parsed, list) or not all(isinstance(root, str) and root.strip() for root in parsed):
                raise ValueError(f"{ALLOWED_ROOTS_ENV} must be a JSON array of absolute directories")
            roots = tuple(_canonical_path(root) for root in parsed)
            if any(is_root_scope(root) for root in roots):
                raise ValueError(f"{ALLOWED_ROOTS_ENV} must contain bounded directories, not drive or share roots")

        allow_unscoped = _enabled(os.environ.get(ALLOW_UNSCOPED_ENV), ALLOW_UNSCOPED_ENV)
        if roots and allow_unscoped:
            raise ValueError(f"{ALLOW_UNSCOPED_ENV} cannot be enabled with {ALLOWED_ROOTS_ENV}")
        return cls(
            allowed_roots=roots,
            allow_unscoped=allow_unscoped,
            allow_metadata=_enabled(os.environ.get(ALLOW_METADATA_ENV), ALLOW_METADATA_ENV),
        )

    def denial_reason(self, scope: str | None, metadata: bool = False) -> str | None:
        if metadata and not self.allow_metadata:
            return "metadata is disabled by server policy"
        if scope is None or not scope.strip():
            return None if self.allow_unscoped else "scope is required by server policy"
        try:
            canonical_scope = _canonical_path(scope)
        except ValueError:
            return "scope must be an absolute Windows directory"
        if is_root_scope(canonical_scope):
            return "scope must be a bounded directory"
        if self.allow_unscoped:
            return None
        if any(is_path_within_scope(canonical_scope, root) for root in self.allowed_roots):
            return None
        return "scope is outside configured allowed roots"

    def allows_path(self, path: str, scope: str | None) -> bool:
        if scope and not is_path_within_scope(path, scope):
            return False
        if self.allow_unscoped:
            return True
        try:
            canonical_path = _canonical_path(path)
            canonical_scope = _canonical_path(scope) if scope else None
        except ValueError:
            return False
        if canonical_scope and not is_path_within_scope(canonical_path, canonical_scope):
            return False
        return any(is_path_within_scope(canonical_path, root) for root in self.allowed_roots)
