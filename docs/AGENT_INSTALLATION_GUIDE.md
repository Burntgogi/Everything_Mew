# Agent Installation Guide - Everything_Mew

## Purpose

This guide tells AI agents how to install and validate Everything_Mew for
Codex, OpenCode, or another local host without destructive actions or
machine-specific assumptions.

Everything_Mew is Windows-only and SDK-first. The primary backend uses the
official voidtools Everything SDK over local IPC.

## Safety Rules

- Do not delete, move, rename, dedupe, quarantine, or clean user files.
- Do not mutate Everything indexes or configuration.
- Do not enable Everything HTTP automatically.
- Do not overwrite an existing SDK DLL. Stop and report its path.
- Do not overwrite global MCP/OpenCode configuration without a backup and
  explicit user confirmation.
- For Codex, prefer the `everything-mew-once` command and keep any MCP rollback
  registration disabled.
- Do not commit SDK DLLs, local configuration, backups, or machine-specific
  validation logs.

## Requirements

- Windows.
- Everything installed and running in the background.
- Official Everything SDK downloaded from
  <https://www.voidtools.com/Everything-SDK.zip>.
- CPython 3.11 through 3.14.
- SDK DLL matching the actual Python process pointer width:
  - 32-bit Python -> `Everything32.dll`
  - 64-bit Python -> `Everything64.dll`
- MCP host with local stdio server support.

## Install The Runtime

From the repository root, create the intended virtual environment, resolve its
Python executable once, and install the dependency-free runtime. FastMCP and
the development toolchain are not part of this default path:

```powershell
py -m venv .venv
$python = (Resolve-Path ".\.venv\Scripts\python.exe").Path
& $python -m pip install -e .
& $python -m pip check
```

Keep using `$python` from this repository-root PowerShell session. Verify the
entrypoints installed beside that interpreter:

```powershell
$scriptsDir = Split-Path -Parent $python
$entrypointNames = @(
    "everything-mew-once.exe",
    "everything-mcp-once.exe",
    "everything-mew-lite.exe",
    "everything-mcp-lite.exe"
)
foreach ($entrypointName in $entrypointNames) {
    $entrypoint = Join-Path $scriptsDir $entrypointName
    if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
        throw "Installed entrypoint is missing: $entrypoint"
    }
    Write-Host $entrypoint
}
$oneShotRunner = (Resolve-Path (Join-Path $scriptsDir "everything-mew-once.exe")).Path
```

The wheel installs the runtime only. Install the skill explicitly from the
repository or plugin. For a repository checkout, the source is:

```text
skills\everything\SKILL.md
```

Configure the Codex skill after selecting the SDK DLL below so both absolute
paths can be injected together.

For OpenCode, choose its skill destination without embedding it in MCP JSON:

```powershell
$skillDest = Join-Path $env:USERPROFILE ".config\opencode\skills\everything\SKILL.md"
Write-Host "OpenCode skill destination: $skillDest"
```

Preserve any existing skill file unless the user explicitly approves replacing
it.

## Optional Development Audit

For an optional source-checkout audit, install the development tools only after
the runtime path above is healthy, then run the test suite. This still does not
install FastMCP:

```powershell
& $python -m pip install -e ".[dev]"
& $python -m pytest -q
```

## Select And Install The SDK DLL

Run this procedure in one PowerShell session. It derives the DLL from the
pointer width of the same `py` interpreter used above, copies that selected
file, and refuses to overwrite an existing DLL.

```powershell
$sdkRoot = Join-Path $env:TEMP ("everything-sdk-official-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $sdkRoot -Force | Out-Null
Invoke-WebRequest -Uri "https://www.voidtools.com/Everything-SDK.zip" -OutFile (Join-Path $sdkRoot "Everything-SDK.zip")
Expand-Archive -Path (Join-Path $sdkRoot "Everything-SDK.zip") -DestinationPath $sdkRoot -Force

$pointerBits = & $python -c "import struct; print(struct.calcsize('P') * 8)"
if ($LASTEXITCODE -ne 0) {
    throw "Unable to determine Python pointer width."
}
switch ($pointerBits.Trim()) {
    "64" { $dllName = "Everything64.dll" }
    "32" { $dllName = "Everything32.dll" }
    default { throw "Unsupported Python pointer width: $pointerBits" }
}

$source = Join-Path $sdkRoot "dll\$dllName"
$destDir = Join-Path $env:USERPROFILE ".config\opencode\mcp-bin\everything-sdk"
$dest = Join-Path $destDir $dllName
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Selected SDK DLL does not exist: $source"
}
New-Item -ItemType Directory -Path $destDir -Force | Out-Null
if (Test-Path -LiteralPath $dest) {
    throw "Refusing to overwrite existing SDK DLL: $dest"
}
Copy-Item -LiteralPath $source -Destination $dest

if (-not (Test-Path -LiteralPath $dest -PathType Leaf)) {
    throw "SDK DLL validation failed: $dest"
}
if ((Split-Path -Leaf $dest) -ne $dllName) {
    throw "SDK DLL name validation failed: $dest"
}
$env:EVERYTHING_SDK_DLL = $dest.Replace("\", "/")
Write-Host "Selected SDK DLL: $dllName"
Write-Host "OpenCode parent environment: $env:EVERYTHING_SDK_DLL"
```

