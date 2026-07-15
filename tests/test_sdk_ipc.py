from __future__ import annotations

import ctypes
from pathlib import Path
from typing import Any

import pytest
from pytest import MonkeyPatch

from everything_mcp.adapters import sdk_ipc
from everything_mcp.config import EverythingConfig
from everything_mcp.contracts import SearchBatch, SearchHit
from everything_mcp.errors import BackendUnavailableError, QueryError


class FakeFunction:
    def __init__(self) -> None:
        self.argtypes: list[object] | None = None
        self.restype: object | None = None


class SignatureDll:
    def __init__(self) -> None:
        function_names = (
            "Everything_IsDBLoaded",
            "Everything_GetMajorVersion",
            "Everything_GetMinorVersion",
            "Everything_GetRevision",
            "Everything_GetBuildNumber",
            "Everything_GetTargetMachine",
            "Everything_GetResultListSort",
            "Everything_GetResultListRequestFlags",
            "Everything_Reset",
            "Everything_SetSearchW",
            "Everything_SetRequestFlags",
            "Everything_SetSort",
            "Everything_SetMax",
            "Everything_QueryW",
            "Everything_GetTotResults",
            "Everything_GetNumResults",
            "Everything_GetLastError",
            "Everything_GetResultFullPathNameW",
            "Everything_GetResultSize",
            "Everything_GetResultDateModified",
            "Everything_GetResultAttributes",
        )
        for name in function_names:
            setattr(self, name, FakeFunction())


class StatusDll:
    def __init__(
        self,
        *,
        db_loaded: bool,
        last_error: int = 0,
        version: tuple[int, int, int, int] = (1, 4, 1, 1032),
        target_machine: int = 2,
    ) -> None:
        self.db_loaded = db_loaded
        self.last_error = last_error
        self.version = version
        self.target_machine = target_machine

    def Everything_IsDBLoaded(self) -> bool:
        return self.db_loaded

    def Everything_GetLastError(self) -> int:
        return self.last_error

    def Everything_GetMajorVersion(self) -> int:
        return self.version[0]

    def Everything_GetMinorVersion(self) -> int:
        return self.version[1]

    def Everything_GetRevision(self) -> int:
        return self.version[2]

    def Everything_GetBuildNumber(self) -> int:
        return self.version[3]

    def Everything_GetTargetMachine(self) -> int:
        return self.target_machine


class BadInt:
    def __int__(self) -> int:
        raise ValueError("conversion failed")


