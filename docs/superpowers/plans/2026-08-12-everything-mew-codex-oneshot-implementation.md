# Everything_Mew Codex One-Shot Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dependency-free one-shot Everything_Mew command that lets the Codex skill perform one existing read-only operation and exit, while retaining the current stdio MCP servers as explicit compatibility modes.

**Architecture:** A new `everything_mcp.oneshot` module reads one bounded UTF-8 JSON envelope from stdin and delegates to the existing lite tool dispatcher. It writes the existing `CallToolResult` JSON shape and exits with a stable status code. Codex documentation and the skill use this command by default; MCP entry points remain unchanged for OpenCode and manual compatibility.

**Tech Stack:** CPython 3.11-3.14, Python standard library, ctypes Everything SDK adapter, pytest, Ruff, strict mypy, Hatch wheel/sdist build, PowerShell 7 for Windows acceptance.

## Global Constraints

- Preserve exactly `everything_status`, `everything_count`, `everything_search`, and `everything_syntax_help`.
- Preserve the existing `ToolSpec`, query safety, scope validation, result hard cap of 100, adapter order, and redacted error boundary.
- Add no runtime dependency, daemon, HTTP listener, process pool, idle timer, Job Object, or background service.
- Read one request only; maximum request size is 65,536 bytes.
- Accept exactly `schemaVersion`, `tool`, and `arguments`; `schemaVersion` is integer `1`.
- Emit no traceback, request value, path, environment value, or exception detail on invocation failure.
- Exit `0` for tool success, `1` for a published tool error, and `2` when no trustworthy result was published.
- Keep the existing four MCP console scripts and add `everything-mew-once` plus `everything-mcp-once`.
- Do not publish, tag, push, or delete an existing installation during this implementation.

---

### Task 1: One-Shot Envelope and Shared Dispatch

**Files:**
- Create: `tests/test_oneshot.py`
- Create: `src/everything_mcp/oneshot.py`
- Modify: `src/everything_mcp/lite_stdio.py:141`

**Interfaces:**
- Consumes: `TOOL_SPEC_BY_NAME` and the existing lite tool-call result builder.
- Produces: `parse_request(data: bytes) -> tuple[str, dict[str, Any]]`, `run(input_stream: BinaryIO, output_stream: BinaryIO, error_stream: TextIO) -> int`, and public `call_tool_result(name: str, arguments: dict[str, Any]) -> dict[str, Any]`.

- [ ] **Step 1: Write failing envelope tests**

Create `tests/test_oneshot.py` with literal expectations for one valid syntax-help call and malformed envelopes:

```python
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

    code = oneshot.run(BytesIO(request("everything_syntax_help", {"topic": "filters"})), stdout, stderr)

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
        b"{not-json",
        b"[]",
        b'{"schemaVersion":true,"tool":"everything_status","arguments":{}}',
        b'{"schemaVersion":1,"tool":"everything_delete","arguments":{}}',
        b'{"schemaVersion":1,"tool":"everything_status","arguments":{},"extra":1}',
    ],
)
def test_run_rejects_malformed_envelope_without_disclosure(payload: bytes) -> None:
    stdout = BytesIO()
    stderr = StringIO()

    code = oneshot.run(BytesIO(payload), stdout, stderr)

    assert code == 2
    assert stdout.getvalue() == b""
    assert stderr.getvalue() == "ONESHOT_INVOCATION_ERROR\n"
```

Add a 65,537-byte case and a valid envelope with an invalid tool argument. The oversized case must exit `2`; the invalid argument must publish the existing `isError: true` result and exit `1`.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -m pytest tests/test_oneshot.py -q
```

Expected: collection fails because `everything_mcp.oneshot` does not exist.

- [ ] **Step 3: Expose the existing dispatcher**

In `lite_stdio.py`, rename `_call_tool_result` to `call_tool_result` and update the `tools/call` path to call the public internal-package function. Do not change its validation, exception redaction, or result shape.

- [ ] **Step 4: Implement the minimal one-shot module**

Create `src/everything_mcp/oneshot.py` with this structure:

```python
from __future__ import annotations

