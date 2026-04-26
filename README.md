# Everything_Mew

Language: English | [한국어](README.ko.md)

<div align="center" aria-label="Everything_Mew cat mascot">
  <img src="docs/assets/everything-mew-icon.png" width="220" alt="Everything_Mew: a small cat that helps AI search">
</div>

**Everything_Mew** is a Windows-only, read-only MCP server and OpenCode skill that helps AI agents search faster with [Everything](https://www.voidtools.com/).

Concept: **a small cat that helps AI search**. The cat does not manage files for you; it finds candidate paths quickly, then lets normal code/content tools inspect only the relevant files.

## What it does

Everything_Mew lets agents use the existing Everything index as a fast, low-token discovery layer for:

- file or folder names;
- paths and project folders;
- extensions;
- file size;
- created or modified dates;
- file attributes.

Preferred flow:

```text
everything_count -> everything_search -> read/grep/ast-grep/LSP on selected paths
```

Everything_Mew is for candidate discovery, not code understanding, duplicate cleanup, or storage management.

## Requirements

- **Windows only.** Everything is a Windows search tool.
- **Everything must be installed and running in the background.**
- **The official Everything SDK is required for the primary SDK/IPC backend.** Download `Everything-SDK.zip` from voidtools and provide the matching DLL:
  - 32-bit Python -> `Everything32.dll`
  - 64-bit Python -> `Everything64.dll`
- The integration uses local Everything IPC; the Everything HTTP server is not required.
- Everything Lite is not supported because it does not allow IPC.
- Python 3.11+.
- OpenCode or another MCP host with local stdio MCP support.

Official sources:

- Everything: <https://www.voidtools.com/>
- Everything SDK: <https://www.voidtools.com/support/everything/sdk/>
- SDK download: <https://www.voidtools.com/Everything-SDK.zip>

## Installation

1. Install and start Everything on Windows.
2. Download and extract the official Everything SDK.
3. Put `Everything64.dll` or `Everything32.dll` in a trusted local support directory.
4. Install this package with server support:

   ```powershell
   py -m pip install -e ".[server]"
   ```

5. Register the local stdio MCP server in your MCP host.

Example OpenCode-style registration:

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew"],
      "enabled": true,
      "environment": {
        "EVERYTHING_SDK_DLL": "%USERPROFILE%\\.config\\opencode\\mcp-bin\\everything-sdk\\Everything64.dll"
      }
    }
  }
}
```

The legacy command name `everything-mcp` is also kept for compatibility.

## Repository contents

- [`src/everything_mcp/`](src/everything_mcp/): Python MCP server implementation.
- [`skills/everything/SKILL.md`](skills/everything/SKILL.md): OpenCode skill instructions.
- [`opencode.example.json`](opencode.example.json): example MCP registration.
- [`docs/AGENT_INSTALLATION_GUIDE.md`](docs/AGENT_INSTALLATION_GUIDE.md): safe agent-facing install guide.
- [`docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md): SDK-focused install notes.
- [`LICENSE`](LICENSE): Apache License 2.0.

Planning notes, workflow drafts, local validation evidence, and machine-specific notes are intentionally excluded from release files and should stay under `_nonrelease/`.

## Architecture

Core operation structure:

```text
AI agent
  -> Everything_Mew MCP tools
  -> SDK/IPC adapter
  -> Everything runtime
  -> Existing Everything index
  -> compact path candidates
  -> read/grep/ast-grep/LSP for selected files
```

Tool roles:

- `everything_status`: reports whether Everything and the selected backend are ready.
- `everything_count`: checks broad or ambiguous queries before returning paths.
- `everything_search`: returns compact path-first candidates, with optional metadata.
- `everything_syntax_help`: gives short Everything query syntax reminders.

Everything_Mew stops at candidate discovery. File content and code semantics remain the job of `read`, `grep`, `ast-grep`, and LSP.

## Usage

Use Everything_Mew when the task is about locating candidate files or folders by indexed filesystem metadata.

Good query examples:

```text
C:\Work\project\ ext:md
C:\Work\project\ config ext:json;yml;yaml !node_modules !.git
%USERPROFILE%\.config\opencode\ settings ext:json;md
C:\Work\ dm:thisweek ext:py;md;json
```

After candidate discovery, switch tools:

- `read` for exact file content;
- `grep` or ripgrep for text inside files;
- `ast-grep` for syntax-aware code patterns;
- LSP for definitions, references, symbols, and diagnostics.

## Safety boundaries

Everything_Mew is read-only. It must not suggest or perform:

- delete, move, rename, quarantine, or cleanup actions;
- dedupe workflows;
- Everything index mutation;
- full-drive result dumps;
- automatic HTTP enabling;
- writes to Everything configuration.

## Validation

Run tests:

```powershell
py -m pytest -q
```

Smoke-check the MCP entrypoint:

```powershell
everything-mew
```

`everything-mew` starts a stdio MCP server and waits for MCP protocol messages on stdin/stdout. In a normal terminal it may appear idle until interrupted. Use an MCP client or inspector for real tool calls.

Expected tool set:

```text
everything_status
everything_count
everything_search
everything_syntax_help
```

## Release checklist

- Keep `_nonrelease/`, caches, virtual environments, builds, SDK DLLs, and local MCP config out of Git.
- Do not commit personal paths, local validation logs, backups, or machine-specific evidence.
- License: Apache License 2.0. Keep copyright and license notices when redistributing.
- Use semver release tags such as `v0.1.0`.

## Trusted local paths

`EVERYTHING_SDK_DLL` and `EVERYTHING_ES_EXE` are trusted local configuration knobs. Point them only at Everything SDK/ES binaries that you installed intentionally. Do not point them at downloaded or untrusted executables.
