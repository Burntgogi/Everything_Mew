"""ctypes-based Everything SDK adapter.

The adapter is intentionally conservative: missing DLLs, bitness mismatches,
or a stopped Everything runtime become status notes instead of import/startup
crashes.
"""

from __future__ import annotations

import ctypes
import math
import os
import platform
import threading
import time
from ctypes import wintypes
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, TypeVar

from everything_mcp.config import EverythingConfig
from everything_mcp.contracts import AdapterStatus, SearchBatch, SearchHit, SortName, TargetMachineName
from everything_mcp.errors import BackendUnavailableError, QueryError
from everything_mcp.query import compose_query

EVERYTHING_SORT_NAME_ASCENDING = 1
EVERYTHING_SORT_PATH_ASCENDING = 3
EVERYTHING_SORT_SIZE_ASCENDING = 5
EVERYTHING_SORT_DATE_MODIFIED_ASCENDING = 13
SORT_FLAGS: dict[str, int] = {
    "name": EVERYTHING_SORT_NAME_ASCENDING,
    "path": EVERYTHING_SORT_PATH_ASCENDING,
    "size": EVERYTHING_SORT_SIZE_ASCENDING,
    "date_modified": EVERYTHING_SORT_DATE_MODIFIED_ASCENDING,
}
REQUEST_FILE_NAME = 0x00000001
REQUEST_PATH = 0x00000002
REQUEST_FULL_PATH = 0x00000004
REQUEST_SIZE = 0x00000010
REQUEST_DATE_MODIFIED = 0x00000040
REQUEST_ATTRIBUTES = 0x00000100
METADATA_FLAGS = REQUEST_FULL_PATH | REQUEST_SIZE | REQUEST_DATE_MODIFIED | REQUEST_ATTRIBUTES
PATH_ONLY_FLAGS = REQUEST_FULL_PATH
UNKNOWN_FILETIME_VALUES = {0, 0xFFFFFFFFFFFFFFFF}
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
MAX_WINDOWS_PATH_CHARACTERS = 32767
SDK_QUERY_TIMEOUT_SECONDS = 15.0
WM_COPYDATA = 0x004A
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
HWND_MESSAGE = -3
MSGFLT_ALLOW = 1
TARGET_MACHINES: dict[int, TargetMachineName] = {1: "x86", 2: "x64", 3: "ARM", 4: "ARM64"}
REQUEST_FLAG_NAMES = (
    (REQUEST_FULL_PATH, "fullPath"),
    (REQUEST_SIZE, "size"),
    (REQUEST_DATE_MODIFIED, "dateModified"),
    (REQUEST_ATTRIBUTES, "attributes"),
)
T = TypeVar("T")


_WNDPROC = ctypes.WINFUNCTYPE(
    ctypes.c_ssize_t,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class _WNDCLASSW(ctypes.Structure):
    _fields_ = (
        ("style", wintypes.UINT),
        ("lpfnWndProc", _WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HANDLE),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HANDLE),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    )


class _QueryReplyWindow(Protocol):
    hwnd: int

    def wait_for_reply(self, timeout_seconds: float) -> bool: ...

    def close(self) -> None: ...


_MAX_REPLY_ID = 0xFFFFFFFF
_REPLY_ID_LOCK = threading.Lock()
_REPLY_ID_COUNTER = 0
_SDK_OPERATION_LOCK = threading.RLock()
_FAILED_REPLY_WINDOWS_LOCK = threading.Lock()
_FAILED_REPLY_WINDOWS: set[Any] = set()


def _next_process_reply_identifier() -> int:
    global _REPLY_ID_COUNTER
    with _REPLY_ID_LOCK:
        if _REPLY_ID_COUNTER >= _MAX_REPLY_ID:
            raise QueryError("Everything SDK reply identifiers are exhausted; restart the MCP process before retrying.")
        _REPLY_ID_COUNTER += 1
        return _REPLY_ID_COUNTER


def _quarantine_reply_window(window: _QueryReplyWindow) -> None:
    with _FAILED_REPLY_WINDOWS_LOCK:
        _FAILED_REPLY_WINDOWS.add(window)


def _release_reply_window(window: _QueryReplyWindow) -> None:
    with _FAILED_REPLY_WINDOWS_LOCK:
        _FAILED_REPLY_WINDOWS.discard(window)