Keep `$dllName`, `$source`, and `$dest` in that session for the validation steps
below. The forward-slash environment value is intentional: OpenCode substitutes
the value directly into raw JSON.

For Codex, now create a configured skill copy with the resolved runner and SDK
paths. Single-quoted PowerShell literals prevent `$` and backticks in valid
paths from being evaluated. Refuse to overwrite an existing skill until it has
been backed up and replacement is explicitly approved:

```powershell
$skillSource = (Resolve-Path ".\skills\everything\SKILL.md").Path
$skillDest = Join-Path $env:USERPROFILE ".codex\skills\everything-mew\SKILL.md"
if (Test-Path -LiteralPath $skillDest -PathType Leaf) {
    throw "Back up the existing Codex skill and obtain approval before replacing it: $skillDest"
}
$runnerPlaceholder = 'C:\replace\with\absolute\path\to\everything-mew-once.exe'
$sdkPlaceholder = 'C:\replace\with\absolute\path\to\EverythingSDK.dll'
$runnerLiteral = $oneShotRunner.Replace("'", "''")
$sdkLiteral = $dest.Replace("'", "''")
$skill = Get-Content -LiteralPath $skillSource -Raw
if ($skill.IndexOf($runnerPlaceholder, [StringComparison]::Ordinal) -lt 0 -or
    $skill.IndexOf($sdkPlaceholder, [StringComparison]::Ordinal) -lt 0) {
    throw "A one-shot installation placeholder was not found."
}
$skill = $skill.Replace($runnerPlaceholder, $runnerLiteral)
$skill = $skill.Replace($sdkPlaceholder, $sdkLiteral)
New-Item -ItemType Directory -Path (Split-Path -Parent $skillDest) -Force | Out-Null
[IO.File]::WriteAllText($skillDest, $skill, [Text.UTF8Encoding]::new($false))
```

## Configure OpenCode

