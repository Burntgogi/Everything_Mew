"""Dependency-free Everything IPC adapter.

This adapter speaks the documented Everything 1.4 IPC protocol from
``everything_ipc.h`` directly through ctypes instead of loading the Everything
SDK DLL. It follows the same wire rules as the SDK's ``Everything_QueryW``
(QUERY2W request, LIST2 reply, request-flag field order) with three deliberate
differences:

* every message sent to Everything uses ``SendMessageTimeoutW`` so a hung or
  busy Everything window cannot block the caller forever (the SDK uses plain
  ``SendMessage``);
* the reply window allows ``WM_COPYDATA`` through UIPI, matching what the SDK
  does for its own reply window, so elevated agents still receive replies;
* the LIST2 reply is copied once and parsed with bounds checks, so no SDK
  global state has to be reset after a query.
"""

from __future__ import annotations

import ctypes
import math
import os
import struct
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from everything_mcp.config import EverythingConfig
from everything_mcp.contracts import AdapterStatus, SearchBatch, SearchHit, SortName, TargetMachineName
from everything_mcp.errors import BackendUnavailableError, QueryError
from everything_mcp.query import compose_query

EVERYTHING_IPC_WNDCLASS = "EVERYTHING_TASKBAR_NOTIFICATION"
WM_USER = 0x0400
EVERYTHING_WM_IPC = WM_USER
IPC_GET_MAJOR_VERSION = 0
IPC_GET_MINOR_VERSION = 1
IPC_GET_REVISION = 2
IPC_GET_BUILD_NUMBER = 3
IPC_GET_TARGET_MACHINE = 5
IPC_IS_DB_LOADED = 401
IPC_IS_FAST_SORT = 410
IPC_IS_FILE_INFO_INDEXED = 411
# EVERYTHING_IPC_FILE_INFO_* values for the properties compose_query can lead with.
FILE_INFO_PROPERTIES = {"size": 1, "date_modified": 4}
COPYDATA_QUERY2W = 18

REQUEST_FILE_NAME = 0x00000001
REQUEST_PATH = 0x00000002
REQUEST_FULL_PATH = 0x00000004
REQUEST_EXTENSION = 0x00000008
REQUEST_SIZE = 0x00000010
REQUEST_DATE_CREATED = 0x00000020
REQUEST_DATE_MODIFIED = 0x00000040
REQUEST_DATE_ACCESSED = 0x00000080
REQUEST_ATTRIBUTES = 0x00000100
METADATA_FLAGS = REQUEST_FULL_PATH | REQUEST_SIZE | REQUEST_DATE_MODIFIED | REQUEST_ATTRIBUTES
PATH_ONLY_FLAGS = REQUEST_FULL_PATH

SORT_FLAGS: dict[str, int] = {"name": 1, "path": 3, "size": 5, "date_modified": 13}
SORT_NAME_ASCENDING = SORT_FLAGS["name"]
TARGET_MACHINES: dict[int, TargetMachineName] = {1: "x86", 2: "x64", 3: "ARM", 4: "ARM64"}

QUERY_TIMEOUT_SECONDS = 15.0
COMMAND_TIMEOUT_MILLISECONDS = 2_000
UNKNOWN_FILETIME_VALUES = {0, 0xFFFFFFFFFFFFFFFF}
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
MAX_WINDOWS_PATH_CHARACTERS = 32_767
_LIST2_HEADER = struct.Struct("<5I")
_ITEM2 = struct.Struct("<II")
_QUERY2_HEADER = struct.Struct("<7I")
_DWORD = struct.Struct("<I")
_INT64 = struct.Struct("<q")
_UINT64 = struct.Struct("<Q")
_STRING_FIELDS = (REQUEST_FILE_NAME, REQUEST_PATH, REQUEST_FULL_PATH, REQUEST_EXTENSION)
_FIXED_FIELDS = (
    (REQUEST_SIZE, 8),
    (REQUEST_DATE_CREATED, 8),
    (REQUEST_DATE_MODIFIED, 8),
    (REQUEST_DATE_ACCESSED, 8),
    (REQUEST_ATTRIBUTES, 4),
)
_REQUEST_FIELD_NAMES = (
    (REQUEST_SIZE, "size"),
    (REQUEST_DATE_MODIFIED, "dateModified"),
    (REQUEST_ATTRIBUTES, "attributes"),
)


