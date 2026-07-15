from __future__ import annotations

from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_SCRIPT = ROOT / "scripts" / "measure_lite_sessions.ps1"
SESSION_COUNT = 6
CYCLE_COUNT = 10


def powershell_path() -> str:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    assert powershell is not None
    return powershell


def run_measurement_function_probe(
    functions: list[str],
    body: str,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    loader = r"""
Set-StrictMode -Version Latest
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $env:TASK7_MEASUREMENT_SCRIPT,
    [ref]$tokens,
    [ref]$parseErrors
)
if (@($parseErrors).Count -ne 0) {
    throw (@($parseErrors | ForEach-Object { $_.Message }) -join "; ")
}
$definitions = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst]
}, $true))
foreach ($functionName in @($env:TASK7_FUNCTIONS -split ',')) {
    $definition = @($definitions | Where-Object { $_.Name -eq $functionName })
    if ($definition.Count -ne 1) {
        throw "Expected exactly one function named '$functionName'; found $($definition.Count)."
    }
    Invoke-Expression $definition[0].Extent.Text
}
"""
    env = os.environ.copy()
    env["TASK7_MEASUREMENT_SCRIPT"] = str(MEASUREMENT_SCRIPT)
    env["TASK7_FUNCTIONS"] = ",".join(functions)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [powershell_path(), "-NoProfile", "-NonInteractive", "-Command", loader + body],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=timeout,
        check=False,
    )


def measurement_function_source(name: str) -> str:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^function {re.escape(name)}\s*\{{.*?^\}}", source)
    assert match is not None, f"PowerShell function {name} was not found"
    return match.group(0)


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


def terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    failures: list[Exception] = []
    if os.name == "nt":
        try:
            taskkill = subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if taskkill.returncode != 0:
                detail = taskkill.stderr.strip() or taskkill.stdout.strip() or "no diagnostic output"
                failures.append(RuntimeError(f"exact-root taskkill failed with {taskkill.returncode}: {detail}"))
        except Exception as error:
            failures.append(error)
    else:
        try:
            process.kill()
        except Exception as error:
            failures.append(error)
    try:
        process.wait(timeout=5)
    except Exception as error:
        failures.append(error)
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception as fallback_error:
            failures.append(fallback_error)
    if failures:
        raise ExceptionGroup(f"Failed to clean exact process tree rooted at PID {process.pid}", failures)


def cleanup_lite_processes(processes: list[subprocess.Popen[str]]) -> list[Exception]:
    failures: list[Exception] = []
    for process in processes:
        try:
            if process.poll() is None:
                terminate_process_tree(process)
        except Exception as error:
            failures.append(error)
    return failures


def run_lite_sessions(payloads: list[str]) -> list[subprocess.CompletedProcess[str]]:
    processes: list[subprocess.Popen[str]] = []
    primary_error: BaseException | None = None
    try:
        for _ in payloads:
            processes.append(start_lite())
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
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_failures = cleanup_lite_processes(processes)
        if cleanup_failures:
            detail = "; ".join(f"{type(error).__name__}: {error}" for error in cleanup_failures)
            if primary_error is not None:
                primary_error.add_note(f"Process cleanup failures after primary error: {detail}")
            else:
                raise ExceptionGroup("Process cleanup failures", cleanup_failures)


def test_run_lite_sessions_cleans_processes_started_before_later_launch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        def poll(self) -> None:
            return None

    first = FakeProcess()
    launches = 0
    cleaned: list[FakeProcess] = []

    def fake_start_lite() -> Any:
        nonlocal launches
        launches += 1
        if launches == 1:
            return first
        raise RuntimeError("launch N failed")

    monkeypatch.setattr(sys.modules[__name__], "start_lite", fake_start_lite)
    monkeypatch.setattr(sys.modules[__name__], "terminate_process_tree", cleaned.append)

    with pytest.raises(RuntimeError, match="launch N failed"):
        run_lite_sessions(["first", "second"])

    assert cleaned == [first]


