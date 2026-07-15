from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

from everything_mcp.adapters import es_cli
from everything_mcp.contracts import SearchHit
from everything_mcp.errors import QueryError


def _adapter() -> es_cli.EsCliAdapter:
    return es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))


def test_status_probes_everything_version_with_dedicated_timeout(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="1.4.1.1026\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = _adapter().status()

    assert status.everything_running is True
    assert status.backend == "es-cli"
    assert calls == [
        (
            [r"C:\Tools\es.exe", "-get-everything-version"],
            {
                "check": False,
                "capture_output": True,
                "text": True,
                "shell": False,
                "timeout": es_cli.STATUS_PROBE_TIMEOUT_SECONDS,
            },
        )
    ]


def test_status_reports_stopped_everything_for_ipc_window_not_found(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=8, stdout="", stderr=""),
    )

    status = _adapter().status()

    assert status.everything_running is False
    assert status.backend == "es-cli"
    assert any("IPC window not found" in note for note in status.notes)


def test_status_reports_other_official_return_codes_as_not_running(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=7, stdout="", stderr=""),
    )

    status = _adapter().status()

    assert status.everything_running is False
    assert status.backend == "es-cli"
    assert any("failed IPC query" in note for note in status.notes)


def test_status_reports_timeout_as_not_running(monkeypatch: MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="es.exe", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = _adapter().status()

    assert status.everything_running is False
    assert status.backend == "es-cli"
    assert any("timed out" in note for note in status.notes)


def test_status_reports_execution_error_as_not_running(monkeypatch: MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("access denied")

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = _adapter().status()

    assert status.everything_running is False
    assert status.backend == "es-cli"
    assert any("could not be executed" in note for note in status.notes)


def test_query_failure_includes_official_return_code_note(monkeypatch: MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(7, "es.exe", stderr="runtime detail")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(QueryError, match="failed IPC query"):
        _adapter().count("ext:md")


def test_search_permits_hard_limit_plus_one_for_truncation(monkeypatch: MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="\n".join(f"C:\\Work\\file{i}" for i in range(101)), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    hits = _adapter().search("ext:md", limit=101)

    assert len(hits) == 101
    assert calls[0][1:3] == ["-n", "101"]


@pytest.mark.parametrize(
    ("sort", "expected"),
    [
        ("name", "name-ascending"),
        ("path", "path-ascending"),
        ("size", "size-ascending"),
        ("date_modified", "date-modified-ascending"),
    ],
)
def test_search_uses_explicit_ascending_sort_arguments(monkeypatch: MonkeyPatch, sort: str, expected: str) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    _adapter().search("ext:md", sort=sort)  # type: ignore[arg-type]

    assert calls[0][3:5] == ["-sort", expected]


def test_metadata_uses_deterministic_switches_and_parses_recorded_korean_csv(monkeypatch: MonkeyPatch) -> None:
    calls: list[list[str]] = []
    recorded_csv = '\ufeff"C:\\자료\\보고서, 최종.csv","123456","2026-07-15T12:34:56Z","A"\n'

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=recorded_csv, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    hits = _adapter().search("ext:csv", metadata=True)

    assert calls[0][5:-1] == [
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
    assert hits == [
        SearchHit(
            path=r"C:\자료\보고서, 최종.csv",
            size=123456,
            date_modified="2026-07-15T12:34:56Z",
            attributes="A",
        )
    ]