class IpcTransport(Protocol):
    """Win32 operations used by the adapter; replaced by fakes in tests."""

    def find_window(self, class_name: str) -> int: ...

    def send_command(self, hwnd: int, command: int, l_param: int, timeout_ms: int) -> int | None: ...

    def query(
        self, hwnd: int, build_payload: Callable[[int, int], bytes], timeout_seconds: float
    ) -> bytes | None: ...

    def owner_image(self, hwnd: int) -> str | None: ...


class List2:
    """Parsed EVERYTHING_IPC_LIST2 reply."""

    __slots__ = ("total", "request_flags", "sort", "hits", "unavailable")

    def __init__(
        self,
        total: int,
        request_flags: int,
        sort: int,
        hits: tuple[SearchHit, ...],
        unavailable: frozenset[str],
    ) -> None:
        self.total = total
        self.request_flags = request_flags
        self.sort = sort
        self.hits = hits
        self.unavailable = unavailable


def window_class_name(instance: str | None) -> str:
    """Return the IPC window class, honouring named instances such as Everything 1.5a."""
    name = (instance or "").strip()
    return f"{EVERYTHING_IPC_WNDCLASS}_({name})" if name else EVERYTHING_IPC_WNDCLASS


def build_query2(reply_hwnd: int, reply_id: int, search: str, max_results: int, request_flags: int, sort: int) -> bytes:
    """Serialize EVERYTHING_IPC_QUERY2 followed by the NUL-terminated UTF-16 search."""
    header = _QUERY2_HEADER.pack(
        reply_hwnd & 0xFFFFFFFF,  # Only 32 bits of an HWND are significant, even on x64.
        reply_id & 0xFFFFFFFF,
        0,  # search_flags: Everything's own defaults; syntax lives in the search text.
        0,  # offset
        max_results & 0xFFFFFFFF,
        request_flags & 0xFFFFFFFF,
        sort & 0xFFFFFFFF,
    )
    return header + (search + "\0").encode("utf-16-le", "surrogatepass")


def parse_list2(data: bytes) -> List2:
    """Parse an EVERYTHING_IPC_LIST2 reply, rejecting any out-of-bounds field."""
    if len(data) < _LIST2_HEADER.size:
        raise QueryError("Everything returned a truncated IPC reply; retry the query.")
    total, count, _offset, request_flags, sort = _LIST2_HEADER.unpack_from(data, 0)
    if _LIST2_HEADER.size + count * _ITEM2.size > len(data):
        raise QueryError("Everything returned a malformed IPC result list; retry the query.")

    hits: list[SearchHit] = []
    unavailable: set[str] = set()
    for index in range(count):
        _item_flags, position = _ITEM2.unpack_from(data, _LIST2_HEADER.size + index * _ITEM2.size)
        full_path: str | None = None
        for flag in _STRING_FIELDS:
            if request_flags & flag:
                value, position = _read_string(data, position, index)
                if flag == REQUEST_FULL_PATH:
                    full_path = value
        fixed: dict[int, bytes] = {}
        for flag, size in _FIXED_FIELDS:
            if request_flags & flag:
                if position + size > len(data):
                    raise QueryError(f"Everything IPC result {index} is truncated; retry the query.")
                fixed[flag] = data[position : position + size]
                position += size
        if not full_path:
            raise QueryError(f"Everything did not return a full path for result {index}; retry the query.")
        hit = SearchHit(
            path=full_path,
            size=_size(fixed.get(REQUEST_SIZE)),
            date_modified=_filetime(fixed.get(REQUEST_DATE_MODIFIED)),
            attributes=_attributes(fixed.get(REQUEST_ATTRIBUTES)),
        )
        if REQUEST_SIZE in fixed and hit.size is None:
            unavailable.add("size")
        if REQUEST_DATE_MODIFIED in fixed and hit.date_modified is None:
            unavailable.add("dateModified")
        if REQUEST_ATTRIBUTES in fixed and hit.attributes is None:
            unavailable.add("attributes")
        hits.append(hit)
    return List2(total, request_flags, sort, tuple(hits), frozenset(unavailable))


def _read_string(data: bytes, position: int, index: int) -> tuple[str, int]:
    if position + _DWORD.size > len(data):
        raise QueryError(f"Everything IPC result {index} is truncated; retry the query.")
    (length,) = _DWORD.unpack_from(data, position)
    if length > MAX_WINDOWS_PATH_CHARACTERS:
        raise QueryError(
            f"Everything returned a path longer than the {MAX_WINDOWS_PATH_CHARACTERS} character Windows limit."
        )
    start = position + _DWORD.size
    end = start + length * 2
    if end + 2 > len(data):
        raise QueryError(f"Everything IPC result {index} is truncated; retry the query.")
    # Windows names may contain unpaired surrogates; keep them instead of failing the batch.
    return data[start:end].decode("utf-16-le", "surrogatepass"), end + 2


