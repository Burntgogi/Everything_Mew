# Everything Syntax and SDK Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Everything_Mew 0.2 enforce exact search scopes, count without materializing result rows, fail closed for known Everything 1.5 disk/content syntax, and ship one synchronized version-aware query guide.

**Architecture:** Keep the official Everything query engine authoritative and limit local parsing to safe composition and admission control. Compose a recursive 1.4-compatible scope as a quoted absolute path ending in `\`, verify returned paths against the same normalized boundary, and request zero visible SDK rows for count-only operations. Keep the 1.4.1 core syntax as the supported default while classifying known 1.5 content and forced-disk forms as slow I/O.

**Tech Stack:** Python 3.11+, ctypes Everything SDK/IPC, stdio MCP/FastMCP compatibility layer, pytest, Ruff, mypy, Hatch build.

## Global Constraints

- Windows-only and read-only behavior must remain unchanged.
- Everything 1.4.1 is the minimum supported runtime; 1.5-only syntax must not be required.
- FastAPI must not be introduced; `everything-mew-lite` must remain dependency-light.
- Search result hard cap remains 100 and metadata remains opt-in.
- Existing uncommitted 0.2 release preparation changes must be preserved.
- No commit, tag, push, release, or user-level configuration overwrite is performed without separately verifying its exact scope.

## Design Decisions

1. Use a quoted folder path with a trailing separator, such as `"C:\Work\project\"`, instead of `path:"C:\Work\project"`. The trailing separator creates a recursive folder boundary on Everything 1.4 and does not match prefix siblings such as `project-backup`.
2. Add a Python path-boundary check after SDK/ES result conversion as defense in depth. This is not the primary filter because post-filtering alone cannot correct `everything_count` totals or recover valid rows displaced by prefix-sibling matches.
3. Use `Everything_SetRequestFlags(0)` and `Everything_SetMax(0)` for count. `Everything_GetTotResults` remains authoritative and live SDK verification must show the same total with zero visible rows.
4. Canonicalize Everything identifiers by lowercasing and removing optional hyphens. Treat known content aliases, byte-stream content functions, alternate-data-stream content functions, and `from-disk:` as slow I/O requiring a non-root scope plus a separate indexed narrowing filter.
5. Keep one repository skill named `everything-mew`. The MCP syntax helper and installed Codex skill must use the same 1.4-default, 1.5-aware guidance; user-level synchronization is verified by a dry-run diff before replacement.

---

### Task 1: Add exact-scope regression tests

**Files:**
- Modify: `tests/test_safety.py`
- Modify: `tests/test_es_cli.py`
- Modify: `tests/test_sdk_ipc.py`

**Interfaces:**
- Consumes: `compose_query(query: str, scope: str | None) -> str`
- Produces: `is_path_within_scope(path: str, scope: str | None) -> bool`

- [x] Add tests proving `C:\Users\name\.config\opencode` composes with a trailing `\` and cannot match `opencode-backups`.
- [x] Add drive, spaced path, forward-slash, UNC, and relative-scope cases.
- [x] Add a server-level test that discards an adapter hit outside the normalized scope.
- [x] Run `python -m pytest tests/test_safety.py tests/test_es_cli.py tests/test_sdk_ipc.py -q` and confirm the new assertions fail for the existing `path:"..."` behavior.

### Task 2: Implement exact scope composition and defense-in-depth filtering

**Files:**
- Modify: `src/everything_mcp/query.py`
- Modify: `src/everything_mcp/server.py`
- Test: `tests/test_safety.py`
- Test: `tests/test_es_cli.py`

**Interfaces:**
- Produces: `is_absolute_scope(scope: str | None) -> bool`
- Produces: `is_path_within_scope(path: str, scope: str | None) -> bool`
- Changes: `compose_query()` emits `"<absolute-scope>\" <query>`

- [x] Reject non-empty relative scopes during broad-query validation and direct composition.
- [x] Normalize drive and UNC scopes without resolving or touching the filesystem.
- [x] Append exactly one trailing backslash inside the quoted Everything path term.
- [x] Filter `SearchHit` values against the normalized scope before truncation and serialization.
- [x] Run the Task 1 tests and confirm they pass.

### Task 3: Add and implement zero-row SDK count

**Files:**
- Modify: `tests/test_sdk_ipc.py`
- Modify: `src/everything_mcp/adapters/sdk_ipc.py`

**Interfaces:**
- Changes: `SdkIpcAdapter.count()` requests no result fields and zero visible rows, then returns `Everything_GetTotResults()`.

- [x] Add a test asserting count calls `Everything_SetRequestFlags(0)` and `Everything_SetMax(0)` before `Everything_QueryW(False)`.
- [x] Run that test and confirm it fails because count currently requests full paths and leaves max at `0xffffffff`.
- [x] Set request flags and max to zero without changing search behavior.
- [x] Run all SDK adapter tests and confirm reset, timeout, and process-lock behavior remains green.
- [x] Run a live bounded SDK comparison and confirm current total equals max-zero total while max-zero visible results equal zero.

### Task 4: Guard 1.5 slow-I/O syntax

**Files:**
- Modify: `tests/test_safety.py`
- Modify: `src/everything_mcp/query.py`

**Interfaces:**
- Produces: canonical identifier handling for optional hyphens.
- Changes: `_TermInfo` records forced disk access separately from content access.

