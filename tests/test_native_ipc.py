from __future__ import annotations

import struct
from collections.abc import Callable
from pathlib import Path

import pytest

from everything_mcp.adapters import native_ipc
from everything_mcp.adapters.native_ipc import (
    METADATA_FLAGS,
    REQUEST_ATTRIBUTES,
    REQUEST_DATE_MODIFIED,
    REQUEST_FILE_NAME,
    REQUEST_FULL_PATH,
    REQUEST_PATH,
    REQUEST_SIZE,
    NativeIpcAdapter,
    build_query2,
    parse_list2,
    window_class_name,
)
from everything_mcp.config import EverythingConfig
from everything_mcp.contracts import SearchHit
from everything_mcp.errors import BackendUnavailableError, QueryError

WINDOW = 0x1234
OWNER = str(Path(__file__))
FILETIME_2025 = 133801632000000000  # 2025-01-01T00:00:00Z


def _string(value: str) -> bytes:
    encoded = value.encode("utf-16-le", "surrogatepass")
    return struct.pack("<I", len(encoded) // 2) + encoded + b"\0\0"


def build_list2(
    items: list[dict[str, object]], request_flags: int, total: int | None = None, sort: int = 1
) -> bytes:
    """Serialize a LIST2 reply the way Everything does: header, ITEM2 table, then field data."""
    header_size = 20 + 8 * len(items)
    table = b""
    data = b""
    for item in items:
        table += struct.pack("<II", 0, header_size + len(data))
        if request_flags & REQUEST_FILE_NAME:
            data += _string(str(item["name"]))
        if request_flags & REQUEST_PATH:
            data += _string(str(item["parent"]))
        if request_flags & REQUEST_FULL_PATH:
            data += _string(str(item["path"]))
        if request_flags & REQUEST_SIZE:
            data += struct.pack("<q", int(item.get("size", -1)))  # type: ignore[call-overload]
        if request_flags & REQUEST_DATE_MODIFIED:
            data += struct.pack("<Q", int(item.get("dm", 0)))  # type: ignore[call-overload]
        if request_flags & REQUEST_ATTRIBUTES:
            data += struct.pack("<I", int(item.get("attributes", 0xFFFFFFFF)))  # type: ignore[call-overload]
    count = len(items)
    return struct.pack("<5I", count if total is None else total, count, 0, request_flags, sort) + table + data


class FakeTransport:
    def __init__(
        self,
        reply: bytes | None = b"",
        db_loaded: int | None = 1,
        window: int = WINDOW,
        indexed_info: frozenset[int] = frozenset(),
        owner: str | None = OWNER,
    ) -> None:
        self.owner = owner
        self.reply = reply
        self.db_loaded = db_loaded
        self.indexed_info = indexed_info
        self.window = window
        self.commands: list[tuple[int, int]] = []
        self.payloads: list[bytes] = []
        self.found_classes: list[str] = []

    def find_window(self, class_name: str) -> int:
        self.found_classes.append(class_name)
        return self.window

    def send_command(self, hwnd: int, command: int, l_param: int, timeout_ms: int) -> int | None:
        assert hwnd == WINDOW
        assert timeout_ms > 0
        self.commands.append((command, l_param))
        if command == native_ipc.IPC_IS_DB_LOADED:
            return self.db_loaded
        if command == native_ipc.IPC_GET_TARGET_MACHINE:
            return 2
        if command == native_ipc.IPC_IS_FAST_SORT:
            return 1 if l_param == native_ipc.SORT_FLAGS["date_modified"] else 0
        if command == native_ipc.IPC_IS_FILE_INFO_INDEXED:
            return 1 if l_param in self.indexed_info else 0
        return {0: 1, 1: 4, 2: 1, 3: 1024}.get(command, 0)

    def owner_image(self, hwnd: int) -> str | None:
        assert hwnd == WINDOW
        return self.owner

    def query(self, hwnd: int, build_payload: Callable[[int, int], bytes], timeout_seconds: float) -> bytes | None:
        assert timeout_seconds > 0
        self.payloads.append(build_payload(0xABCDEF, 77))
        return self.reply


def adapter_with(transport: FakeTransport, instance: str | None = None, verify: bool = True) -> NativeIpcAdapter:
    config = EverythingConfig(everything_exe=Path(OWNER), instance=instance, verify_ipc_owner=verify)
    return NativeIpcAdapter(config, transport=transport)


def test_query2_payload_matches_everything_ipc_layout() -> None:
    payload = build_query2(0x1_0000_ABCD, 9, "ext:py 😺", 26, REQUEST_FULL_PATH, 13)

    assert struct.unpack_from("<7I", payload) == (0xABCD, 9, 0, 0, 26, REQUEST_FULL_PATH, 13)
    assert payload[28:].decode("utf-16-le") == "ext:py 😺\0"


def test_parse_list2_reads_paths_and_metadata_in_sdk_field_order() -> None:
    flags = REQUEST_FILE_NAME | REQUEST_PATH | METADATA_FLAGS
    reply = build_list2(
        [
            {
                "name": "a.py",
                "parent": "C:\\Work",
                "path": "C:\\Work\\a.py",
                "size": 123,
                "dm": FILETIME_2025,
                "attributes": 0x20,
            },
            {"name": "b", "parent": "C:\\Work", "path": "C:\\Work\\b", "size": -1, "dm": 0},
        ],
        flags,
        total=9,
    )

    parsed = parse_list2(reply)

    assert parsed.total == 9
    assert parsed.hits[0] == SearchHit("C:\\Work\\a.py", 123, "2025-01-01T00:00:00+00:00", "0x00000020")
    assert parsed.hits[1] == SearchHit("C:\\Work\\b")
    assert parsed.unavailable == {"size", "dateModified", "attributes"}


def test_parse_list2_keeps_non_bmp_and_unpaired_surrogate_names() -> None:
    names = ["C:\\Work\\emoji-😺.txt", "C:\\Work\\한글.txt", "C:\\Work\\lone-\udc80.txt"]
    reply = build_list2([{"path": name} for name in names], REQUEST_FULL_PATH)

    assert [hit.path for hit in parse_list2(reply).hits] == names


@pytest.mark.parametrize(
    "reply",
    [
        b"\0" * 12,
        struct.pack("<5I", 1, 1, 0, REQUEST_FULL_PATH, 1),
        struct.pack("<5I", 1, 1, 0, REQUEST_FULL_PATH, 1) + struct.pack("<II", 0, 999),
        struct.pack("<5I", 1, 1, 0, REQUEST_FULL_PATH, 1) + struct.pack("<II", 0, 28) + struct.pack("<I", 50) + b"ab",
        struct.pack("<5I", 1, 1, 0, REQUEST_FULL_PATH, 1) + struct.pack("<II", 0, 28) + struct.pack("<I", 40_000),
    ],
)
def test_parse_list2_rejects_out_of_bounds_replies(reply: bytes) -> None:
    with pytest.raises(QueryError):
        parse_list2(reply)


def test_parse_list2_requires_a_full_path() -> None:
    reply = build_list2([{"name": "a.py"}], REQUEST_FILE_NAME)

    with pytest.raises(QueryError, match="full path"):
        parse_list2(reply)


def test_window_class_honours_named_instances() -> None:
    assert window_class_name(None) == "EVERYTHING_TASKBAR_NOTIFICATION"
    assert window_class_name("1.5a") == "EVERYTHING_TASKBAR_NOTIFICATION_(1.5a)"

    transport = FakeTransport(build_list2([], 0))
    adapter_with(transport, instance="1.5a").count("ext:py", r"C:\Work")

    assert transport.found_classes == ["EVERYTHING_TASKBAR_NOTIFICATION_(1.5a)"]


def test_count_requests_no_rows_and_returns_the_total() -> None:
    transport = FakeTransport(build_list2([], 0, total=4321))

    assert adapter_with(transport).count("ext:py", r"C:\Work") == 4321
    header = struct.unpack_from("<7I", transport.payloads[0])
    assert header[4:] == (0, 0, 1)  # max_results, request_flags, name sort
    assert transport.payloads[0][28:].decode("utf-16-le") == '<ext:py> "C:\\Work\\"\0'


@pytest.mark.parametrize(
    ("indexed_info", "expected"),
    [
        (frozenset({4}), '<dm:today ext:py> "C:\\Work\\"\0'),
        (frozenset(), '"C:\\Work\\" <dm:today ext:py>\0'),
    ],
)
def test_date_filters_lead_only_when_everything_reports_them_indexed(indexed_info: frozenset[int], expected: str) -> None:
    transport = FakeTransport(build_list2([], 0), indexed_info=indexed_info)

    adapter_with(transport).count("dm:today ext:py", r"C:\Work")

    assert transport.payloads[0][28:].decode("utf-16-le") == expected
    assert (native_ipc.IPC_IS_FILE_INFO_INDEXED, 4) in transport.commands


def test_search_returns_paths_total_and_requested_shape() -> None:
    transport = FakeTransport(build_list2([{"path": r"C:\Work\a.py"}], REQUEST_FULL_PATH, total=50, sort=3))

    batch = adapter_with(transport).search("ext:py", r"C:\Work", limit=26, sort="path")

    assert batch.hits == (SearchHit(r"C:\Work\a.py"),)
    assert batch.total_count == 50
    assert batch.notes == ()
    header = struct.unpack_from("<7I", transport.payloads[0])
    assert header[1] == 77
    assert header[4:] == (26, REQUEST_FULL_PATH, 3)


def test_search_reports_sort_and_missing_metadata_differences() -> None:
    transport = FakeTransport(build_list2([{"path": r"C:\Work\a.py", "size": 5}], REQUEST_FULL_PATH | REQUEST_SIZE))

    batch = adapter_with(transport).search("ext:py", r"C:\Work", sort="size", metadata=True)

    assert batch.hits[0].size == 5
    assert batch.notes == (
        "Everything returned different result capabilities: sort 1 (requested 5); "
        "unavailable result fields: dateModified, attributes.",
    )


def test_search_strips_unrequested_metadata() -> None:
    transport = FakeTransport(build_list2([{"path": r"C:\Work\a.py", "size": 5}], REQUEST_FULL_PATH | REQUEST_SIZE))

    batch = adapter_with(transport).search("ext:py", r"C:\Work")

    assert batch.hits == (SearchHit(r"C:\Work\a.py"),)


@pytest.mark.parametrize(
    ("transport", "error", "message"),
    [
        (FakeTransport(window=0), BackendUnavailableError, "was not found"),
        (FakeTransport(db_loaded=None), BackendUnavailableError, "did not answer"),
        (FakeTransport(db_loaded=0), BackendUnavailableError, "still loading"),
        (FakeTransport(reply=None), QueryError, "timed out"),
    ],
)
def test_query_failures_are_classified(transport: FakeTransport, error: type[Exception], message: str) -> None:
    with pytest.raises(error, match=message):
        adapter_with(transport).count("ext:py", r"C:\Work")


def test_status_reports_version_target_and_fast_sorts() -> None:
    status = adapter_with(FakeTransport()).status().to_tool_result()

    assert status["backend"] == "native-ipc"
    assert status["everythingRunning"] is True
    assert status["dbLoaded"] is True
    assert status["version"] == "1.4.1.1024"
    assert status["targetMachine"] == "x64"
    assert status["fastSorts"] == ["name", "date_modified"]


def test_status_distinguishes_missing_and_hung_everything() -> None:
    missing = adapter_with(FakeTransport(window=0)).status()
    hung = adapter_with(FakeTransport(db_loaded=None)).status()

    assert (missing.everything_running, missing.backend) == (False, "none")
    assert (hung.everything_running, hung.backend) == (True, "none")
    assert any("hung or busy" in note for note in hung.notes)


def _live_adapter() -> NativeIpcAdapter:
    adapter = NativeIpcAdapter(EverythingConfig())
    if not adapter.is_reachable() or adapter.status().db_loaded is not True:
        pytest.skip("a running Everything instance is required")
    return adapter


def test_live_everything_returns_unicode_paths(tmp_path: Path) -> None:
    adapter = _live_adapter()
    names = ["ascii-mewlive.txt", "한글-mewlive.txt", "emoji-😺-mewlive.txt"]
    for name in names:
        (tmp_path / name).write_text("x", encoding="utf-8")

    import time

    deadline = time.monotonic() + 10
    batch = adapter.search("mewlive", str(tmp_path), limit=10)
    while len(batch.hits) < len(names) and time.monotonic() < deadline:
        time.sleep(0.2)
        batch = adapter.search("mewlive", str(tmp_path), limit=10)

    if len(batch.hits) < len(names):
        pytest.skip("Everything does not index the pytest temporary directory")
    assert sorted(hit.path for hit in batch.hits) == sorted(str(tmp_path / name) for name in names)
    assert batch.total_count == len(names)
