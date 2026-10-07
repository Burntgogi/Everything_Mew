"""Trust boundaries: who may answer IPC, who may reply, and what the model sees."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from everything_mcp import lite_stdio
from everything_mcp.adapters import native_ipc
from everything_mcp.adapters.native_ipc import REQUEST_FULL_PATH, NativeIpcAdapter
from everything_mcp.config import EverythingConfig
from everything_mcp.errors import BackendUnavailableError
from everything_mcp.tool_specs import TOOL_SPEC_BY_NAME
from test_native_ipc import OWNER, FakeTransport, adapter_with, build_list2


@pytest.mark.parametrize(
    ("owner", "message"),
    [(r"C:\Users\Public\Everything.exe", "is owned by"), (None, "Could not identify")],
)
def test_an_ipc_window_owned_by_another_program_is_refused_before_any_query(owner: str | None, message: str) -> None:
    transport = FakeTransport(build_list2([{"path": r"C:\Work\fake.txt"}], REQUEST_FULL_PATH), owner=owner)
    adapter = adapter_with(transport)

    with pytest.raises(BackendUnavailableError, match=message):
        adapter.search("ext:txt", r"C:\Work")
    status = adapter.status()

    # The impostor never receives the query or any command.
    assert transport.payloads == []
    assert transport.commands == []
    assert (status.backend, status.everything_running) == ("none", False)
    assert any(message in note for note in status.notes)


def test_owner_paths_compare_case_insensitively() -> None:
    transport = FakeTransport(build_list2([], 0, total=3), owner=OWNER.upper())

    assert adapter_with(transport).count("ext:py", r"C:\Work") == 3


def test_owner_verification_can_be_disabled_for_portable_installs() -> None:
    transport = FakeTransport(build_list2([], 0, total=3), owner=r"D:\Portable\Everything.exe")

    assert adapter_with(transport, verify=False).count("ext:py", r"C:\Work") == 3


@pytest.mark.parametrize(("raw", "expected"), [(None, True), ("1", True), ("0", False)])
def test_owner_verification_setting(monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: bool) -> None:
    monkeypatch.delenv("EVERYTHING_MCP_BACKEND", raising=False)
    if raw is None:
        monkeypatch.delenv("EVERYTHING_MCP_VERIFY_IPC_OWNER", raising=False)
    else:
        monkeypatch.setenv("EVERYTHING_MCP_VERIFY_IPC_OWNER", raw)

    assert EverythingConfig.from_env().verify_ipc_owner is expected


def test_owner_verification_setting_rejects_other_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EVERYTHING_MCP_VERIFY_IPC_OWNER", "yes")

    with pytest.raises(ValueError, match="EVERYTHING_MCP_VERIFY_IPC_OWNER"):
        EverythingConfig.from_env()


def test_live_owner_is_identified_and_uipi_stays_closed_for_a_peer() -> None:
    adapter = NativeIpcAdapter(EverythingConfig())
    hwnd = adapter._window()
    if not hwnd:
        pytest.skip("a running Everything instance is required")

    image = native_ipc._Win32Transport().owner_image(hwnd)

    assert image is not None and image.lower().endswith(".exe")
    current = native_ipc._integrity_level(native_ipc._kernel32().GetCurrentProcess())
    if current == 0x2000:
        # A medium-integrity agent never needs to accept WM_COPYDATA from a lower level.
        assert native_ipc.everything_needs_uipi_exception() is False


def test_model_text_escapes_invisible_and_bidirectional_characters() -> None:
    names = [
        "C:\\Work\\invoice\u202eexe.txt",  # right-to-left override
        "C:\\Work\\a\u200bb.txt",  # zero-width space
        "C:\\Work\\tag\U000e0041.txt",  # Unicode tag character
        "C:\\Work\\lone\udc80.txt",  # unpaired surrogate allowed by NTFS
        "C:\\Work\\한글-😺.txt",
    ]
    payload = {"items": names}

    text = lite_stdio.model_visible_json(payload)

    assert json.loads(text) == payload
    text.encode("utf-8")
    for escaped in ("\\u202e", "\\u200b", "\\udb40\\udc41", "\\udc80"):
        assert escaped in text
    assert "한글-😺" in text
    result = lite_stdio._tool_success(payload)
    assert result["structuredContent"]["items"] == names
    assert result["content"][0]["text"] == text


def test_instructions_and_search_description_mark_paths_as_untrusted() -> None:
    assert "untrusted data" in lite_stdio.SERVER_INSTRUCTIONS
    assert "untrusted data" in TOOL_SPEC_BY_NAME["everything_search"].description


def test_owner_check_uses_the_configured_executable(tmp_path: Path) -> None:
    trusted = tmp_path / "Everything.exe"
    transport = FakeTransport(build_list2([], 0, total=1), owner=str(trusted))
    adapter = NativeIpcAdapter(EverythingConfig(everything_exe=trusted), transport=transport)

    assert adapter.count("ext:py", r"C:\Work") == 1
