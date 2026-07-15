# Agent Installation Guide - Everything_Mew

## Purpose

This guide tells AI agents how to install and validate Everything_Mew in
OpenCode or another local MCP host without destructive actions or
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
- For Codex Desktop, prefer the low-standby `everything-mew-lite` entrypoint
  when the server will remain enabled across many sessions.
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

From the repository root, run the tests and install the standard server
dependencies:

```powershell
py -m pytest -q
py -m pip check
py -m pip install -e ".[server]"
```

Verify the installed entrypoints:

```powershell
py -c "import shutil; print(shutil.which('everything-mew')); print(shutil.which('everything-mcp')); print(shutil.which('everything-mew-lite'))"
```

The wheel installs the MCP runtime only. Install the OpenCode skill explicitly
from the repository or plugin. For a repository checkout, the source is:

```text
skills\everything\SKILL.md
```

Choose the destination without embedding it in OpenCode JSON:

```powershell
$skillDest = Join-Path $env:USERPROFILE ".config\opencode\skills\everything\SKILL.md"
Write-Host "OpenCode skill destination: $skillDest"
```

Preserve any existing skill file unless the user explicitly approves replacing
it.

## Select And Install The SDK DLL

Run this procedure in one PowerShell session. It derives the DLL from the
pointer width of the same `py` interpreter used above, copies that selected
file, and refuses to overwrite an existing DLL.

```powershell
$sdkRoot = Join-Path $env:TEMP ("everything-sdk-official-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $sdkRoot -Force | Out-Null
Invoke-WebRequest -Uri "https://www.voidtools.com/Everything-SDK.zip" -OutFile (Join-Path $sdkRoot "Everything-SDK.zip")
Expand-Archive -Path (Join-Path $sdkRoot "Everything-SDK.zip") -DestinationPath $sdkRoot -Force

$pointerBits = py -c "import struct; print(struct.calcsize('P') * 8)"
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

## Configure OpenCode

Back up the global OpenCode configuration before editing it, preserve all
existing MCP entries, and add this valid local MCP shape:

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew"],
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

## Configure Codex Low-Standby Mode

Codex does not use the OpenCode JSON substitution above. Put the exact
forward-slash value printed for `$env:EVERYTHING_SDK_DLL` into the Codex MCP
environment value:

```toml
[mcp_servers.everything-mew]
command = "everything-mew-lite"
args = []
enabled = true
startup_timeout_sec = 20
tool_timeout_sec = 20
enabled_tools = ["everything_status", "everything_count", "everything_search", "everything_syntax_help"]

[mcp_servers.everything-mew.env]
EVERYTHING_SDK_DLL = "C:/replace/with/the/selected/sdk-dll"
```

The lite entrypoint handles MCP stdio directly and does not import FastMCP at
startup or during idle tool discovery. Backend modules are imported lazily when
a tool is called, which keeps standby memory lower while retaining the same four
read-only tools.

## Optional Environment Paths

- `EVERYTHING_EXE`: trusted path to `Everything.exe` when it is not installed in
  the default location.
- `EVERYTHING_SDK_DLL`: trusted path to the selected SDK DLL. The SDK/IPC backend
  uses this first.
- `EVERYTHING_ES_EXE`: trusted path to `es.exe` only when the optional ES CLI
  fallback is intentionally installed.

## Validation Steps

In the PowerShell session that selected and installed the DLL, validate the same
name and path before creating the server:

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
py -c "from everything_mcp.server import create_mcp; print(type(create_mcp()).__name__)"
```

Expected server type:

```text
FastMCP
```

Use OpenCode MCP tools or direct Python from that same session for read-only
status and search checks:

```powershell
@'
from everything_mcp.server import everything_status, everything_count, everything_search

print(everything_status())
print(everything_count("ext:md", scope=r"C:\Path\to\project"))
print(everything_search("ext:md", scope=r"C:\Path\to\project", limit=5, metadata=True))
'@ | py -
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
