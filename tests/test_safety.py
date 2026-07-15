from importlib import import_module
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import pytest
from pytest import MonkeyPatch

es_cli = import_module("everything_mcp.adapters.es_cli")
sdk_ipc = import_module("everything_mcp.adapters.sdk_ipc")
config_module = import_module("everything_mcp.config")
errors = import_module("everything_mcp.errors")
query_module = import_module("everything_mcp.query")
server = import_module("everything_mcp.server")


class SubprocessRunCall(TypedDict):
    args: list[str]
    check: bool
    capture_output: bool
    text: bool
    shell: bool
    timeout: float


class SafetyAdapter:
    name = "fake"

    def __init__(self) -> None:
        self.count_calls: list[tuple[str, str | None]] = []
        self.search_calls: list[tuple[str, str | None]] = []

    def count(self, query: str, scope: str | None = None) -> int:
        self.count_calls.append((query, scope))
        return 1

    def search(self, query: str, scope: str | None = None, **_: Any) -> list[object]:
        self.search_calls.append((query, scope))
        return []


SAFETY_MATRIX = (
    ("single exclusion", "!node_modules", None, True),
    ("multiple exclusions", "!node_modules !.git", None, True),
    ("quoted exclusion", '!"node modules"', None, True),
    ("quoted exclusion scope", '!"node modules"', r"C:\Work\project", True),
    ("mixed quoted and unquoted exclusions", '!"node modules" !.git', None, True),
    ("extension with trailing exclusion", "ext:md !node_modules", None, True),
    ("extension with leading exclusion", "!node_modules ext:md", None, True),
    ("content with excluded extension text", 'content:foo !"ignored ext:md"', r"C:\Work\project", True),
    (
        "content with excluded size and date text",
        'content:foo !"ignored size:100 dm:2024-01-01"',
        r"C:\Work\project",
        True,
    ),
    ("drive root", "C:\\", None, True),
    ("quoted drive root", r'"C:\"', None, True),
    ("slash-normalized drive root", "C:/", None, True),
    ("path drive root", "path:C:\\", None, True),
    ("quoted path drive root", r'path:"C:\"', None, True),
    ("drive root scope", "README.md", "C:\\", True),
    ("UNC server root", r"\\server", None, True),
    ("UNC share root", r"\\server\share", None, True),
    ("quoted UNC share root", r'"\\server\share"', None, True),
    ("path UNC share root", r"path:\\server\share", None, True),
    ("quoted path UNC share root", r'path:"\\server\share"', None, True),
    ("UNC share root scope", "README.md", r"\\server\share", True),
    ("local project filename", "README.md", r"C:\\Work\\project", False),
    ("UNC project filter", "ext:md", r"\\server\share\project", False),
    ("unscoped positive filter", "report ext:md", None, False),
    ("filename, extension, and exclusion", "README.md ext:md !node_modules", None, False),
    ("project extension and exclusion", "ext:md !node_modules", r"C:\\Work\\project", False),
    ("content real extension and exclusion", 'content:foo ext:md !"ignored ext:md"', r"C:\\Work\\project", False),
)


@pytest.mark.parametrize(("_name", "query", "scope", "expected_broad"), SAFETY_MATRIX)
def test_broad_query_safety_matrix(_name: str, query: str, scope: str | None, expected_broad: bool) -> None:
    assert server.is_broad_query(query, scope) is expected_broad


@pytest.mark.parametrize("query", ('!"node modules"', '!"node modules" !.git'))
def test_exclusion_only_query_recognizes_quoted_phrases(query: str) -> None:
    assert query_module.is_exclusion_only_query(query) is True


@pytest.mark.parametrize(
    ("query", "expected"),
    (
        ('content:foo !"ignored ext:md"', False),
        ('content:foo !"ignored size:100 dm:2024-01-01"', False),
        ('content:foo "ignored ext:md"', False),
        ('content:foo EXT:md !"ignored size:100"', True),
    ),
)
def test_positive_narrowing_filter_uses_only_real_positive_filter_terms(query: str, expected: bool) -> None:
    assert query_module.has_positive_narrowing_filter(query) is expected


@pytest.mark.parametrize(
    ("_name", "query", "scope", "expected_broad"),
    tuple(case for case in SAFETY_MATRIX if case[3]),
)
def test_rejected_safety_matrix_cases_do_not_call_adapters(
    _name: str, query: str, scope: str | None, expected_broad: bool
) -> None:
    adapter = SafetyAdapter()

    count = server.everything_count(query, scope=scope, adapter=adapter)
    search = server.everything_search(query, scope=scope, adapter=adapter)

    assert expected_broad is True
    assert count["tooBroad"] is True
    assert search["tooBroad"] is True
    assert adapter.count_calls == []
    assert adapter.search_calls == []
    assert "exclusion" not in count["recommendation"]
    assert "exclusion" not in search["recommendation"]


