from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

from everything_mcp.adapters import es_cli
from everything_mcp.contracts import SearchHit
from everything_mcp.errors import QueryError


RETURN_CODE_CASES = [
    (1, "register window class failure", "restart ES CLI and retry"),
    (2, "listening window failure", "restart Everything and retry"),
    (3, "out of memory", "reduce the result scope or restart Everything"),
    (4, "missing option argument", "supply the missing option argument"),
    (5, "export output failure", "verify the export destination permissions"),
    (6, "unknown switch", "use a compatible es.exe version"),
    (7, "failed IPC query", "restart Everything and retry the query"),
    (8, "Everything IPC window not found", "start Everything and retry"),
]


def _adapter(everything_installed: bool = True) -> es_cli.EsCliAdapter:
    return es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"), everything_installed=everything_installed)


def test_status_probes_valid_everything_version_with_dedicated_timeout(monkeypatch: MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="1.4.1.1026\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = _adapter(everything_installed=False).status()

    assert status.everything_installed is True
    assert status.everything_running is True
    assert status.backend == "es-cli"
    assert any("1.4.1.1026" in note for note in status.notes)
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


@pytest.mark.parametrize("stdout", ["", "Everything 1.4.1.1026", "1.4.1", "1.4.1.1026\n2.0.0.0"])
def test_status_rejects_empty_or_malformed_successful_version_output(monkeypatch: MonkeyPatch, stdout: str) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr=""),
    )

    status = _adapter(everything_installed=False).status()

    assert status.everything_installed is False
    assert status.everything_running is False
    assert any("version output" in note for note in status.notes)


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


@pytest.mark.parametrize(("returncode", "description", "action"), RETURN_CODE_CASES)
def test_query_failure_includes_official_return_code_description_and_remediation(
    monkeypatch: MonkeyPatch, returncode: int, description: str, action: str
) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.CalledProcessError(returncode, "es.exe", stderr="runtime detail")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(QueryError) as caught:
        _adapter().count("ext:md")

    assert description in str(caught.value)
    assert action in str(caught.value)
    assert "check syntax" not in str(caught.value)


def test_status_reports_locale_decode_error_without_claiming_runtime(monkeypatch: MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise UnicodeDecodeError("cp949", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(subprocess, "run", fake_run)

    status = _adapter().status()

    assert status.everything_running is False
    assert any("active Windows locale" in note for note in status.notes)
    assert all("invalid start byte" not in note for note in status.notes)


def test_query_reports_locale_decode_error_without_traceback_leakage(monkeypatch: MonkeyPatch) -> None:
    def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise UnicodeDecodeError("cp949", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(QueryError) as caught:
        _adapter().count("ext:md")

    assert "active Windows locale" in str(caught.value)
    assert "recorded or live ES fixture" in str(caught.value)
    assert "invalid start byte" not in str(caught.value)
    assert caught.value.__cause__ is None


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
    run_options: list[dict[str, Any]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(args)
        run_options.append(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=recorded_csv.encode("utf-8-sig"), stderr=b"")

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
    assert run_options == [
        {
            "check": True,
            "capture_output": True,
            "text": False,
            "shell": False,
            "timeout": es_cli.DEFAULT_ES_TIMEOUT_SECONDS,
        }
    ]
    assert hits == [
        SearchHit(
            path=r"C:\자료\보고서, 최종.csv",
            size=123456,
            date_modified="2026-07-15T12:34:56Z",
            attributes="A",
        )
    ]


def test_metadata_empty_size_maps_to_none(monkeypatch: MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        output = '"C:\\Work\\empty.txt","","2026-07-15T12:34:56Z","A"\n'.encode("utf-8")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=output, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _adapter().search("empty", metadata=True) == [
        SearchHit(path=r"C:\Work\empty.txt", size=None, date_modified="2026-07-15T12:34:56Z", attributes="A")
    ]


@pytest.mark.parametrize(
    ("output", "message"),
    [
        ('"C:\\Work\\short.txt","1","2026-07-15T12:34:56Z"\n', "exactly four columns"),
        ('"C:\\Work\\extra.txt","1","2026-07-15T12:34:56Z","A","extra"\n', "exactly four columns"),
        ('"C:\\Work\\unterminated.txt","1","2026-07-15T12:34:56Z,"A"\n', "malformed"),
        ('"C:\\Work\\grouped.txt","1,234","2026-07-15T12:34:56Z","A"\n', "ungrouped byte integer"),
        ('"C:\\Work\\nonnumeric.txt","many","2026-07-15T12:34:56Z","A"\n', "ungrouped byte integer"),
    ],
)
def test_metadata_rejects_invalid_csv_rows(monkeypatch: MonkeyPatch, output: str, message: str) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=output.encode("utf-8"), stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(QueryError, match=message):
        _adapter().search("ext:txt", metadata=True)


def test_metadata_rejects_invalid_utf8_without_locale_fallback(monkeypatch: MonkeyPatch) -> None:
    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"\x81\n", stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(QueryError, match="UTF-8"):
        _adapter().search("ext:txt", metadata=True)
