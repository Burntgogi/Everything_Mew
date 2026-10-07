# 2026-10 Audit: Process Lifecycle, SDK Rules, and Native IPC

Audit date: 2026-10-08. Base: `main` at `13b9578` (v0.3.0 plus the allowed-root
policy from #8 and #9). Environment: Windows 11, Python 3.13.5 x64,
Everything 1.4.1.1024 running, official Everything SDK sources and DLLs for
reference.

The local checkout was 17 commits behind `origin/main` when this audit
started; all findings below are against the current `main`.

## Goals

1. Return memory after each search: the agent-facing process should hold as
   little as possible between calls.
2. Use the Everything SDK rules precisely and remove avoidable cost.
3. Close the defects left open by the 2026-10-03 audit.

## Findings

### F1. Lite MCP retained the backend after the first call

`everything-mew-lite` imported ctypes, the SDK DLL, the adapters, and the
policy stack in-process on the first tool call and kept them for the host
session. Working set grew from 17.7 MB to 22.8 MB after one search and never
came back. A hung `SendMessage` inside the SDK could also block the server
indefinitely, because nothing could stop it short of killing the host's
server.

### F2. SDK rules the Python adapter did not follow

Read against `Everything.c` from the official SDK:

| SDK behaviour | Previous adapter | Effect |
| --- | --- | --- |
| `GetResultFullPathNameW` returns UTF-16 code units | compared with Python code points | any emoji path failed the whole search ("changed while copying") |
| Reply window calls `ChangeWindowMessageFilterEx(WM_COPYDATA, MSGFLT_ALLOW)` | not called | an elevated agent never received replies from a non-elevated Everything (15 s timeout) |
| `IsDBLoaded`, version, and query sends use plain `SendMessage` (the SDK itself notes "use SendMessageTimeout") | inherited through the DLL | a hung Everything blocks the caller without a bound |
| `LIST2.totitems` arrives with every query | discarded on search | agents needed a second `count` round trip |
| `IS_FAST_SORT` IPC | unused | agents could not tell which sorts are cheap |

The SDK DLL is also only needed because the protocol is wrapped in it: the
DLL keeps global state that must be `Everything_Reset` after every call, and
Everything does not ship it, so a missing DLL was the main installation
failure (`backend=none`).

### F3. Query term order cost

Everything evaluates AND operands left to right. The server always composed
`"<scope>\" <query>`, so the full-path scope match ran over the whole index
first. Measured median count times on this index:

| Query inside a scope | Scope first | Query first |
| --- | ---: | ---: |
| `ext:py` | 64 ms | 27 ms |
| `size:>1mb` | 86 ms | 23 ms |
| `dm:thisweek` | 67 ms | 26 ms |
| `*.py` | 73 ms | 38 ms |
| `lite*` | 69 ms | 24 ms |
| `lite` (plain word) | 72 ms | 182 ms |
| `*lite*` | 74 ms | 160 ms |

Anchored, indexed filters are cheap; unanchored substring scans are not. The
best order depends on the leading operand.

### F4. Open items from the 2026-10-03 audit

- Emoji paths failed (F2, row 1).
- Lite stdio used the Windows ANSI code page: Korean input was corrupted and
  emoji output crashed the server under CP949.
- Backend failures, query failures, and policy denials were reported with
  `isError=false`, and the one-shot runner exited `0`, so a failure looked
  like "no files".

### F5. Start-up cost of a fresh process

A worker pays interpreter start-up plus imports. `importlib.metadata`
(version lookup, 43 ms) and `dataclasses` (pulls in `inspect`, `ast`, `dis`;
about 19 ms) dominated, plus the venv redirector process and `.pth`
processing on every start.

## Changes

| Area | Change |
| --- | --- |
| Backend | New `native-ipc` adapter implements `QUERY2`/`LIST2` from `everything_ipc.h` directly: `SendMessageTimeout` everywhere, UIPI-allowed reply window, per-query reply IDs, bounds-checked parsing, UTF-16 with surrogates preserved, named instances via `EVERYTHING_INSTANCE`. Results were verified identical to the SDK DLL for six query/sort/metadata combinations. |
| Selection | `auto`: native IPC, then the SDK DLL only when configured, then ES CLI. `EVERYTHING_MCP_BACKEND` forces one. SDK and ES modules are imported only when selected. |
| Lifecycle | Lite is a broker; every Everything tool call runs `python -I -S` (base interpreter) with the one-shot runner and exits. Hard timeout (`EVERYTHING_MCP_WORKER_TIMEOUT`, 30 s) kills stuck workers. `EVERYTHING_MCP_EXECUTION=inprocess` opts out; `EVERYTHING_MCP_IDLE_EXIT_SECONDS` optionally ends an idle server. |
| Query | Grouped query goes before the scope when it leads with `ext:`, `size:`, `dm:`, `wfn:`, `file:`/`folder:`, or an anchored or `*.ext` wildcard; otherwise the scope leads. Grouping, and therefore OR safety, is unchanged. |
| Results | `everything_search` returns `totalCount`; `everything_status` returns `fastSorts`. Tool text is compact JSON. |
| Errors | Failures carry `error.code` (`denied`, `backend_unavailable`, `query_failed`, `configuration_error`) and `isError=true`; one-shot exits `1`. Policy denial is checked before any backend is touched. |
| Transport | Lite reads and writes UTF-8 bytes; invalid UTF-8 becomes a parse error; unpaired surrogates fall back to escaped JSON. |
| SDK DLL | Kept as an optional backend with the emoji length check and UIPI filter fixed. |
| Start-up | Version is resolved only at `initialize`; hot-path dataclasses became `NamedTuple` or slotted classes. Worker imports fell from about 88 ms to 53 ms. |

## Measurements

Same machine, scoped `ext:py` search returning 20 paths, ten calls.

| | Resident after 10 searches | Warm latency |
| --- | --- | --- |
| Before (in-process lite) | 22.8 MB WS / 14.5 MB private, retained | 88 ms |
| Worker (default) | 18.2 MB WS / 12.2 MB private | 110 ms; worker peak 17 MB WS, freed on exit |
| In-process (opt-in) | 20.5 MB WS / 12.6 MB private | 34 ms |

`scripts/measure_lite_sessions.ps1` (2 sessions, 2 cycles, SDK backend) also
passed: lite 13.4 MiB private per session against FastMCP 72 MiB, 12 owned
PIDs all exited, independent PID recheck clean.

Tests: 549 passed (443 before, 106 added), Ruff clean, strict mypy clean on
`src` and `tests`. New live tests skip when Everything is not running.

## Claude Code benchmark

Engine level, medians: Claude Code's Glob is its embedded ripgrep run as
`rg --files --null --glob P --sort=modified --no-ignore --hidden`; PowerShell
is a recursive `Get-ChildItem`; Everything_Mew is a tools/call round trip to a
running lite server.

| Scope | Query | Glob | PowerShell | Everything_Mew (worker) |
| --- | --- | ---: | ---: | ---: |
| repository, 36k files | `*.toml` | 348 ms | 1,180 ms | 106 ms |
| user profile, 603k files | `*.toml` | 9.0 s | 25.7 s | 148 ms |
| user profile, 603k files | `pyproject.toml` | 10.2 s | 25.8 s | 114 ms |
| user profile, 603k files | `.py` modified today | not possible | 29.3 s | 183 ms |

End to end with `claude -p` (Sonnet), mean of two runs:

| Task | Built-in tools | Everything_Mew before | Everything_Mew after |
| --- | --- | --- | --- |
| `pyproject.toml` in the profile | 27.2 s, 3 turns, one wrong count | 22.6 s, 4 turns | 18.8 s, 2 turns |
| `.py` modified today in the profile | 216.7 s, one wrong answer | 20.9 s, 3 turns | 16.5 s, 2 turns |
| `.toml` in the repository | 7.3 s, 2 turns | 16.1 s, 3 turns | 11.2 s, 2 turns |

Two avoidable turns were found in the transcripts and removed:

1. Claude Code deferred the MCP tools behind a `ToolSearch` turn in every run.
   `everything_search` now sets `_meta["anthropic/alwaysLoad"]`.
2. The model guessed `name:pyproject.toml`; Everything 1.4 has no `name:`
   function and searched the literal text, silently returning zero, and the
   model retried. The tool description now shows 1.4 syntax, and empty results
   name the unknown function.

After both changes, tool execution outside model time is 0.2 to 0.4 s with
Everything_Mew against 0.4 s (repository) to 94 s (profile) with built-in
tools. The repository task's remaining gap is start-up: `claude -p` spends
about 3 s more connecting any MCP server, while the lite handshake itself takes
124 ms. An interactive session pays that once, not per search.

## Security review

Scope: every change in this audit. Bandit reported only low-severity notes for
`subprocess` calls with fixed argument lists and no shell. A manual review
found three issues in this audit's own changes and fixed them before release:

| Issue | Risk | Fix |
| --- | --- | --- |
| The scope moved after a leading `ext:` filter even when the query also had `content:` or `from-disk:` | Everything evaluates left to right, so it would read every matching file in the whole index before it applied the scope: heavy disk I/O, and reads outside the allowed roots | A query with any disk-reading term always keeps the scope first |
| `size:` and `dm:` counted as cheap without checking the index | With those properties unindexed, Everything reads them from disk for every candidate | Native IPC asks `IS_FILE_INFO_INDEXED` and uses the answer; other backends never lead with them |
| The worker bootstrap prepended its package directory to `sys.path` | For an installed wheel that directory is site-packages, so a module there could shadow the standard library | The directory is appended |
| Native IPC reply IDs were sequential | With `WM_COPYDATA` allowed through UIPI, a lower-integrity program could guess the ID and inject results into an elevated agent | Reply IDs are random 32-bit values |

A second pass patched three trust limits that the first pass had only
documented:

| Limit | Patch | Evidence |
| --- | --- | --- |
| A same-user program can register the Everything window class, read queries, and return false results | Native IPC resolves the window's owner (`GetWindowThreadProcessId`, `QueryFullProcessImageNameW`) and fails closed unless it is `EVERYTHING_EXE`. Program Files is administrator-protected, so a non-elevated program cannot plant that image | A separate process registered the class and returned a fake path. With the check, the call failed and the impostor received no query; without it, the fake path was accepted |
| The reply window always accepted `WM_COPYDATA` from lower integrity levels | The exception is added only when the owner's token integrity is lower than the agent's | Live: a medium-integrity agent and Everything left UIPI closed |
| Attacker-chosen file names reach the model | Model-visible text escapes format (`Cf`) and surrogate (`Cs`) characters such as U+202E and zero-width spaces; instructions and the search description mark paths as untrusted data. `structuredContent` keeps exact names | Unit tests round-trip the escaped JSON to the original names |

Each check costs about 0.03 ms per call. Remaining limits: the forced `sdk`
and `es` backends do not verify the owner; ordinary words in a file name can
still attempt prompt injection; and the worker inherits the host environment,
though `-I -S` keeps `PYTHON*` variables and `.pth` files from affecting it.

Other checks found no issue: policy denial still runs before any backend call,
compose grouping still binds OR branches to the scope, LIST2 parsing rejects
out-of-bounds data, every message to Everything has a timeout, and error
messages expose only configuration names and the Everything window class.

## Trade-offs and remaining work

- The worker model costs about 75 ms per call against in-process execution.
  For an agent loop that already spends seconds per tool call, returned memory
  and a killable worker were judged worth it; hosts that prefer latency can
  set `EVERYTHING_MCP_EXECUTION=inprocess`.
- Idle exit is off by default: MCP hosts generally do not restart a stdio
  server that exits, so enabling it can leave the tools unavailable.
- The cheap-filter list assumes Everything's default index (size and date
  modified indexed). Disabling those indexes only loses the speed-up.
- The package version is still `0.3.0`; a release should bump it so the
  native IPC runtime can be told apart from the published wheel.
- The sdist still omits documents that two repository tests read; those tests
  should skip outside a Git checkout.
