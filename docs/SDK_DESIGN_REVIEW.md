# Everything SDK Design Review

Review date: 2026-07-15

## Executive Finding

The project should keep its current backend strategy:

1. Everything SDK/IPC as the primary backend;
2. ES CLI as an optional fallback;
3. direct stdio as the default low-standby MCP transport;
4. FastMCP as a compatibility entrypoint;
5. no automatically enabled HTTP server or FastAPI service.

The architecture is appropriate, but the current implementation has a release-blocking
SDK sort defect and incomplete readiness/result diagnostics.

## Confirmed SDK Properties

The official SDK documentation confirms that:

- the SDK is a DLL/Lib wrapper over Everything IPC;
- the Everything client must be running in the background;
- the SDK supports Unicode, blocking and nonblocking queries, and is thread safe;
- a nonblocking query requires a reply window and reply-message handling;
- `Everything_IsDBLoaded` is needed to distinguish a ready database from a running
  client that is still loading;
- `Everything_Reset` frees the current search/result state;
- `Everything_CleanUp` should be the final SDK call;
- the actual sort and available result fields can differ from those requested.

These properties support the current synchronous SDK adapter. They do not justify a
FastAPI layer, a global SDK lock, or a nonblocking Windows message-loop rewrite.

## Release-Blocking Defect

`src/everything_mcp/adapters/sdk_ipc.py` currently maps:

```text
date_modified -> 11
```

The official `Everything_SetSort` table defines:

```text
11 = EVERYTHING_SORT_DATE_CREATED_ASCENDING
13 = EVERYTHING_SORT_DATE_MODIFIED_ASCENDING
```

A live SDK query confirmed that requesting the public `date_modified` sort currently
produces actual result-list sort value `11`. The adapter therefore returns creation-date
ordering under a date-modified label.

Required correction:

- map `date_modified` to `13`;
- replace numeric literals with named SDK constants;
- unit-test the complete public sort mapping;
- integration-test `Everything_GetResultListSort() == 13` on a controlled fixture with
  date-modified fast sort enabled, plus the documented fallback-note path.

## Required SDK Improvements

### 1. Database readiness

The current status check uses `Everything_GetMajorVersion` to infer that IPC is running.
That does not prove the database is loaded. Official documentation warns that queries
while loading can appear to return no results.

Add:

- `dbLoaded` to status output;
- a distinct running-but-loading state/note;
- query rejection with an actionable retry message while loading.

### 2. Version and target diagnostics

Bind the complete version getters and `Everything_GetTargetMachine`. Add optional status
fields for:

- `version`;
- `targetMachine`;
- `sdkDll` for local diagnostics.

These fields make IPC, version, and x86/x64 failures diagnosable without increasing
normal search payloads.

### 3. Deterministic SDK state cleanup

Use `Everything_Reset` after copying count/results into Python values. The reset should
run in `finally` so both successful and failed result processing release current SDK
search/result allocations and restore default state.

Do not call `Everything_CleanUp` after each query. The documentation defines it as the
last SDK call. Add it only if the implementation later introduces one explicit
process-lifetime SDK owner and a final shutdown hook.

### 4. Actual result verification

After each successful query:

- call `Everything_GetResultListSort` and compare it with the requested sort;
- call `Everything_GetResultListRequestFlags` and compare it with requested fields;
- return a concise note when a sort falls back or metadata is unavailable;
- omit unavailable metadata instead of implying that it was returned.

This requires a small adapter result envelope, such as a typed search batch containing
items, actual sort, available metadata, and notes.

### 5. Count and pagination

Keep `everything_count` total-only for the current release. Total-only applies to the
IPC payload, not only the public response: set request flags to zero and call
`Everything_SetMax(0)` before the query, then read `Everything_GetTotResults`. Live
verification on Everything 1.4.1 must show the same total with zero visible results.
File/folder subtotals and `Everything_SetOffset` pagination remain deferred until a
concrete client use case and live SDK tests require them.