class QueryDll(StatusDll):
    def __init__(
        self,
        *,
        failure: str | None = None,
        actual_sort: int = 1,
        actual_flags: int = sdk_ipc.METADATA_FLAGS,
        num_results: int = 1,
        paths: tuple[str, ...] = (r"C:\Work\everything_mcp\sample.py",),
        unavailable_fields: frozenset[str] = frozenset(),
        size_value: int = 123,
        date_value: int = 133801632000000000,
        attributes_value: int = 0x20,
        reset_failure: bool = False,
    ) -> None:
        super().__init__(db_loaded=True)
        self.failure = failure
        self.actual_sort = actual_sort
        self.actual_flags = actual_flags
        self.num_results = num_results
        self.paths = paths
        self.unavailable_fields = unavailable_fields
        self.size_value = size_value
        self.date_value = date_value
        self.attributes_value = attributes_value
        self.reset_failure = reset_failure
        self.reset_calls = 0
        self.cleanup_calls = 0
        self.path_calls = 0
        self.size_calls = 0
        self.date_calls = 0
        self.attribute_calls = 0
        self.call_order: list[str] = []

    def Everything_SetSearchW(self, query: str) -> None:
        self.call_order.append("set-search")
        if self.failure == "setup":
            raise OSError(r"C:\private\setup-secret.txt")
        self.query = query

    def Everything_SetRequestFlags(self, flags: int) -> None:
        self.request_flags = flags

    def Everything_SetSort(self, sort: int) -> None:
        self.requested_sort = sort

    def Everything_SetMax(self, limit: int) -> None:
        self.limit = limit

    def Everything_QueryW(self, wait: bool) -> bool:
        self.call_order.append("query")
        return self.failure != "query"

    def Everything_GetTotResults(self) -> int | BadInt:
        return BadInt() if self.failure == "copy" else 7

    def Everything_GetNumResults(self) -> int:
        return self.num_results

    def Everything_GetResultListSort(self) -> int:
        self.call_order.append("actual-sort")
        if self.failure == "actual-sort":
            raise OSError(r"C:\private\sort-secret.txt")
        return self.actual_sort

    def Everything_GetResultListRequestFlags(self) -> int:
        self.call_order.append("actual-flags")
        if self.failure == "actual-flags":
            raise AttributeError(r"C:\private\flags-secret.txt")
        return self.actual_flags

    def Everything_GetResultFullPathNameW(self, index: int, buffer: Any, size: int) -> int:
        self.path_calls += 1
        if self.failure == "copy":
            raise ValueError("result copy failed")
        if self.failure == "empty-path":
            return 0
        buffer.value = self.paths[index]
        return len(buffer.value)

    def Everything_GetResultSize(self, index: int, value_pointer: Any) -> bool:
        self.size_calls += 1
        if self.failure == "metadata":
            raise OSError(r"C:\private\metadata-secret.txt")
        if "size" in self.unavailable_fields:
            return False
        value_pointer._obj.value = self.size_value
        return True

    def Everything_GetResultDateModified(self, index: int, value_pointer: Any) -> bool:
        self.date_calls += 1
        if "dateModified" in self.unavailable_fields:
            return False
        value_pointer._obj.dwLowDateTime = self.date_value & 0xFFFFFFFF
        value_pointer._obj.dwHighDateTime = (self.date_value >> 32) & 0xFFFFFFFF
        return True

    def Everything_GetResultAttributes(self, index: int) -> int:
        self.attribute_calls += 1
        return self.attributes_value

    def Everything_Reset(self) -> None:
        self.reset_calls += 1
        self.call_order.append("reset")
        if self.reset_failure:
            raise OSError(r"C:\private\reset-secret.txt")

    def Everything_CleanUp(self) -> None:
        self.cleanup_calls += 1
        raise AssertionError("Everything_CleanUp must not run per query")


def _adapter(dll: object, everything_exe: Path = Path(r"C:\Program Files\Everything\Everything.exe")) -> sdk_ipc.SdkIpcAdapter:
    adapter = sdk_ipc.SdkIpcAdapter.__new__(sdk_ipc.SdkIpcAdapter)
    adapter.config = EverythingConfig(everything_exe=everything_exe)
    adapter._dll = dll
    adapter._load_error = None
    return adapter


def test_sdk_sort_flags_map_all_public_sorts_to_ascending_constants() -> None:
    assert sdk_ipc.SORT_FLAGS == {"name": 1, "path": 3, "size": 5, "date_modified": 13}


def test_sdk_adapter_configures_exact_new_ctypes_signatures() -> None:
    adapter = _adapter(SignatureDll())

    adapter._configure_functions()

    dll = adapter._dll
    assert dll is not None
    assert dll.Everything_IsDBLoaded.argtypes == []
    assert dll.Everything_IsDBLoaded.restype is ctypes.wintypes.BOOL
    for name in (
        "Everything_GetMajorVersion",
        "Everything_GetMinorVersion",
        "Everything_GetRevision",
        "Everything_GetBuildNumber",
        "Everything_GetTargetMachine",
        "Everything_GetResultListSort",
        "Everything_GetResultListRequestFlags",
    ):
        function = getattr(dll, name)
        assert function.argtypes == []
        assert function.restype is ctypes.wintypes.DWORD
    assert dll.Everything_Reset.argtypes == []
    assert dll.Everything_Reset.restype is None
    assert dll.Everything_GetResultSize.argtypes == [
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.LARGE_INTEGER),
    ]
    assert dll.Everything_GetResultDateModified.argtypes == [
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.FILETIME),
    ]