def _size(raw: bytes | None) -> int | None:
    if raw is None:
        return None
    (value,) = _INT64.unpack(raw)
    return value if value >= 0 else None


def _filetime(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    (value,) = _UINT64.unpack(raw)
    if value in UNKNOWN_FILETIME_VALUES:
        return None
    try:
        return (datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=value // 10)).isoformat()
    except (OverflowError, ValueError):
        return None


def _attributes(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    (value,) = _DWORD.unpack(raw)
    return None if value == INVALID_FILE_ATTRIBUTES else f"0x{value:08X}"


class NativeIpcAdapter:
    name = "native-ipc"

    def __init__(self, config: EverythingConfig | None = None, transport: IpcTransport | None = None) -> None:
        self.config = config or EverythingConfig.from_env()
        self._transport = transport
        self._class_name = window_class_name(self.config.instance)

    def _ipc(self) -> IpcTransport:
        if self._transport is None:
            self._transport = _Win32Transport()
        return self._transport

    def _window(self) -> int:
        try:
            return self._ipc().find_window(self._class_name)
        except OSError:
            return 0

    def is_reachable(self) -> bool:
        return self._window() != 0

    def _owner_problem(self, hwnd: int) -> str | None:
        """Refuse a window that the configured Everything executable does not own.

        Any program running as the same user can register the Everything window
        class. Checking the owning image against a path in an admin-protected
        directory, such as Program Files, keeps such a program from reading
        queries or returning false results.
        """
        if not self.config.verify_ipc_owner:
            return None
        try:
            image = self._ipc().owner_image(hwnd)
        except OSError:
            image = None
        expected = str(self.config.everything_exe)
        if image is None:
            return "Could not identify the process that owns the Everything IPC window; refusing to query it."
        if _same_path(image, expected):
            return None
        return (
            f"The Everything IPC window is owned by {image}, not the trusted EVERYTHING_EXE {expected}; refusing to "
            "query it. Set EVERYTHING_EXE to the real Everything executable, or EVERYTHING_MCP_VERIFY_IPC_OWNER=0 "
            "to disable this check."
        )

    def _command(self, hwnd: int, command: int, l_param: int = 0) -> int | None:
        try:
            return self._ipc().send_command(hwnd, command, l_param, COMMAND_TIMEOUT_MILLISECONDS)
        except OSError:
            return None

    def status(self) -> AdapterStatus:
        installed = self.config.everything_exe.exists()
        notes: list[str] = []
        if not installed:
            notes.append(f"Everything.exe was not found at {self.config.everything_exe}.")
        hwnd = self._window()
        if not hwnd:
            notes.append(f"Everything IPC window {self._class_name!r} was not found; start Everything and retry.")
            return AdapterStatus(installed, False, "none", False, notes=tuple(notes))
        problem = self._owner_problem(hwnd)
        if problem:
            notes.append(problem)
            return AdapterStatus(installed, False, "none", False, notes=tuple(notes))
        db_loaded = self._command(hwnd, IPC_IS_DB_LOADED)
        if db_loaded is None:
            notes.append("Everything did not answer IPC within 2 seconds; it may be hung or busy. Retry shortly.")
            return AdapterStatus(installed, True, "none", False, notes=tuple(notes))
        if not db_loaded:
            notes.append("Everything is running but its database is still loading; retry when dbLoaded is true.")
        parts = [
            self._command(hwnd, command)
            for command in (IPC_GET_MAJOR_VERSION, IPC_GET_MINOR_VERSION, IPC_GET_REVISION, IPC_GET_BUILD_NUMBER)
        ]
        version = ".".join(str(part) for part in parts) if all(part is not None for part in parts) else None
        target = TARGET_MACHINES.get(self._command(hwnd, IPC_GET_TARGET_MACHINE) or 0)
        fast_sorts = tuple(
            name for name, sort in SORT_FLAGS.items() if name != "name" and self._command(hwnd, IPC_IS_FAST_SORT, sort)
        )
        return AdapterStatus(
            installed,
            True,
            "native-ipc",
            False,
            db_loaded=bool(db_loaded),
            version=version,
            target_machine=target,
            fast_sorts=("name", *fast_sorts),
            notes=tuple(notes),
        )

    def count(self, query: str, scope: str | None = None) -> int:
        return self._run(query, scope, max_results=0, request_flags=0, sort=SORT_NAME_ASCENDING).total

    def search(
        self,
        query: str,
        scope: str | None = None,
        limit: int = 25,
        sort: SortName = "name",
        metadata: bool = False,
    ) -> SearchBatch:
        try:
            requested_sort = SORT_FLAGS[sort]
        except KeyError:
            raise QueryError(f"Unsupported Everything sort {sort!r}; use one of {', '.join(SORT_FLAGS)}.") from None
        requested_flags = METADATA_FLAGS if metadata else PATH_ONLY_FLAGS
        reply = self._run(query, scope, max_results=limit, request_flags=requested_flags, sort=requested_sort)
        missing = {name for flag, name in _REQUEST_FIELD_NAMES if requested_flags & flag and not reply.request_flags & flag}
        note = _diagnostic_note(requested_sort, reply.sort, missing | reply.unavailable)
        hits = reply.hits if metadata else tuple(SearchHit(path=hit.path) for hit in reply.hits)
        return SearchBatch(hits=hits, notes=(note,) if note else (), total_count=reply.total)

    def _run(self, query: str, scope: str | None, *, max_results: int, request_flags: int, sort: int) -> List2:
        hwnd = self._window()
        if not hwnd:
            raise BackendUnavailableError(
                f"Everything IPC window {self._class_name!r} was not found; start Everything and retry."
            )
        problem = self._owner_problem(hwnd)
        if problem:
            raise BackendUnavailableError(problem)
        db_loaded = self._command(hwnd, IPC_IS_DB_LOADED)
        if db_loaded is None:
            raise BackendUnavailableError("Everything did not answer IPC within 2 seconds; retry shortly.")
        if not db_loaded:
            raise BackendUnavailableError("Everything database is still loading; retry when dbLoaded is true.")

        # Ask Everything which properties it indexes, so the query only leads with
        # filters it can answer without reading every file's metadata from disk.
        indexed = frozenset(
            name for name, info in FILE_INFO_PROPERTIES.items() if self._command(hwnd, IPC_IS_FILE_INFO_INDEXED, info)
        )
        search = compose_query(query, scope, indexed)

        def payload(reply_hwnd: int, reply_id: int) -> bytes:
            return build_query2(reply_hwnd, reply_id, search, max_results, request_flags, sort)

        try:
            data = self._ipc().query(hwnd, payload, QUERY_TIMEOUT_SECONDS)
        except OSError:
            raise QueryError("Everything IPC query failed; verify the Everything runtime and retry.") from None
        if data is None:
            raise QueryError(
                f"Everything query timed out after {QUERY_TIMEOUT_SECONDS:g} seconds; refine the query or restart Everything."
            )
        return parse_list2(data)


def _diagnostic_note(requested_sort: int, actual_sort: int, unavailable: set[str] | frozenset[str]) -> str | None:
    differences: list[str] = []
    if actual_sort != requested_sort:
        differences.append(f"sort {actual_sort} (requested {requested_sort})")
    names = [name for _, name in _REQUEST_FIELD_NAMES if name in unavailable]
    if names:
        differences.append(f"unavailable result fields: {', '.join(names)}")
    if not differences:
        return None
    return "Everything returned different result capabilities: " + "; ".join(differences) + "."


# --- Win32 transport -------------------------------------------------------------------------

WM_COPYDATA = 0x004A
SMTO_ABORTIFHUNG = 0x0002
MSGFLT_ALLOW = 1
PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF
MWMO_INPUTAVAILABLE = 0x0004
WAIT_FAILED = 0xFFFFFFFF
HWND_MESSAGE = -3
_REPLY_CLASS_NAME = "EverythingMewIpcReply"

_WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class _COPYDATASTRUCT(ctypes.Structure):
    _fields_ = (("dwData", ctypes.c_size_t), ("cbData", wintypes.DWORD), ("lpData", ctypes.c_void_p))


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


class _PendingReply:
    __slots__ = ("reply_id", "data")

    def __init__(self, reply_id: int) -> None:
        self.reply_id = reply_id
        self.data: bytes | None = None


_LOCK = threading.RLock()
_PENDING: dict[int, _PendingReply] = {}


def _window_proc(hwnd: int, message: int, w_param: int, l_param: int) -> int:
    if message == WM_COPYDATA and l_param:
        pending = _PENDING.get(int(hwnd or 0))
        if pending is not None and pending.data is None:
            try:
                copied = ctypes.cast(l_param, ctypes.POINTER(_COPYDATASTRUCT)).contents
                if copied.dwData == pending.reply_id:
                    size = int(copied.cbData)
                    pending.data = ctypes.string_at(copied.lpData, size) if size and copied.lpData else b""
                    return 1
            except Exception:  # pragma: no cover - a callback must never raise into Win32
                return 0
    return int(_user32().DefWindowProcW(hwnd, message, w_param, l_param))


_WINDOW_PROC = _WNDPROC(_window_proc)
_USER32: Any = None
_CLASS_ATOM = 0


def _user32() -> Any:
    global _USER32
    if _USER32 is None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.FindWindowW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR)
        user32.FindWindowW.restype = wintypes.HWND
        user32.SendMessageTimeoutW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        )
        user32.SendMessageTimeoutW.restype = ctypes.c_ssize_t
        user32.RegisterClassW.argtypes = (ctypes.POINTER(_WNDCLASSW),)
        user32.RegisterClassW.restype = wintypes.ATOM
        user32.CreateWindowExW.argtypes = (
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
        )
        user32.CreateWindowExW.restype = wintypes.HWND
        user32.DestroyWindow.argtypes = (wintypes.HWND,)
        user32.DestroyWindow.restype = wintypes.BOOL
        user32.DefWindowProcW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.ChangeWindowMessageFilterEx.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.DWORD, wintypes.LPVOID)
        user32.ChangeWindowMessageFilterEx.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.MsgWaitForMultipleObjectsEx.argtypes = (
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        user32.MsgWaitForMultipleObjectsEx.restype = wintypes.DWORD
        user32.PeekMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT)
        user32.PeekMessageW.restype = wintypes.BOOL
        user32.TranslateMessage.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.argtypes = (ctypes.POINTER(wintypes.MSG),)
        user32.DispatchMessageW.restype = ctypes.c_ssize_t
        _USER32 = user32
    return _USER32


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TOKEN_QUERY = 0x0008
TOKEN_INTEGRITY_LEVEL = 25
_KERNEL32: Any = None
_ADVAPI32: Any = None


