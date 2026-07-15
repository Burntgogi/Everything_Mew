# Everything SDK Install Guide for AI Agents

## Purpose

Install and validate the official voidtools Everything SDK DLL for the read-only Everything_Mew backend.

The MCP is SDK-first. `es.exe` is only a fallback when SDK/IPC is unavailable.

## Safety Rules

- Do not delete, move, rename, dedupe, quarantine, or clean user files.
- Do not mutate Everything indexes or configuration.
- Do not enable Everything HTTP.
- Do not overwrite an existing SDK DLL without making a backup and getting explicit confirmation.
- Treat `EVERYTHING_EXE`, `EVERYTHING_SDK_DLL`, and `EVERYTHING_ES_EXE` as trusted local paths. Never point them to untrusted downloads.

## Official Sources

- SDK overview: https://www.voidtools.com/support/everything/sdk/
- SDK download: https://www.voidtools.com/Everything-SDK.zip
- C/C++ setup notes: https://www.voidtools.com/support/everything/sdk/c/
- IPC notes: https://www.voidtools.com/support/everything/sdk/ipc/
- Python example: https://www.voidtools.com/support/everything/sdk/python/

## Requirements

- Windows.
- Everything installed.
- Everything client running in the background.
- CPython 3.11 through 3.14.
- Python process bitness must match the SDK DLL:
  - 64-bit Python -> `Everything64.dll`
  - 32-bit Python -> `Everything32.dll`

## Recommended Install Strategy

Official C docs recommend copying the DLL beside the consuming program executable. For OpenCode MCP usage, prefer a user-writable support directory and configure the MCP environment explicitly:

```text
%USERPROFILE%\.config\opencode\mcp-bin\everything-sdk\Everything64.dll
```

Then set in global OpenCode config:

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
      }
    }
  }
}
```

Alternative admin install:

```text
C:\Program Files\Everything\Everything64.dll
```

The MCP auto-detects that location because it looks beside `Everything.exe`. This usually requires elevated permission.

## Environment Variables

- `EVERYTHING_EXE`: optional path to the trusted Everything client executable.
  The default is `C:\Program Files\Everything\Everything.exe`. Set this when
  Everything is installed elsewhere; the path also anchors SDK DLL discovery.
- `EVERYTHING_SDK_DLL`: optional explicit path to the trusted official SDK DLL.
  Use `Everything64.dll` for 64-bit Python and `Everything32.dll` for 32-bit
  Python. The primary SDK/IPC backend uses this value first.
- `EVERYTHING_ES_EXE`: optional path to a trusted `es.exe` command-line client.
  Configure it only when the ES CLI fallback is intentionally installed. It
  does not replace the SDK DLL for the primary backend.

An SDK-first OpenCode environment can set the first two values and omit the ES
fallback:

```json
{
  "EVERYTHING_EXE": "C:\\Program Files\\Everything\\Everything.exe",
  "EVERYTHING_SDK_DLL": "%USERPROFILE%\\.config\\opencode\\mcp-bin\\everything-sdk\\Everything64.dll"
}
```

## Agent Procedure

1. Inspect current Everything install folder:

   ```powershell
   Test-Path "C:\Program Files\Everything\Everything.exe"
   Get-ChildItem "C:\Program Files\Everything" -Filter "Everything*.dll"
   ```

2. Download official SDK zip to a temporary folder:

   ```powershell
   $sdkRoot = Join-Path $env:TEMP ("everything-sdk-official-" + [guid]::NewGuid().ToString("N"))
   New-Item -ItemType Directory -Path $sdkRoot -Force | Out-Null
   Invoke-WebRequest -Uri "https://www.voidtools.com/Everything-SDK.zip" -OutFile (Join-Path $sdkRoot "Everything-SDK.zip")
   Expand-Archive -Path (Join-Path $sdkRoot "Everything-SDK.zip") -DestinationPath $sdkRoot -Force
   ```

3. Choose the DLL matching Python bitness:

   ```powershell
   py -c "import platform; print(platform.architecture()[0])"
   ```

4. Copy the DLL to the OpenCode MCP support directory:

   ```powershell
   $destDir = "$env:USERPROFILE\.config\opencode\mcp-bin\everything-sdk"
   $dest = Join-Path $destDir "Everything64.dll"
   New-Item -ItemType Directory -Path $destDir -Force | Out-Null
   if (Test-Path $dest) {
       Write-Host "Existing SDK DLL found: $dest"
       Write-Host "Stop here unless the user explicitly confirms replacement."
       return
   }
   Copy-Item "$sdkRoot\dll\Everything64.dll" $dest
   ```

5. Backup global OpenCode config before editing:

   ```powershell
   Copy-Item "$env:USERPROFILE\.config\opencode\opencode.json" "$env:USERPROFILE\.config\opencode\opencode.backup-before-everything-sdk.json"
   ```

6. Add or update the `everything-mew` MCP environment. Set
   `EVERYTHING_SDK_DLL`; set `EVERYTHING_EXE` if Everything is not in its
   default location; set `EVERYTHING_ES_EXE` only for the optional CLI fallback.

7. Restart OpenCode or start a new OpenCode session so global MCP config is reloaded.

8. Validate with read-only tool calls:

   ```python
   from everything_mcp.server import everything_status, everything_count, everything_search

   print(everything_status())
   print(everything_count("ext:md", scope=r"C:\Path\to\project"))
   print(everything_search("ext:md", scope=r"C:\Path\to\project", limit=5, metadata=True))
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

- `Everything SDK DLL was not found`: `EVERYTHING_SDK_DLL` is missing or points to the wrong path.
- `everythingInstalled` is false: verify `EVERYTHING_EXE` or the default Everything install path.
- ES fallback is unavailable: verify `EVERYTHING_ES_EXE` only if the optional `es.exe` fallback is intended.
- `ctypes WinDLL is unavailable`: not running on Windows.
- Runtime/version check failed: Everything client may not be running.
- Empty results while Everything is starting: wait for the Everything database to load.
- Permission denied copying to `C:\Program Files\Everything`: use the OpenCode support directory approach or rerun the copy step elevated.

## Release Note

Do not commit downloaded SDK DLLs, local OpenCode config files, or machine-specific validation logs. Keep those under local support directories and point `EVERYTHING_EXE`, `EVERYTHING_SDK_DLL`, and `EVERYTHING_ES_EXE` only at trusted local binaries.
