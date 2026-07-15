"""ctypes-based Everything SDK adapter.

The adapter is intentionally conservative: missing DLLs, bitness mismatches,
or a stopped Everything runtime become status notes instead of import/startup
crashes.
"""

from __future__ import annotations

import ctypes
import platform
from ctypes import wintypes
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
WINDOWS_EPOCH_AS_UNIX_SECONDS = 11644473600
UNKNOWN_FILETIME_VALUES = {0, 0xFFFFFFFFFFFFFFFF}
TARGET_MACHINES: dict[int, TargetMachineName] = {1: "x86", 2: "x64", 3: "ARM", 4: "ARM64"}
METADATA_FLAG_NAMES = {
    REQUEST_SIZE: "size",
    REQUEST_DATE_MODIFIED: "dateModified",
    REQUEST_ATTRIBUTES: "attributes",
}


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
        except AttributeError as exc:
            self._dll = None
            self._load_error = f"Everything SDK DLL loading or required function lookup failed: {exc}"

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
        self._dll.Everything_QueryW.argtypes = [wintypes.BOOL]
        self._dll.Everything_QueryW.restype = wintypes.BOOL
        self._dll.Everything_GetTotResults.argtypes = []
        self._dll.Everything_GetTotResults.restype = wintypes.DWORD
        self._dll.Everything_GetNumResults.argtypes = []
        self._dll.Everything_GetNumResults.restype = wintypes.DWORD
        self._dll.Everything_GetLastError.argtypes = []
        self._dll.Everything_GetLastError.restype = wintypes.DWORD
        self._dll.Everything_GetResultFullPathNameW.argtypes = [wintypes.DWORD, wintypes.LPWSTR, wintypes.DWORD]
        self._dll.Everything_GetResultFullPathNameW.restype = wintypes.DWORD
        self._dll.Everything_GetResultSize.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_ulonglong)]
        self._dll.Everything_GetResultSize.restype = wintypes.BOOL
        self._dll.Everything_GetResultDateModified.argtypes = [wintypes.DWORD, ctypes.POINTER(ctypes.c_ulonglong)]
        self._dll.Everything_GetResultDateModified.restype = wintypes.BOOL
        self._dll.Everything_GetResultAttributes.argtypes = [wintypes.DWORD]
        self._dll.Everything_GetResultAttributes.restype = wintypes.DWORD

    def status(self) -> AdapterStatus:
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
        except (AttributeError, OSError) as exc:
            notes.append(f"Everything SDK loaded but the IPC readiness check failed: {exc}")
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
        try:
            self._set_query(query, scope)
            dll.Everything_SetRequestFlags(PATH_ONLY_FLAGS)
            self._query()
            return int(dll.Everything_GetTotResults())
        finally:
            dll.Everything_Reset()

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> list[SearchHit] | SearchBatch:
        self._ensure_ready()
        dll = self._ready_dll()
        requested_sort = _sort_flag(sort)
        requested_flags = METADATA_FLAGS if metadata else PATH_ONLY_FLAGS
        try:
            self._set_query(query, scope)
            dll.Everything_SetSort(requested_sort)
            dll.Everything_SetRequestFlags(requested_flags)
            dll.Everything_SetMax(limit)
            self._query()
            actual_sort = int(dll.Everything_GetResultListSort())
            actual_flags = int(dll.Everything_GetResultListRequestFlags())
            num_results = int(dll.Everything_GetNumResults())
            hits = [self._result_hit(index, metadata, actual_flags) for index in range(num_results)]
            note = _result_diagnostic_note(requested_sort, actual_sort, metadata, actual_flags)
            return SearchBatch(hits=hits, notes=(note,)) if note is not None else hits
        finally:
            dll.Everything_Reset()

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
        ok = dll.Everything_QueryW(True)
        if not ok:
            error_code = dll.Everything_GetLastError()
            raise QueryError(f"Everything SDK query failed with error {error_code}; check syntax or call everything_syntax_help.")

    def _result_full_path(self, index: int) -> str:
        dll = self._ready_dll()
        size = 260
        while size <= 32768:
            buffer = ctypes.create_unicode_buffer(size)
            copied = int(dll.Everything_GetResultFullPathNameW(index, buffer, size))
            if copied < size - 1:
                return buffer.value
            size *= 2
        raise QueryError("Everything SDK returned a path longer than the 32768 character safety buffer.")

    def _result_hit(self, index: int, metadata: bool, actual_flags: int = METADATA_FLAGS) -> SearchHit:
        path = self._result_full_path(index)
        if not metadata:
            return SearchHit(path=path)
        return SearchHit(
            path=path,
            size=self._result_size(index) if actual_flags & REQUEST_SIZE else None,
            date_modified=self._result_date_modified(index) if actual_flags & REQUEST_DATE_MODIFIED else None,
            attributes=self._result_attributes(index) if actual_flags & REQUEST_ATTRIBUTES else None,
        )

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
        value = ctypes.c_ulonglong()
        if self._ready_dll().Everything_GetResultSize(index, ctypes.byref(value)):
            return int(value.value)
        return None

    def _result_date_modified(self, index: int) -> str | None:
        value = ctypes.c_ulonglong()
        if not self._ready_dll().Everything_GetResultDateModified(index, ctypes.byref(value)):
            return None
        raw_filetime = int(value.value)
        if raw_filetime in UNKNOWN_FILETIME_VALUES:
            return None
        unix_seconds = (raw_filetime / 10_000_000) - WINDOWS_EPOCH_AS_UNIX_SECONDS
        try:
            return datetime.fromtimestamp(unix_seconds, tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    def _result_attributes(self, index: int) -> str | None:
        value = int(self._ready_dll().Everything_GetResultAttributes(index))
        return f"0x{value:08X}" if value else None


def _sort_flag(sort: SortName) -> int:
    try:
        return SORT_FLAGS[sort]
    except KeyError as exc:
        raise QueryError(f"Unsupported Everything SDK sort {sort!r}; use one of {', '.join(SORT_FLAGS)}.") from exc


def _result_diagnostic_note(requested_sort: int, actual_sort: int, metadata: bool, actual_flags: int) -> str | None:
    differences: list[str] = []
    if actual_sort != requested_sort:
        differences.append(f"sort {actual_sort} (requested {requested_sort})")
    if metadata:
        missing = [name for flag, name in METADATA_FLAG_NAMES.items() if not actual_flags & flag]
        if missing:
            differences.append(f"unavailable metadata: {', '.join(missing)}")
    if not differences:
        return None
    return "Everything returned different result capabilities: " + "; ".join(differences) + "."
