# Everything SDK Design Review

## Summary

The current Skill/MCP design is correctly SDK-centered. `es.exe` is optional fallback only; it is not required when SDK/IPC is healthy.

Official SDK docs confirm the SDK provides a DLL/Lib interface over IPC, requires the Everything client running in the background, and exposes direct APIs for search, counts, sort, request flags, and metadata.

## Current Strengths

- SDK/IPC is the primary backend.
- ES CLI remains fallback only.
- HTTP is deferred and not auto-enabled.
- MCP exposes only read-only tools:
  - `everything_status`
  - `everything_count`
  - `everything_search`
  - `everything_syntax_help`
- Search output defaults to path-first.
- Metadata is opt-in.
- Broad/root searches are guarded.
- Scope composition uses `path:"..."` style quoting.
- SDK adapter already uses:
  - `Everything_SetSearchW`
  - `Everything_SetSort`
  - `Everything_SetRequestFlags`
  - `Everything_SetMax`
  - `Everything_QueryW`
  - `Everything_GetTotResults`
  - `Everything_GetNumResults`
  - `Everything_GetResultFullPathNameW`
  - `Everything_GetResultSize`
  - `Everything_GetResultDateModified`
  - `Everything_GetResultAttributes`

## SDK-Driven Gaps to Consider

### 1. Database readiness

Official SDK docs expose `Everything_IsDBLoaded`. The current status check confirms SDK runtime/version availability, but does not explicitly report database-loaded state.

Recommended enhancement:

- Add `dbLoaded` to `everything_status` output.
- If DB is not loaded, return actionable note: wait for Everything indexing to finish.

### 2. Version and target-machine reporting

Official APIs include version getters and `Everything_GetTargetMachine`.

Recommended enhancement:

- Add optional status fields:
  - `version`
  - `targetMachine`
  - `sdkDll`

This helps diagnose x86/x64 and runtime mismatch issues.

### 3. Result-list verification

Official SDK provides `Everything_GetResultListSort` and `Everything_GetResultListRequestFlags`.

Recommended enhancement:

- After query, verify actual sort/request flags.
- If requested metadata was not available, add `notes` or omit unavailable fields explicitly.

### 4. File/folder counts

Official SDK exposes file/folder count APIs, but docs note some are unsupported when request flags are used.

Recommended enhancement:

- Keep `everything_count` simple with total count for MVP.
- Add optional future fields only after testing with and without request flags:
  - `fileCount`
  - `folderCount`

### 5. Offset/pagination

SDK supports `Everything_SetOffset`.

Recommended enhancement:

- Add `offset` input to `everything_search` only if paging becomes necessary.
- Maintain hard cap per page.

### 6. Date handling

Unknown dates can be returned as sentinel values.

Recommended enhancement:

- Guard against `0xFFFFFFFFFFFFFFFF` before converting FILETIME.

### 7. More metadata fields

Official SDK offers more result fields than MVP exposes.

Possible future metadata fields:

- extension
- dateCreated
- dateAccessed
- folder/file result type
- highlighted path/file name

Keep MVP minimal unless a scenario needs these fields.

## Skill Design Recommendations

Add a short SDK readiness section to the skill:

- If `everything_status.backend != "sdk-ipc"`, explain that SDK DLL or Everything runtime is missing.
- If DB is not loaded, wait or ask user to open Everything.
- Use `metadata=true` only when size/date/attributes are needed.
- Never use Everything for cleanup; only return candidate paths/groups.

## Conclusion

The MCP is aligned with official SDK architecture. The next useful improvements are diagnostics-oriented rather than feature-heavy:

1. expose DB-loaded state;
2. expose SDK version/target machine;
3. verify actual request flags/sort;
4. guard unknown FILETIME values;
5. document SDK installation and restart requirements for agents.
