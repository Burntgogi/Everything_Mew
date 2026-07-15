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
    if os.name == "nt":
        subprocess.run(
            ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    else:
        process.kill()
    process.wait(timeout=5)


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
                terminate_process_tree(process)


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
    assert "taskkill.exe" in lowered
    assert "/pid" in lowered
    assert "/t" in lowered
    assert "/im" not in lowered
    assert "get-process -name" not in lowered
    assert "stop-process -name" not in lowered
    assert "def terminate_process_tree" in test_source
    assert '"taskkill.exe", "/PID", str(process.pid), "/T", "/F"' in test_source


def test_measurement_script_does_not_bind_the_read_only_pid_automatic_variable() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    register_function = re.search(
        r"function Register-OwnedProcess\s*\{(?P<body>.*?)\n\}",
        source,
        flags=re.DOTALL,
    )
    assert register_function is not None
    assert re.search(r"(?i)\[int\]\$pid\b", register_function.group("body")) is None


def test_measurement_script_accepts_an_initially_empty_owned_process_registry() -> None:
    body = r"""
$registry = New-Object System.Collections.ArrayList
$session = [pscustomobject]@{
    Backend = 'registry-test'
    Index = 1
    OwnedProcesses = New-Object System.Collections.ArrayList
}
$process = [System.Diagnostics.Process]::GetCurrentProcess()
$record = Register-OwnedProcess -Session $session -Process $process -CimProcess $null `
    -ProcessId $process.Id -Role 'launcher' -OwnedRegistry $registry
$record.startTimeUtc = ([DateTime]::Parse($record.startTimeUtc).AddMilliseconds(-0.5)).ToString('o')
$sameRecord = Register-OwnedProcess -Session $session -Process $process -CimProcess $null `
    -ProcessId $process.Id -Role 'runtime' -OwnedRegistry $registry
[pscustomobject]@{
    registryCount = $registry.Count
    sessionCount = $session.OwnedProcesses.Count
    pid = $sameRecord.pid
    roles = @($sameRecord.roles)
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        ["Get-ProcessStartTimeUtc", "Register-OwnedProcess"],
        body,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["registryCount"] == 1
    assert result["sessionCount"] == 1
    assert result["pid"] > 0
    assert set(result["roles"]) == {"launcher", "runtime"}


def test_measurement_evidence_contract_fails_closed_on_exit_codes_and_pid_audit() -> None:
    source = MEASUREMENT_SCRIPT.read_text(encoding="utf-8")
    for field in (
        "scriptSha256",
        "gitHead",
        "gitTree",
        "responseDeadlineMode",
        "exitCodesComplete",
        "processAudit",
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
    alivePids = @()
    entries = @(
        [pscustomobject]@{ pid = 101; status = 'exited' },
        [pscustomobject]@{ pid = 102; status = 'exited' }
    )
}
$validPidAudit = Test-ProcessAuditComplete -Audit $validAudit
$validAudit.alivePids = @(102)
$alivePidFails = -not (Test-ProcessAuditComplete -Audit $validAudit)
$validAudit.alivePids = @()
$validAudit.checkedPids = $null
$missingAuditFails = -not (Test-ProcessAuditComplete -Audit $validAudit)

$components = [pscustomobject]@{
    liteProtocolAccountingCleanup = $true
    fastMcpProtocolAccountingCleanup = $true
    postRunPidAudit = $true
    exitCodeCompleteness = $true
    memoryStability = $true
    liteFastMcpIdleRatio = $true
    evidenceIntegrity = $true
}
$validOverall = Test-AllExplicitPassComponents -Components $components
$components.postRunPidAudit = $null
$missingOverallFails = -not (Test-AllExplicitPassComponents -Components $components)

[pscustomobject]@{
    validExitCodes = $validExitCodes
    nullRuntimeFails = $nullRuntimeFails
    nonzeroRuntimeFails = $nonzeroRuntimeFails
    validPidAudit = $validPidAudit
    alivePidFails = $alivePidFails
    missingAuditFails = $missingAuditFails
    validOverall = $validOverall
    missingOverallFails = $missingOverallFails
} | ConvertTo-Json -Compress
"""
    completed = run_measurement_function_probe(
        [
            "Test-ObjectProperty",
            "Test-SessionExitCodesComplete",
            "Test-ProcessAuditComplete",
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