def _kernel32() -> Any:
    global _KERNEL32
    if _KERNEL32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.argtypes = ()
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.QueryFullProcessImageNameW.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        )
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        _KERNEL32 = kernel32
    return _KERNEL32


def _advapi32() -> Any:
    global _ADVAPI32
    if _ADVAPI32 is None:
        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi32.OpenProcessToken.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE))
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.GetSidSubAuthorityCount.argtypes = (ctypes.c_void_p,)
        advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
        advapi32.GetSidSubAuthority.argtypes = (ctypes.c_void_p, wintypes.DWORD)
        advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
        _ADVAPI32 = advapi32
    return _ADVAPI32


def _instance_handle() -> Any:
    return _kernel32().GetModuleHandleW(None)


def _same_path(left: str, right: str) -> bool:
    import ntpath

    return ntpath.normcase(ntpath.normpath(left)) == ntpath.normcase(ntpath.normpath(right))


def _window_process(hwnd: int) -> Any:
    """Open the process that owns a window for limited queries, or return None."""
    pid = wintypes.DWORD(0)
    _user32().GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    return _kernel32().OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value) or None


def _process_image(process: Any) -> str | None:
    size = wintypes.DWORD(MAX_WINDOWS_PATH_CHARACTERS + 1)
    buffer = ctypes.create_unicode_buffer(size.value)
    if not _kernel32().QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
        return None
    return buffer.value or None