import json
import sys
from typing import Any, BinaryIO, TextIO, cast

from .lite_stdio import call_tool_result
from .tool_specs import TOOL_SPEC_BY_NAME

MAX_REQUEST_BYTES = 65_536
INVOCATION_ERROR = "ONESHOT_INVOCATION_ERROR\n"


def parse_request(data: bytes) -> tuple[str, dict[str, Any]]:
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict) or set(value) != {"schemaVersion", "tool", "arguments"}:
        raise ValueError("invalid request")
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("invalid request")
    tool = value["tool"]
    arguments = value["arguments"]
    if not isinstance(tool, str) or tool not in TOOL_SPEC_BY_NAME or not isinstance(arguments, dict):
        raise ValueError("invalid request")
    return tool, cast(dict[str, Any], arguments)
```

`run` must read at most `MAX_REQUEST_BYTES + 1`, reject empty/oversized data, call `call_tool_result`, serialize with compact UTF-8 JSON, flush stdout, and return based on `isError`. Catch runner-boundary exceptions and write only `INVOCATION_ERROR` to stderr. `main()` raises `SystemExit(run())`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_oneshot.py tests/test_lite_stdio.py -q
```

Expected: all focused tests pass and existing lite dispatch behavior remains unchanged.

- [ ] **Step 6: Commit Task 1**

```powershell
git add src/everything_mcp/lite_stdio.py src/everything_mcp/oneshot.py tests/test_oneshot.py
git commit -m "feat: add bounded one-shot runtime"
```

### Task 2: Console Scripts and Real Subprocess Lifecycle

**Files:**
- Create: `tests/test_oneshot_subprocess.py`
- Modify: `pyproject.toml:34`
- Modify: `.github/workflows/ci.yml:66`

**Interfaces:**
- Consumes: `python -m everything_mcp.oneshot` and its stdin/stdout/exit contract.
- Produces: installed commands `everything-mew-once` and `everything-mcp-once`.

- [ ] **Step 1: Write failing subprocess tests**

Create a helper that invokes `[sys.executable, "-m", "everything_mcp.oneshot"]` with UTF-8 input and captured output. Add tests proving:

```python
def test_oneshot_subprocess_executes_unicode_syntax_request_and_exits() -> None:
    process = run_oneshot({"schemaVersion": 1, "tool": "everything_syntax_help", "arguments": {"topic": "필터"}})
    result = json.loads(process.stdout)
    assert process.returncode == 0
    assert result["isError"] is False
    assert process.stderr == ""


def test_oneshot_subprocess_rejects_unknown_tool_without_traceback() -> None:
    process = run_oneshot({"schemaVersion": 1, "tool": "everything_delete", "arguments": {}})
    assert process.returncode == 2
    assert process.stdout == ""
    assert process.stderr == "ONESHOT_INVOCATION_ERROR\n"
    assert "Traceback" not in process.stderr
```

Also assert `fastmcp` and `mcp` are not imported by a valid syntax-help call using a small probe request that reports `sys.modules` only from test code after importing `everything_mcp.oneshot`.

- [ ] **Step 2: Run subprocess tests and verify RED for console metadata**

Run:

```powershell
python -m pytest tests/test_oneshot_subprocess.py -q
```

Expected: module subprocess behavior passes after Task 1, but a `tomllib` assertion for missing `everything-mew-once` and `everything-mcp-once` script entries fails.

- [ ] **Step 3: Add console script metadata**

Add to `[project.scripts]`:

```toml
everything-mew-once = "everything_mcp.oneshot:main"
everything-mcp-once = "everything_mcp.oneshot:main"
```

Extend the Windows installed-wheel CI smoke to invoke `everything-mew-once.exe` with a syntax-help request. Assert exit `0`, valid JSON, and no FastMCP requirement.

- [ ] **Step 4: Run Task 2 tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_oneshot_subprocess.py tests/test_contracts.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 2**

```powershell
git add pyproject.toml .github/workflows/ci.yml tests/test_oneshot_subprocess.py tests/test_contracts.py
git commit -m "test: verify one-shot console lifecycle"
```