def _retry_quarantined_reply_windows() -> None:
    with _FAILED_REPLY_WINDOWS_LOCK:
        retained = tuple(_FAILED_REPLY_WINDOWS)
    for window in retained:
        try:
            window.close()
        except BaseException:
            continue
        _release_reply_window(window)


class _Win32QueryReplyWindow:
    def __init__(self, dll: Any, reply_id: int) -> None:
        self._dll = dll
        self._reply_id = reply_id
        self._received = False
        self._callback_failed = False
        self._closed = False
        self._window_destroyed = True
        self._class_unregistered = True
        self.hwnd = 0
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._configure_win32()
        self._window_proc = _WNDPROC(self._handle_message)
        self._instance = self._kernel32.GetModuleHandleW(None)
        self._class_name = (
            f"EverythingMewReply_{os.getpid()}_{threading.get_ident()}_{reply_id}_{id(self)}"
        )
        window_class = _WNDCLASSW(
            0,
            self._window_proc,
            0,
            0,
            self._instance,
            None,
            None,
            None,
            None,
            self._class_name,
        )
        self._atom = int(self._user32.RegisterClassW(ctypes.byref(window_class)))
        if self._atom == 0:
            raise OSError("Could not register the Everything SDK reply window class.")
        self._class_unregistered = False
        hwnd = self._user32.CreateWindowExW(
            0,
            self._class_name,
            self._class_name,
            0,
            0,
            0,
            0,
            0,
            wintypes.HWND(HWND_MESSAGE),
            None,
            self._instance,
            None,
        )
        if not hwnd:
            if self._user32.UnregisterClassW(self._class_name, self._instance):
                self._class_unregistered = True
                self._closed = True
            else:
                _quarantine_reply_window(self)
            raise OSError("Could not create the Everything SDK reply window.")
        self.hwnd = int(hwnd)
        self._window_destroyed = False
        # Like the SDK's own reply window, let a lower-integrity Everything reply to an elevated
        # caller, but only when Everything really runs lower; otherwise keep UIPI closed.
        from .native_ipc import everything_needs_uipi_exception

        if everything_needs_uipi_exception():
            self._user32.ChangeWindowMessageFilterEx(self.hwnd, WM_COPYDATA, MSGFLT_ALLOW, None)

    def _configure_win32(self) -> None:
        self._kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self._user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
        self._user32.RegisterClassW.restype = wintypes.ATOM
        self._user32.CreateWindowExW.argtypes = [
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            wintypes.LPVOID,
        ]
        self._user32.CreateWindowExW.restype = wintypes.HWND
        self._user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self._user32.DefWindowProcW.restype = ctypes.c_ssize_t
        self._user32.MsgWaitForMultipleObjectsEx.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
        self._user32.PeekMessageW.argtypes = [
            ctypes.POINTER(wintypes.MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
            wintypes.UINT,
        ]
        self._user32.PeekMessageW.restype = wintypes.BOOL
        self._user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.TranslateMessage.restype = wintypes.BOOL
        self._user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
        self._user32.DispatchMessageW.restype = ctypes.c_ssize_t
        self._user32.DestroyWindow.argtypes = [wintypes.HWND]
        self._user32.DestroyWindow.restype = wintypes.BOOL
        self._user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
        self._user32.UnregisterClassW.restype = wintypes.BOOL
        self._user32.ChangeWindowMessageFilterEx.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.DWORD, wintypes.LPVOID]
        self._user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL

    def _handle_message(self, hwnd: int, message: int, w_param: int, l_param: int) -> int:
        if message == WM_COPYDATA:
            try:
                if self._dll.Everything_IsQueryReply(message, w_param, l_param, self._reply_id):
                    self._received = True
                    return 1
            except BaseException:
                self._callback_failed = True
                return 0
        return int(self._user32.DefWindowProcW(hwnd, message, w_param, l_param))

    def wait_for_reply(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        message = wintypes.MSG()
        while not self._received:
            if self._callback_failed:
                raise OSError("Everything SDK reply processing failed.")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            wait_milliseconds = min(max(1, math.ceil(remaining * 1000)), 0xFFFFFFFE)
            wait_result = int(
                self._user32.MsgWaitForMultipleObjectsEx(
                    0,
                    None,
                    wait_milliseconds,
                    QS_ALLINPUT,
                    MWMO_INPUTAVAILABLE,
                )
            )
            if wait_result == WAIT_TIMEOUT:
                return False
            if wait_result == WAIT_FAILED:
                raise OSError("Everything SDK reply wait failed.")
            while self._user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
                self._user32.TranslateMessage(ctypes.byref(message))
                self._user32.DispatchMessageW(ctypes.byref(message))
        return True

    def close(self) -> None:
        if self._closed:
            return
        if not self._window_destroyed:
            if not self._user32.DestroyWindow(self.hwnd):
                _quarantine_reply_window(self)
                raise OSError("Everything SDK reply window cleanup failed.")
            self._window_destroyed = True
        if not self._class_unregistered:
            if not self._user32.UnregisterClassW(self._class_name, self._instance):
                _quarantine_reply_window(self)
                raise OSError("Everything SDK reply window cleanup failed.")
            self._class_unregistered = True
        self._closed = self._window_destroyed and self._class_unregistered
        if not self._closed:
            _quarantine_reply_window(self)
            raise OSError("Everything SDK reply window cleanup failed.")
        _release_reply_window(self)


def _create_query_reply_window(dll: Any, reply_id: int) -> _QueryReplyWindow:
    return _Win32QueryReplyWindow(dll, reply_id)


class SdkIpcAdapter:
    name = "sdk-ipc"

    def __init__(self, config: EverythingConfig | None = None) -> None:
        self.config = config or EverythingConfig.from_env()
        self._dll: Any | None = None
        self._load_error: str | None = None
        self._load_dll()

    def _candidate_dll(self) -> Path | None:
        if self.config.sdk_dll is not None:
            return self.config.sdk_dll
        dll_name = "Everything64.dll" if platform.architecture()[0] == "64bit" else "Everything32.dll"
        default = self.config.everything_exe.parent / dll_name
        return default if default.exists() else None

    def _load_dll(self) -> None:
        dll_path = self._candidate_dll()
        if dll_path is None:
            self._load_error = "Everything SDK DLL was not found; configure EVERYTHING_SDK_DLL with matching 64/32-bit DLL."
            return
        try:
            self._dll = ctypes.WinDLL(str(dll_path))
            self._configure_functions()
        except OSError:
            self._dll = None
            self._load_error = (
                "Could not load the configured Everything SDK DLL; verify the configured path, process bitness, "
                "and DLL dependencies."
            )
        except AttributeError:
            self._dll = None
            self._load_error = (
                "Everything SDK DLL loading or required function lookup failed; verify the SDK DLL version and "
                "process bitness."
            )

    def _configure_functions(self) -> None:
        if self._dll is None:
            return
        self._dll.Everything_IsDBLoaded.argtypes = []
        self._dll.Everything_IsDBLoaded.restype = wintypes.BOOL
        self._dll.Everything_GetMajorVersion.argtypes = []
        self._dll.Everything_GetMajorVersion.restype = wintypes.DWORD
        self._dll.Everything_GetMinorVersion.argtypes = []
        self._dll.Everything_GetMinorVersion.restype = wintypes.DWORD
        self._dll.Everything_GetRevision.argtypes = []
        self._dll.Everything_GetRevision.restype = wintypes.DWORD
        self._dll.Everything_GetBuildNumber.argtypes = []
        self._dll.Everything_GetBuildNumber.restype = wintypes.DWORD
        self._dll.Everything_GetTargetMachine.argtypes = []
        self._dll.Everything_GetTargetMachine.restype = wintypes.DWORD
        self._dll.Everything_GetResultListSort.argtypes = []
        self._dll.Everything_GetResultListSort.restype = wintypes.DWORD
        self._dll.Everything_GetResultListRequestFlags.argtypes = []
        self._dll.Everything_GetResultListRequestFlags.restype = wintypes.DWORD
        self._dll.Everything_Reset.argtypes = []
        self._dll.Everything_Reset.restype = None
        self._dll.Everything_SetSearchW.argtypes = [wintypes.LPCWSTR]
        self._dll.Everything_SetSearchW.restype = None
        self._dll.Everything_SetRequestFlags.argtypes = [wintypes.DWORD]
        self._dll.Everything_SetRequestFlags.restype = None
        self._dll.Everything_SetSort.argtypes = [wintypes.DWORD]
        self._dll.Everything_SetSort.restype = None
        self._dll.Everything_SetMax.argtypes = [wintypes.DWORD]
        self._dll.Everything_SetMax.restype = None
        self._dll.Everything_SetReplyWindow.argtypes = [wintypes.HWND]
        self._dll.Everything_SetReplyWindow.restype = None
        self._dll.Everything_SetReplyID.argtypes = [wintypes.DWORD]
        self._dll.Everything_SetReplyID.restype = None
        self._dll.Everything_QueryW.argtypes = [wintypes.BOOL]
        self._dll.Everything_QueryW.restype = wintypes.BOOL
        self._dll.Everything_IsQueryReply.argtypes = [
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.DWORD,
        ]
        self._dll.Everything_IsQueryReply.restype = wintypes.BOOL
        self._dll.Everything_GetTotResults.argtypes = []
        self._dll.Everything_GetTotResults.restype = wintypes.DWORD
        self._dll.Everything_GetNumResults.argtypes = []
        self._dll.Everything_GetNumResults.restype = wintypes.DWORD
        self._dll.Everything_GetLastError.argtypes = []
        self._dll.Everything_GetLastError.restype = wintypes.DWORD
        self._dll.Everything_GetResultFullPathNameW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD]
        self._dll.Everything_GetResultFullPathNameW.restype = wintypes.DWORD
        self._dll.Everything_GetResultSize.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.LARGE_INTEGER)]
        self._dll.Everything_GetResultSize.restype = wintypes.BOOL
        self._dll.Everything_GetResultDateModified.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.FILETIME)]
        self._dll.Everything_GetResultDateModified.restype = wintypes.BOOL
        self._dll.Everything_GetResultAttributes.argtypes = [wintypes.DWORD]
        self._dll.Everything_GetResultAttributes.restype = wintypes.DWORD

    def status(self) -> AdapterStatus:
        with self._lock():
            return self._status_unlocked()

    def _status_unlocked(self) -> AdapterStatus:
        notes: list[str] = []
        everything_installed = self.config.everything_exe.exists()
        if not everything_installed:
            notes.append(f"Everything.exe was not found at {self.config.everything_exe}.")
        if self._load_error:
            notes.append(self._load_error)
        if self._dll is None:
            return AdapterStatus(everything_installed, False, "none", False, notes=tuple(notes))

        try:
            db_loaded = bool(self._dll.Everything_IsDBLoaded())
            last_error = int(self._dll.Everything_GetLastError()) if not db_loaded else 0
        except (AttributeError, OSError, TypeError, ValueError):
            notes.append("Everything SDK loaded but the IPC readiness check failed; restart Everything and retry.")
            return AdapterStatus(everything_installed, False, "none", False, notes=tuple(notes))

        if not db_loaded and last_error != 0:
            notes.append(f"Everything SDK IPC readiness check failed with error {last_error}; start Everything and retry.")
            return AdapterStatus(
                everything_installed,
                False,
                "none",
                False,
                db_loaded=False,
                notes=tuple(notes),
            )

        version = self._version()
        target_machine = self._target_machine()
        if not db_loaded:
            notes.append("Everything is running but its database is still loading; retry when dbLoaded is true.")
        return AdapterStatus(
            everything_installed,
            True,
            "sdk-ipc",
            False,
            db_loaded=db_loaded,
            version=version,
            target_machine=target_machine,
            notes=tuple(notes),
        )

    def count(self, query: str, scope: str | None = None) -> int:
        self._ensure_ready()
        dll = self._ready_dll()

        def operation() -> int:
            self._set_query(query, scope)
            dll.Everything_SetRequestFlags(0)
            dll.Everything_SetMax(0)
            self._query()
            return int(dll.Everything_GetTotResults())

        return self._run_with_reset("count", operation)

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> SearchBatch:
        self._ensure_ready()
        dll = self._ready_dll()
        requested_sort = _sort_flag(sort)
        requested_flags = METADATA_FLAGS if metadata else PATH_ONLY_FLAGS

        def operation() -> SearchBatch:
            self._set_query(query, scope)
            dll.Everything_SetSort(requested_sort)
            dll.Everything_SetRequestFlags(requested_flags)
            dll.Everything_SetMax(limit)
            self._query()
            actual_sort = int(dll.Everything_GetResultListSort())
            actual_flags = int(dll.Everything_GetResultListRequestFlags())
            num_results = int(dll.Everything_GetNumResults())
            unavailable_fields = _missing_requested_fields(requested_flags, actual_flags)
            hits: list[SearchHit] = []
            for index in range(num_results):
                hit, unavailable = self._result_hit(index, metadata, actual_flags)
                hits.append(hit)
                unavailable_fields.update(unavailable)
            note = _result_diagnostic_note(requested_sort, actual_sort, unavailable_fields)
            total = int(dll.Everything_GetTotResults())
            return SearchBatch(hits=tuple(hits), notes=(note,) if note is not None else (), total_count=total)

        return self._run_with_reset("search", operation)

    def _run_with_reset(self, operation_name: str, operation: Callable[[], T]) -> T:
        with self._lock():
            dll = self._ready_dll()
            try:
                try:
                    result = operation()
                except QueryError:
                    raise
                except (AttributeError, OSError, TypeError, ValueError, OverflowError, ctypes.ArgumentError):
                    raise QueryError(
                        f"Everything SDK {operation_name} operation failed; verify the Everything runtime and retry."
                    ) from None
            except BaseException:
                try:
                    dll.Everything_Reset()
                except BaseException:
                    # Reset must never replace the operation's primary failure.
                    pass
                raise

            try:
                dll.Everything_Reset()
            except Exception:
                raise QueryError(
                    f"Everything SDK reset failed after a successful {operation_name} operation; restart Everything and retry."
                ) from None
            return result

    def _lock(self) -> threading.RLock:
        return _SDK_OPERATION_LOCK

    def _ensure_ready(self) -> None:
        status = self.status()
        if status.backend != "sdk-ipc" or not status.everything_running:
            raise BackendUnavailableError("Everything SDK/IPC is unavailable: " + "; ".join(status.notes))
        if status.db_loaded is not True:
            raise BackendUnavailableError("Everything SDK database is still loading; retry when dbLoaded is true.")

    def _ready_dll(self) -> Any:
        if self._dll is None:
            raise BackendUnavailableError("Everything SDK/IPC is unavailable: DLL is not loaded.")
        return self._dll

    def _set_query(self, query: str, scope: str | None) -> None:
        full_query = compose_query(query, scope)
        self._ready_dll().Everything_SetSearchW(full_query)

    def _query(self) -> None:
        dll = self._ready_dll()
        _retry_quarantined_reply_windows()
        reply_id = self._next_reply_identifier()
        reply_window = _create_query_reply_window(dll, reply_id)
        primary_failure: BaseException | None = None
        primary_traceback: Any | None = None
        try:
            dll.Everything_SetReplyWindow(reply_window.hwnd)
            dll.Everything_SetReplyID(reply_id)
            ok = dll.Everything_QueryW(False)
            if not ok:
                error_code = int(dll.Everything_GetLastError())
                raise QueryError(
                    f"Everything SDK query failed with error {error_code}; check syntax or call everything_syntax_help."
                )
            if not reply_window.wait_for_reply(SDK_QUERY_TIMEOUT_SECONDS):
                raise QueryError(
                    f"Everything SDK query timed out after {SDK_QUERY_TIMEOUT_SECONDS:g} seconds; refine the query or restart Everything."
                )
        except BaseException as exc:
            primary_failure = exc
            primary_traceback = exc.__traceback__

        cleanup_failures: list[BaseException] = []
        try:
            dll.Everything_SetReplyWindow(0)
        except BaseException as exc:
            cleanup_failures.append(exc)
        try:
            reply_window.close()
        except BaseException as exc:
            cleanup_failures.append(exc)
            _quarantine_reply_window(reply_window)

        if primary_failure is not None:
            if cleanup_failures and isinstance(primary_failure, Exception):
                raise QueryError(
                    f"{primary_failure} Everything SDK reply window cleanup also failed; "
                    "the callback was retained for a later cleanup retry."
                ) from primary_failure
            if cleanup_failures:
                primary_failure.add_note(
                    "Everything SDK reply window cleanup also failed; the callback was retained for retry."
                )
            raise primary_failure.with_traceback(primary_traceback)
        if cleanup_failures:
            raise QueryError(
                "Everything SDK reply window cleanup failed; the callback was retained for a later cleanup retry."
            )

    def _next_reply_identifier(self) -> int:
        return _next_process_reply_identifier()

    def _result_full_path(self, index: int) -> str:
        dll = self._ready_dll()
        required = int(dll.Everything_GetResultFullPathNameW(index, None, 0))
        if required <= 0:
            raise QueryError(
                f"Everything SDK did not return a full path for result {index}; retry the query or restart Everything."
            )
        if required > MAX_WINDOWS_PATH_CHARACTERS:
            raise QueryError(
                f"Everything SDK returned a path longer than the {MAX_WINDOWS_PATH_CHARACTERS} character Windows limit."
            )
        buffer = ctypes.create_unicode_buffer(required + 1)
        copied = int(dll.Everything_GetResultFullPathNameW(index, buffer, required + 1))
        path = buffer.value
        # The SDK counts UTF-16 code units; non-BMP characters such as emoji use two.
        if copied != required or _utf16_length(path) != required:
            raise QueryError(
                f"Everything SDK result {index} changed while copying its full path; retry the query."
            )
        if not path:
            raise QueryError(
                f"Everything SDK returned an empty full path for result {index}; retry the query or restart Everything."
            )
        return path

    def _result_hit(
        self, index: int, metadata: bool, actual_flags: int = METADATA_FLAGS
    ) -> tuple[SearchHit, set[str]]:
        path = self._result_full_path(index)
        if not metadata:
            return SearchHit(path=path), set()
        unavailable: set[str] = set()
        size = self._result_size(index) if actual_flags & REQUEST_SIZE else None
        date_modified = self._result_date_modified(index) if actual_flags & REQUEST_DATE_MODIFIED else None
        attributes = self._result_attributes(index) if actual_flags & REQUEST_ATTRIBUTES else None
        if actual_flags & REQUEST_SIZE and size is None:
            unavailable.add("size")
        if actual_flags & REQUEST_DATE_MODIFIED and date_modified is None:
            unavailable.add("dateModified")
        if actual_flags & REQUEST_ATTRIBUTES and attributes is None:
            unavailable.add("attributes")
        return SearchHit(
            path=path,
            size=size,
            date_modified=date_modified,
            attributes=attributes,
        ), unavailable

    def _version(self) -> str | None:
        dll = self._ready_dll()
        try:
            parts = (
                int(dll.Everything_GetMajorVersion()),
                int(dll.Everything_GetMinorVersion()),
                int(dll.Everything_GetRevision()),
                int(dll.Everything_GetBuildNumber()),
            )
        except (AttributeError, OSError, TypeError, ValueError):
            return None
        return ".".join(str(part) for part in parts) if any(parts) else None

    def _target_machine(self) -> TargetMachineName | None:
        try:
            return TARGET_MACHINES.get(int(self._ready_dll().Everything_GetTargetMachine()))
        except (AttributeError, OSError, TypeError, ValueError):
            return None

    def _result_size(self, index: int) -> int | None:
        value = wintypes.LARGE_INTEGER()
        if not self._ready_dll().Everything_GetResultSize(index, ctypes.byref(value)):
            return None
        size = int(value.value)
        return size if size >= 0 else None

    def _result_date_modified(self, index: int) -> str | None:
        value = wintypes.FILETIME()
        if not self._ready_dll().Everything_GetResultDateModified(index, ctypes.byref(value)):
            return None
        raw_filetime = ((int(value.dwHighDateTime) & 0xFFFFFFFF) << 32) | (int(value.dwLowDateTime) & 0xFFFFFFFF)
        if raw_filetime in UNKNOWN_FILETIME_VALUES:
            return None
        try:
            windows_epoch = datetime(1601, 1, 1, tzinfo=timezone.utc)
            return (windows_epoch + timedelta(microseconds=raw_filetime // 10)).isoformat()
        except (OverflowError, ValueError):
            return None

    def _result_attributes(self, index: int) -> str | None:
        value = int(self._ready_dll().Everything_GetResultAttributes(index)) & 0xFFFFFFFF
        return None if value == INVALID_FILE_ATTRIBUTES else f"0x{value:08X}"


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le", "surrogatepass")) // 2


def _sort_flag(sort: SortName) -> int:
    try:
        return SORT_FLAGS[sort]
    except KeyError as exc:
        raise QueryError(f"Unsupported Everything SDK sort {sort!r}; use one of {', '.join(SORT_FLAGS)}.") from exc


def _missing_requested_fields(requested_flags: int, actual_flags: int) -> set[str]:
    return {
        name
        for flag, name in REQUEST_FLAG_NAMES
        if requested_flags & flag and not actual_flags & flag
    }


def _result_diagnostic_note(requested_sort: int, actual_sort: int, unavailable_fields: set[str]) -> str | None:
    differences: list[str] = []
    if actual_sort != requested_sort:
        differences.append(f"sort {actual_sort} (requested {requested_sort})")
    unavailable = [name for _, name in REQUEST_FLAG_NAMES if name in unavailable_fields]
    if unavailable:
        differences.append(f"unavailable result fields: {', '.join(unavailable)}")
    if not differences:
        return None
    return "Everything returned different result capabilities: " + "; ".join(differences) + "."
