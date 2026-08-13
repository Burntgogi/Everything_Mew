<p align="center">
  <img src="docs/assets/everything-mew-banner.png" width="420" alt="Everything_Mew cat mascot helping an AI agent discover file paths">
</p>

<h1 align="center">Everything_Mew</h1>

<p align="center"><strong>A lightweight, read-only MCP bridge to Everything for fast Windows file discovery.</strong></p>

<p align="center">
  <a href="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Burntgogi/Everything_Mew/releases/tag/v0.2.0"><img alt="Stable v0.2.0" src="https://img.shields.io/badge/stable-v0.2.0-5865F2"></a>
  <a href="https://github.com/Burntgogi/Everything_Mew/releases/tag/v0.3.0-rc.1"><img alt="Preview v0.3.0-rc.1" src="https://img.shields.io/badge/preview-v0.3.0--rc.1-D97706"></a>
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078D4">
  <img alt="Python 3.11 through 3.14" src="https://img.shields.io/badge/Python-3.11--3.14-3776AB">
  <img alt="Read-only MCP tools" src="https://img.shields.io/badge/MCP-read--only-1F883D">
  <a href="LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

<p align="center">
  <a href="README.ko.md">한국어</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#tools">Tools</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#safety-boundaries">Safety</a> ·
  <a href="#release-history">Release notes</a>
</p>

## Overview

Everything_Mew connects AI agents to the existing local
[Everything](https://www.voidtools.com/) index. It discovers candidate files and
folders by name, path, extension, size, date, and attributes before more
expensive content tools inspect them.

The project is Windows-only and exposes four read-only MCP tools. It does not
edit files, mutate the Everything index, or enable Everything's HTTP server.
The repository also contains an optional agent skill with concise Everything
syntax guidance; the Python wheel installs only the MCP runtime and console
entrypoints.

## How it works

Use Everything_Mew as a low-token discovery layer, then switch to the normal
content or code tool for the selected paths:

```text
everything_count -> everything_search -> read/grep/ast-grep/LSP on selected paths
```

- `everything_count` checks result volume without materializing paths.
- `everything_search` returns compact path-first candidates with optional
  metadata.
- `read`, `grep`, `ast-grep`, and LSP remain responsible for file contents and
  code semantics.

For Codex, the recommended path is the agent skill plus
`everything-mew-once`. Each request starts one short-lived process tree,
returns one result, and exits, so no Everything_Mew Python process remains while
unused. The shared `Everything.exe` indexer continues running. OpenCode and
manual MCP hosts can keep using `everything-mew-lite`; that compatibility mode
may retain one server process tree per host session. One-shot removes that idle
multiplication but does not claim a fixed peak-memory value during active
searches.

## Quick start

### 1. Prepare Everything and Python

You need:

- Windows with the standard Everything application installed and running;
- CPython 3.11 through 3.14;
- the official Everything SDK DLL matching Python's architecture:
  `Everything64.dll` for 64-bit Python or `Everything32.dll` for 32-bit Python.

Everything Lite is not supported because it does not expose the required IPC
interface. Download the SDK from the
[official Everything SDK page](https://www.voidtools.com/support/everything/sdk/)
and keep the DLL in a trusted local support directory. The DLL is not bundled
with this repository or its Python package.

### 2. Install the 0.3 release candidate

```powershell
git clone https://github.com/Burntgogi/Everything_Mew.git
cd Everything_Mew
git checkout v0.3.0-rc.1
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
```

### 3. Use the one-shot runner from Codex

The DLL value below is an example placeholder. Replace it with the exact
forward-slash path to the trusted, architecture-matching SDK DLL selected on
your computer. Make the installed command available to the Codex process, then
resolve it to an absolute path before each invocation:

```powershell
$env:EVERYTHING_SDK_DLL = "C:/replace/with/the/selected/sdk-dll"
$runner = (Resolve-Path ".\.venv\Scripts\everything-mew-once.exe").Path
$request = @{
    schemaVersion = 1
    tool = "everything_status"
    arguments = @{}
} | ConvertTo-Json -Compress -Depth 4
$resultJson = $request | & $runner
$exitCode = $LASTEXITCODE
if ($exitCode -notin 0, 1) { throw "Everything_Mew one-shot invocation failed." }
$resultJson | ConvertFrom-Json
```

If an existing Codex MCP registration is retained as a rollback path, keep it
disabled:

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

`enabled = false` makes the MCP unavailable; it does not sleep and wake on
demand. After changing this setting, fully restart Codex. Servers already owned
by a running Codex or OpenCode host exit only when that host closes. Confirm
that the one-shot `everything_status` result reports `sdk-ipc`, a loaded
database, and matching Python architecture before searching.

For OpenCode, keep the SDK path in the parent environment and use its literal
environment placeholder:

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew-lite"],
      "enabled": true,
      "environment": {
        "EVERYTHING_SDK_DLL": "{env:EVERYTHING_SDK_DLL}"
      }
    }
  }
}
```

For the complete path-validation, installation, and smoke-test procedure, see
the [agent installation guide](docs/AGENT_INSTALLATION_GUIDE.md) and the
[SDK installation guide](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md).

### Optional FastMCP compatibility

Install the compatibility runtime only when a host explicitly needs the
original FastMCP entrypoints:

```powershell
.\.venv\Scripts\python.exe -m pip install ".[server]"
```

For an editable source checkout, the equivalent compatibility command is:

```powershell
py -m pip install -e ".[server]"
```

The existing `everything-mew` and `everything-mcp` commands use this optional
path. The recommended Codex command `everything-mew-once`, its
`everything-mcp-once` alias, and the lite compatibility commands do not require
it.

## Tools

| Tool | Purpose |
| --- | --- |
| `everything_status` | Reports Everything, database, architecture, and selected backend readiness. |
| `everything_count` | Counts a bounded query before any candidate paths are returned. |
| `everything_search` | Returns compact, path-first candidates with optional metadata. |
| `everything_syntax_help` | Provides short, version-aware Everything query guidance. |

All four tools are read-only. The public MCP contract intentionally stops at
candidate discovery.

## Requirements

- **Operating system:** Windows.
- **Everything:** the standard application must be installed and running.
- **Everything SDK:** a trusted DLL matching Python's 32-bit or 64-bit
  architecture is required for the primary SDK/IPC backend.
- **Python:** CPython 3.11, 3.12, 3.13, or 3.14.
- **MCP host:** Codex Desktop or another host with local stdio MCP support.
- **Network services:** Everything HTTP is not required and is never enabled
  automatically.

Official references:

- [Everything](https://www.voidtools.com/)
- [Everything 1.4 searching guide](https://www.voidtools.com/support/everything/searching/)
- [Everything 1.5 search syntax](https://www.voidtools.com/support/everything/search_syntax/)
- [Everything SDK](https://www.voidtools.com/support/everything/sdk/)
- [Everything_SetMax](https://www.voidtools.com/support/everything/sdk/everything_setmax/)
- [Everything_GetTotResults](https://www.voidtools.com/support/everything/sdk/everything_gettotresults/)

## Query compatibility and safety

Everything 1.4.1 core syntax is the default compatibility profile. Check
`everything_status` before using features documented only for Everything 1.5;
the running Everything process, not the Python adapter, interprets the query.

Pass a bounded absolute directory through the `scope` argument. For example,
`scope="C:\Work\project"` is composed as the exact recursive folder term
`"C:\Work\project\"`, and returned paths are checked against that normalized
boundary. A partial query such as `path:"C:\Work\project"` can also match a
prefix sibling such as `C:\Work\project-backup` and is not equivalent.

