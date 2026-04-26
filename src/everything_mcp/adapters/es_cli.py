"""ES CLI adapter using subprocess without shell expansion."""

from __future__ import annotations

import shutil
import subprocess
import csv
from io import StringIO
from pathlib import Path

from everything_mcp.contracts import AdapterStatus, SearchHit, SortName
from everything_mcp.errors import QueryError
from everything_mcp.query import compose_query

DEFAULT_ES_TIMEOUT_SECONDS = 15

SORT_ARGS: dict[str, str] = {
    "name": "name",
    "path": "path",
    "size": "size",
    "date_modified": "date-modified",
}


def find_es_cli(configured: Path | None = None) -> Path | None:
    if configured is not None and configured.exists():
        return configured
    found = shutil.which("es.exe") or shutil.which("es")
    return Path(found) if found else None


class EsCliAdapter:
    name = "es-cli"

    def __init__(self, es_exe: Path, everything_installed: bool = True, sdk_notes: tuple[str, ...] = ()) -> None:
        self.es_exe = es_exe
        self.everything_installed = everything_installed
        self.sdk_notes = sdk_notes

    def status(self) -> AdapterStatus:
        notes = list(self.sdk_notes)
        notes.append(f"Using ES CLI fallback at {self.es_exe}.")
        return AdapterStatus(
            everything_installed=self.everything_installed,
            everything_running=True,
            backend="es-cli",
            es_cli_available=True,
            notes=tuple(notes),
        )

    def count(self, query: str, scope: str | None = None) -> int:
        completed = self._run(["-get-result-count", self._safe_query_arg(query, scope)])
        try:
            return int(completed.stdout.strip().splitlines()[-1])
        except (IndexError, ValueError) as exc:
            raise QueryError(f"ES CLI did not return a numeric count: {completed.stdout!r}") from exc

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit]:
        safe_limit = max(1, min(int(limit), 100))
        args = ["-n", str(safe_limit), "-sort", _sort_arg(sort)]
        if metadata:
            args.extend(["-csv", "-size", "-dm", "-attribs"])
        args.append(self._safe_query_arg(query, scope))
        completed = self._run(args)
        if metadata:
            return _parse_metadata_csv(completed.stdout)
        return [SearchHit(path=line.strip()) for line in completed.stdout.splitlines() if line.strip()]

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [str(self.es_exe), *args],
                check=True,
                capture_output=True,
                text=True,
                shell=False,
                timeout=DEFAULT_ES_TIMEOUT_SECONDS,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() if exc.stderr else "no stderr"
            raise QueryError(f"ES CLI query failed: {stderr}; check syntax or call everything_syntax_help.") from exc
        except subprocess.TimeoutExpired as exc:
            raise QueryError(f"ES CLI query timed out after {DEFAULT_ES_TIMEOUT_SECONDS} seconds; refine the query or check Everything runtime status.") from exc
        except OSError as exc:
            raise QueryError(f"ES CLI could not be executed at {self.es_exe}: {exc}") from exc

    def _compose_query(self, query: str, scope: str | None) -> str:
        return compose_query(query, scope)

    def _safe_query_arg(self, query: str, scope: str | None) -> str:
        composed = self._compose_query(query, scope)
        if composed.startswith("-"):
            raise QueryError("ES CLI queries must not start with '-' because es.exe may parse them as options; add a path/scope or a non-option token.")
        return composed


def _sort_arg(sort: SortName) -> str:
    try:
        return SORT_ARGS[sort]
    except KeyError as exc:
        raise QueryError(f"Unsupported ES CLI sort {sort!r}; use one of {', '.join(SORT_ARGS)}.") from exc


def _parse_metadata_csv(output: str) -> list[SearchHit]:
    rows = csv.reader(StringIO(output))
    hits: list[SearchHit] = []
    for row in rows:
        if not row:
            continue
        path = row[0].strip()
        if not path or path.lower() == "filename":
            continue
        hits.append(
            SearchHit(
                path=path,
                size=_parse_int(row[1]) if len(row) > 1 else None,
                date_modified=row[2].strip() if len(row) > 2 and row[2].strip() else None,
                attributes=row[3].strip() if len(row) > 3 and row[3].strip() else None,
            )
        )
    return hits


def _parse_int(value: str) -> int | None:
    stripped = value.strip().replace(",", "")
    if not stripped:
        return None
    try:
        return int(stripped)
    except ValueError:
        return None
