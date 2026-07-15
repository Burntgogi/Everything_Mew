from __future__ import annotations

import ctypes
import inspect
from pathlib import Path
from typing import Any

import pytest

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
    def __init__(self, *, failure: str | None = None, actual_sort: int = 1, actual_flags: int = sdk_ipc.METADATA_FLAGS) -> None:
        super().__init__(db_loaded=True)
        self.failure = failure
        self.actual_sort = actual_sort
        self.actual_flags = actual_flags
        self.reset_calls = 0
        self.path_calls = 0
        self.size_calls = 0
        self.date_calls = 0
        self.attribute_calls = 0

    def Everything_SetSearchW(self, query: str) -> None:
        self.query = query

    def Everything_SetRequestFlags(self, flags: int) -> None:
        self.request_flags = flags

    def Everything_SetSort(self, sort: int) -> None:
        self.requested_sort = sort

    def Everything_SetMax(self, limit: int) -> None:
        self.limit = limit

    def Everything_QueryW(self, wait: bool) -> bool:
        return self.failure != "query"

    def Everything_GetTotResults(self) -> int | BadInt:
        return BadInt() if self.failure == "copy" else 7

    def Everything_GetNumResults(self) -> int:
        return 1

    def Everything_GetResultListSort(self) -> int:
        return self.actual_sort

    def Everything_GetResultListRequestFlags(self) -> int:
        return self.actual_flags

    def Everything_GetResultFullPathNameW(self, index: int, buffer: Any, size: int) -> int:
        self.path_calls += 1
        if self.failure == "copy":
            raise ValueError("result copy failed")
        buffer.value = r"C:\Work\everything_mcp\sample.py"
        return len(buffer.value)

    def Everything_GetResultSize(self, index: int, value_pointer: Any) -> bool:
        self.size_calls += 1
        value_pointer._obj.value = 123
        return True

    def Everything_GetResultDateModified(self, index: int, value_pointer: Any) -> bool:
        self.date_calls += 1
        value_pointer._obj.value = 133801632000000000
        return True

    def Everything_GetResultAttributes(self, index: int) -> int:
        self.attribute_calls += 1
        return 0x20

    def Everything_Reset(self) -> None:
        self.reset_calls += 1


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
        with pytest.raises(ValueError):
            getattr(adapter, operation)("ext:py", scope=r"C:\Work")
    else:
        result = getattr(adapter, operation)("ext:py", scope=r"C:\Work")
        if operation == "count":
            assert result == 7
        else:
            assert result == [SearchHit(path=r"C:\Work\everything_mcp\sample.py")]

    assert dll.reset_calls == 1


def test_sdk_diagnostics_cross_adapter_boundary_and_omit_unavailable_metadata() -> None:
    actual_flags = sdk_ipc.REQUEST_FULL_PATH | sdk_ipc.REQUEST_SIZE
    dll = QueryDll(actual_sort=sdk_ipc.EVERYTHING_SORT_NAME_ASCENDING, actual_flags=actual_flags)
    adapter = _adapter(dll)

    result = adapter.search("ext:py", scope=r"C:\Work", sort="date_modified", metadata=True)

    assert isinstance(result, SearchBatch)
    assert result.hits == [SearchHit(path=r"C:\Work\everything_mcp\sample.py", size=123)]
    assert len(result.notes) == 1
    assert "sort" in result.notes[0]
    assert "dateModified" in result.notes[0]
    assert "attributes" in result.notes[0]
    assert dll.date_calls == 0
    assert dll.attribute_calls == 0


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


def test_sdk_per_query_code_has_no_cleanup_reference() -> None:
    assert "Everything_CleanUp" not in inspect.getsource(sdk_ipc.SdkIpcAdapter.count)
    assert "Everything_CleanUp" not in inspect.getsource(sdk_ipc.SdkIpcAdapter.search)
