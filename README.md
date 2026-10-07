<p align="center">
  <img src="docs/assets/everything-mew-banner.png" width="420" alt="Everything_Mew cat mascot helping an AI agent discover file paths">
</p>

<h1 align="center">Everything_Mew</h1>

<p align="center"><strong>Index-backed file discovery for AI agents on Windows: find files in about 0.1 s where a directory walk takes 10 to 30 s.</strong></p>

<p align="center">
  <a href="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Burntgogi/Everything_Mew/actions/workflows/ci.yml/badge.svg"></a>
  <a href="https://github.com/Burntgogi/Everything_Mew/releases/tag/v0.4.0"><img alt="Stable v0.4.0" src="https://img.shields.io/badge/stable-v0.4.0-5865F2"></a>
  <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078D4">
  <img alt="Python 3.11 through 3.14" src="https://img.shields.io/badge/Python-3.11--3.14-3776AB">
  <img alt="Read-only MCP tools" src="https://img.shields.io/badge/MCP-read--only-1F883D">
  <a href="LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-blue"></a>
</p>

<p align="center">
  <a href="README.ko.md">한국어</a> ·
  <a href="#why-use-the-everything-index">Benchmarks</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#tools">Tools</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#safety-boundaries">Safety</a> ·
  <a href="#release-history">Release notes</a>
</p>

## Overview