def test_sdk_status_distinguishes_absent_dll(tmp_path: Path) -> None:
    adapter = _adapter(None, tmp_path / "Everything.exe")
    adapter._load_error = "Everything SDK DLL was not found."

    result = adapter.status().to_tool_result()

    assert result["everythingRunning"] is False
    assert result["backend"] == "none"
    assert "dbLoaded" not in result
    assert "version" not in result
    assert "targetMachine" not in result


def test_sdk_status_does_not_expose_configured_dll_path(tmp_path: Path) -> None:
    dll_path = tmp_path / "private" / "Everything64.dll"
    adapter = sdk_ipc.SdkIpcAdapter(EverythingConfig(everything_exe=tmp_path / "Everything.exe", sdk_dll=dll_path))

    result = adapter.status().to_tool_result()

    assert str(dll_path) not in " ".join(result["notes"])


def test_sdk_loader_symbol_lookup_does_not_expose_raw_error_or_dll_path(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    secret = r"C:\private\loader-symbol-secret.txt"
    dll_path = tmp_path / "private" / "Everything64.dll"

    class MissingSymbolDll:
        def __getattr__(self, name: str) -> object:
            raise AttributeError(f"{name} failed near {secret}")

    monkeypatch.setattr(ctypes, "WinDLL", lambda path: MissingSymbolDll())

    adapter = sdk_ipc.SdkIpcAdapter(EverythingConfig(everything_exe=Path(r"C:\Program Files\Everything\Everything.exe"), sdk_dll=dll_path))
    notes = " ".join(adapter.status().notes)

    assert secret not in notes
    assert str(dll_path) not in notes
    assert "required function" in notes


@pytest.mark.parametrize("error_type", (AttributeError, OSError))
def test_sdk_readiness_failure_does_not_expose_raw_error(error_type: type[Exception]) -> None:
    secret = r"C:\private\readiness-secret.txt"

    class BrokenReadinessDll(StatusDll):
        def Everything_IsDBLoaded(self) -> bool:
            raise error_type(secret)

    adapter = _adapter(BrokenReadinessDll(db_loaded=False))

    notes = " ".join(adapter.status().notes)

    assert secret not in notes
    assert "readiness" in notes


def test_sdk_status_distinguishes_stopped_ipc(tmp_path: Path) -> None:
    everything_exe = tmp_path / "Everything.exe"
    everything_exe.touch()
    adapter = _adapter(StatusDll(db_loaded=False, last_error=8, version=(0, 0, 0, 0), target_machine=0), everything_exe)

    result = adapter.status().to_tool_result()

    assert result["everythingRunning"] is False
    assert result["backend"] == "none"
    assert result["dbLoaded"] is False
    assert "version" not in result
    assert "targetMachine" not in result
    assert any("error 8" in note for note in result["notes"])


def test_sdk_status_distinguishes_running_but_loading_database(tmp_path: Path) -> None:
    everything_exe = tmp_path / "Everything.exe"
    everything_exe.touch()
    adapter = _adapter(StatusDll(db_loaded=False), everything_exe)

    result = adapter.status().to_tool_result()

    assert result["everythingRunning"] is True
    assert result["backend"] == "sdk-ipc"
    assert result["dbLoaded"] is False
    assert result["version"] == "1.4.1.1032"
    assert result["targetMachine"] == "x64"
    assert any("loading" in note.lower() for note in result["notes"])


def test_sdk_status_reports_ready_version_and_target(tmp_path: Path) -> None:
    everything_exe = tmp_path / "Everything.exe"
    everything_exe.touch()
    adapter = _adapter(StatusDll(db_loaded=True), everything_exe)

    result = adapter.status().to_tool_result()

    assert result["everythingRunning"] is True
    assert result["backend"] == "sdk-ipc"
    assert result["dbLoaded"] is True
    assert result["version"] == "1.4.1.1032"
    assert result["targetMachine"] == "x64"


def test_loading_database_cannot_query_or_masquerade_as_empty_results() -> None:
    adapter = _adapter(StatusDll(db_loaded=False))

    with pytest.raises(BackendUnavailableError, match="loading"):
        adapter.search("ext:py", scope=r"C:\Work")


@pytest.mark.parametrize("operation", ("count", "search"))
@pytest.mark.parametrize("failure", (None, "query", "copy"))
def test_sdk_queries_reset_exactly_once_on_every_exit(operation: str, failure: str | None) -> None:
    dll = QueryDll(failure=failure)
    adapter = _adapter(dll)

    if failure == "query":
        with pytest.raises(QueryError):
            getattr(adapter, operation)("ext:py", scope=r"C:\Work")
    elif failure == "copy":
        with pytest.raises(QueryError):
            getattr(adapter, operation)("ext:py", scope=r"C:\Work")
    else:
        result = getattr(adapter, operation)("ext:py", scope=r"C:\Work")
        if operation == "count":
            assert result == 7
        else:
            assert result == [SearchHit(path=r"C:\Work\everything_mcp\sample.py")]

    assert dll.reset_calls == 1


@pytest.mark.parametrize("failure", ("setup", "actual-sort", "actual-flags", "metadata"))
def test_sdk_search_preserves_shaped_primary_failure_and_resets_for_each_boundary(failure: str) -> None:
    dll = QueryDll(failure=failure)
    adapter = _adapter(dll)

    with pytest.raises(QueryError) as exc_info:
        adapter.search("ext:py", scope=r"C:\Work", metadata=True)

    assert "C:\\private" not in str(exc_info.value)
    assert dll.reset_calls == 1
    assert dll.call_order[-1] == "reset"


@pytest.mark.parametrize("operation", ("count", "search"))
def test_sdk_successful_operation_reset_failure_is_shaped_and_sanitized(operation: str) -> None:
    dll = QueryDll(reset_failure=True)
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="reset") as exc_info:
        getattr(adapter, operation)("ext:py", scope=r"C:\Work")

    assert "C:\\private" not in str(exc_info.value)
    assert dll.reset_calls == 1