def test_sdk_adapter_missing_dll_reports_status_without_crashing(tmp_path: Path) -> None:
    adapter = sdk_ipc.SdkIpcAdapter(config_module.EverythingConfig(everything_exe=tmp_path / "Everything.exe", sdk_dll=tmp_path / "missing.dll"))

    status = adapter.status()

    assert status.backend == "none"
    assert status.everything_running is False
    assert any("DLL" in note for note in status.notes)


def test_es_cli_uses_subprocess_without_shell(monkeypatch: MonkeyPatch) -> None:
    calls: list[SubprocessRunCall] = []

    def fake_run(
        args: list[str],
        check: bool,
        capture_output: bool,
        text: bool,
        shell: bool,
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        calls.append({"args": args, "check": check, "capture_output": capture_output, "text": text, "shell": shell, "timeout": timeout})
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    assert adapter.count("ext:md", scope=r"C:\Work") == 3
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == es_cli.DEFAULT_ES_TIMEOUT_SECONDS
    assert calls[0]["args"][0] == r"C:\Tools\es.exe"
    assert calls[0]["args"][-1] == 'path:"C:\\Work" ext:md'


def test_es_cli_bad_count_raises_actionable_query_error(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args=args, returncode=0, stdout="not-a-number\n", stderr=""),
    )
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    try:
        adapter.count("ext:md", scope=r"C:\Work")
    except errors.QueryError as exc:
        assert "numeric count" in str(exc)
    else:
        raise AssertionError("Expected QueryError for non-numeric ES count output")


def test_es_cli_rejects_leading_dash_query() -> None:
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    try:
        adapter.count("-dangerous-option")
    except errors.QueryError as exc:
        assert "must not start with '-'" in str(exc)
    else:
        raise AssertionError("Expected QueryError for leading-dash ES query")


def test_es_cli_rejects_unknown_sort() -> None:
    try:
        es_cli._sort_arg("unknown")
    except errors.QueryError as exc:
        assert "Unsupported ES CLI sort" in str(exc)
    else:
        raise AssertionError("Expected QueryError for unsupported ES sort")


def test_es_cli_timeout_raises_query_error(monkeypatch: MonkeyPatch) -> None:
    def fake_run(*args: object, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd="es.exe", timeout=kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    try:
        adapter.count("ext:md", scope=r"C:\Work")
    except errors.QueryError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("Expected QueryError for ES timeout")


def test_es_cli_spaced_scope_is_quoted() -> None:
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    assert adapter._compose_query("ext:exe", r"C:\Program Files") == 'path:"C:\\Program Files" ext:exe'


def test_sdk_adapter_configures_ctypes_signatures() -> None:
    class FakeFunction:
        def __init__(self) -> None:
            self.argtypes = None
            self.restype = None

    class FakeDll:
        def __init__(self) -> None:
            self.Everything_GetMajorVersion = FakeFunction()
            self.Everything_SetSearchW = FakeFunction()
            self.Everything_SetRequestFlags = FakeFunction()
            self.Everything_SetSort = FakeFunction()
            self.Everything_SetMax = FakeFunction()
            self.Everything_QueryW = FakeFunction()
            self.Everything_GetTotResults = FakeFunction()
            self.Everything_GetNumResults = FakeFunction()
            self.Everything_GetLastError = FakeFunction()
            self.Everything_GetResultFullPathNameW = FakeFunction()
            self.Everything_GetResultSize = FakeFunction()
            self.Everything_GetResultDateModified = FakeFunction()
            self.Everything_GetResultAttributes = FakeFunction()

    adapter = sdk_ipc.SdkIpcAdapter.__new__(sdk_ipc.SdkIpcAdapter)
    adapter._dll = FakeDll()

    adapter._configure_functions()

    assert adapter._dll.Everything_QueryW.argtypes is not None
    assert adapter._dll.Everything_GetResultFullPathNameW.restype is not None
    assert adapter._dll.Everything_SetSort.argtypes is not None


def test_sdk_date_modified_ignores_unknown_and_out_of_range_filetime() -> None:
    class FakeDll:
        def __init__(self, raw_value: int) -> None:
            self.raw_value = raw_value

        def Everything_GetResultDateModified(self, index: int, value_pointer: Any) -> bool:
            value_pointer._obj.value = self.raw_value
            return True

    adapter = sdk_ipc.SdkIpcAdapter.__new__(sdk_ipc.SdkIpcAdapter)
    adapter._dll = FakeDll(0)
    assert adapter._result_date_modified(0) is None

    adapter._dll = FakeDll(0xFFFFFFFFFFFFFFFF)
    assert adapter._result_date_modified(0) is None

    adapter._dll = FakeDll(0x7FFFFFFFFFFFFFFF)
    assert adapter._result_date_modified(0) is None