Everything_Mew connects AI agents to the local
[Everything](https://www.voidtools.com/) index. Everything already keeps every
file and folder name of the indexed volumes in memory. Everything_Mew lets an
agent query that index to find files by name, path, extension, size, and date.
The agent then reads only the selected files with its normal tools.

Coding agents usually find files by walking directories: Claude Code's Glob
runs ripgrep over the tree, and shell fallbacks run `Get-ChildItem -Recurse`.
A walk reads every directory on each call, so its time grows with the tree.
An index query does not walk the tree, so its time stays near 0.1 s.

The project is Windows-only and exposes four read-only MCP tools. It does not
edit files, change the Everything index, or enable the Everything HTTP server.
The repository also contains an optional agent skill with concise Everything
syntax guidance. The Python wheel installs only the MCP runtime and console
entry points. The runtime has no third-party dependencies and needs no
Everything SDK DLL.

## Why use the Everything index

We measured the search paths of Claude Code on one Windows 11 computer with
Everything 1.4.1.1024 and about 8.8 million indexed files. Glob is Claude
Code's embedded ripgrep, run with the exact arguments of its Glob tool.
PowerShell is a recursive `Get-ChildItem`. Everything_Mew is one MCP tool call
to a running `everything-mew-lite` server. Each value is a median of repeated
runs.

| Search | Glob | PowerShell | Everything_Mew |
| --- | ---: | ---: | ---: |
| `*.toml`, user profile (603k files) | 9.0 s | 25.7 s | **0.15 s** |
| `pyproject.toml`, user profile (603k files) | 10.2 s | 25.8 s | **0.11 s** |
| name contains `lite`, user profile (603k files) | 9.9 s | 28.8 s | **0.18 s** |
| `.py` changed today, user profile (603k files) | not supported | 29.3 s | **0.18 s** |
| `*.toml`, one repository (36k files) | 0.35 s | 1.2 s | **0.11 s** |

The results matched where the methods are comparable: each method found the
same 553 `.toml` files and the same 30 `pyproject.toml` files.

We also ran the same tasks end to end with `claude -p` (Sonnet, two runs each):

| Task | Built-in tools only | With Everything_Mew |
| --- | --- | --- |
| Find every `pyproject.toml` in the user profile | 27.2 s, 3 turns, one wrong count | **18.8 s, 2 turns, both correct** |
| Find `.py` files changed today in the user profile | 216.7 s, one wrong answer | **16.5 s, 2 turns, both correct** |
| List `.toml` files in one repository | **7.3 s** | 11.2 s |

What this means for Windows agent work:

- **Large or unknown locations gain the most.** A user profile, several
  projects, or "somewhere on this drive" take seconds to minutes to walk.
  Everything answers in a fraction of a second, so the agent stops waiting on
  file discovery.
- **Date and size questions become one call.** Glob cannot filter by date or
  size. Without the index, the agent writes a slow shell pipeline, and in our
  runs that pipeline gave a wrong answer.
- **Fewer and cheaper turns.** One `everything_search` call returns the paths
  and `totalCount`, so the agent does not need a separate count call.
- **Small known folders do not gain.** Inside one repository, Glob is already
  fast. The remaining gap in the last row is start-up: `claude -p` spends
  about 3 s connecting to any MCP server, while the Everything_Mew handshake
  takes 0.12 s. An interactive session pays this cost once, not per search.

The full method, raw numbers, and limits are in
[the 2026-10 audit](docs/AUDIT_2026-10_LIFECYCLE_NATIVE_IPC.md#claude-code-benchmark).
Your numbers depend on the disk, the tree size, and Everything's index
settings.

## How it works

Use Everything_Mew as a low-token discovery layer, then switch to the normal
content or code tool for the selected paths:

```text
everything_search (check totalCount) -> read/grep/ast-grep/LSP on selected paths
```

- `everything_search` returns compact path-first candidates with optional
  metadata, plus `totalCount`: Everything's full match count from the same
  query, so one call both sizes and samples the result.
- `everything_count` checks result volume without materializing any paths.
- `read`, `grep`, `ast-grep`, and LSP remain responsible for file contents and
  code semantics.

For Codex, the recommended path is the agent skill plus
`everything-mew-once`. Each request starts one short-lived process, returns one
result, and exits, so no Everything_Mew Python process remains while unused.
The shared `Everything.exe` indexer continues running.

OpenCode, Claude Code, and other MCP hosts use `everything-mew-lite`. The host
keeps that stdio process for its session, so it is kept as a small protocol
broker: every Everything tool call runs in a fresh one-shot worker process that
exits after answering. The worker's memory returns to Windows after each call,
and a stuck call is stopped by killing its worker. See
[Runtime lifecycle](#runtime-lifecycle).

## Quick start

### 1. Prepare Everything and Python

You need:

- Windows with the standard Everything application installed and running;
- CPython 3.11 through 3.14.

Everything Lite is not supported because it does not expose the IPC interface.
You do not need the Everything SDK DLL: Everything_Mew speaks the documented
Everything IPC protocol itself.

### 2. Install version 0.4.0 or later

Version 0.4.0 is the first release that enforces the allowed-root policy.
Version 0.3.0 ignores the policy settings below. Install the `v0.4.0` tag:

```powershell
git clone https://github.com/Burntgogi/Everything_Mew.git
cd Everything_Mew
git checkout --detach v0.4.0
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\python.exe -c "from everything_mcp.policy import SearchPolicy; assert SearchPolicy().denial_reason(None) is not None; print('Allowed-root policy available')"
```

Stop if the policy check fails. Do not enable a search host until the check
passes.

### 3. Register the lite server in Claude Code

Set the directories the server may search as a user environment variable. The
value is a JSON array of absolute paths. Claude Code passes its environment to
the server.

```powershell
[Environment]::SetEnvironmentVariable('EVERYTHING_MCP_ALLOWED_ROOTS', '["C:\\Users\\me","D:\\Projects"]', 'User')
$lite = (Resolve-Path ".\.venv\Scripts\everything-mew-lite.exe").Path
claude mcp add everything-mew -s user -- $lite
```

Do not pass the JSON with `claude mcp add -e` from Windows PowerShell 5.1.
That shell removes the double quotes from native command arguments, and the
server then rejects the value.

Open a new terminal, start Claude Code, and ask for a file search. The
`everything_search` tool loads with the session, so the agent can use it
without a tool-search turn.

### 4. Use the one-shot runner from Codex

Make the installed command available to the Codex process. Resolve it to an
absolute path before each invocation:

```powershell
$env:EVERYTHING_MCP_ALLOWED_ROOTS = '["C:\\Work\\project"]'
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
EVERYTHING_MCP_ALLOWED_ROOTS = '["C:\\Work\\project"]'
```

`enabled = false` makes the MCP unavailable; it does not sleep and wake on
demand. After changing this setting, fully restart Codex. Servers already owned
by a running Codex or OpenCode host exit only when that host closes. Before you
search, confirm that the one-shot `everything_status` result reports
`native-ipc` and a loaded database.

For OpenCode, keep the allowed roots in the parent environment and use its
literal environment placeholder:

```json
{
  "mcp": {
    "everything-mew": {
      "type": "local",
      "command": ["everything-mew-lite"],
      "enabled": true,
      "environment": {
        "EVERYTHING_MCP_ALLOWED_ROOTS": "{env:EVERYTHING_MCP_ALLOWED_ROOTS}"
      }
    }
  }
}
```

To use the Everything SDK DLL instead of native IPC, set `EVERYTHING_SDK_DLL`
to a trusted DLL that matches the Python architecture and set
`EVERYTHING_MCP_BACKEND=sdk`.

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
| `everything_count` | Counts a bounded query without returning paths. |
| `everything_search` | Returns compact, path-first candidates, `totalCount`, and optional metadata. |
| `everything_syntax_help` | Provides short, version-aware Everything query guidance. |

All four tools are read-only. The public MCP contract intentionally stops at
candidate discovery.

## Requirements

- **Operating system:** Windows.
- **Everything:** the standard application must be installed and running.
- **Everything SDK:** optional. The default native IPC backend needs no DLL;
  a trusted DLL matching Python's architecture is used only when
  `EVERYTHING_SDK_DLL` is configured.
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

For roots and scopes, use standard drive paths such as `C:\Work\project` or UNC
paths such as `\\server\share\project`; device and extended-length prefixes
(`\\.\` and `\\?\`) are unsupported.

Set `EVERYTHING_MCP_ALLOWED_ROOTS` to a JSON array of absolute, bounded Windows
directories before calling `everything_count` or `everything_search`. For
example, `['C:\\Work\\project']` is **not** JSON; use
`'["C:\\Work\\project"]'` as the PowerShell or TOML literal value. Every
search and count request must supply a `scope` inside one of those directories.
With no allowed roots configured, searches and counts are denied before the
Everything backend runs. A request for `metadata=true` also requires the
trusted process setting `EVERYTHING_MCP_ALLOW_METADATA=1`.

Operators who intentionally need the previous unrestricted index behavior can
set `EVERYTHING_MCP_ALLOW_UNSCOPED=1` instead of allowed roots. This mode may
search any indexed path and cannot be combined with `EVERYTHING_MCP_ALLOWED_ROOTS`.
The existing broad-query guard remains active in either mode. Set these values
in the host or one-shot process environment, never in tool arguments. Restart a
persistent MCP host after changing them.

Everything 1.4.1 core syntax is the default compatibility profile. Check
`everything_status` before using features documented only for Everything 1.5;
the running Everything process, not the Python adapter, interprets the query.

Pass a bounded absolute directory through the `scope` argument. For example,
`scope="C:\Work\project"` is composed as the exact recursive folder term
`"C:\Work\project\"`; the query expression is grouped under that term and
returned paths are checked against the normalized boundary. A partial query
such as `path:"C:\Work\project"` can also match a
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
  -> native IPC adapter (SDK DLL or ES CLI as fallbacks)
  -> Everything runtime
  -> Existing Everything index
  -> compact path candidates
  -> read/grep/ast-grep/LSP for selected files
```

The primary adapter speaks the documented Everything 1.4 IPC protocol
(`everything_ipc.h`) itself: one `QUERY2` request and one `LIST2` reply copied
and parsed with bounds checks. Compared with the SDK DLL it follows the same
wire rules but sends every message with `SendMessageTimeout` (the SDK uses an
unbounded `SendMessage`), allows `WM_COPYDATA` through UIPI on its reply window
so elevated agents still get replies, uses a unique reply ID per query with a
bounded 15-second wait, and keeps no global SDK state to reset.
`everything_count` requests only the total count with zero result flags and a
maximum of zero results, like `Everything_SetMax(0)`. `everything_search`
reports the reply's total count as `totalCount`.

Everything evaluates AND operands from left to right, and the trusted scope is a
full-path match. The server puts the grouped query before the scope only when
both conditions are true:

- the query starts with an indexed filter: `ext:`, `wfn:`, `file:`, `folder:`,
  an anchored wildcard such as `name*` or `*.py`, or `size:` and `dm:` when
  Everything reports that size and date modified are indexed;
- no term reads from disk, such as `content:` or `from-disk:`.

In all other cases the scope comes first, so Everything narrows to the scope
before it does slower work. Both orders give the same results. On a 1.4.1 index,
a scoped `ext:py` search took about 27 ms instead of 64 ms.

The one-shot path validates one bounded JSON request, reuses the same tool
dispatcher, publishes one result, and exits. It adds no daemon, HTTP listener,
or process pool.

The lite MCP path implements the small required stdio lifecycle directly:
`initialize`, notifications, `ping`, `tools/list`, and `tools/call`, reading
and writing UTF-8 regardless of the Windows code page. The FastMCP server
remains an optional compatibility layer over the same tool functions and
adapters.

### Runtime lifecycle

`everything-mew-lite` is a broker. It validates arguments, answers
`everything_syntax_help` from static text, and runs every other tool call in a
worker: the base Python interpreter in isolated mode (`-I -S`, no venv
redirector, no `.pth` processing, no `PYTHON*` variables) executing the
one-shot runner. The worker loads ctypes and the adapter, queries Everything,
prints one result, and exits, so its memory goes back to Windows.

| Mode | Resident broker after 10 searches | Per-call latency |
| --- | --- | --- |
| 0.3.x in-process lite | 22.8 MB working set, retained | about 88 ms |
| Worker (default) | 18.2 MB working set | about 110 ms, worker peak about 17 MB freed on exit |
| `EVERYTHING_MCP_EXECUTION=inprocess` | 20.5 MB working set, retained | about 34 ms |

Measured on Windows 11, Python 3.13, Everything 1.4.1.1024, with a scoped
`ext:py` search returning 20 paths. Treat these as one machine's evidence,
not guarantees.

| Variable | Default | Effect |
| --- | --- | --- |
| `EVERYTHING_MCP_EXECUTION` | `worker` | `inprocess` keeps the backend loaded in the server for lower latency. |
| `EVERYTHING_MCP_WORKER_TIMEOUT` | `30` | Seconds before a worker is killed and the call fails with `isError=true`. |
| `EVERYTHING_MCP_IDLE_EXIT_SECONDS` | unset | Exit the lite server after this many idle seconds. Only use it with hosts that restart stdio servers on demand. |
| `EVERYTHING_MCP_BACKEND` | `auto` | `native`, `sdk`, or `es` forces one backend. |
| `EVERYTHING_INSTANCE` | unset | Named Everything instance, for example `1.5a`. |

Failed calls are tool errors, not empty results: backend unavailability, query
failures, policy denials, and configuration errors set `isError=true` with an
`error.code`, and the one-shot runner exits with `1`.

## Usage examples

Find an exact file name. Read `totalCount` to see the full match count:

```text
everything_search(query="wfn:pyproject.toml", scope="C:\Work")
```

Count only, when the paths are not needed:

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
ext:py dm:today
dm:thisweek ext:py;md;json
size:>100mb
lite*
regex:"gr(a|e)y" ext:txt
```

Everything 1.4 has no `name:` function. A plain word such as `lite` already
matches names that contain it.

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

Know these trust limits:

- **File names are untrusted input.** Anyone who can create a file can choose
  its name, and the agent reads returned paths as text. Treat a path that looks
  like an instruction as data.
- **The Everything IPC window is not authenticated.** Any program that runs as
  the same Windows user can register the Everything window class. It can then
  read the queries and return false results. This applies equally to the SDK
  DLL and `es.exe`. Allowed roots still drop every returned path outside the
  scope.
- **Replies cross the UIPI boundary.** When the agent runs elevated and
  Everything does not, the reply window must accept `WM_COPYDATA` from a lower
  integrity level. The native IPC backend uses a random 32-bit reply ID for
  each query, so another program cannot easily inject a reply.

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

The `v0.4.0` release candidate passed these local gates on Python 3.13:

- 537 pytest cases, Ruff, and strict mypy across 37 source and test files;
- native IPC results identical to the Everything SDK DLL for six query, sort,
  and metadata combinations on a live Everything 1.4.1 index;
- live ASCII, Korean, and emoji file names through native IPC;
- `scripts/measure_lite_sessions.ps1` with two lite and two FastMCP sessions:
  all 12 owned processes exited and the independent PID recheck was clean;
- a Bandit scan with only low-severity notes for fixed-argument `subprocess`
  calls without a shell.

Windows GitHub Actions verifies Python 3.11 and 3.14 for pushed commits and
pull requests. See the
[v0.4.0 release notes](docs/releases/v0.4.0.md) for the final
results and [CONTRIBUTING.md](CONTRIBUTING.md) for reproducible build and
installed-wheel verification commands.

## Release history

| Release line | Role | Package version | Git tag |
| --- | --- | --- | --- |
| 0.1 | Original FastMCP-based baseline | `0.1.0` | `v0.1.0` |
| 0.2 | Stable low-standby MCP release | `0.2.0` | `v0.2.0` |
| 0.3 | One-shot release | `0.3.0` | `v0.3.0` |
| 0.4 | Current stable native IPC release | `0.4.0` | `v0.4.0` |

Version 0.4 removes the Everything SDK DLL requirement, runs each lite tool
call in a short-lived worker that returns its memory, and enforces the
allowed-root policy. The four public tools keep their names and arguments.
`everything_search` adds `totalCount`, and failures now report `isError=true`.

Read the bilingual notes for [v0.1.0](docs/releases/v0.1.0.md),
[v0.2.0](docs/releases/v0.2.0.md), [v0.3.0](docs/releases/v0.3.0.md), and the
current [v0.4.0](docs/releases/v0.4.0.md) release. The complete bilingual
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