def test_sdk_primary_failure_is_preserved_when_reset_also_fails() -> None:
    dll = QueryDll(failure="setup", reset_failure=True)
    adapter = _adapter(dll)

    with pytest.raises(QueryError) as exc_info:
        adapter.search("ext:py", scope=r"C:\Work")

    message = str(exc_info.value)
    assert "operation failed" in message
    assert "reset" not in message
    assert "C:\\private" not in message
    assert dll.reset_calls == 1


def test_path_only_search_compares_missing_full_path_flag_and_keeps_nonempty_path() -> None:
    dll = QueryDll(actual_flags=0)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work")

    assert isinstance(result, SearchBatch)
    assert result.hits == (SearchHit(path=r"C:\Work\everything_mcp\sample.py"),)
    assert result.notes[0].count("fullPath") == 1


def test_zero_result_search_still_reports_missing_requested_full_path_flag() -> None:
    dll = QueryDll(actual_flags=0, num_results=0, paths=())
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work")

    assert isinstance(result, SearchBatch)
    assert result.hits == ()
    assert result.notes[0].count("fullPath") == 1


def test_extra_unrequested_actual_flags_do_not_produce_warning() -> None:
    dll = QueryDll(actual_flags=sdk_ipc.REQUEST_FULL_PATH | sdk_ipc.REQUEST_SIZE)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work")

    assert result == [SearchHit(path=r"C:\Work\everything_mcp\sample.py")]


