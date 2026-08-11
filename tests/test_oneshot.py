from __future__ import annotations

from io import BytesIO, StringIO
import json

import pytest

from everything_mcp import oneshot


def request(tool: str, arguments: dict[str, object]) -> bytes:
    return json.dumps(
        {"schemaVersion": 1, "tool": tool, "arguments": arguments},
        ensure_ascii=False,
    ).encode("utf-8")


def test_run_executes_one_existing_tool_and_writes_call_tool_result() -> None:
    stdout = BytesIO()
    stderr = StringIO()

    code = oneshot.run(
        BytesIO(request("everything_syntax_help", {"topic": "filters"})),
        stdout,
        stderr,
    )

    result = json.loads(stdout.getvalue())
    assert code == 0
    assert result["isError"] is False
    assert result["content"][0]["type"] == "text"
    assert "ext:" in result["content"][0]["text"]
    assert stderr.getvalue() == ""


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"not-json",
        b"[]",
        json.dumps(
            {"schemaVersion": True, "tool": "everything_status", "arguments": {}}
        ).encode(),
        request("unknown_tool", {}),
        json.dumps(
            {
                "schemaVersion": 1,
                "tool": "everything_status",
                "arguments": {},
                "extra": True,
            }
        ).encode(),
    ],
)
def test_run_rejects_invalid_invocations_without_leaking_details(payload: bytes) -> None:
    stdout = BytesIO()
    stderr = StringIO()

    code = oneshot.run(BytesIO(payload), stdout, stderr)

    assert code == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == "ONESHOT_INVOCATION_ERROR\n"


def test_run_rejects_requests_larger_than_65536_bytes() -> None:
    valid = request("everything_status", {})
    payload = valid + (b" " * (65_537 - len(valid)))
    stdout = BytesIO()
    stderr = StringIO()

    code = oneshot.run(BytesIO(payload), stdout, stderr)

    assert code == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == "ONESHOT_INVOCATION_ERROR\n"


def test_run_publishes_tool_validation_errors_with_exit_code_one() -> None:
    stdout = BytesIO()
    stderr = StringIO()

    code = oneshot.run(BytesIO(request("everything_search", {})), stdout, stderr)

    result = json.loads(stdout.getvalue())
    assert code == 1
    assert result["isError"] is True
    assert stderr.getvalue() == ""


def test_run_redacts_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_tool: str, _arguments: dict[str, object]) -> dict[str, object]:
        raise RuntimeError(r"C:\private\secret.txt")

    monkeypatch.setattr(oneshot, "call_tool_result", fail)
    stdout = BytesIO()
    stderr = StringIO()

    code = oneshot.run(BytesIO(request("everything_status", {})), stdout, stderr)

    assert code == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == "ONESHOT_INVOCATION_ERROR\n"