def _integrity_level(process: Any) -> int | None:
    """Return the mandatory integrity RID of a process token, or None when unreadable."""
    advapi32 = _advapi32()
    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(process, TOKEN_QUERY, ctypes.byref(token)):
        return None
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(token, TOKEN_INTEGRITY_LEVEL, buffer, size, ctypes.byref(size)):
            return None
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        count = advapi32.GetSidSubAuthorityCount(sid)[0]
        return int(advapi32.GetSidSubAuthority(sid, count - 1)[0]) if count else None
    finally:
        _kernel32().CloseHandle(token)


def owner_has_lower_integrity(hwnd: int) -> bool:
    """Return whether the window's process runs below this process's integrity level.

    Only then must the reply window accept WM_COPYDATA across UIPI. Opening it
    otherwise would let every lower-integrity program on the desktop send to it.
    """
    process = _window_process(hwnd)
    if process is None:
        return False
    try:
        target = _integrity_level(process)
    finally:
        _kernel32().CloseHandle(process)
    current = _integrity_level(_kernel32().GetCurrentProcess())
    return target is not None and current is not None and target < current


def everything_needs_uipi_exception(class_name: str = EVERYTHING_IPC_WNDCLASS) -> bool:
    """Return whether a reply window must accept WM_COPYDATA from the running Everything."""
    hwnd = int(_user32().FindWindowW(class_name, None) or 0)
    return bool(hwnd) and owner_has_lower_integrity(hwnd)


