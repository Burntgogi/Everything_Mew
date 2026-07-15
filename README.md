# Everything_Mew

Language: English | [한국어](README.ko.md)

<div align="center" aria-label="Everything_Mew cat mascot">
  <img src="https://raw.githubusercontent.com/Burntgogi/Everything_Mew/main/docs/assets/everything-mew-banner.png" width="420" alt="Everything_Mew: a small cat that helps AI search">
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

## Update notes

### Planned 0.2.0 (Unreleased): low-standby MCP mode

This update adds a low-standby stdio MCP path for Codex Desktop and other MCP
hosts that keep one server process alive per active session.

- `everything-mew-lite` and `everything-mcp-lite` are new console entrypoints.
- `python -m everything_mcp.lite_stdio` is available for hosts that prefer module-based commands.
- The lite path answers `initialize`, `ping`, `tools/list`, and `tools/call` without importing FastMCP while idle.
- Everything SDK/IPC backend loading is deferred until a tool is actually called.
- Existing `everything-mew` and `everything-mcp` entrypoints remain available for the FastMCP path.
- The package top-level import now lazy-loads the FastMCP server module.
- The server version comes from installed package metadata, with a structural `pyproject.toml` fallback in source checkouts.
- Windows CI verifies Python 3.11 and 3.14, strict typing, both distribution formats, and the installed-wheel lite handshake.
- Contract and safety tests were expanded around lite stdio behavior, read-only tool metadata, adapter selection, broad-query safety, and syntax validation.

The feature branch package version remains `0.1.0`; `0.2.0` is a planned,
unreleased target. See the bilingual [changelog](CHANGELOG.md).

Recommended adoption:

- Use `everything-mew-lite` for Codex Desktop or always-enabled local MCP hosts.
- Keep `everything-mew` for hosts that already work well with FastMCP or require the existing FastMCP runtime behavior.

## Requirements

- **Windows only.** Everything is a Windows search tool.
- **Everything must be installed and running in the background.**
- **The official Everything SDK is required for the primary SDK/IPC backend.** Download `Everything-SDK.zip` from voidtools and provide the matching DLL:
  - 32-bit Python -> `Everything32.dll`
  - 64-bit Python -> `Everything64.dll`
- The integration uses local Everything IPC; the Everything HTTP server is not required.
- Everything Lite is not supported because it does not allow IPC.
- CPython 3.11 through 3.14.
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

The built Python wheel contains the MCP runtime and console entrypoints only.
It does not install [`skills/everything/SKILL.md`](skills/everything/SKILL.md)
into an agent host. Install that skill explicitly from this repository or from
a plugin that packages it. The optional `[server]` extra installs FastMCP for
the standard entrypoints; the lite entrypoints work with the runtime-only
wheel.

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

For Codex Desktop or other hosts that keep one stdio MCP process alive per
active session, prefer the low-standby entrypoint:

```text
everything-mew-lite
```

`everything-mew-lite` implements the small MCP tool surface directly over
stdio. It does not start or require FastAPI, and it does not require FastMCP.
It imports the Everything backend only when a tool is called. If the console
script has not been regenerated after an editable install, use
`python -m everything_mcp.lite_stdio` with the same environment variables.

Low-standby mode changes:

- adds `everything-mew-lite` and `everything-mcp-lite` console entrypoints;
- adds `python -m everything_mcp.lite_stdio` for hosts that prefer module-based commands;
- keeps package import lightweight by lazy-loading the FastMCP server module;
- advertises read-only MCP instructions during `initialize`;
- validates MCP `params` and tool argument types before loading the backend;
- keeps `everything-mew` and `everything-mcp` unchanged for the existing FastMCP path.

## Repository contents

- [`src/everything_mcp/`](src/everything_mcp/): Python MCP server implementation.
- [`skills/everything/SKILL.md`](skills/everything/SKILL.md): OpenCode skill instructions.
- [`opencode.example.json`](opencode.example.json): example MCP registration.
- [`docs/AGENT_INSTALLATION_GUIDE.md`](docs/AGENT_INSTALLATION_GUIDE.md): safe agent-facing install guide.
- [`docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md): SDK-focused install notes.
- [`CHANGELOG.md`](CHANGELOG.md): bilingual planned and released changes.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): contributor and release reproduction commands.
- [`SECURITY.md`](SECURITY.md): private vulnerability reporting policy.
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

Smoke-check the low-standby entrypoint:

```powershell
everything-mew-lite
```

`everything-mew-lite` uses the same stdin/stdout MCP transport, but it does not load FastMCP while idle. It should answer `initialize`, `ping`, `tools/list`, and `tools/call` for the four read-only tools.

Recommended release checks:

```powershell
py -m pytest -q
py -m ruff check .
py -m mypy --strict src tests
py -m build
```

The complete fresh-venv installed-wheel smoke and artifact inspection commands
are in [CONTRIBUTING.md](CONTRIBUTING.md). They clear `PYTHONPATH`, invoke the
installed `everything-mew-lite` entrypoint, and verify `initialize`,
`notifications/initialized`, `tools/list`, installed metadata version, and all
four tools.

Expected tool set:

```text
everything_status
everything_count
everything_search
everything_syntax_help
```

## Contributor notes

- Do not commit SDK DLLs, `.env` files, local MCP config, caches, build outputs, or machine-specific validation logs.
- Keep personal paths out of examples and documentation.
- `EVERYTHING_EXE`, `EVERYTHING_SDK_DLL`, and `EVERYTHING_ES_EXE` must point only to trusted local Everything binaries.
- This project is licensed under Apache License 2.0.