def test_zero_or_failed_full_path_copy_never_publishes_empty_path() -> None:
    dll = QueryDll(failure="empty-path", actual_flags=0)
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="full path"):
        adapter.search("ext:py", scope=r"C:\Work")

    assert dll.reset_calls == 1


def test_sdk_diagnostics_cross_adapter_boundary_and_omit_unavailable_metadata() -> None:
    actual_flags = sdk_ipc.REQUEST_FULL_PATH | sdk_ipc.REQUEST_SIZE
    dll = QueryDll(actual_sort=sdk_ipc.EVERYTHING_SORT_NAME_ASCENDING, actual_flags=actual_flags)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work", sort="date_modified", metadata=True)

    assert isinstance(result, SearchBatch)
    assert result.hits == (SearchHit(path=r"C:\Work\everything_mcp\sample.py", size=123),)
    assert len(result.notes) == 1
    assert "sort" in result.notes[0]
    assert "dateModified" in result.notes[0]
    assert "attributes" in result.notes[0]
    assert dll.date_calls == 0
    assert dll.attribute_calls == 0


def test_advertised_but_unavailable_metadata_is_omitted_with_one_deduplicated_note() -> None:
    dll = QueryDll(
        num_results=2,
        paths=(r"C:\Work\one.py", r"C:\Work\two.py"),
        unavailable_fields=frozenset({"size", "dateModified"}),
        attributes_value=0xFFFFFFFF,
    )
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work", metadata=True)

    assert isinstance(result, SearchBatch)
    assert result.hits == (SearchHit(path=r"C:\Work\one.py"), SearchHit(path=r"C:\Work\two.py"))
    assert len(result.notes) == 1
    assert result.notes[0].count("size") == 1
    assert result.notes[0].count("dateModified") == 1
    assert result.notes[0].count("attributes") == 1
    assert dll.size_calls == 2
    assert dll.date_calls == 2
    assert dll.attribute_calls == 2


def test_negative_size_is_unavailable_but_zero_attributes_are_valid() -> None:
    dll = QueryDll(size_value=-1, attributes_value=0)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work", metadata=True)

    assert isinstance(result, SearchBatch)
    assert result.hits[0].size is None
    assert result.hits[0].attributes == "0x00000000"
    assert result.notes[0].count("size") == 1
    assert "attributes" not in result.notes[0]


@pytest.mark.parametrize("raw_filetime", (0, 0xFFFFFFFFFFFFFFFF))
def test_unknown_filetime_is_unavailable_and_noted(raw_filetime: int) -> None:
    dll = QueryDll(date_value=raw_filetime)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work", metadata=True)

    assert isinstance(result, SearchBatch)
    assert result.hits[0].date_modified is None
    assert result.notes[0].count("dateModified") == 1


def test_invalid_file_attributes_are_unavailable_and_noted() -> None:
    dll = QueryDll(attributes_value=0xFFFFFFFF)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work", metadata=True)

    assert isinstance(result, SearchBatch)
    assert result.hits[0].attributes is None
    assert result.notes[0].count("attributes") == 1


def test_sdk_actual_sort_and_metadata_match_produces_no_warning() -> None:
    dll = QueryDll(actual_sort=sdk_ipc.EVERYTHING_SORT_DATE_MODIFIED_ASCENDING)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work", sort="date_modified", metadata=True)

    assert isinstance(result, list)
    assert result[0].to_metadata_result() == {
        "path": r"C:\Work\everything_mcp\sample.py",
        "size": 123,
        "dateModified": "2025-01-01T00:00:00+00:00",
        "attributes": "0x00000020",
    }


def test_sdk_count_and_search_never_call_cleanup_even_through_helpers() -> None:
    for operation in ("count", "search"):
        dll = QueryDll()
        adapter = _adapter(dll)

        getattr(adapter, operation)("ext:py", scope=r"C:\Work")

        assert dll.cleanup_calls == 0
