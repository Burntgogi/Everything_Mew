from __future__ import annotations

import ctypes
import gc
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

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
            "Everything_SetReplyWindow",
            "Everything_SetReplyID",
            "Everything_QueryW",
            "Everything_IsQueryReply",
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
        self.path_size_queries = 0
        self.path_copy_sizes: list[int] = []
        self.size_calls = 0
        self.date_calls = 0
        self.attribute_calls = 0
        self.call_order: list[str] = []
        self.query_wait_values: list[bool] = []
        self.reply_windows: list[int] = []
        self.reply_ids: list[int] = []

    def Everything_SetSearchW(self, query: str) -> None:
        self.call_order.append("set-search")
        if self.failure == "setup":
            raise OSError(r"C:\private\setup-secret.txt")
        self.query = query

    def Everything_SetRequestFlags(self, flags: int) -> None:
        self.call_order.append("set-request-flags")
        self.request_flags = flags

    def Everything_SetSort(self, sort: int) -> None:
        self.requested_sort = sort

    def Everything_SetMax(self, limit: int) -> None:
        self.call_order.append("set-max")
        self.limit = limit

    def Everything_SetReplyWindow(self, window: int) -> None:
        self.reply_windows.append(window)

    def Everything_SetReplyID(self, reply_id: int) -> None:
        self.reply_ids.append(reply_id)

    def Everything_QueryW(self, wait: bool) -> bool:
        self.call_order.append("query")
        self.query_wait_values.append(wait)
        return self.failure != "query"

    def Everything_IsQueryReply(self, message: int, w_param: int, l_param: int, reply_id: int) -> bool:
        return False

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
        path = self.paths[index]
        if buffer is None:
            self.path_size_queries += 1
            return len(path)
        self.path_copy_sizes.append(size)
        copied = min(len(path), max(0, size - 1))
        buffer.value = path[:copied]
        return copied

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
    assert dll.Everything_SetReplyWindow.argtypes == [ctypes.wintypes.HWND]
    assert dll.Everything_SetReplyID.argtypes == [ctypes.wintypes.DWORD]
    assert dll.Everything_IsQueryReply.argtypes == [
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
        ctypes.wintypes.DWORD,
    ]
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
            assert result.hits == (SearchHit(path=r"C:\Work\everything_mcp\sample.py"),)
            assert result.notes == ()

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

    assert result.hits == (SearchHit(path=r"C:\Work\everything_mcp\sample.py"),)
    assert result.notes == ()


def test_zero_or_failed_full_path_copy_never_publishes_empty_path() -> None:
    dll = QueryDll(failure="empty-path", actual_flags=0)
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="full path"):
        adapter.search("ext:py", scope=r"C:\Work")

    assert dll.reset_calls == 1


def test_full_path_uses_required_length_query_and_accepts_exact_windows_limit() -> None:
    path = "C:\\" + "a" * 32764
    dll = QueryDll(paths=(path,))
    adapter = _adapter(dll)

    assert adapter._result_full_path(0) == path
    assert dll.path_size_queries == 1
    assert dll.path_copy_sizes == [32768]


def test_full_path_rejects_above_windows_limit_before_allocating_copy_buffer() -> None:
    dll = QueryDll(paths=("C:\\" + "a" * 32765,))
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="32767"):
        adapter._result_full_path(0)

    assert dll.path_size_queries == 1
    assert dll.path_copy_sizes == []