### Task 3: Codex Skill and Bilingual Operating Documentation

**Files:**
- Modify: `skills/everything/SKILL.md`
- Modify: `README.md`
- Modify: `README.ko.md`
- Modify: `docs/AGENT_INSTALLATION_GUIDE.md`
- Modify: `docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`
- Modify: `CHANGELOG.md`
- Modify: `tests/test_lite_stdio.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Consumes: installed absolute path to `everything-mew-once` and the JSON envelope from Task 1.
- Produces: a Codex skill workflow that uses one-shot by default and explicit MCP compatibility instructions for Codex/OpenCode.

- [ ] **Step 1: Update failing repository contract tests**

Change the established documentation/install contract tests so they require all four runtime entry points plus both one-shot entry points. The Codex guide expectation must require `everything-mew-once`, an `enabled = false` rollback MCP entry, and a one-shot JSON example. Keep OpenCode expectations on `everything-mew-lite`.

Run:

```powershell
python -m pytest tests/test_lite_stdio.py tests/test_contracts.py -q
```

Expected: tests fail because current documents still recommend an always-enabled Codex MCP server.

- [ ] **Step 2: Update the skill**

Retain all existing syntax, scoping, result-limit, content-handoff, and safety guidance. Replace MCP-call assumptions with this execution contract:

```powershell
$runner = (Get-Command everything-mew-once -ErrorAction Stop).Source
$request = @{
    schemaVersion = 1
    tool = "everything_search"
    arguments = @{ query = "ext:py"; scope = "C:\Work\project"; limit = 10; metadata = $false }
} | ConvertTo-Json -Compress -Depth 4
$request | & $runner
if ($LASTEXITCODE -notin 0, 1) { throw "Everything_Mew one-shot invocation failed." }
```

Require an absolute resolved runner, one request per invocation, JSON parsing only after exit, no arbitrary command evaluation, and normal filesystem tools for reading selected paths.

- [ ] **Step 3: Update English and Korean docs**

Document these facts consistently:

- Codex default: skill plus `everything-mew-once`, zero persistent Everything_Mew Python process while unused.
- OpenCode/manual compatibility: `everything-mew-lite` remains supported and may retain one process per host session.
- `enabled = false` means unavailable as MCP; it does not sleep/wake automatically.
- Existing servers exit only when their owning Codex/OpenCode host closes.
- `Everything.exe` remains running as the shared indexer.
- One-shot removes idle multiplication but makes no fixed active-search peak-memory claim.

Add an `[Unreleased]` changelog entry in English and Korean. Do not claim a release version, tag, or publication.

- [ ] **Step 4: Run documentation contract tests and verify GREEN**

Run:

```powershell
python -m pytest tests/test_lite_stdio.py tests/test_contracts.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 3**

```powershell
git add skills/everything/SKILL.md README.md README.ko.md docs/AGENT_INSTALLATION_GUIDE.md docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md CHANGELOG.md tests/test_lite_stdio.py tests/test_contracts.py
git commit -m "docs: make Codex one-shot the default"
```

### Task 4: Full Quality and Installed-Wheel Gates

**Files:**
- Modify only if a gate exposes a real defect in files already owned by Tasks 1-3.
- Generate locally and do not commit: `dist/*`, temporary venvs, and `_nonrelease/*` evidence.

**Interfaces:**
- Consumes: complete source tree and built wheel.
- Produces: fresh verification evidence for tests, static analysis, archives, installed entry points, live SDK calls, concurrency, and cleanup.

- [ ] **Step 1: Run the complete source gates**

```powershell
python -m pytest -q
python -m ruff check src tests
python -m mypy src
python -m build
```

Expected: every command exits `0`; one wheel and one sdist are created.

- [ ] **Step 2: Inspect archive contents**

Use `zipfile` and `tarfile` from the standard library to assert:

