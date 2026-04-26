# Agent Installation Guide — Everything_Mew

## Purpose

This guide tells AI agents how to install and validate Everything_Mew in OpenCode or another local MCP host without destructive actions or machine-specific assumptions.

Everything_Mew is Windows-only and SDK-first. The primary backend uses the official voidtools Everything SDK over local IPC.

## Safety Rules

- Do not delete, move, rename, dedupe, quarantine, or clean user files.
- Do not mutate Everything indexes or configuration.
- Do not enable Everything HTTP automatically.
- Do not overwrite global MCP/OpenCode config without a backup and explicit user confirmation.
- Do not commit SDK DLLs, local config, backups, or machine-specific validation logs.

## Requirements

- Windows.
- Everything installed and running in the background.
- Official Everything SDK downloaded from <https://www.voidtools.com/Everything-SDK.zip>.
- SDK DLL matching Python bitness:
  - 32-bit Python -> `Everything32.dll`
  - 64-bit Python -> `Everything64.dll`
- Python 3.11+.
- MCP host with local stdio server support.

## Recommended Local Paths

Use placeholders in documentation and examples. Do not hardcode a real user's account name.

```text
%USERPROFILE%\.config\opencode\skills\everything\SKILL.md
%USERPROFILE%\.config\opencode\mcp-bin\everything-sdk\Everything64.dll
```

Alternative SDK location, if the user chooses an elevated install:

```text
C:\Program Files\Everything\Everything64.dll
```

## Install Steps

### 1. Verify package tests first

```powershell
py -m pytest -q
py -m pip check
```

### 2. Install the Python package with server dependencies

From the repository root:

```powershell
py -m pip install -e ".[server]"
```

Verify entrypoints:

```powershell
py -c "import shutil; print(shutil.which('everything-mew')); print(shutil.which('everything-mcp'))"
```

### 3. Install the OpenCode skill

Copy:

```text
skills\everything\SKILL.md
```

To the user's OpenCode skill directory, for example:

```text
%USERPROFILE%\.config\opencode\skills\everything\SKILL.md
```

### 4. Install the official Everything SDK DLL

Download and extract:

```text
https://www.voidtools.com/Everything-SDK.zip
```

Copy the DLL matching Python bitness to a trusted local support directory, for example:

```text
%USERPROFILE%\.config\opencode\mcp-bin\everything-sdk\Everything64.dll
```

### 5. Add MCP entry

Add or update the MCP block. Preserve existing MCP entries.

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew"],
      "enabled": true,
      "timeout": 20000,
      "environment": {
        "EVERYTHING_SDK_DLL": "%USERPROFILE%\\.config\\opencode\\mcp-bin\\everything-sdk\\Everything64.dll"
      },
      "description": "Everything_Mew local Windows-only read-only MCP server. SDK/IPC first; ES CLI fallback; no automatic HTTP enabling."
    }
  }
}
```

### 6. Restart or reload the MCP host

OpenCode reinstall is not required. Start a new session if the current session does not show the new MCP tools.

## Validation Steps

### 1. Validate MCP server creation

```powershell
$env:EVERYTHING_SDK_DLL="$env:USERPROFILE\.config\opencode\mcp-bin\everything-sdk\Everything64.dll"
py -c "from everything_mcp.server import create_mcp; print(type(create_mcp()).__name__)"
```

Expected:

```text
FastMCP
```

### 2. Validate tool status

Use OpenCode MCP tools or direct Python with the same environment:

```powershell
$env:EVERYTHING_SDK_DLL="$env:USERPROFILE\.config\opencode\mcp-bin\everything-sdk\Everything64.dll"
@'
from everything_mcp.server import everything_status, everything_count, everything_search
print(everything_status())
print(everything_count('ext:md', scope=r'C:\Work\project'))
print(everything_search('ext:md', scope=r'C:\Work\project', limit=5, metadata=True))
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

- `backend=none`: check `EVERYTHING_SDK_DLL`, Everything client, and DLL bitness.
- `Everything SDK DLL was not found`: environment path is missing or wrong.
- `Everything running=false`: open the Everything client and wait for DB load.
- OpenCode does not show tools: restart OpenCode or start a new session.
- Plain shell tests fail while OpenCode works: set `$env:EVERYTHING_SDK_DLL` before direct shell tests.

## Complementary Search Tools

Do not remove `grep`, ripgrep, `ast-grep`, or LSP tooling.

Recommended chain:

```text
Everything_Mew -> grep/ripgrep -> ast-grep -> LSP
```

Everything_Mew is for metadata discovery. grep/ripgrep is for text search. ast-grep is for syntax-aware code search.
