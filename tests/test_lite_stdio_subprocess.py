from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def run_lite(messages: list[dict[str, Any] | str]) -> subprocess.CompletedProcess[str]:
    lines = [message if isinstance(message, str) else json.dumps(message) for message in messages]
    payload = "\n".join(lines) + ("\n" if lines else "")
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT / "src")
    if existing_pythonpath:
        env["PYTHONPATH"] += os.pathsep + existing_pythonpath
    return subprocess.run(
        [sys.executable, "-m", "everything_mcp.lite_stdio"],
        cwd=ROOT,
        env=env,
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def parse_stdout(process: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in process.stdout.splitlines()]


def test_lite_stdio_subprocess_frames_session_and_exits_cleanly_at_eof() -> None:
    process = run_lite(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "subprocess-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "everything_syntax_help", "arguments": {"topic": "filters"}},
            },
        ]
    )
    responses = parse_stdout(process)

    assert process.returncode == 0
    assert process.stderr == ""
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == {
        "everything_status",
        "everything_count",
        "everything_search",
        "everything_syntax_help",
    }
    assert responses[2]["result"]["isError"] is False
    assert "ext:" in responses[2]["result"]["content"][0]["text"]


def test_lite_stdio_subprocess_reports_protocol_errors_without_tracebacks() -> None:
    process = run_lite(
        [
            "{not-json",
            {"jsonrpc": "1.0", "id": 4, "method": "ping"},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        ]
    )
    responses = parse_stdout(process)

    assert process.returncode == 0
    assert process.stderr == ""
    assert [response["error"]["code"] for response in responses] == [-32700, -32600, -32002]
    assert "Traceback" not in process.stdout