- `everything_mcp/oneshot.py` exists in wheel and sdist;
- wheel metadata exposes both one-shot console scripts;
- no `skills/`, SDK DLL, `.env`, `_nonrelease`, or machine-specific path is in the wheel;
- the sdist includes source, tests, and bilingual runtime docs, while both archives exclude the separately installed skill according to existing package policy.

- [ ] **Step 3: Install the wheel in a fresh temporary venv**

```powershell
py -3.11 -m venv $temporaryVenv
& "$temporaryVenv\Scripts\python.exe" -m pip install --no-deps --no-index $wheel
& "$temporaryVenv\Scripts\python.exe" -m pip check
```

Clear `PYTHONPATH`. Invoke `everything-mew-once.exe` with syntax help and the configured `EVERYTHING_SDK_DLL` with status/search. Assert valid output and exit codes.

- [ ] **Step 4: Run sequential and concurrent live lifecycle acceptance**

Capture exact pre-run Python identities using PID plus UTC start ticks. Run ten sequential `everything_search` calls and six concurrent calls against `C:\Work\everything_mcp` with `query="ext:py"`, `limit=1`. Require every result to contain one existing path inside scope.

After a 500 ms settlement, capture exact identities again. The acceptance passes only when no process identity created by the one-shot runs remains. Do not kill by image name; cleanup fallback may target only retained owned process handles.

- [ ] **Step 5: Record local candidate evidence**

Write a non-committed JSON/Markdown receipt under `_nonrelease/local-evidence/` containing Git SHA/tree, wheel SHA-256, Python version, SDK DLL filename only, sequential/concurrent counts, timing summary, exact PID/start-time cleanup result, and pass/fail. Do not include personal paths beyond the repository scope already present in the command.

- [ ] **Step 6: Review the diff and commit gate-driven fixes**

Run:

```powershell
git diff --check
git status --short
git diff --stat origin/main...HEAD
```

If verification required source changes, commit only those focused fixes with a message naming the corrected contract. Generated build/evidence files remain untracked and ignored.

### Task 5: Local Codex Candidate Activation

**Files outside Git, changed only after diff-first approval:**
- Backup and modify: `C:\Users\JMTFAM01\.codex\config.toml`
- Backup and replace: `C:\Users\JMTFAM01\.codex\skills\everything-mew\SKILL.md`
- Create: versioned candidate venv under `%LOCALAPPDATA%\Everything_Mew\venvs\`

**Interfaces:**
- Consumes: verified wheel SHA-256 and repository skill from Task 4.
- Produces: an installed one-shot runner, a Codex-local skill with its resolved absolute runner path, and a disabled rollback MCP entry.

- [ ] **Step 1: Prepare a diff-first activation preview**

Resolve the built wheel, compute its SHA-256 prefix, and propose a new venv path that cannot collide with the existing `0.2.0-442b15e0` environment. Show the exact config diff:

```toml
[mcp_servers.everything-mew]
enabled = false
```

Keep command, args, timeouts, enabled tools, and SDK environment unchanged for rollback. Show the installed-skill diff with the candidate `everything-mew-once.exe` absolute path. Do not write yet.

- [ ] **Step 2: Back up and install after approval**

Create timestamped backups beside both user files. Create the candidate venv, install only the verified wheel with `--no-deps --no-index`, run `pip check`, and execute syntax/status/search one-shot smokes before changing Codex files.

- [ ] **Step 3: Replace the skill and disable MCP**

Copy the reviewed skill, inject only the resolved candidate runner path in its local installation block, and atomically replace the installed skill. Atomically change only `enabled = true` to `enabled = false` inside the existing Everything_Mew MCP table. Re-read both files and compare to the preview.

- [ ] **Step 4: Verify pre-restart state**

Invoke the installed skill runner ten times directly and require zero new survivors. Confirm existing Codex-owned `lite_stdio` processes are unchanged rather than claiming they were reclaimed.

- [ ] **Step 5: Hand off the required restart**

Tell the user to close all Codex Desktop windows/tasks and restart Codex. Existing MCP processes are host-owned and cannot be reclaimed safely from this task. After restart, run a fresh process audit and skill-driven search before declaring the migration accepted.