Back up the global OpenCode configuration before editing it, preserve all
existing MCP entries, and add this valid local MCP shape:

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew-lite"],
      "enabled": true,
      "timeout": 20000,
      "environment": {
        "EVERYTHING_SDK_DLL": "{env:EVERYTHING_SDK_DLL}"
      }
    }
  }
}
```

`{env:EVERYTHING_SDK_DLL}` is OpenCode's literal environment placeholder. Set
`$env:EVERYTHING_SDK_DLL` in the parent PowerShell as shown above, then launch
OpenCode from that same shell:

```powershell
if ([string]::IsNullOrWhiteSpace($env:EVERYTHING_SDK_DLL)) {
    throw "Set EVERYTHING_SDK_DLL before launching OpenCode."
}
opencode
```

Do not place a shell-specific profile expression or a backslash path in the JSON
value. OpenCode recognizes only the `{env:EVERYTHING_SDK_DLL}` placeholder and
receives the normalized forward-slash path from its parent environment.

## Configure Codex One-Shot Mode

Codex does not use the OpenCode JSON substitution above. Set the selected DLL
in the environment inherited by Codex and resolve the installed one-shot
runner to an absolute path. Send exactly one JSON request and parse stdout only
after the process exits:

```powershell
$env:EVERYTHING_SDK_DLL = $dest.Replace("\", "/")
$runner = $oneShotRunner
$request = @{
    schemaVersion = 1
    tool = "everything_search"
    arguments = @{
        query = "ext:py"
        scope = "C:\Work\project"
        limit = 10
        metadata = $false
    }
} | ConvertTo-Json -Compress -Depth 4
$resultJson = $request | & $runner
$exitCode = $LASTEXITCODE
if ($exitCode -notin 0, 1) {
    throw "Everything_Mew one-shot invocation failed."
}
$result = $resultJson | ConvertFrom-Json
```

Do not evaluate the runner path or request as shell code. Exit `0` publishes a
successful tool result, exit `1` publishes a tool error, and exit `2` means no
trustworthy result was published.

If the previous MCP registration is retained for rollback, use the absolute
lite entrypoint path when needed and keep it disabled:

```toml
[mcp_servers.everything-mew]
command = "everything-mew-lite"
args = []
enabled = false
startup_timeout_sec = 20
tool_timeout_sec = 20
enabled_tools = ["everything_status", "everything_count", "everything_search", "everything_syntax_help"]

[mcp_servers.everything-mew.env]
EVERYTHING_SDK_DLL = "C:/replace/with/the/selected/sdk-dll"
```

`enabled = false` makes that MCP unavailable. It does not implement automatic
sleep and wake. Fully restart Codex after changing the setting; an existing
server exits only when its owning Codex or OpenCode host closes. The one-shot
path leaves no Everything_Mew Python process while unused. `Everything.exe`
remains running as the shared indexer. This removes per-session idle
multiplication but makes no fixed active-search peak-memory claim.

OpenCode and manual MCP hosts may keep using `everything-mew-lite`. It handles
MCP stdio directly without importing FastMCP and may retain one Python process
per host session.

## Optional Environment Paths

- `EVERYTHING_EXE`: trusted path to `Everything.exe` when it is not installed in
  the default location.
- `EVERYTHING_SDK_DLL`: trusted path to the selected SDK DLL. The SDK/IPC backend
  uses this first.
- `EVERYTHING_ES_EXE`: trusted path to `es.exe` only when the optional ES CLI
  fallback is intentionally installed.

## Validation Steps

In the PowerShell session that selected and installed the DLL, validate the same
name and path, then run one status request:

```powershell
if (-not (Test-Path -LiteralPath $dest -PathType Leaf)) {
    throw "Selected SDK DLL is missing: $dest"
}
if ((Split-Path -Leaf $dest) -ne $dllName) {
    throw "Selected SDK DLL changed: $dest"
}
if ($env:EVERYTHING_SDK_DLL -ne $dest.Replace("\", "/")) {
    throw "EVERYTHING_SDK_DLL does not match the selected DLL path."
}
$runner = $oneShotRunner
$request = @{
    schemaVersion = 1
    tool = "everything_status"
    arguments = @{}
} | ConvertTo-Json -Compress -Depth 4
$resultJson = $request | & $runner
if ($LASTEXITCODE -notin 0, 1) {
    throw "Everything_Mew one-shot validation failed."
}
$resultJson | ConvertFrom-Json
```

For an OpenCode or manual MCP compatibility check, validate the lite lifecycle
separately:

```powershell
@'
from everything_mcp.lite_stdio import LiteSession, handle_message

session = LiteSession()
response = handle_message({
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-11-25",
        "capabilities": {},
        "clientInfo": {"name": "install-check", "version": "1"}
    }
}, session)
assert response is not None
assert response["result"]["serverInfo"]["name"] == "Everything_Mew_Lite"
print(response["result"]["serverInfo"])
'@ | & $python -
```

FastMCP validation is optional. Only when the host explicitly requires the
legacy FastMCP entrypoint, install and validate it separately:

```powershell
& $python -m pip install -e ".[server]"
& $python -c "from everything_mcp.server import create_mcp; print(type(create_mcp()).__name__)"
```

Use OpenCode MCP tools or direct Python from that same session for read-only
status and search checks:

```powershell
@'
from everything_mcp.server import everything_status, everything_count, everything_search

print(everything_status())
print(everything_count("ext:md", scope=r"C:\Path\to\project"))
print(everything_search("ext:md", scope=r"C:\Path\to\project", limit=5, metadata=True))
'@ | & $python -
```

Expected healthy status:

```json
{
  "everythingInstalled": true,
  "everythingRunning": true,
  "backend": "sdk-ipc",
  "esCliAvailable": false,
  "httpAvailable": false,
  "notes": []
}
```

## Troubleshooting

- `backend=none`: check `EVERYTHING_SDK_DLL`, the Everything client, and the
  selected DLL bitness.
- `Everything SDK DLL was not found`: the environment path is missing or wrong.
- `Everything running=false`: open the Everything client and wait for its
  database to load.
- OpenCode does not show tools: confirm the parent environment, then restart
  OpenCode or start a new session.
- Direct shell tests fail while OpenCode works: use the same parent PowerShell
  and selected `$dest` value for both.

## Complementary Search Tools

Do not remove `grep`, ripgrep, `ast-grep`, or LSP tooling.

```text
Everything_Mew -> grep/ripgrep -> ast-grep -> LSP
```

Everything_Mew discovers files and metadata. grep/ripgrep searches text,
ast-grep searches syntax, and LSP tooling provides language-aware navigation.