### 6. Search syntax and exact scope addendum (2026-07-17)

Use Everything 1.4.1 core syntax as the default compatibility profile. A recursive
scope such as `C:\Work\project` must be sent as `"C:\Work\project\"`, not as
`path:"C:\Work\project"`; `path:` is a partial full-path modifier and can include a
prefix sibling. Returned SDK and ES paths are checked against the normalized scope as
defense in depth.

Known Everything 1.5 content aliases, alternate-data-stream content functions,
`content*:` literal-tail forms, nested content modifier chains, and `from-disk:` are
classified as slow I/O. Each affected OR branch requires a non-root scope and a
separate indexed narrowing filter. Regex containing `|`, spaces, `<`, or `>` must be
quoted before it reaches the local safety parser.

## ES Fallback Implications

The SDK review does not remove the need for ES fallback hardening:

- probe the running Everything client instead of treating an existing `es.exe` as ready;
- allow the internal 101st result used to detect truncation at the public limit of 100;
- specify ascending direction because ES defaults size/date sorts to descending while
  the SDK contract currently uses ascending sorts;
- request deterministic byte size, UTC ISO date, no-header, and no-grouping output;
- verify non-ASCII path decoding with an integration fixture.

## Confirmed Non-Goals

- Do not add FastAPI or automatically enable Everything HTTP.
- Do not replace blocking SDK queries without a reproducible hang requirement.
- Do not add a global SDK lock while the official SDK is documented as thread safe.
- Do not expand metadata or pagination merely because the SDK exposes more functions.
- Do not add file mutation or index-management tools.

## Final Priority

1. Correct the `date_modified` SDK sort constant.
2. Enforce lite MCP lifecycle and advertised schemas.
3. Close broad-query and ES fallback correctness gaps.
4. Add database readiness, SDK reset, and actual-result verification.
5. Add version/target diagnostics, CI, integration tests, and release evidence.

The low-standby direct stdio design remains the correct product direction. Release
readiness depends on correctness and reproducibility work, not another architecture
change.

## Official References

- [Everything SDK overview](https://www.voidtools.com/support/everything/sdk/)
- [Everything_Query](https://www.voidtools.com/support/everything/sdk/everything_query/)
- [Everything_SetSort](https://www.voidtools.com/support/everything/sdk/everything_setsort/)
- [Everything_IsDBLoaded](https://www.voidtools.com/support/everything/sdk/everything_isdbloaded/)
- [Everything_Reset](https://www.voidtools.com/support/everything/sdk/everything_reset/)
- [Everything_CleanUp](https://www.voidtools.com/support/everything/sdk/everything_cleanup/)
- [Everything_SetRequestFlags](https://www.voidtools.com/support/everything/sdk/everything_setrequestflags/)
- [Everything_SetMax](https://www.voidtools.com/support/everything/sdk/everything_setmax/)
- [Everything_GetTotResults](https://www.voidtools.com/support/everything/sdk/everything_gettotresults/)
- [Everything_GetResultListSort](https://www.voidtools.com/support/everything/sdk/everything_getresultlistsort/)
- [Everything_GetResultListRequestFlags](https://www.voidtools.com/support/everything/sdk/everything_getresultlistrequestflags/)
- [Everything_GetTargetMachine](https://www.voidtools.com/support/everything/sdk/everything_gettargetmachine/)
- [Everything Searching](https://www.voidtools.com/support/everything/searching/)
- [Everything 1.5 Search Syntax](https://www.voidtools.com/support/everything/search_syntax/)
- [Everything 1.5 Search Modifiers](https://www.voidtools.com/support/everything/search_modifiers/)
- [Everything 1.5 Search Functions](https://www.voidtools.com/support/everything/search_functions/)
- [ES command-line interface](https://www.voidtools.com/support/everything/command_line_interface/)