def test_run_lite_sessions_continues_cleanup_failures_and_preserves_primary_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeProcess:
        def __init__(self, name: str) -> None:
            self.name = name

        def poll(self) -> None:
            return None

    first = FakeProcess("first")
    second = FakeProcess("second")
    launch_results: list[Any] = [first, second, RuntimeError("primary launch failure")]
    attempted: list[str] = []

    def fake_start_lite() -> Any:
        result = launch_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def fake_terminate(process: FakeProcess) -> None:
        attempted.append(process.name)
        if process is first:
            raise TimeoutError("first cleanup timed out")

    monkeypatch.setattr(sys.modules[__name__], "start_lite", fake_start_lite)
    monkeypatch.setattr(sys.modules[__name__], "terminate_process_tree", fake_terminate)

    with pytest.raises(RuntimeError, match="primary launch failure") as caught:
        run_lite_sessions(["first", "second", "third"])

    assert attempted == ["first", "second"]
    assert caught.value.__notes__ == [
        "Process cleanup failures after primary error: TimeoutError: first cleanup timed out"
    ]


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
        [powershell_path(), "-NoProfile", "-NonInteractive", "-Command", probe],
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
    missing_sdk = tmp_path / "missing Everything64.dll"
    completed = subprocess.run(
        [
            powershell_path(),
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


def test_measurement_script_uses_one_absolute_deadline_across_notifications(tmp_path: Path) -> None:
    helper = tmp_path / "delayed_protocol.py"
    helper.write_text(
        """\
import json
import time

for index in range(8):
    print(json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {"index": index}}), flush=True)
    time.sleep(0.2)
print(json.dumps({"jsonrpc": "2.0", "id": "expected", "result": {}}), flush=True)
""",
        encoding="utf-8",
    )
    body = r"""
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = $env:TASK7_PYTHON
$startInfo.Arguments = '"' + $env:TASK7_PROTOCOL_HELPER + '"'
$startInfo.WorkingDirectory = $env:TASK7_REPO_ROOT
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo
$null = $process.Start()
$session = [pscustomobject]@{
    Backend = 'deadline-test'
    Index = 1
    Process = $process
    Notifications = New-Object System.Collections.ArrayList
    SeenIds = New-Object 'System.Collections.Generic.HashSet[string]'
}
$watch = [System.Diagnostics.Stopwatch]::StartNew()
$timedOut = $false
try {
    $null = Receive-MatchingResponse -Session $session -ExpectedId 'expected' -TimeoutSeconds 1
}
catch {
    $timedOut = $_.Exception.Message -like '*Timed out waiting*'
}
finally {
    $watch.Stop()
    if (-not $process.HasExited) {
        $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        & $taskkill /PID $process.Id /T /F *> $null
        $null = $process.WaitForExit(5000)
    }
}
[pscustomobject]@{
    timedOut = $timedOut
    elapsedMilliseconds = $watch.ElapsedMilliseconds
    notifications = $session.Notifications.Count
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        ["Test-ObjectProperty", "Assert-ValidNotification", "Get-RemainingMilliseconds", "Receive-MatchingResponse"],
        body,
        extra_env={
            "TASK7_PYTHON": sys.executable,
            "TASK7_PROTOCOL_HELPER": str(helper),
            "TASK7_REPO_ROOT": str(ROOT),
        },
        timeout=8,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["timedOut"] is True
    assert 700 <= result["elapsedMilliseconds"] < 1800
    assert result["notifications"] >= 3


def test_measurement_script_rejects_malformed_notifications() -> None:
    body = r"""
$valid = @(
    '{"jsonrpc":"2.0","method":"notifications/progress","params":{}}'
)
$invalid = @(
    '{"jsonrpc":"1.0","method":"notifications/progress"}',
    '{"jsonrpc":"2.0","method":""}',
    '{"jsonrpc":"2.0","method":"notifications/progress","id":1}',
    '{"jsonrpc":"2.0","method":"notifications/progress","result":{}}',
    '{"jsonrpc":"2.0","method":"notifications/progress","error":{}}',
    '{"jsonrpc":"2.0","method":"notifications/progress","params":[]}',
    '{"jsonrpc":"2.0","method":"notifications/progress","params":null}'
)
$accepted = 0
$rejected = 0
foreach ($line in $valid) {
    Assert-ValidNotification -Message ($line | ConvertFrom-Json) -Context 'test'
    $accepted += 1
}
foreach ($line in $invalid) {
    try {
        Assert-ValidNotification -Message ($line | ConvertFrom-Json) -Context 'test'
    }
    catch {
        $rejected += 1
    }
}
[pscustomobject]@{ accepted = $accepted; rejected = $rejected } | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(["Test-ObjectProperty", "Assert-ValidNotification"], body)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {"accepted": 1, "rejected": 7}


def test_measurement_script_bounds_async_pipe_drains() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    assert ".StandardOutput.ReadToEnd()" not in source
    assert re.search(r"\.Wait\(\s*\)", source) is None

    body = r"""
$task = [System.Threading.Tasks.Task]::Delay(5000)
$watch = [System.Diagnostics.Stopwatch]::StartNew()
$completed = Wait-TaskUntilDeadline -Task $task -Deadline ([DateTime]::UtcNow.AddMilliseconds(150))
$watch.Stop()
[pscustomobject]@{
    completed = $completed
    elapsedMilliseconds = $watch.ElapsedMilliseconds
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(["Get-RemainingMilliseconds", "Wait-TaskUntilDeadline"], body)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["completed"] is False
    assert 75 <= result["elapsedMilliseconds"] < 1000


def test_measurement_script_bounds_every_cim_call_with_one_absolute_deadline() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    bounded_cim_source = measurement_function_source("Get-BoundedCimProcess")
    sync_source = measurement_function_source("Sync-OwnedDescendants")
    runtime_source = measurement_function_source("Get-PythonRuntimeChild")
    cleanup_source = measurement_function_source("Stop-ExactOwnedProcessTree")

    assert source.count("Get-CimInstance") == 1
    assert bounded_cim_source.count("Get-CimInstance") == 1
    assert "-OperationTimeoutSec $operationTimeoutSeconds" in bounded_cim_source
    assert bounded_cim_source.count("Get-RemainingMilliseconds") >= 2
    assert "[DateTime]$DeadlineUtc" in sync_source
    assert "Get-CimInstance" not in sync_source
    assert sync_source.count("Get-BoundedCimProcess") >= 2
    assert runtime_source.count("Sync-OwnedDescendants") == runtime_source.count("-DeadlineUtc $deadline")
    assert "-DeadlineUtc $discoveryDeadline" in cleanup_source


def test_bounded_cim_probe_caps_timeout_and_fails_expired_deadlines_promptly() -> None:
    body = r"""
$script:cimCalls = 0
$script:operationTimeouts = New-Object System.Collections.ArrayList
$script:delayMilliseconds = 0
function Get-CimInstance {
    [CmdletBinding()]
    param(
        [string]$ClassName,
        [string]$Filter,
        [uint32]$OperationTimeoutSec
    )
    $script:cimCalls += 1
    $null = $script:operationTimeouts.Add([int]$OperationTimeoutSec)
    if ($script:delayMilliseconds -gt 0) {
        Start-Sleep -Milliseconds $script:delayMilliseconds
    }
    return [pscustomobject]@{ ProcessId = 1; ParentProcessId = 0; Name = 'python.exe' }
}

$bounded = @(Get-BoundedCimProcess -Filter 'ProcessId = 1' `
    -DeadlineUtc ([DateTime]::UtcNow.AddSeconds(4)) -Context 'bounded-test')

$expiredStopwatch = [Diagnostics.Stopwatch]::StartNew()
$expiredFailed = $false
try {
    $null = @(Get-BoundedCimProcess -Filter 'ProcessId = 2' `
        -DeadlineUtc ([DateTime]::UtcNow.AddMilliseconds(-1)) -Context 'expired-test')
}
catch {
    $expiredFailed = $_.Exception.Message -match 'deadline'
}
$expiredStopwatch.Stop()

$script:delayMilliseconds = 1500
$overrunStopwatch = [Diagnostics.Stopwatch]::StartNew()
$overrunFailed = $false
try {
    $null = @(Get-BoundedCimProcess -Filter 'ProcessId = 3' `
        -DeadlineUtc ([DateTime]::UtcNow.AddMilliseconds(1200)) -Context 'overrun-test')
}
catch {
    $overrunFailed = $_.Exception.Message -match 'deadline'
}
$overrunStopwatch.Stop()

[pscustomobject]@{
    boundedCount = $bounded.Count
    cimCalls = $script:cimCalls
    operationTimeouts = @($script:operationTimeouts)
    expiredFailed = $expiredFailed
    expiredMilliseconds = $expiredStopwatch.ElapsedMilliseconds
    overrunFailed = $overrunFailed
    overrunMilliseconds = $overrunStopwatch.ElapsedMilliseconds
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        ["Get-RemainingMilliseconds", "Get-CimOperationTimeoutSeconds", "Get-BoundedCimProcess"],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["boundedCount"] == 1
    assert result["cimCalls"] == 2
    assert result["operationTimeouts"] == [2, 1]
    assert result["expiredFailed"] is True
    assert result["expiredMilliseconds"] < 500
    assert result["overrunFailed"] is True
    assert 1400 <= result["overrunMilliseconds"] < 3000


def test_probe_lifetime_is_locally_owned_and_disposed_inside_outer_cleanup() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    probe_source = measurement_function_source("Invoke-PythonProbe")
    close_source = measurement_function_source("Close-ProbeResources")

    assert "try {" in probe_source and "finally {" in probe_source
    assert "Register-OwnedProcess" in probe_source
    assert "Complete-ProbeDiscovery" in probe_source
    assert "-DeadlineUtc $discoveryDeadline" in probe_source
    assert "Stop-ExactOwnedProcessTree" in probe_source
    assert "$cleanupResult.treeKill" in probe_source
    assert "$cleanupResult.waitResults" in probe_source
    assert "Close-ProbeResources" in probe_source
    assert "ownedEntries" in probe_source
    assert "output = $stdout" in probe_source
    assert ".Dispose()" in close_source
    assert "$null = Invoke-ExactTreeKill" not in probe_source
    assert re.search(
        r"(?ms)\$startedAtUtc\s*=.*?\ntry\s*\{\s*\n\s*\$probe\s*=\s*Invoke-PythonProbe",
        source,
    )


def test_probe_topology_requires_repeated_post_exit_reconciliation_and_rooted_roles() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    probe_source = measurement_function_source("Invoke-PythonProbe")
    sync_source = measurement_function_source("Sync-OwnedDescendants")
    register_source = measurement_function_source("Register-OwnedProcess")
    discovery_source = measurement_function_source("Complete-ProbeDiscovery")
    summary_source = measurement_function_source("New-MarkdownSummary")

    assert "Complete-ProbeDiscovery" in probe_source
    assert "Get-ProbeTopologyEvidence" in probe_source
    assert "IncludeExitedParents" not in source
    assert "-ParentRecord $parent" in sync_source
    assert "parentStartTimeUtcTicks" in register_source
    assert "while (" in discovery_source
    assert "Sync-OwnedDescendants" in discovery_source
    assert "postExitReconciliationPasses" in discovery_source
    assert "stablePassesRequired" in discovery_source
    assert "probeLifecycle.topology.criterion" in summary_source
    assert "probeLifecycle.topology.stablePassesObserved" in summary_source
    assert "probeLifecycle.topology.stablePassesRequired" in summary_source
    assert "probeLifecycle.topology.postExitDiscoveryPasses" in summary_source
    assert "parentStartTimeUtcTicks" in summary_source
    assert "schemaVersion = 6" in source

    body = r"""
$entries = @(
    [pscustomobject]@{
        pid = 101
        parentPid = 0
        parentStartTimeUtcTicks = [long]0
        roles = @('launcher')
        owned = $true
        actionable = $true
        startTimeUtc = '2026-01-01T00:00:00Z'
        startTimeUtcTicks = [long]1
        status = 'exited'
    },
    [pscustomobject]@{
        pid = 102
        parentPid = 101
        parentStartTimeUtcTicks = [long]1
        roles = @('descendant', 'runtime')
        owned = $true
        actionable = $true
        startTimeUtc = '2026-01-01T00:00:01Z'
        startTimeUtcTicks = [long]2
        status = 'exited'
    },
    [pscustomobject]@{
        pid = 103
        parentPid = 101
        parentStartTimeUtcTicks = [long]1
        roles = @('descendant')
        owned = $true
        actionable = $true
        startTimeUtc = '2026-01-01T00:00:01Z'
        startTimeUtcTicks = [long]3
        status = 'exited'
    }
)
$topology = [pscustomobject]@{
    pass = $true
    criterion = 'exact rooted roles plus stable post-exit reconciliation'
    expectedRuntimeChild = $true
    launcherCount = 1
    runtimeCount = 1
    parentChainsRooted = $true
    noUnverifiableObservations = $true
    observationCount = 0
    rootExitObserved = $true
    allOwnedExitedBeforeCleanup = $true
    discoveryScanCount = 4
    ownershipDiscoveryPasses = 2
    postExitDiscoveryPasses = 0
    postExitReconciliationPasses = 2
    stablePassesRequired = 2
    stablePassesObserved = 2
}
$valid = Test-ProbeTopologyComplete -Topology $topology -OwnedEntries $entries

$missingRuntime = @($entries | Where-Object { $_.pid -ne 102 })
$missingRuntimeFails = -not (
    Test-ProbeTopologyComplete -Topology $topology -OwnedEntries $missingRuntime
)

$entries[1].parentPid = 999
$unrootedFails = -not (Test-ProbeTopologyComplete -Topology $topology -OwnedEntries $entries)
$entries[1].parentPid = 101

$entries[1].parentStartTimeUtcTicks = [long]999
$wrongParentIdentityFails = -not (Test-ProbeTopologyComplete -Topology $topology -OwnedEntries $entries)
$entries[1].parentStartTimeUtcTicks = [long]1

$topology.postExitDiscoveryPasses = 1
$postExitDiscoveryFails = -not (Test-ProbeTopologyComplete -Topology $topology -OwnedEntries $entries)
$topology.postExitDiscoveryPasses = 0

$topology.stablePassesObserved = 1
$unstableFails = -not (Test-ProbeTopologyComplete -Topology $topology -OwnedEntries $entries)

[pscustomobject]@{
    valid = $valid
    missingRuntimeFails = $missingRuntimeFails
    unrootedFails = $unrootedFails
    wrongParentIdentityFails = $wrongParentIdentityFails
    postExitDiscoveryFails = $postExitDiscoveryFails
    unstableFails = $unstableFails
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        ["Test-ObjectProperty", "Test-ProbeTopologyComplete"],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    assert all(json.loads(completed.stdout).values())


def test_descendant_is_not_owned_if_exact_parent_exits_before_registration() -> None:
    body = r"""
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = Join-Path $PSHOME 'powershell.exe'
$startInfo.Arguments = '-NoProfile -Command "Start-Sleep -Seconds 30"'
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$child = New-Object System.Diagnostics.Process
$child.StartInfo = $startInfo
$null = $child.Start()
$null = $child.Handle

$registry = New-Object System.Collections.ArrayList
$parent = [pscustomobject]@{
    pid = 424242
    parentPid = 0
    parentStartTimeUtcTicks = [long]0
    startTimeUtcTicks = [long]123
    depth = 0
    roles = @('launcher')
    owned = $true
    actionable = $true
    process = $null
}
$session = [pscustomobject]@{
    Backend = 'pid-reuse-test'
    Index = 1
    OwnedProcesses = New-Object System.Collections.ArrayList
}
$null = $session.OwnedProcesses.Add($parent)
$script:stateCalls = 0
$script:registerCalls = 0
$script:cimCalls = 0

function Get-RemainingMilliseconds { param([DateTime]$Deadline) return 1000 }
function Get-OwnedProcessState {
    param($Record)
    $script:stateCalls += 1
    if ($script:stateCalls -eq 1) {
        return [pscustomobject]@{ status = 'alive-owned' }
    }
    return [pscustomobject]@{ status = 'exited' }
}
function Get-BoundedCimProcess {
    param([string]$Filter, [DateTime]$DeadlineUtc, [string]$Context)
    $script:cimCalls += 1
    if ($Filter -like 'ParentProcessId*') {
        return [pscustomobject]@{ ProcessId = [int]$child.Id; Name = 'powershell.exe' }
    }
    return [pscustomobject]@{
        ProcessId = [int]$child.Id
        ParentProcessId = 424242
        Name = 'powershell.exe'
    }
}
function Register-ProcessObservation { throw 'no observation should be registered' }
function Register-OwnedProcess {
    param($Session, $Process, $Role, $ParentRecord, $Depth, $ImageName, $OwnedRegistry)
    $script:registerCalls += 1
    $Process.Dispose()
    throw 'unsafe ownership registration reached'
}

$failure = $null
try {
    $null = @(Sync-OwnedDescendants -Session $session -OwnedRegistry $registry `
        -DeadlineUtc ([DateTime]::UtcNow.AddSeconds(2)))
}
catch {
    $failure = $_.Exception.Message
}
$child.Refresh()
$childAliveBeforeCleanup = -not $child.HasExited
[pscustomobject]@{
    failedClosed = -not [string]::IsNullOrWhiteSpace($failure)
    registerCalls = $script:registerCalls
    ownedCount = $session.OwnedProcesses.Count
    childAliveBeforeCleanup = $childAliveBeforeCleanup
    cimCalls = $script:cimCalls
} | ConvertTo-Json -Compress

try {
    if (-not $child.HasExited) {
        $child.Kill()
        $null = $child.WaitForExit(2000)
    }
}
finally {
    $child.Dispose()
}
"""
    completed = run_measurement_function_probe(
        ["Get-ExactProcessIdentity", "Sync-OwnedDescendants"],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "failedClosed": True,
        "registerCalls": 0,
        "ownedCount": 1,
        "childAliveBeforeCleanup": True,
        "cimCalls": 1,
    }


def test_post_exit_probe_reconciliation_validates_retained_records_without_discovery() -> None:
    body = r"""
$launcher = [pscustomobject]@{
    pid = 101
    parentPid = 0
    parentStartTimeUtcTicks = [long]0
    startTimeUtcTicks = [long]1
    depth = 0
    roles = @('launcher')
    owned = $true
    actionable = $true
}
$session = [pscustomobject]@{
    Backend = 'post-exit-test'
    Index = 1
    DiscoveryComplete = $false
    ForcedCleanup = $false
    OwnedProcesses = New-Object System.Collections.ArrayList
}
$null = $session.OwnedProcesses.Add($launcher)
$registry = New-Object System.Collections.ArrayList
$script:syncCalls = 0
function Sync-OwnedDescendants {
    param($Session, $OwnedRegistry, [DateTime]$DeadlineUtc, [switch]$BestEffort)
    $script:syncCalls += 1
    throw 'post-exit discovery must not run'
}
function Get-OwnedProcessState {
    param($Record)
    return [pscustomobject]@{ status = 'exited' }
}
$result = Complete-ProbeDiscovery -Session $session -OwnedRegistry $registry `
    -DeadlineUtc ([DateTime]::UtcNow.AddSeconds(2)) -PollMilliseconds 10
[pscustomobject]@{
    pass = $result.pass
    syncCalls = $script:syncCalls
    ownedCount = $session.OwnedProcesses.Count
    stablePasses = $result.stablePassesObserved
    forcedCleanup = $session.ForcedCleanup
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        ["Get-RemainingMilliseconds", "Complete-ProbeDiscovery"],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "pass": True,
        "syncCalls": 0,
        "ownedCount": 1,
        "stablePasses": 2,
        "forcedCleanup": False,
    }


def test_probe_establishes_cleanup_ownership_immediately_after_start() -> None:
    probe_source = measurement_function_source("Invoke-PythonProbe")
    retained_cleanup_source = measurement_function_source("Stop-RetainedProcessHandle")

    start_index = probe_source.index("if (-not $process.Start())")
    started_index = probe_source.index("$processStarted = $true", start_index)
    ownership_index = probe_source.index("$localOwnershipEstablished = $true", started_index)
    setup_index = probe_source.index("Initialize-ProbeOwnershipSession", ownership_index)
    handle_index = probe_source.index("Open-RetainedProcessHandle", ownership_index)
    identity_index = probe_source.index("Get-ExactProcessIdentity", ownership_index)
    registration_index = probe_source.index("Register-OwnedProcess", ownership_index)
    session_construction_index = probe_source.index("$probeSession =", ownership_index)

    assert start_index < started_index < ownership_index
    assert re.search(
        r"(?ms)if \(-not \$process\.Start\(\)\) \{.*?^\s*\}\s*"
        r"\$processStarted = \$true\s*\$localOwnershipEstablished = \$true",
        probe_source,
    )
    assert ownership_index < min(
        session_construction_index,
        setup_index,
        handle_index,
        identity_index,
        registration_index,
    )
    assert "$processStarted" in probe_source
    assert "Stop-RetainedProcessHandle" in probe_source
    assert "$retainedRootCleanup.waitCompleted" in probe_source
    assert "$retainedRootCleanup.exited" in probe_source
    assert ".Kill()" in retained_cleanup_source
    assert ".WaitForExit(" in retained_cleanup_source
    assert "GetProcessById" not in retained_cleanup_source


@pytest.mark.parametrize(
    "failure_stage", ["construction", "session", "handle", "identity", "registration"]
)
def test_probe_setup_failures_kill_wait_and_dispose_retained_process(
    failure_stage: str,
) -> None:
    overrides = {
        "construction": r"""
function New-ProbeOwnershipSession {
    param($Process, $OwnedRegistry)
    $script:probePid = [int]$Process.Id
    throw 'injected session construction failure'
}
""",
        "session": r"""
function Initialize-ProbeOwnershipSession {
    param($Session)
    $script:probePid = [int]$Session.Process.Id
    throw 'injected session setup failure'
}
""",
        "handle": r"""
function Open-RetainedProcessHandle {
    param($Process)
    $script:probePid = [int]$Process.Id
    throw 'injected handle setup failure'
}
""",
        "identity": r"""
function Get-ExactProcessIdentity {
    param($Process)
    $script:probePid = [int]$Process.Id
    throw 'injected identity setup failure'
}
""",
        "registration": r"""
function Register-OwnedProcess {
    param($Session, $Process, $Role, $ParentPid, $Depth, $ImageName, $OwnedRegistry)
    $script:probePid = [int]$Process.Id
    throw 'injected registration setup failure'
}
""",
    }
    body = (
        r"""
$script:probePid = $null
$script:retainedCleanupAttempted = $false
$script:exactCleanupAttempted = $false
$script:cleanupWaitCompleted = $false
$script:closeAttempted = $false

$script:originalRetainedCleanup = ${function:Stop-RetainedProcessHandle}
function Stop-RetainedProcessHandle {
    param($Process, $TimeoutSeconds)
    $script:retainedCleanupAttempted = $true
    $result = & $script:originalRetainedCleanup -Process $Process -TimeoutSeconds $TimeoutSeconds
    $script:cleanupWaitCompleted = $result.waitCompleted -eq $true -and $result.exited -eq $true
    return $result
}

$script:originalExactCleanup = ${function:Stop-ExactOwnedProcessTree}
function Stop-ExactOwnedProcessTree {
    param($Session, $OwnedRegistry, $DiscoveryTimeoutSeconds, $HandleWaitTimeoutSeconds)
    $script:exactCleanupAttempted = $true
    $result = & $script:originalExactCleanup -Session $Session -OwnedRegistry $OwnedRegistry `
        -DiscoveryTimeoutSeconds $DiscoveryTimeoutSeconds -HandleWaitTimeoutSeconds $HandleWaitTimeoutSeconds
    $script:cleanupWaitCompleted = @($result.waitResults | Where-Object { -not $_.waitCompleted }).Count -eq 0
    return $result
}