class _Win32Transport:
    def owner_image(self, hwnd: int) -> str | None:
        process = _window_process(hwnd)
        if process is None:
            return None
        try:
            return _process_image(process)
        finally:
            _kernel32().CloseHandle(process)

    def find_window(self, class_name: str) -> int:
        return int(_user32().FindWindowW(class_name, None) or 0)

    def send_command(self, hwnd: int, command: int, l_param: int, timeout_ms: int) -> int | None:
        result = ctypes.c_size_t(0)
        sent = _user32().SendMessageTimeoutW(
            hwnd, EVERYTHING_WM_IPC, command, l_param, SMTO_ABORTIFHUNG, timeout_ms, ctypes.byref(result)
        )
        return int(result.value) if sent else None

    def query(self, hwnd: int, build_payload: Callable[[int, int], bytes], timeout_seconds: float) -> bytes | None:
        with _LOCK:
            user32 = _user32()
            reply_hwnd = self._create_reply_window(user32, owner_has_lower_integrity(hwnd))
            # Unpredictable: when the reply window accepts WM_COPYDATA across UIPI, a
            # lower-integrity process must not be able to guess the expected reply ID.
            pending = _PendingReply(int.from_bytes(os.urandom(4), "little") or 1)
            _PENDING[reply_hwnd] = pending
            try:
                payload = build_payload(reply_hwnd, pending.reply_id)
                buffer = ctypes.create_string_buffer(payload, len(payload))
                copy = _COPYDATASTRUCT(COPYDATA_QUERY2W, len(payload), ctypes.cast(buffer, ctypes.c_void_p))
                deadline = time.monotonic() + timeout_seconds
                accepted = ctypes.c_size_t(0)
                # No SMTO_BLOCK: Everything may send the reply while this call is still waiting.
                sent = user32.SendMessageTimeoutW(
                    hwnd,
                    WM_COPYDATA,
                    reply_hwnd,
                    ctypes.addressof(copy),
                    SMTO_ABORTIFHUNG,
                    max(1, math.ceil(timeout_seconds * 1000)),
                    ctypes.byref(accepted),
                )
                if not sent:
                    return None
                if not accepted.value:
                    raise OSError("Everything rejected the IPC query.")
                return self._wait(user32, pending, deadline)
            finally:
                _PENDING.pop(reply_hwnd, None)
                user32.DestroyWindow(reply_hwnd)

    @staticmethod
    def _create_reply_window(user32: Any, allow_lower_integrity: bool) -> int:
        global _CLASS_ATOM
        instance = _instance_handle()
        if not _CLASS_ATOM:
            window_class = _WNDCLASSW(0, _WINDOW_PROC, 0, 0, instance, None, None, None, None, _REPLY_CLASS_NAME)
            _CLASS_ATOM = int(user32.RegisterClassW(ctypes.byref(window_class)))
            if not _CLASS_ATOM:
                raise OSError("Could not register the Everything IPC reply window class.")
        hwnd = user32.CreateWindowExW(
            0, _REPLY_CLASS_NAME, None, 0, 0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE), None, instance, None
        )
        if not hwnd:
            raise OSError("Could not create the Everything IPC reply window.")
        if allow_lower_integrity:
            # Like the SDK, but only when needed: let a lower-integrity Everything reply to an elevated agent.
            user32.ChangeWindowMessageFilterEx(hwnd, WM_COPYDATA, MSGFLT_ALLOW, None)
        return int(hwnd)

    @staticmethod
    def _wait(user32: Any, pending: _PendingReply, deadline: float) -> bytes | None:
        message = wintypes.MSG()
        while pending.data is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            wait = user32.MsgWaitForMultipleObjectsEx(
                0, None, min(max(1, math.ceil(remaining * 1000)), 0xFFFFFFFE), QS_ALLINPUT, MWMO_INPUTAVAILABLE
            )
            if wait == WAIT_FAILED:
                raise OSError("Everything IPC reply wait failed.")
            while user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_REMOVE):
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        return pending.data