Broad-query validation parses quotes, negation, grouping, and every OR branch.
Each possible branch must be independently narrow. Content, direct-disk, and
regular-expression searches require additional indexed narrowing conditions;
universal wildcards, exclusions, or a regex alone do not qualify.

Everything 1.5 `content*:` literal-tail forms and `from-disk:` are treated as
slow I/O operations. Use them only with a non-root scope and a separate indexed
filter in every affected branch.

Use `everything_syntax_help` for concise guidance and the official Everything
documentation for the complete grammar.

## Architecture

```text
AI agent
  -> one-shot process or MCP compatibility host
  -> Everything_Mew read-only tools
  -> SDK/IPC adapter
  -> Everything runtime
  -> Existing Everything index
  -> compact path candidates
  -> read/grep/ast-grep/LSP for selected files
```

The primary adapter uses the official asynchronous SDK reply-window flow with a
bounded 15-second wait, process-wide SDK serialization, unique reply IDs, and
state reset after operations. `everything_count` requests only the total count
with zero result flags and `Everything_SetMax(0)`.

The one-shot path validates one bounded JSON request, reuses the same tool
dispatcher, publishes one result, and exits. It adds no daemon, HTTP listener,
or process pool.

The lite MCP path implements the small required stdio lifecycle directly:
`initialize`, notifications, `ping`, `tools/list`, and `tools/call`. The
FastMCP server remains an optional compatibility layer over the same tool
functions and adapters.

## Usage examples

Count before returning a potentially large candidate set:

```text
everything_count(query="ext:md", scope="C:\Work\project")
```

Search within a bounded project directory:

```text
everything_search(
  query="config ext:json;yml;yaml !node_modules !.git",
  scope="C:\Work\project",
  limit=50
)
```

Other useful Everything query shapes:

```text
dm:thisweek ext:py;md;json
size:>100mb
regex:"gr(a|e)y" ext:txt
```

After discovery, use a content-aware tool on only the selected paths.

## Safety boundaries

Everything_Mew is read-only. It must not suggest or perform:

- file deletion, movement, renaming, quarantine, or cleanup;
- duplicate-removal workflows;
- Everything index or configuration mutation;
- unbounded full-drive result dumps;
- automatic HTTP service activation;
- content inspection disguised as indexed metadata search.

The query guard reduces accidental broad or slow searches; it is not an OS
sandbox. Run high-risk workloads under an appropriately restricted Windows
account or other operating-system isolation.

Report vulnerabilities through the private process in [SECURITY.md](SECURITY.md).
Do not attach credentials, private paths, or personal file contents to a public
issue.

## Validation

Run the local checks:

```powershell
py -m pytest -q
py -m ruff check .
py -m mypy --strict src tests
py -m build
```

The `v0.3.0-rc.1` candidate passed these local gates:

- 422 pytest cases, Ruff, and strict mypy across 31 source and test files on
  local Python 3.11;
- isolated installed-wheel lite MCP and one-shot lifecycles without
  `PYTHONPATH`, FastAPI, or FastMCP;
- four-tool read-only contract checks for both lite and compatibility paths;
- ten sequential and six concurrent live SDK one-shot searches with no
  surviving Everything_Mew process after completion;
- an actual Windows reboot check confirming zero idle Everything_Mew Python
  processes before and after the live calls;
- source and distribution inspection for credentials, environment files, SDK
  binaries, caches, and machine-specific path evidence.

Windows GitHub Actions verifies Python 3.11 and 3.14 for pushed commits and
pull requests. See the
[v0.3.0-rc.1 release notes](docs/releases/v0.3.0-rc.1.md) for the candidate
results and [CONTRIBUTING.md](CONTRIBUTING.md) for reproducible build and
installed-wheel verification commands.

## Release history

| Release line | Role | Package version | Git tag |
| --- | --- | --- | --- |
| 0.1 | Original FastMCP-based baseline | `0.1.0` | `v0.1.0` |
| 0.2 | Stable low-standby MCP release | `0.2.0` | `v0.2.0` |
| 0.3 | Current one-shot prerelease | `0.3.0rc1` | `v0.3.0-rc.1` |

Version 0.3 adds bounded one-shot commands and makes them the default Codex
workflow. The lite stdio server remains available for OpenCode and manual MCP
compatibility, and all four public tools remain unchanged.

Read the bilingual notes for [v0.1.0](docs/releases/v0.1.0.md),
[v0.2.0](docs/releases/v0.2.0.md), and the current
[v0.3.0-rc.1](docs/releases/v0.3.0-rc.1.md) candidate. The complete bilingual
history is in [CHANGELOG.md](CHANGELOG.md).

## Repository contents

- [`src/everything_mcp/`](src/everything_mcp/): Python MCP runtime and adapters.
- [`tests/`](tests/): protocol, safety, adapter, and E2E-oriented tests.
- [`skills/everything/SKILL.md`](skills/everything/SKILL.md): optional agent
  guidance for Everything searches.
- [`docs/AGENT_INSTALLATION_GUIDE.md`](docs/AGENT_INSTALLATION_GUIDE.md): safe
  host installation procedure.
- [`docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md`](docs/SDK_INSTALL_GUIDE_FOR_AGENTS.md):
  SDK-focused setup and validation.
- [`docs/releases/`](docs/releases/): bilingual release notes.
- [CONTRIBUTING.md](CONTRIBUTING.md): development and release reproduction.
- [SECURITY.md](SECURITY.md): private vulnerability reporting policy.
- [LICENSE](LICENSE): Apache License 2.0.

## Contributor notes

- Do not commit SDK DLLs, environment files, local MCP configuration, caches,
  build outputs, or machine-specific validation logs.
- Keep personal paths, email addresses, credentials, and user file contents out
  of examples and public artifacts.
- Point `EVERYTHING_EXE`, `EVERYTHING_SDK_DLL`, and `EVERYTHING_ES_EXE` only to
  trusted local Everything binaries.
- Keep `README.md` and `README.ko.md` structurally synchronized when changing
  commands, links, badges, requirements, or release claims.
- This project is licensed under Apache License 2.0.