$script:originalClose = ${function:Close-ProbeResources}
function Close-ProbeResources {
    param($Process, $StdoutTask, $StderrTask, $OwnedRecords)
    $script:closeAttempted = $true
    return & $script:originalClose -Process $Process -StdoutTask $StdoutTask `
        -StderrTask $StderrTask -OwnedRecords $OwnedRecords
}

function Sync-OwnedDescendants {
    param($Session, $OwnedRegistry, [DateTime]$DeadlineUtc, [switch]$IncludeExitedParents, [switch]$BestEffort)
    return @()
}
function Invoke-ExactTreeKill {
    param($RootProcess, $ExpectedStartTimeUtcTicks, $TimeoutSeconds)
    return [pscustomobject]@{
        pass = $false
        invoked = $false
        waitCompleted = $true
        exitCode = $null
        issue = 'disabled by setup-failure regression'
    }
}
"""
        + overrides[failure_stage]
        + r"""
$failure = $null
try {
    $null = Invoke-PythonProbe -Executable $env:TASK7_PROBE_PYTHON `
        -Code 'import time; time.sleep(30)' -FailureMessage 'injected probe' `
        -TimeoutSeconds 5 -CleanupTimeoutSeconds 2
}
catch {
    $failure = $_.Exception.Message
}
$alive = $false
$check = $null
try {
    $check = [Diagnostics.Process]::GetProcessById([int]$script:probePid)
    $check.Refresh()
    $alive = -not $check.HasExited
}
catch [ArgumentException] {
    $alive = $false
}
finally {
    if ($null -ne $check) {
        $check.Dispose()
    }
}
[pscustomobject]@{
    failed = -not [string]::IsNullOrWhiteSpace($failure)
    cleanupReported = $failure -match 'cleanup'
    cleanupAttempted = $script:retainedCleanupAttempted -or $script:exactCleanupAttempted
    cleanupWaitCompleted = $script:cleanupWaitCompleted
    closeAttempted = $script:closeAttempted
    alive = $alive
} | ConvertTo-Json -Compress
"""
    )
    completed = run_measurement_function_probe(
        [
            "Test-ObjectProperty",
            "Get-RemainingMilliseconds",
            "Wait-TaskUntilDeadline",
            "Wait-ProcessUntilDeadline",
            "Open-RetainedProcessHandle",
            "New-ProbeOwnershipSession",
            "Initialize-ProbeOwnershipSession",
            "Get-ExactProcessIdentity",
            "Register-OwnedProcess",
            "Get-OwnedProcessState",
            "Stop-RetainedProcessHandle",
            "Stop-ExactOwnedRecord",
            "Stop-ExactOwnedProcessTree",
            "Close-ProbeResources",
            "Invoke-PythonProbe",
        ],
        body,
        extra_env={"TASK7_PROBE_PYTHON": getattr(sys, "_base_executable", sys.executable)},
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "failed": True,
        "cleanupReported": True,
        "cleanupAttempted": True,
        "cleanupWaitCompleted": True,
        "closeAttempted": True,
        "alive": False,
    }


def test_measurement_session_establishes_cleanup_ownership_immediately_after_start() -> None:
    start_source = measurement_function_source("Start-McpSession")
    close_source = measurement_function_source("Close-SessionStartResources")

    start_index = start_source.index("if (-not $process.Start())")
    started_index = start_source.index("$processStarted = $true", start_index)
    ownership_index = start_source.index("$localOwnershipEstablished = $true", started_index)
    registry_index = start_source.index("Register-ActiveSessionOwnership", ownership_index)
    handle_index = start_source.index("Open-RetainedProcessHandle", ownership_index)
    identity_index = start_source.index("Get-ExactProcessIdentity", ownership_index)
    registration_index = start_source.index("Register-OwnedProcess", ownership_index)

    assert start_index < started_index < ownership_index
    assert re.search(
        r"(?ms)if \(-not \$process\.Start\(\)\) \{.*?^\s*\}\s*"
        r"\$processStarted = \$true\s*\$localOwnershipEstablished = \$true",
        start_source,
    )
    assert ownership_index < min(registry_index, handle_index, identity_index, registration_index)
    assert "Stop-RetainedProcessHandle" in start_source
    assert "$startCleanup.waitCompleted" in start_source
    assert "$startCleanup.exited" in start_source
    assert "finally" in start_source
    assert "StandardInput" in close_source
    assert ".Dispose()" in close_source


@pytest.mark.parametrize(
    "failure_stage", ["console", "registry", "handle", "identity", "registration"]
)
def test_measurement_session_setup_failures_kill_wait_and_dispose_retained_process(
    failure_stage: str,
) -> None:
    overrides = {
        "console": r"""
function Set-ConsoleInputEncoding {
    param($Encoding, $StartedProcess = $null)
    if ($null -ne $StartedProcess) {
        $script:sessionPid = [int]$StartedProcess.Id
        throw 'injected console restoration failure'
    }
}
""",
        "registry": r"""
function Register-ActiveSessionOwnership {
    param($Registry, $Session)
    $script:sessionPid = [int]$Session.Process.Id
    throw 'injected active-registry failure'
}
""",
        "handle": r"""
function Open-RetainedProcessHandle {
    param($Process)
    $script:sessionPid = [int]$Process.Id
    throw 'injected measurement handle failure'
}
""",
        "identity": r"""
function Get-ExactProcessIdentity {
    param($Process)
    $script:sessionPid = [int]$Process.Id
    throw 'injected measurement identity failure'
}
""",
        "registration": r"""
function Register-OwnedProcess {
    param($Session, $Process, $Role, $ParentPid, $Depth, $ImageName, $OwnedRegistry)
    $script:sessionPid = [int]$Process.Id
    throw 'injected measurement registration failure'
}
""",
    }
    body = (
        r"""
$script:sessionPid = $null
$script:cleanupAttempted = $false
$script:cleanupWaitCompleted = $false
$script:closeAttempted = $false

$script:originalCleanup = ${function:Stop-RetainedProcessHandle}
function Stop-RetainedProcessHandle {
    param($Process, $TimeoutSeconds)
    $script:cleanupAttempted = $true
    $result = & $script:originalCleanup -Process $Process -TimeoutSeconds $TimeoutSeconds
    $script:cleanupWaitCompleted = $result.waitCompleted -eq $true -and $result.exited -eq $true
    return $result
}

$script:originalClose = ${function:Close-SessionStartResources}
function Close-SessionStartResources {
    param($Session, $Process, $OwnedRecords)
    $script:closeAttempted = $true
    return & $script:originalClose -Session $Session -Process $Process -OwnedRecords $OwnedRecords
}
"""
        + overrides[failure_stage]
        + r"""
$active = New-Object System.Collections.ArrayList
$owned = New-Object System.Collections.ArrayList
$failure = $null
try {
    $null = Start-McpSession -Backend 'injected' -Module 'everything_mcp.lite_stdio' -Index 1 `
        -Executable $env:TASK7_PROBE_PYTHON -WorkingDirectory $env:TASK7_REPO_ROOT `
        -SdkPath $env:TASK7_MEASUREMENT_SCRIPT -ExpectRuntimeChild $false `
        -ProcessRegistry $active -OwnedRegistry $owned
}
catch {
    $failure = $_.Exception.Message
}
$alive = $false
$check = $null
try {
    $check = [Diagnostics.Process]::GetProcessById([int]$script:sessionPid)
    $check.Refresh()
    $alive = -not $check.HasExited
}
catch [ArgumentException] {
    $alive = $false
}
finally {
    if ($null -ne $check) {
        $check.Dispose()
    }
}
[pscustomobject]@{
    pid = $script:sessionPid
    failure = $failure
    failed = -not [string]::IsNullOrWhiteSpace($failure)
    cleanupReported = $failure -match 'cleanup'
    cleanupAttempted = $script:cleanupAttempted
    cleanupWaitCompleted = $script:cleanupWaitCompleted
    closeAttempted = $script:closeAttempted
    activeRegistryEmpty = $active.Count -eq 0
    alive = $alive
} | ConvertTo-Json -Compress
"""
    )
    completed = run_measurement_function_probe(
        [
            "Test-ObjectProperty",
            "Set-ConsoleInputEncoding",
            "Open-RetainedProcessHandle",
            "Register-ActiveSessionOwnership",
            "Get-ExactProcessIdentity",
            "Register-OwnedProcess",
            "Stop-RetainedProcessHandle",
            "Close-SessionStartResources",
            "Start-McpSession",
        ],
        body,
        extra_env={
            "TASK7_PROBE_PYTHON": getattr(sys, "_base_executable", sys.executable),
            "TASK7_REPO_ROOT": str(ROOT),
        },
        timeout=15,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["pid"] is not None, result
    assert result.pop("pid") > 0
    assert "injected" in result.pop("failure")
    assert result == {
        "failed": True,
        "cleanupReported": True,
        "cleanupAttempted": True,
        "cleanupWaitCompleted": True,
        "closeAttempted": True,
        "activeRegistryEmpty": True,
        "alive": False,
    }


def test_measurement_cleanup_has_no_delayed_pid_tree_kill_helper() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    cleanup_source = measurement_function_source("Stop-ExactOwnedProcessTree")
    record_cleanup_source = measurement_function_source("Stop-ExactOwnedRecord")

    assert "taskkill.exe" not in source.lower()
    assert "function Invoke-ExactTreeKill" not in source
    assert "Invoke-ExactTreeKill" not in cleanup_source
    assert "retained-handles-child-first" in cleanup_source
    assert "IncludeExitedParents" not in cleanup_source
    assert "Sync-OwnedDescendants" in cleanup_source
    assert "Sort-Object depth -Descending" in cleanup_source
    assert "stableReconciliationPasses" in cleanup_source
    assert "Stop-ExactOwnedRecord" in cleanup_source
    assert ".Kill()" in record_cleanup_source
    assert ".WaitForExit(" in record_cleanup_source
    assert "GetProcessById" not in record_cleanup_source


def test_exact_cleanup_continues_after_bounded_reconciliation_failure() -> None:
    body = r"""
