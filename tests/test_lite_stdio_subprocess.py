from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_SCRIPT = ROOT / "scripts" / "measure_lite_sessions.ps1"
SESSION_COUNT = 6
CYCLE_COUNT = 10


def lite_environment() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(ROOT / "src")
    if existing_pythonpath:
        env["PYTHONPATH"] += os.pathsep + existing_pythonpath
    return env


def run_lite(messages: list[dict[str, Any] | str]) -> subprocess.CompletedProcess[str]:
    lines = [message if isinstance(message, str) else json.dumps(message) for message in messages]
    payload = "\n".join(lines) + ("\n" if lines else "")
    return subprocess.run(
        [sys.executable, "-m", "everything_mcp.lite_stdio"],
        cwd=ROOT,
        env=lite_environment(),
        input=payload,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def parse_stdout(process: subprocess.CompletedProcess[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in process.stdout.splitlines()]


def start_lite() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "everything_mcp.lite_stdio"],
        cwd=ROOT,
        env=lite_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )


def repeated_session_payload(session_index: int) -> tuple[str, set[str]]:
    prefix = f"session-{session_index}"
    initialize_id = f"{prefix}:initialize"
    expected_ids = {initialize_id}
    messages: list[dict[str, Any]] = [
        {
            "jsonrpc": "2.0",
            "id": initialize_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "multi-session-subprocess-test", "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
    ]
    for cycle in range(1, CYCLE_COUNT + 1):
        for operation in ("list", "status", "count", "search"):
            expected_ids.add(f"{prefix}:cycle-{cycle}:{operation}")
        messages.extend(
            [
                {
                    "jsonrpc": "2.0",
                    "id": f"{prefix}:cycle-{cycle}:list",
                    "method": "tools/list",
                },
                {
                    "jsonrpc": "2.0",
                    "id": f"{prefix}:cycle-{cycle}:status",
                    "method": "tools/call",
                    "params": {"name": "everything_status", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": f"{prefix}:cycle-{cycle}:count",
                    "method": "tools/call",
                    "params": {"name": "everything_count", "arguments": {"query": "*"}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": f"{prefix}:cycle-{cycle}:search",
                    "method": "tools/call",
                    "params": {"name": "everything_search", "arguments": {"query": "*", "limit": 1}},
                },
            ]
        )
    return "\n".join(json.dumps(message) for message in messages) + "\n", expected_ids


def communicate_with_lite(process: subprocess.Popen[str], payload: str) -> tuple[str, str]:
    return process.communicate(payload, timeout=20)


def run_lite_sessions(payloads: list[str]) -> list[subprocess.CompletedProcess[str]]:
    processes = [start_lite() for _ in payloads]
    try:
        assert all(process.poll() is None for process in processes)
        with ThreadPoolExecutor(max_workers=len(processes)) as executor:
            futures = [
                executor.submit(communicate_with_lite, process, payload)
                for process, payload in zip(processes, payloads, strict=True)
            ]
            streams = [future.result(timeout=25) for future in futures]

        completed: list[subprocess.CompletedProcess[str]] = []
        for process, (stdout, stderr) in zip(processes, streams, strict=True):
            assert process.returncode is not None
            completed.append(
                subprocess.CompletedProcess(
                    args=process.args,
                    returncode=process.returncode,
                    stdout=stdout,
                    stderr=stderr,
                )
            )
        return completed
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


def assert_successful_tool_response(response: dict[str, Any]) -> dict[str, Any]:
    assert response["jsonrpc"] == "2.0"
    assert "error" not in response
    result = response["result"]
    assert result["isError"] is False
    assert isinstance(result["content"], list) and result["content"]
    assert result["content"][0]["type"] == "text"
    assert isinstance(result["content"][0]["text"], str)
    structured = result["structuredContent"]
    assert isinstance(structured, dict)
    return structured


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


def test_lite_stdio_runs_six_isolated_repeated_sessions_and_exits_at_eof() -> None:
    session_inputs = [repeated_session_payload(index) for index in range(1, SESSION_COUNT + 1)]
    processes = run_lite_sessions([payload for payload, _ in session_inputs])

    assert len(processes) == SESSION_COUNT
    all_expected_ids = set().union(*(expected_ids for _, expected_ids in session_inputs))
    assert len(all_expected_ids) == SESSION_COUNT * (1 + CYCLE_COUNT * 4)

    all_seen_ids: list[str] = []
    expected_tools = {
        "everything_status",
        "everything_count",
        "everything_search",
        "everything_syntax_help",
    }
    for session_index, (process, (_, expected_ids)) in enumerate(zip(processes, session_inputs, strict=True), start=1):
        responses = parse_stdout(process)
        response_ids = [response["id"] for response in responses]
        response_by_id = {response["id"]: response for response in responses}
        prefix = f"session-{session_index}:"

        assert process.returncode == 0
        assert process.stderr == ""
        assert "Traceback" not in process.stdout
        assert Counter(response_ids) == Counter(expected_ids)
        assert all(isinstance(response_id, str) and response_id.startswith(prefix) for response_id in response_ids)
        assert len(response_by_id) == len(expected_ids)
        all_seen_ids.extend(response_ids)

        initialize = response_by_id[f"session-{session_index}:initialize"]
        assert initialize["jsonrpc"] == "2.0"
        assert "error" not in initialize
        assert initialize["result"]["protocolVersion"] == "2025-11-25"

        for cycle in range(1, CYCLE_COUNT + 1):
            cycle_prefix = f"session-{session_index}:cycle-{cycle}"
            tools_response = response_by_id[f"{cycle_prefix}:list"]
            assert tools_response["jsonrpc"] == "2.0"
            assert "error" not in tools_response
            assert {tool["name"] for tool in tools_response["result"]["tools"]} == expected_tools

            status = assert_successful_tool_response(response_by_id[f"{cycle_prefix}:status"])
            assert isinstance(status["everythingInstalled"], bool)
            assert isinstance(status["everythingRunning"], bool)
            assert status["backend"] in {"sdk-ipc", "es-cli", "http", "none"}

            count = assert_successful_tool_response(response_by_id[f"{cycle_prefix}:count"])
            assert count == {
                "count": None,
                "tooBroad": True,
                "recommendation": "refine with a path, filename, extension, date, or size before searching",
            }

            search = assert_successful_tool_response(response_by_id[f"{cycle_prefix}:search"])
            assert search == {
                "countReturned": 0,
                "truncated": False,
                "tooBroad": True,
                "recommendation": "call everything_count after adding path, filename, extension, date, or size filters",
                "items": [],
            }

    assert Counter(all_seen_ids) == Counter(all_expected_ids)


def test_measurement_script_has_machine_neutral_defaults_and_valid_powershell_syntax() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    lowered_source = source.lower()
    assert "c:\\users\\" not in lowered_source
    assert ".config\\opencode" not in lowered_source
    assert "$env:EVERYTHING_SDK_DLL" in source

    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    assert powershell is not None
    probe = r"""
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TASK7_MEASUREMENT_SCRIPT,
    [ref]$tokens,
    [ref]$parseErrors
)
$defaults = @{}
foreach ($parameter in $ast.ParamBlock.Parameters) {
    if ($null -ne $parameter.DefaultValue) {
        $defaults[$parameter.Name.VariablePath.UserPath] = $parameter.DefaultValue.Extent.Text
    }
}
[pscustomobject]@{
    errors = @($parseErrors | ForEach-Object { $_.Message })
    parameters = @($ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath })
    defaults = [pscustomobject]$defaults
} | ConvertTo-Json -Depth 5 -Compress
"""
    env = os.environ.copy()
    env["TASK7_MEASUREMENT_SCRIPT"] = str(MEASUREMENT_SCRIPT)
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    metadata = json.loads(completed.stdout)
    assert metadata["errors"] == []
    assert set(metadata["parameters"]) >= {
        "RepoRoot",
        "PythonPath",
        "SdkDll",
        "OutputDirectory",
        "Sessions",
        "Cycles",
        "IdleSeconds",
        "Query",
        "Scope",
        "Limit",
        "Sort",
        "Metadata",
    }
    assert metadata["defaults"]["Sessions"] == "6"
    assert metadata["defaults"]["Cycles"] == "10"
    assert metadata["defaults"]["IdleSeconds"] == "60"


def test_measurement_script_resolves_default_repo_before_clear_sdk_preflight_failure(tmp_path: Path) -> None:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    assert powershell is not None
    missing_sdk = tmp_path / "missing Everything64.dll"
    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(MEASUREMENT_SCRIPT),
            "-PythonPath",
            sys.executable,
            "-SdkDll",
            str(missing_sdk),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=False,
    )

    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "Everything SDK DLL does not exist as a leaf" in output
    assert "PSScriptRoot" not in output
