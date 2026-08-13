# Everything_Mew Codex One-Shot Runtime Design

**Date:** 2026-08-12
**Status:** Approved for implementation by the user's 2026-08-12 request
**Scope:** Local Codex Desktop integration; existing MCP clients remain supported

## 1. Problem

`everything_mcp.lite_stdio` reduced the private-memory cost of one idle server,
but Codex and OpenCode still retain one stdio server per host session. The live
2026-08-12 audit found 18 logical Everything_Mew servers represented by 36
Python rows: 16 servers owned by Codex and two by OpenCode. Their summed working
set was 405.85 MiB and summed private memory was 239.15 MiB.

The server already resets SDK query state after every operation. The remaining
cost is the persistent Python interpreter and stdio MCP session, so additional
SDK cleanup cannot solve the session multiplier.

## 2. Goals

1. Keep Everything_Mew discoverable through its Codex skill.
2. Start Python and load `Everything64.dll` only for an actual Everything_Mew
   operation.
3. Execute exactly one existing read-only tool contract and then exit.
4. Leave zero Everything_Mew Python processes after normal completion.
5. Preserve all four existing tool names, argument validation, query safety,
   result limits, adapter selection, and error redaction.
6. Keep `everything-mew-lite` and the optional FastMCP server available as
   explicit compatibility modes.

## 3. Non-Goals

- Do not stop or restart the shared `Everything.exe` indexing application.
- Do not add an HTTP server, daemon, broker, port, process pool, idle timer, or
  native frontend.
- Do not add a Windows Job Object while the runtime owns no document/helper
  process tree.
- Do not make the one-shot runner mutate files, indexes, or Everything settings.
- Do not kill MCP processes owned by already-running Codex or OpenCode hosts.
- Do not publish, tag, or push a release as part of this implementation.

## 4. Considered Approaches

### A. Skill plus one-shot Python CLI - selected

The skill invokes a fixed console entry point for each operation. The process
reads one bounded JSON request from standard input, dispatches through the
existing lite tool validation and server functions, writes one JSON result, and
exits. This removes the idle process instead of reducing it.

### B. Self-terminating stdio MCP - rejected

An MCP server that exits after a call or idle timeout breaks its established
stdio transport. Codex currently documents enablement and startup/tool
timeouts, but not lazy startup, idle shutdown, or call-time reconnect. Automatic
recovery therefore cannot be treated as a product contract.

### C. Shared daemon or native frontend - deferred

A daemon would require local authentication, upgrade coordination, crash
recovery, and a new service lifecycle. A native frontend would still remain
resident per session. Both are larger than the current four-tool, read-only
workflow requires.

## 5. One-Shot Contract

The new console commands are `everything-mew-once` and the compatibility alias
`everything-mcp-once`. They read one UTF-8 JSON object from standard input:

```json
{
  "schemaVersion": 1,
  "tool": "everything_search",
  "arguments": {
    "query": "ext:py",
    "scope": "C:\\Work\\project",
    "limit": 10,
    "metadata": false
  }
}
```

Requirements:

- Input is non-empty and no larger than 64 KiB.
- The top-level keys are exactly `schemaVersion`, `tool`, and `arguments`.
- `schemaVersion` is the integer `1`, not a Boolean or string.
- `tool` is one of the existing four tool names.
- `arguments` is a JSON object and is validated by the existing `ToolSpec`.
- No query, scope, path, environment value, or exception detail is written to
  stderr on an invocation failure.

Output is the existing MCP `CallToolResult` JSON shape. Exit codes are:

- `0`: a successful tool result was written to stdout;
- `1`: a validated tool call returned `isError: true`, and that bounded result
  was written to stdout;
- `2`: the invocation was malformed or no trustworthy result was written;
  stderr contains only `ONESHOT_INVOCATION_ERROR`.

The runner accepts no command, module, environment map, or executable path in
the request.

## 6. Runtime Flow

1. Codex activates the installed `everything-mew` skill for Windows metadata
   discovery.
2. The skill resolves the installed `everything-mew-once` entry point to an
   absolute path and sends one JSON request through stdin.
3. The runner validates the envelope before importing `everything_mcp.server`.
4. Existing lite dispatch validates tool arguments and invokes the existing
   server function.
5. Adapter selection loads the configured SDK DLL only when the tool needs it.
6. The runner serializes one result, flushes stdout, and exits.
7. The shell waits for process completion; Windows reclaims Python, the loaded
   DLL, message-only windows, and process handles. `Everything.exe` remains.

Count and search remain separate operations and therefore separate short-lived
processes. Batching is deferred unless measured startup latency becomes a real
workflow problem.

## 7. Codex and Compatibility Modes

The recommended Codex installation is skill-only at runtime:

- install the wheel and the skill;
- configure the skill with the resolved absolute one-shot entry point;
- retain the existing MCP configuration with `enabled = false` for rollback;
- fully restart Codex once so servers owned by the previous host exit.

The repository continues to ship `everything-mew-lite`,
`everything-mcp-lite`, `everything-mew`, and `everything-mcp`. OpenCode remains
on MCP compatibility mode until its own integration is deliberately changed.

Disabling the Codex MCP configuration prevents future server creation but does
not terminate processes owned by a running host. The migration must not ship a
name-based process killer.

## 8. Error and Cancellation Behavior

Tool validation failures use the existing redacted tool-error result and exit
`1`. Malformed envelopes, invalid UTF-8, oversized input, unknown tools, and
unexpected runner failures exit `2` without traceback or input disclosure.

The Everything SDK query already has a 15-second bounded wait and per-operation
window cleanup. The first release relies on normal process exit and the shell
host's process supervision. Add stronger containment only if a forced-timeout
test demonstrates a surviving owned Python process.

## 9. Verification

Automated gates:

1. Unit tests cover exact envelope validation, UTF-8, size, unknown tools,
   result/exit mapping, and redacted invocation failures.
2. Subprocess tests prove stdin/stdout framing, no traceback, installed console
   scripts, and clean EOF exit without FastMCP.
3. Existing tests, Ruff, strict mypy, wheel/sdist builds, and archive checks pass.
4. An isolated wheel installation performs a real SDK status and search.
5. Ten sequential and six concurrent real one-shot searches succeed, and an
   exact PID/start-time audit finds no newly surviving owned process.

Manual Codex acceptance after installation:

- a fresh Codex host has zero idle Everything_Mew Python processes;
- the skill can perform status, count, search, and syntax-help operations;
- ten repeated searches leave zero new Everything_Mew processes;
- two Codex tasks can search concurrently without a persistent server;
- re-enabling the preserved MCP entry restores manual compatibility.

## 10. Acceptance Criteria

The implementation is ready for release preparation when the four operations
remain behaviorally compatible, the default Codex workflow needs no persistent
Everything_Mew server, normal and concurrent one-shot calls leave no owned
process, no new production dependency or daemon exists, and documentation
clearly separates Codex one-shot mode from manual MCP compatibility mode.