- [x] Add parameterized tests for `ansi-content:`, `ascii-content:`, `binary-content:`, `byte-stream-content:`, `octet-stream-content:`, UTF content aliases, alternate-data-stream content forms, and `from-disk:`.
- [x] Assert each slow form is rejected with scope alone, accepted with a non-root scope plus `ext:`, and rejected at drive/UNC roots.
- [x] Run the new tests and confirm the currently unrecognized 1.5 forms fail the assertions.
- [x] Canonicalize identifiers and classify content and forced-disk forms.
- [x] Apply the existing content safety rule to all classified slow-I/O forms and every OR branch.
- [x] Cover `content*:` literal tails and nested modifier chains such as `binary:content:` and `dot-all:regex:content:` by scanning the full function chain conservatively.
- [x] Run the complete safety matrix.

### Task 5: Make syntax help and repository skill version-aware

**Files:**
- Modify: `src/everything_mcp/syntax.py`
- Modify: `skills/everything/SKILL.md`
- Modify: `tests/test_syntax.py`
- Modify: `tests/test_contracts.py`

**Interfaces:**
- Changes: `everything_syntax_help()` gains `compatibility`, `scope`, and expanded safety guidance while preserving existing topic names.

- [x] Add tests requiring an Everything 1.4.1 default profile, exact recursive scope examples, OR precedence, quoted regex operators, content restrictions, and 1.5 slow-I/O warnings.
- [x] Add a contract test requiring repository skill metadata `name: everything-mew` and no OpenCode-only compatibility declaration.
- [x] Run the tests and confirm the current compact helper and repository skill fail them.
- [x] Update syntax topics and default output without turning the helper into a full manual.
- [x] Update the repository skill with the same supported profile and examples.
- [x] Dry-run compare the repository skill with `%USERPROFILE%\.codex\skills\everything-mew\SKILL.md`, then synchronize the approved user-level skill and verify matching SHA-256 hashes.

### Task 6: Document the hardening in release materials

**Files:**
- Modify: `README.md`
- Modify: `README.ko.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/releases/v0.2.0-rc.1.md`
- Modify: `docs/SDK_DESIGN_REVIEW.md`

**Interfaces:**
- Produces: English and Korean operator guidance and release notes aligned with implemented behavior.

- [x] Document the Everything 1.4.1 default profile and that current official syntax/function pages marked 1.5 are not copied wholesale.
- [x] Document exact scope semantics, zero-row count, known 1.5 slow-I/O admission rules, and regex quoting.
- [x] Correct the SDK design review's total-only count section to require `SetMax(0)` and zero request flags.
- [x] Add direct links to official Searching, Search Syntax, Search Modifiers, Search Functions, SetMax, and GetTotResults documentation.
- [x] Extend release consistency tests where needed.

### Task 7: Verify behavior, quality, and packaging

**Files:**
- Verify only; do not change unrelated files.

**Interfaces:**
- Consumes: all preceding tasks.

- [x] Run `python -m pytest -q` and require exit code 0.
- [x] Run `python -m ruff check .` and require exit code 0.
- [x] Run `python -m mypy src tests` and require exit code 0.
- [x] Run `python -m build` and inspect wheel/sdist contents.
- [ ] Run live Codex MCP status and bounded scope-prefix E2E checks; require SDK/IPC, database loaded, x64, one in-scope result, and no prefix-sibling result.
- [x] Repeat bounded count/search calls and confirm no query failure or persistent extra MCP process.
- [x] Review `git diff --check`, `git diff --stat`, and the complete diff for regressions or unrelated edits.
- [x] Compare every implemented requirement against this plan and record any remaining limitation explicitly.

## Verification Record

- 2026-07-17: 407 pytest cases, Ruff, and strict mypy passed on Python 3.11.
- 2026-07-17: Hatch built `everything_mew-0.2.0rc1` wheel and sdist; archive contents were inspected.
- 2026-07-17: A clean temporary virtual environment installed the wheel without source-path dependence and passed version, syntax safety, SDK status, exact count, and exact search smoke assertions.
- 2026-07-17: Everything 1.4.1.1032 x64 with a loaded database returned the same total (1) for a normal bounded query and a zero-row query; `SetMax(0)` returned zero visible rows.
- 2026-07-17: Ten fresh SDK processes each returned exactly one in-scope DLL, used about 20.6-20.8 MiB working set and 11.8-12.0 MiB private memory, and left no additional MCP process.
- 2026-07-17: An isolated candidate-wheel lite MCP session ran `size:>100mb` against Everything 1.4.1.1032. Its count was 1,005; all SDK oracle paths were unique and over 100 MiB, size ordering was monotonic, and the public first 100 rows exactly matched the oracle.
- Known limitation: Everything 1.5 property functions outside the classified content and forced-disk forms can be indexed or disk-backed depending on the local Everything configuration. The server does not inspect that per-property index state dynamically; the supported default remains Everything 1.4.1 core syntax, and clients must check status before deliberately using 1.5-only functions.
- Operational activation note: a Codex-hosted MCP process started before these edits continues to serve its already-loaded code until Codex restarts. New source and installed-wheel processes passed the current E2E checks; no reinstall or configuration edit is required for the existing `.pth` setup.
