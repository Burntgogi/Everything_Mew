"""ES CLI adapter using subprocess without shell expansion."""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
from io import StringIO
from pathlib import Path

from everything_mcp.contracts import AdapterStatus, HARD_LIMIT, SearchHit, SortName
from everything_mcp.errors import QueryError
from everything_mcp.query import compose_query

DEFAULT_ES_TIMEOUT_SECONDS = 15
STATUS_PROBE_TIMEOUT_SECONDS = 2
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+")
LOCALE_DECODE_ERROR_NOTE = (
    "ES CLI output could not be decoded using the active Windows locale; verify locale configuration with a recorded "
    "or live ES fixture before configuring an explicit encoding."
)

ES_RETURN_CODE_NOTES: dict[int, tuple[str, str]] = {
    1: ("register window class failure", "restart ES CLI and retry"),
    2: ("listening window failure", "restart Everything and retry"),
    3: ("out of memory", "reduce the result scope or restart Everything"),
    4: ("missing option argument", "supply the missing option argument"),
    5: ("export output failure", "verify the export destination permissions"),
    6: ("unknown switch", "use a compatible es.exe version"),
    7: ("failed IPC query", "restart Everything and retry the query"),
    8: ("Everything IPC window not found", "start Everything and retry"),
}

SORT_ARGS: dict[str, str] = {
    "name": "name-ascending",
    "path": "path-ascending",
    "size": "size-ascending",
    "date_modified": "date-modified-ascending",
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
        try:
            completed = subprocess.run(
                [str(self.es_exe), "-get-everything-version"],
                check=False,
                capture_output=True,
                text=True,
                shell=False,
                timeout=STATUS_PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            notes.append(f"ES CLI status probe timed out after {STATUS_PROBE_TIMEOUT_SECONDS} seconds; Everything is not confirmed running.")
        except UnicodeError:
            notes.append(LOCALE_DECODE_ERROR_NOTE)
        except OSError as exc:
            notes.append(f"ES CLI status probe could not be executed at {self.es_exe}: {exc}")
        else:
            if completed.returncode == 0:
                version = _version_from_stdout(completed.stdout)
                if version is not None:
                    notes.append(f"Everything version {version} confirmed by ES CLI.")
                    return AdapterStatus(
                        everything_installed=True,
                        everything_running=True,
                        backend="es-cli",
                        es_cli_available=True,
                        notes=tuple(notes),
                    )
                notes.append(
                    "ES CLI status probe returned invalid version output; expected exactly one numeric "
                    "major.minor.revision.build version."
                )
            else:
                notes.append(f"ES CLI status probe failed with exit code {completed.returncode}: {_return_code_note(completed.returncode)}.")
        return AdapterStatus(
            everything_installed=self.everything_installed,
            everything_running=False,
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
        safe_limit = max(1, min(int(limit), HARD_LIMIT + 1))
        args = ["-n", str(safe_limit), "-sort", _sort_arg(sort)]
        if metadata:
            args.extend(
                [
                    "-csv",
                    "-no-header",
                    "-size",
                    "-size-format",
                    "1",
                    "-no-digit-grouping",
                    "-dm",
                    "-date-format",
                    "3",
                    "-attribs",
                ]
            )
        args.append(self._safe_query_arg(query, scope))
        completed = self._run(args)
        if metadata:
            return _parse_metadata_csv(completed.stdout)
        return [SearchHit(path=line.strip()) for line in completed.stdout.splitlines() if line.strip()]

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            # Keep locale-aware text decoding; the recorded CSV fixture covers Korean and quoted commas.
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
            note = _return_code_note(exc.returncode)
            raise QueryError(f"ES CLI query failed with exit code {exc.returncode}: {note}. Details: {stderr}") from exc
        except subprocess.TimeoutExpired as exc:
            raise QueryError(f"ES CLI query timed out after {DEFAULT_ES_TIMEOUT_SECONDS} seconds; refine the query or check Everything runtime status.") from exc
        except UnicodeError:
            raise QueryError(LOCALE_DECODE_ERROR_NOTE) from None
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


def _return_code_note(returncode: int) -> str:
    description, action = ES_RETURN_CODE_NOTES.get(returncode, ("unrecognized ES CLI failure", "inspect ES CLI output and retry"))
    return f"{description}; {action}"


def _version_from_stdout(output: str) -> str | None:
    versions = [line.strip() for line in output.splitlines() if line.strip()]
    if len(versions) != 1:
        return None
    version = versions[0]
    return version if VERSION_PATTERN.fullmatch(version) else None


def _parse_metadata_csv(output: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    try:
        rows = csv.reader(StringIO(output.lstrip("\ufeff")), strict=True)
        for row in rows:
            if not row:
                continue
            if len(row) != 4:
                raise QueryError(f"ES CLI metadata CSV must contain exactly four columns; received {len(row)}.")
            path = row[0].lstrip("\ufeff").strip()
            if not path:
                continue
            hits.append(
                SearchHit(
                    path=path,
                    size=_parse_size(row[1]),
                    date_modified=row[2].strip() or None,
                    attributes=row[3].strip() or None,
                )
            )
    except csv.Error as exc:
        raise QueryError("ES CLI metadata CSV is malformed; retry the query or verify the local ES CLI output.") from exc
    return hits


def _parse_size(value: str) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    if not stripped.isascii() or not stripped.isdecimal():
        raise QueryError(f"ES CLI metadata size must be an ungrouped byte integer or empty; received {value!r}.")
    return int(stripped)