def test_full_path_rejects_copy_length_that_differs_from_required_length() -> None:
    class ShortCopyDll(QueryDll):
        def Everything_GetResultFullPathNameW(self, index: int, buffer: Any, size: int) -> int:
            required = super().Everything_GetResultFullPathNameW(index, None, 0)
            if buffer is None:
                return required
            buffer.value = self.paths[index][:-1]
            self.path_copy_sizes.append(size)
            return required - 1

    dll = ShortCopyDll()
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="changed while copying"):
        adapter._result_full_path(0)


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

    assert result.notes == ()
    assert result.hits[0].to_metadata_result() == {
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


class FakeReplyWindow:
    def __init__(self, *, reply: bool, hwnd: int = 1234) -> None:
        self.hwnd = hwnd
        self.reply = reply
        self.wait_calls: list[float] = []
        self.close_calls = 0

    def wait_for_reply(self, timeout_seconds: float) -> bool:
        self.wait_calls.append(timeout_seconds)
        return self.reply

    def close(self) -> None:
        self.close_calls += 1


@pytest.fixture(autouse=True)
def successful_fake_win32_reply(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(
        sdk_ipc,
        "_create_query_reply_window",
        lambda *_: FakeReplyWindow(reply=True),
    )


def test_sdk_query_uses_async_reply_window_and_bounded_wait(monkeypatch: MonkeyPatch) -> None:
    dll = QueryDll()
    window = FakeReplyWindow(reply=True)
    created: list[tuple[object, int]] = []

    def create_window(received_dll: object, reply_id: int) -> FakeReplyWindow:
        created.append((received_dll, reply_id))
        return window

    monkeypatch.setattr(sdk_ipc, "_create_query_reply_window", create_window)
    adapter = _adapter(dll)

    assert adapter.count("ext:py", scope=r"C:\Work") == 7
    assert dll.query_wait_values == [False]
    assert dll.reply_windows == [window.hwnd, 0]
    assert dll.reply_ids == [created[0][1]]
    assert created[0][0] is dll
    assert created[0][1] > 0
    assert window.wait_calls == [sdk_ipc.SDK_QUERY_TIMEOUT_SECONDS]
    assert window.close_calls == 1
    assert dll.reset_calls == 1


def test_sdk_count_requests_zero_fields_and_zero_visible_results() -> None:
    dll = QueryDll()
    adapter = _adapter(dll)

    assert adapter.count("ext:py", scope=r"C:\Work") == 7

    assert dll.request_flags == 0
    assert dll.limit == 0
    assert dll.call_order[:4] == ["set-search", "set-request-flags", "set-max", "query"]


def test_sdk_timeout_resets_and_late_reply_cannot_poison_retry(monkeypatch: MonkeyPatch) -> None:
    dll = QueryDll()
    windows = [FakeReplyWindow(reply=False, hwnd=1001), FakeReplyWindow(reply=True, hwnd=1002)]

    def create_window(received_dll: object, reply_id: int) -> FakeReplyWindow:
        assert received_dll is dll
        assert reply_id > 0
        return windows.pop(0)

    monkeypatch.setattr(sdk_ipc, "_create_query_reply_window", create_window)
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="timed out"):
        adapter.count("ext:py", scope=r"C:\Work")

    assert adapter.count("ext:py", scope=r"C:\Work") == 7
    assert dll.reply_ids[0] != dll.reply_ids[1]
    assert dll.reply_windows == [1001, 0, 1002, 0]
    assert dll.reset_calls == 2


def test_sdk_reply_ids_are_unique_across_adapter_instances_and_reused_hwnd(monkeypatch: MonkeyPatch) -> None:
    dll = QueryDll()
    windows = [FakeReplyWindow(reply=False, hwnd=777), FakeReplyWindow(reply=True, hwnd=777)]
    created_ids: list[int] = []

    def create_window(received_dll: object, reply_id: int) -> FakeReplyWindow:
        assert received_dll is dll
        created_ids.append(reply_id)
        return windows.pop(0)

    monkeypatch.setattr(sdk_ipc, "_create_query_reply_window", create_window)
    first_adapter = _adapter(dll)
    second_adapter = _adapter(dll)

    with pytest.raises(QueryError, match="timed out"):
        first_adapter.count("ext:py", scope=r"C:\Work")
    assert second_adapter.count("ext:py", scope=r"C:\Work") == 7

    assert created_ids[0] != created_ids[1]
    assert dll.reply_windows == [777, 0, 777, 0]
    assert dll.reply_ids == created_ids


def test_sdk_reply_identifier_exhaustion_never_reuses_an_old_id(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(sdk_ipc, "_REPLY_ID_COUNTER", sdk_ipc._MAX_REPLY_ID)

    with pytest.raises(QueryError, match="identifiers are exhausted"):
        _adapter(QueryDll())._next_reply_identifier()

    assert sdk_ipc._REPLY_ID_COUNTER == sdk_ipc._MAX_REPLY_ID


def test_reused_hwnd_rejects_late_reply_for_previous_identifier() -> None:
    class ReplyDll:
        def Everything_IsQueryReply(self, message: int, w_param: int, l_param: int, reply_id: int) -> bool:
            return l_param == reply_id

    class DefaultProc:
        def DefWindowProcW(self, hwnd: int, message: int, w_param: int, l_param: int) -> int:
            return 0

    window = sdk_ipc._Win32QueryReplyWindow.__new__(sdk_ipc._Win32QueryReplyWindow)
    raw_window = cast(Any, window)
    raw_window._dll = ReplyDll()
    raw_window._reply_id = 2
    raw_window._received = False
    raw_window._callback_failed = False
    raw_window._user32 = DefaultProc()

    assert window._handle_message(777, sdk_ipc.WM_COPYDATA, 0, 1) == 0
    assert window._received is False
    assert window._handle_message(777, sdk_ipc.WM_COPYDATA, 0, 2) == 1
    assert window._received is True


def test_sdk_reply_window_is_closed_when_async_query_post_fails(monkeypatch: MonkeyPatch) -> None:
    dll = QueryDll(failure="query")
    window = FakeReplyWindow(reply=True)
    monkeypatch.setattr(sdk_ipc, "_create_query_reply_window", lambda *_: window)
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="error 0"):
        adapter.count("ext:py", scope=r"C:\Work")

    assert window.wait_calls == []
    assert window.close_calls == 1
    assert dll.reply_windows == [window.hwnd, 0]
    assert dll.reset_calls == 1


class CleanupUser32:
    def __init__(self, *, destroy_results: list[bool], unregister_results: list[bool]) -> None:
        self.destroy_results = destroy_results
        self.unregister_results = unregister_results
        self.destroy_calls = 0
        self.unregister_calls = 0

    def DestroyWindow(self, hwnd: int) -> bool:
        self.destroy_calls += 1
        return self.destroy_results.pop(0)

    def UnregisterClassW(self, class_name: str, instance: int) -> bool:
        self.unregister_calls += 1
        return self.unregister_results.pop(0)


def _cleanup_window(user32: CleanupUser32) -> sdk_ipc._Win32QueryReplyWindow:
    window = sdk_ipc._Win32QueryReplyWindow.__new__(sdk_ipc._Win32QueryReplyWindow)
    raw_window = cast(Any, window)
    raw_window._user32 = user32
    raw_window.hwnd = 4321
    raw_window._class_name = "EverythingMewTestReply"
    raw_window._instance = 1
    raw_window._closed = False
    raw_window._window_destroyed = False
    raw_window._class_unregistered = False
    raw_window._window_proc = object()
    return window


def test_reply_window_destroy_failure_is_retryable_and_not_marked_closed() -> None:
    user32 = CleanupUser32(destroy_results=[False, True], unregister_results=[True])
    window = _cleanup_window(user32)

    with pytest.raises(OSError, match="cleanup failed"):
        window.close()

    assert window._closed is False
    assert window._window_destroyed is False
    assert window._class_unregistered is False

    window.close()

    assert window._closed is True
    assert user32.destroy_calls == 2
    assert user32.unregister_calls == 1


def test_reply_window_unregister_failure_retries_without_redestroying_window() -> None:
    user32 = CleanupUser32(destroy_results=[True], unregister_results=[False, True])
    window = _cleanup_window(user32)

    with pytest.raises(OSError, match="cleanup failed"):
        window.close()

    assert window._closed is False
    assert window._window_destroyed is True
    assert window._class_unregistered is False

    window.close()

    assert window._closed is True
    assert user32.destroy_calls == 1
    assert user32.unregister_calls == 2


def test_primary_query_failure_surfaces_cleanup_failure_with_cause(monkeypatch: MonkeyPatch) -> None:
    class FailingCloseWindow(FakeReplyWindow):
        def close(self) -> None:
            self.close_calls += 1
            raise OSError("injected cleanup failure")

    dll = QueryDll(failure="query")
    window = FailingCloseWindow(reply=True)
    monkeypatch.setattr(sdk_ipc, "_create_query_reply_window", lambda *_: window)
    adapter = _adapter(dll)

    with pytest.raises(QueryError) as caught:
        adapter.count("ext:py", scope=r"C:\Work")

    assert "query failed with error 0" in str(caught.value)
    assert "cleanup also failed" in str(caught.value)
    assert isinstance(caught.value.__cause__, QueryError)
    assert window.close_calls == 1


def test_failed_cleanup_quarantines_live_callback_until_retry_succeeds(monkeypatch: MonkeyPatch) -> None:
    class Callback:
        pass

    class RetryableCloseWindow(FakeReplyWindow):
        def __init__(self) -> None:
            super().__init__(reply=True)
            self.fail_cleanup = True
            self._window_proc = Callback()

        def close(self) -> None:
            self.close_calls += 1
            if self.fail_cleanup:
                raise OSError("injected cleanup failure")

    sdk_ipc._FAILED_REPLY_WINDOWS.clear()
    dll = QueryDll()
    window = RetryableCloseWindow()
    callback_ref = weakref.ref(window._window_proc)
    window_ref = weakref.ref(window)
    holder = [window]
    monkeypatch.setattr(sdk_ipc, "_create_query_reply_window", lambda *_: holder.pop())
    adapter = _adapter(dll)

    with pytest.raises(QueryError, match="cleanup failed"):
        adapter.count("ext:py", scope=r"C:\Work")

    del window
    gc.collect()
    assert window_ref() is not None
    assert callback_ref() is not None

    retained = window_ref()
    assert retained is not None
    retained.fail_cleanup = False
    sdk_ipc._retry_quarantined_reply_windows()
    del retained
    gc.collect()

    assert sdk_ipc._FAILED_REPLY_WINDOWS == set()
    assert window_ref() is None
    assert callback_ref() is None


def test_sdk_serializes_shared_query_and_reset_state(monkeypatch: MonkeyPatch) -> None:
    dll = QueryDll()
    first_waiting = threading.Event()
    release_first = threading.Event()
    factory_calls: list[int] = []

    class BlockingReplyWindow(FakeReplyWindow):
        def __init__(self, call_number: int) -> None:
            super().__init__(reply=True, hwnd=2000 + call_number)
            self.call_number = call_number

        def wait_for_reply(self, timeout_seconds: float) -> bool:
            if self.call_number == 1:
                first_waiting.set()
                assert release_first.wait(timeout=1)
            return super().wait_for_reply(timeout_seconds)

    def create_window(received_dll: object, reply_id: int) -> BlockingReplyWindow:
        assert received_dll is dll
        factory_calls.append(reply_id)
        return BlockingReplyWindow(len(factory_calls))

    monkeypatch.setattr(sdk_ipc, "_create_query_reply_window", create_window)
    first_adapter = _adapter(dll)
    second_adapter = _adapter(dll)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_adapter.count, "ext:py", r"C:\Work")
        assert first_waiting.wait(timeout=1)
        second = executor.submit(second_adapter.count, "ext:txt", r"C:\Work")
        time.sleep(0.05)
        assert len(factory_calls) == 1
        release_first.set()
        assert first.result(timeout=1) == 7
        assert second.result(timeout=1) == 7

    assert len(factory_calls) == 2
    assert dll.reset_calls == 2