$startInfo = New-Object System.Diagnostics.ProcessStartInfo
$startInfo.FileName = Join-Path $PSHOME 'powershell.exe'
$startInfo.Arguments = '-NoProfile -Command "Start-Sleep -Seconds 30"'
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$process = New-Object System.Diagnostics.Process
$process.StartInfo = $startInfo
$null = $process.Start()
$process.EnableRaisingEvents = $true
$null = $process.Handle

$registry = New-Object System.Collections.ArrayList
$session = [pscustomobject]@{
    Backend = 'probe-cleanup-test'
    Index = 1
    Process = $process
    OwnedRegistry = $registry
    OwnedProcesses = New-Object System.Collections.ArrayList
    ForcedCleanup = $false
    DiscoveryComplete = $true
}
$null = Register-OwnedProcess -Session $session -Process $process -Role 'launcher' -OwnedRegistry $registry
$script:syncDeadline = $null
function Sync-OwnedDescendants {
    param(
        $Session,
        $OwnedRegistry,
        [DateTime]$DeadlineUtc,
        [switch]$IncludeExitedParents,
        [switch]$BestEffort
    )
    $script:syncDeadline = $DeadlineUtc
    throw 'simulated bounded reconciliation failure'
}

try {
    $result = Stop-ExactOwnedProcessTree -Session $session -OwnedRegistry $registry `
        -DiscoveryTimeoutSeconds 1 -HandleWaitTimeoutSeconds 2
    $process.Refresh()
    [pscustomobject]@{
        pass = $result.pass
        discoveryComplete = $result.discoveryComplete
        syncDeadlineProvided = $null -ne $script:syncDeadline
        treeKillPass = $result.treeKill.pass
        treeKillInvoked = $result.treeKill.invoked
        treeKillMethod = $result.treeKill.method
        fallbackPids = @($result.fallbackAttemptedPids)
        waitResultCount = @($result.waitResults).Count
        allWaitsCompleted = @($result.waitResults | Where-Object { -not $_.waitCompleted }).Count -eq 0
        verificationStatuses = @($result.verification | ForEach-Object { $_.status })
        issues = @($result.issues)
        exited = $process.HasExited
        forcedCleanup = $session.ForcedCleanup
    } | ConvertTo-Json -Depth 10 -Compress
}
finally {
    try {
        $process.Refresh()
        if (-not $process.HasExited) {
            $process.Kill()
            $null = $process.WaitForExit(2000)
        }
    }
    catch { }
    $process.Dispose()
}
"""
    completed = run_measurement_function_probe(
        [
            "Get-RemainingMilliseconds",
            "Get-ExactProcessIdentity",
            "Register-OwnedProcess",
            "Get-OwnedProcessState",
            "Stop-ExactOwnedRecord",
            "Stop-ExactOwnedProcessTree",
        ],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["pass"] is False
    assert result["discoveryComplete"] is False
    assert result["syncDeadlineProvided"] is True
    assert result["treeKillPass"] is True
    assert result["treeKillInvoked"] is False
    assert result["treeKillMethod"] == "retained-handles-child-first"
    assert len(result["fallbackPids"]) == 1
    assert result["waitResultCount"] == 1
    assert result["allWaitsCompleted"] is True
    assert result["verificationStatuses"] == ["exited"]
    assert result["exited"] is True
    assert result["forcedCleanup"] is True
    assert any("could not be proven" in issue for issue in result["issues"])


def test_probe_resource_closer_disposes_streams_tasks_and_unique_handles() -> None:
    body = r"""
$script:disposed = @{}
function New-Disposable {
    param([string]$Name, [bool]$IsCompleted = $true)
    $value = [pscustomobject]@{ Name = $Name; IsCompleted = $IsCompleted }
    $value | Add-Member -MemberType ScriptMethod -Name Dispose -Value {
        if (-not $script:disposed.ContainsKey($this.Name)) {
            $script:disposed[$this.Name] = 0
        }
        $script:disposed[$this.Name] += 1
    }
    return $value
}
$stdout = New-Disposable -Name 'stdout'
$stderr = New-Disposable -Name 'stderr'
$root = New-Disposable -Name 'root'
$root | Add-Member -NotePropertyName StandardOutput -NotePropertyValue $stdout
$root | Add-Member -NotePropertyName StandardError -NotePropertyValue $stderr
$child = New-Disposable -Name 'child'
$stdoutTask = New-Disposable -Name 'stdoutTask'
$stderrTask = New-Disposable -Name 'stderrTask'
$records = @(
    [pscustomobject]@{ owned = $true; process = $root },
    [pscustomobject]@{ owned = $true; process = $child }
)
$result = Close-ProbeResources -Process $root -StdoutTask $stdoutTask -StderrTask $stderrTask `
    -OwnedRecords $records
[pscustomobject]@{
    pass = $result.pass
    issues = @($result.issues)
    stdout = $script:disposed.stdout
    stderr = $script:disposed.stderr
    stdoutTask = $script:disposed.stdoutTask
    stderrTask = $script:disposed.stderrTask
    root = $script:disposed.root
    child = $script:disposed.child
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(["Close-ProbeResources"], body)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result == {
        "pass": True,
        "issues": [],
        "stdout": 1,
        "stderr": 1,
        "stdoutTask": 1,
        "stderrTask": 1,
        "root": 1,
        "child": 1,
    }


def test_probe_lifecycle_contract_is_fail_closed_and_joined_to_pid_recheck() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    assert "probeLifecycle" in source
    assert "$allAuditEntries" in source
    assert "Invoke-IndependentPidRecheck -AuditEntries @($allAuditEntries)" in source

    body = r"""
$lifecycle = [pscustomobject]@{
    pass = $true
    exitCode = [int]0
    forcedCleanup = $false
    discoveryComplete = $true
    ownedCount = 1
    ownedEntries = @(
        [pscustomobject]@{
            pid = 101
            parentPid = 0
            parentStartTimeUtcTicks = [long]0
            roles = @('launcher', 'runtime')
            startTimeUtc = '2026-01-01T00:00:00Z'
            startTimeUtcTicks = [long]1
            status = 'exited'
        }
    )
    treeKill = [pscustomobject]@{ pass = $true; invoked = $false; waitCompleted = $true }
    topology = [pscustomobject]@{
        pass = $true
        criterion = 'exact rooted roles plus stable post-exit reconciliation'
        expectedRuntimeChild = $false
        launcherCount = 1
        runtimeCount = 1
        parentChainsRooted = $true
        noUnverifiableObservations = $true
        observationCount = 0
        rootExitObserved = $true
        allOwnedExitedBeforeCleanup = $true
        discoveryScanCount = 3
        ownershipDiscoveryPasses = 1
        postExitDiscoveryPasses = 0
        postExitReconciliationPasses = 2
        stablePassesRequired = 2
        stablePassesObserved = 2
    }
    fallbackAttemptedPids = @()
    waitResults = @([pscustomobject]@{ pid = 101; waitCompleted = $true })
    verification = @([pscustomobject]@{ pid = 101; startTimeUtcTicks = [long]1; status = 'exited' })
    resourceDisposal = [pscustomobject]@{ pass = $true; disposedProcessHandleCount = 1 }
}
$valid = Test-ProbeLifecycleComplete -Lifecycle $lifecycle
$lifecycle.exitCode = $null
$nullExitFails = -not (Test-ProbeLifecycleComplete -Lifecycle $lifecycle)
$lifecycle.exitCode = [int]0
$lifecycle.ownedEntries[0].startTimeUtcTicks = $null
$nullIdentityFails = -not (Test-ProbeLifecycleComplete -Lifecycle $lifecycle)
$lifecycle.ownedEntries[0].startTimeUtcTicks = [long]1
$lifecycle.resourceDisposal.pass = $false
$disposalFails = -not (Test-ProbeLifecycleComplete -Lifecycle $lifecycle)
[pscustomobject]@{
    valid = $valid
    nullExitFails = $nullExitFails
    nullIdentityFails = $nullIdentityFails
    disposalFails = $disposalFails
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        [
            "Test-ObjectProperty",
            "Test-ProbeTopologyComplete",
            "Test-ProbeLifecycleComplete",
        ],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    assert all(json.loads(completed.stdout).values())


def test_measurement_script_owns_and_cleans_only_exact_process_trees() -> None:
    measurement_source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    test_source = Path(__file__).read_text(encoding="utf-8")
    lowered = measurement_source.lower()

    for function_name in (
        "Register-OwnedProcess",
        "Sync-OwnedDescendants",
        "Stop-ExactOwnedProcessTree",
        "Invoke-OwnedPidAudit",
    ):
        assert f"function {function_name}" in measurement_source
    assert "RuntimeProcess = $null" in measurement_source
    assert re.search(r"(?m)^\s*RuntimeProcess = \$process\s*$", measurement_source) is None
    assert "taskkill.exe" not in lowered
    assert "function invoke-exacttreekill" not in lowered
    assert "stop-exactownedrecord" in lowered
    assert "retained-handles-child-first" in lowered
    assert "/im" not in lowered
    assert "get-process -name" not in lowered
    assert "stop-process -name" not in lowered
    assert "def terminate_process_tree" in test_source
    assert '"taskkill.exe", "/PID", str(process.pid), "/T", "/F"' in test_source
    assert "timeout=10" in test_source
    assert "process.wait(timeout=5)" in test_source


def test_measurement_script_does_not_bind_the_read_only_pid_automatic_variable() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    register_function = re.search(
        r"function Register-OwnedProcess\s*\{(?P<body>.*?)\n\}",
        source,
        flags=re.DOTALL,
    )
    assert register_function is not None
    assert re.search(r"(?i)\[int\]\$pid\b", register_function.group("body")) is None


def test_measurement_script_requires_exact_handle_backed_owned_process_identity() -> None:
    body = r"""
$registry = New-Object System.Collections.ArrayList
$session = [pscustomobject]@{
    Backend = 'registry-test'
    Index = 1
    OwnedProcesses = New-Object System.Collections.ArrayList
}
$process = [System.Diagnostics.Process]::GetCurrentProcess()
$record = Register-OwnedProcess -Session $session -Process $process -Role 'launcher' -OwnedRegistry $registry
$state = Get-OwnedProcessState -Record $record
$recordedTicks = $record.startTimeUtcTicks
$sameRecord = Register-OwnedProcess -Session $session -Process $process -Role 'runtime' -OwnedRegistry $registry
$record.startTimeUtcTicks = $recordedTicks + 1
$mismatchedState = Get-OwnedProcessState -Record $record
[pscustomobject]@{
    registryCount = $registry.Count
    sessionCount = $session.OwnedProcesses.Count
    pid = $sameRecord.pid
    roles = @($sameRecord.roles)
    owned = $sameRecord.owned
    actionable = $sameRecord.actionable
    startTimeUtc = $sameRecord.startTimeUtc
    recordedTicks = $recordedTicks
    exactState = $state.status
    mismatchedState = $mismatchedState.status
    sameHandle = [object]::ReferenceEquals($sameRecord.process, $process)
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        ["Get-ExactProcessIdentity", "Register-OwnedProcess", "Get-OwnedProcessState"],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["registryCount"] == 1
    assert result["sessionCount"] == 1
    assert result["pid"] > 0
    assert set(result["roles"]) == {"launcher", "runtime"}
    assert result["owned"] is True
    assert result["actionable"] is True
    assert result["startTimeUtc"]
    assert result["recordedTicks"] > 0
    assert result["exactState"] == "alive-owned"
    assert result["mismatchedState"] == "pid-reused"
    assert result["sameHandle"] is True


def test_measurement_script_never_fuzzily_matches_or_reopens_pids_for_cleanup() -> None:
    register_source = measurement_function_source("Register-OwnedProcess")
    sync_source = measurement_function_source("Sync-OwnedDescendants")
    state_source = measurement_function_source("Get-OwnedProcessState")
    cleanup_source = measurement_function_source("Stop-ExactOwnedProcessTree")
    record_cleanup_source = measurement_function_source("Stop-ExactOwnedRecord")
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")

    assert "CimProcess" not in register_source
    assert "startTimeUtcTicks" in register_source
    assert "TotalMilliseconds" not in register_source
    assert "TotalMilliseconds" not in state_source
    assert "Get-CimInstance" not in state_source
    assert sync_source.count("GetProcessById") == 1
    assert sync_source.index("GetProcessById") < sync_source.index("Register-OwnedProcess")
    assert sync_source.index(".Handle") < sync_source.index("Register-OwnedProcess")
    assert "GetProcessById" not in cleanup_source
    assert "GetProcessById" not in record_cleanup_source
    assert "$Record.process.Kill()" in record_cleanup_source
    assert "Get-OwnedProcessState" in record_cleanup_source
    assert "function Invoke-ExactTreeKill" not in source


def test_measurement_script_rejects_invalid_memory_evidence_before_arithmetic() -> None:
    body = r"""
function New-Checkpoint {
    param([long[]]$WorkingSets, [long[]]$PrivateBytes)
    $perProcess = @()
    for ($index = 0; $index -lt $WorkingSets.Count; $index++) {
        $perProcess += [pscustomobject]@{
            session = $index + 1
            workingSetBytes = $WorkingSets[$index]
            privateBytes = $PrivateBytes[$index]
        }
    }
    return [pscustomobject]@{
        perProcess = @($perProcess)
        aggregate = [pscustomobject]@{
            workingSetBytes = [long](($WorkingSets | Measure-Object -Sum).Sum)
            privateBytes = [long](($PrivateBytes | Measure-Object -Sum).Sum)
        }
    }
}
function New-BackendRun {
    return [pscustomobject]@{
        backend = 'memory-test'
        memory = [pscustomobject]@{
            afterFirstSearch = New-Checkpoint -WorkingSets @(101, 102) -PrivateBytes @(51, 52)
            postIdle = New-Checkpoint -WorkingSets @(99, 100) -PrivateBytes @(49, 50)
        }
    }
}

$valid = Assert-ValidMemoryEvidence -BackendRun (New-BackendRun) -ExpectedSessions 2
$rejected = New-Object System.Collections.ArrayList
$unexpected = New-Object System.Collections.ArrayList
$cases = @(
    [pscustomobject]@{ name = 'nullAggregate'; mutate = { param($run) $run.memory.afterFirstSearch.aggregate.privateBytes = $null } },
    [pscustomobject]@{ name = 'nullPerProcess'; mutate = { param($run) $run.memory.postIdle.perProcess[0].privateBytes = $null } },
    [pscustomobject]@{ name = 'wrongCardinality'; mutate = { param($run) $run.memory.afterFirstSearch.perProcess = @($run.memory.afterFirstSearch.perProcess[0]) } },
    [pscustomobject]@{ name = 'badSum'; mutate = { param($run) $run.memory.postIdle.aggregate.workingSetBytes += 1 } },
    [pscustomobject]@{ name = 'zero'; mutate = { param($run) $run.memory.afterFirstSearch.perProcess[0].workingSetBytes = 0 } },
    [pscustomobject]@{ name = 'negative'; mutate = { param($run) $run.memory.afterFirstSearch.perProcess[0].privateBytes = -1 } },
    [pscustomobject]@{ name = 'nan'; mutate = { param($run) $run.memory.postIdle.perProcess[0].privateBytes = [double]::NaN } },
    [pscustomobject]@{ name = 'string'; mutate = { param($run) $run.memory.postIdle.aggregate.privateBytes = 'garbage' } },
    [pscustomobject]@{ name = 'missing'; mutate = { param($run) $run.memory.afterFirstSearch.aggregate.PSObject.Properties.Remove('privateBytes') } },
    [pscustomobject]@{ name = 'duplicateSession'; mutate = { param($run) $run.memory.postIdle.perProcess[1].session = 1 } }
)
foreach ($case in $cases) {
    $run = New-BackendRun
    & $case.mutate $run
    try {
        $null = Assert-ValidMemoryEvidence -BackendRun $run -ExpectedSessions 2
        $null = $unexpected.Add($case.name)
    }
    catch {
        $null = $rejected.Add($case.name)
    }
}
[pscustomobject]@{
    validPass = $valid.pass
    validCheckpoints = @($valid.checkpoints).Count
    rejected = @($rejected)
    unexpected = @($unexpected)
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        [
            "Test-ObjectProperty",
            "ConvertTo-RequiredPositiveInt64",
            "Assert-ValidMemoryCheckpoint",
            "Assert-ValidMemoryEvidence",
        ],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["validPass"] is True
    assert result["validCheckpoints"] == 2
    assert set(result["rejected"]) == {
        "nullAggregate",
        "nullPerProcess",
        "wrongCardinality",
        "badSum",
        "zero",
        "negative",
        "nan",
        "string",
        "missing",
        "duplicateSession",
    }
    assert result["unexpected"] == []


def test_pid_recheck_json_uses_consistent_true_empty_arrays() -> None:
    body = r"""
$entries = @(
    [pscustomobject]@{
        pid = 42
        recordedStartTimeUtcTicks = 123
        currentStartTimeUtcTicks = $null
        status = 'not-present'
    }
)
New-PidRecheckResult -Entries $entries | ConvertTo-Json -Depth 10 -Compress
"""
    completed = run_measurement_function_probe(["New-PidRecheckResult"], body)

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["checkedPids"] == [42]
    assert result["checkedCount"] == len(result["checkedPids"]) == 1
    for field, count_field in (
        ("aliveOwnedPids", "aliveOwnedCount"),
        ("reusedPids", "reusedCount"),
        ("unreadablePids", "unreadableCount"),
    ):
        assert isinstance(result[field], list)
        assert result[field] == []
        assert result[count_field] == len(result[field]) == 0
    assert result["entries"] == [
        {
            "pid": 42,
            "recordedStartTimeUtcTicks": 123,
            "currentStartTimeUtcTicks": None,
            "status": "not-present",
        }
    ]
    assert result["pass"] is True


def test_measurement_evidence_contract_fails_closed_on_exit_codes_and_pid_audit() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    for field in (
        "scriptSha256",
        "gitHead",
        "gitTree",
        "responseDeadlineMode",
        "exitCodesComplete",
        "processAudit",
        "memoryValidation",
        "probeLifecycle",
        "independentPidRecheck",
        "pidRecheckPath",
        "overallComponents",
    ):
        assert field in source

    body = r"""
$session = [pscustomobject]@{ launcherExitCode = [int]0; runtimeExitCode = [int]0 }
$validExitCodes = Test-SessionExitCodesComplete -SessionEvidence $session
$session.runtimeExitCode = $null
$nullRuntimeFails = -not (Test-SessionExitCodesComplete -SessionEvidence $session)
$session.runtimeExitCode = [int]1
$nonzeroRuntimeFails = -not (Test-SessionExitCodesComplete -SessionEvidence $session)

$validAudit = [pscustomobject]@{
    pass = $true
    checkedPids = @(101, 102)
    checkedCount = 2
    alivePids = @()
    aliveCount = 0
    reusedPids = @()
    reusedCount = 0
    unreadablePids = @()
    unreadableCount = 0
    ownedCount = 2
    observationCount = 0
    ownedIdentitiesComplete = $true
    ownedEdgesComplete = $true
    entries = @(
        [pscustomobject]@{ pid = 101; parentPid = 0; parentStartTimeUtcTicks = [long]0; owned = $true; actionable = $true; startTimeUtc = '2026-01-01T00:00:00Z'; startTimeUtcTicks = [long]1; status = 'exited' },
        [pscustomobject]@{ pid = 102; parentPid = 101; parentStartTimeUtcTicks = [long]1; owned = $true; actionable = $true; startTimeUtc = '2026-01-01T00:00:01Z'; startTimeUtcTicks = [long]2; status = 'exited' }
    )
}
$validPidAudit = Test-ProcessAuditComplete -Audit $validAudit
$validAudit.alivePids = @(102)
$alivePidFails = -not (Test-ProcessAuditComplete -Audit $validAudit)
$validAudit.alivePids = @()
$validAudit.entries[0].startTimeUtcTicks = $null
$missingIdentityFails = -not (Test-ProcessAuditComplete -Audit $validAudit)
$validAudit.entries[0].startTimeUtcTicks = [long]1
$validAudit.ownedEdgesComplete = $false
$missingEdgeFails = -not (Test-ProcessAuditComplete -Audit $validAudit)
$validAudit.ownedEdgesComplete = $true
$validAudit.reusedPids = @(101)
$validAudit.reusedCount = 1
$reusedPidFails = -not (Test-ProcessAuditComplete -Audit $validAudit)
$validAudit.reusedPids = @()
$validAudit.reusedCount = 0
$validAudit.checkedCount = 1
$badAuditCountFails = -not (Test-ProcessAuditComplete -Audit $validAudit)
$validAudit.checkedCount = 2
$validAudit.checkedPids = $null
$missingAuditFails = -not (Test-ProcessAuditComplete -Audit $validAudit)

$validRecheck = [pscustomobject]@{
    pass = $true
    checkedPids = @(101, 102)
    checkedCount = 2
    aliveOwnedPids = @()
    aliveOwnedCount = 0
    reusedPids = @()
    reusedCount = 0
    unreadablePids = @()
    unreadableCount = 0
    entries = @(
        [pscustomobject]@{ pid = 101; recordedStartTimeUtcTicks = [long]1; status = 'not-present' },
        [pscustomobject]@{ pid = 102; recordedStartTimeUtcTicks = [long]2; status = 'not-present' }
    )
}
$validPidRecheck = Test-IndependentPidRecheckComplete -Recheck $validRecheck
$validRecheck.unreadablePids = @(102)
$validRecheck.unreadableCount = 1
$unreadableRecheckFails = -not (Test-IndependentPidRecheckComplete -Recheck $validRecheck)
$validRecheck.unreadablePids = @()
$validRecheck.unreadableCount = 0
$validRecheck.entries[0].recordedStartTimeUtcTicks = $null
$missingRecheckIdentityFails = -not (Test-IndependentPidRecheckComplete -Recheck $validRecheck)

$components = [pscustomobject]@{
    liteProtocolAccountingCleanup = $true
    fastMcpProtocolAccountingCleanup = $true
    postRunPidAudit = $true
    probeLifecycleCleanup = $true
    independentPidRecheck = $true
    exitCodeCompleteness = $true
    memoryEvidenceValidation = $true
    memoryStability = $true
    liteFastMcpIdleRatio = $true
    evidenceIntegrity = $true
}
$validOverall = Test-AllExplicitPassComponents -Components $components
$components.memoryEvidenceValidation = $null
$missingOverallFails = -not (Test-AllExplicitPassComponents -Components $components)
$components.memoryEvidenceValidation = $true
$components.independentPidRecheck = $null
$missingRecheckFails = -not (Test-AllExplicitPassComponents -Components $components)
$components.independentPidRecheck = $true
$components.probeLifecycleCleanup = $null
$missingProbeLifecycleFails = -not (Test-AllExplicitPassComponents -Components $components)

[pscustomobject]@{
    validExitCodes = $validExitCodes
    nullRuntimeFails = $nullRuntimeFails
    nonzeroRuntimeFails = $nonzeroRuntimeFails
    validPidAudit = $validPidAudit
    alivePidFails = $alivePidFails
    missingIdentityFails = $missingIdentityFails
    missingEdgeFails = $missingEdgeFails
    reusedPidFails = $reusedPidFails
    badAuditCountFails = $badAuditCountFails
    missingAuditFails = $missingAuditFails
    validPidRecheck = $validPidRecheck
    unreadableRecheckFails = $unreadableRecheckFails
    missingRecheckIdentityFails = $missingRecheckIdentityFails
    validOverall = $validOverall
    missingOverallFails = $missingOverallFails
    missingRecheckFails = $missingRecheckFails
    missingProbeLifecycleFails = $missingProbeLifecycleFails
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        [
            "Test-ObjectProperty",
            "Test-SessionExitCodesComplete",
            "Test-ProcessAuditComplete",
            "Test-IndependentPidRecheckComplete",
            "Test-AllExplicitPassComponents",
        ],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    assert all(json.loads(completed.stdout).values())


def test_owned_task7_files_contain_no_personal_machine_paths() -> None:
    combined = MEASUREMENT_SCRIPT.read_text(encoding="utf-8") + Path(__file__).read_text(encoding="utf-8")
    lowered = combined.lower()
    assert "c:\\users\\" not in lowered
    assert "jmt" + "fam" not in lowered
