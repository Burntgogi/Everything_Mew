from importlib import import_module
import subprocess
from pathlib import Path

es_cli = import_module("everything_mcp.adapters.es_cli")
sdk_ipc = import_module("everything_mcp.adapters.sdk_ipc")
config_module = import_module("everything_mcp.config")
errors = import_module("everything_mcp.errors")


def test_sdk_adapter_missing_dll_reports_status_without_crashing(tmp_path: Path) -> None:
    adapter = sdk_ipc.SdkIpcAdapter(config_module.EverythingConfig(everything_exe=tmp_path / "Everything.exe", sdk_dll=tmp_path / "missing.dll"))

    status = adapter.status()

    assert status.backend == "none"
    assert status.everything_running is False
    assert any("DLL" in note for note in status.notes)


def test_es_cli_uses_subprocess_without_shell(monkeypatch) -> None:
    calls = []

    def fake_run(args, check, capture_output, text, shell, timeout):
        calls.append({"args": args, "check": check, "capture_output": capture_output, "text": text, "shell": shell, "timeout": timeout})
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    adapter = es_cli.EsCliAdapter(Path(r"C:\Tools\es.exe"))

    assert adapter.count("ext:md", scope=r"C:\Work") == 3
    assert calls[0]["shell"] is False
    assert calls[0]["timeout"] == es_cli.DEFAULT_ES_TIMEOUT_SECONDS
    assert calls[0]["args"][0] == r"C:\Tools\es.exe"
    assert calls[0]["args"][-1] == 'path:"C:\\Work" ext:md'


def test_es_cli_bad_count_raises_actionable_query_error(monkeypatch) -> None:
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


def test_es_cli_timeout_raises_query_error(monkeypatch) -> None:
    def fake_run(*args, **kwargs):
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

        def Everything_GetResultDateModified(self, index, value_pointer):
            value_pointer._obj.value = self.raw_value
            return True

    adapter = sdk_ipc.SdkIpcAdapter.__new__(sdk_ipc.SdkIpcAdapter)
    adapter._dll = FakeDll(0)
    assert adapter._result_date_modified(0) is None

    adapter._dll = FakeDll(0xFFFFFFFFFFFFFFFF)
    assert adapter._result_date_modified(0) is None

    adapter._dll = FakeDll(0x7FFFFFFFFFFFFFFF)
    assert adapter._result_date_modified(0) is None
