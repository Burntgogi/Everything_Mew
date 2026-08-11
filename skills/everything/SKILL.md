---
name: everything-mew
description: Use when locating files or folders by name, path, extension, date, size, attributes, or other Everything-indexed Windows filesystem metadata.
---

# Everything

Use this skill when a user needs to find candidate files or folders on Windows by metadata that Everything indexes. Everything is a fast path discovery layer, not a file reader, code search engine, cleanup tool, or project mapper.

## Supported syntax profile

- Treat Everything 1.4.1 core syntax as the default compatibility profile.
- Call `everything_status` before using a feature documented only for Everything 1.5.
- The SDK transports the query string; Everything itself interprets the search syntax.
- Do not copy the current 1.5 Search Syntax, Search Modifiers, or Search Functions manuals wholesale into a 1.4 query.

## Use Everything for

- File or folder names.
- Known or partial paths.
- Extensions such as `ext:md` or `ext:ts;tsx`.
- Date filters such as `dm:today` or `dm:thisweek`.
- Size filters such as `size:>100mb`.
- File or folder filters such as `file:` and `folder:`.
- Attribute or other Everything-indexed metadata lookup.

## Don't use Everything for

- Reading file contents.
- Understanding code semantics.
- Symbol lookup, references, or diagnostics.
- Broad full-drive inventory dumps.
- Deleting, moving, renaming, quarantining, deduping, or cleaning files.
- Changing Everything indexes or configuration.
- Enabling HTTP automatically.

## Execution contract

In Codex, invoke the installed one-shot runner once per tool request. Resolve
the runner to an absolute path, pass one JSON object on stdin, wait for exit,
and only then parse stdout:

```powershell
$runner = "C:\replace\with\absolute\path\to\everything-mew-once.exe"
if (-not [IO.Path]::IsPathFullyQualified($runner) -or -not (Test-Path -LiteralPath $runner -PathType Leaf)) {
    throw "Configure the absolute Everything_Mew one-shot runner path."
}
$request = @{
    schemaVersion = 1
    tool = "everything_search"
    arguments = @{
        query = "ext:py"
        scope = "C:\Work\project"
        limit = 10
        metadata = $false
    }
} | ConvertTo-Json -Compress -Depth 4
$resultJson = $request | & $runner
if ($LASTEXITCODE -notin 0, 1) { throw "Everything_Mew one-shot invocation failed." }
$result = $resultJson | ConvertFrom-Json
```

Never evaluate a path or request as shell code. Exit `0` publishes success,
exit `1` publishes a tool error, and exit `2` means stdout is not trustworthy.
Use normal filesystem tools to read any selected path. OpenCode or a manual MCP
host may continue to use `everything-mew-lite` as a compatibility server.

## Default workflow

1. Decide whether the task is metadata discovery. If it is content or code understanding, use `read`, `grep`, `ast-grep`, or LSP instead.
2. Build a scoped Everything query. Pass a known absolute project directory through the `scope` argument, then add extension, date, size, and noisy-directory exclusions.
3. Count before broad search. If the query is broad, ambiguous, or unscoped, call `everything_count` first.
4. Refine until the result size is useful.
5. Call `everything_search` with path-first output and `metadata=false` unless metadata is required.
6. Inspect only selected candidate paths with `read`, `grep`, `ast-grep`, or LSP.

Preferred chain:

```text
everything_count -> everything_search -> read/grep/ast-grep/LSP
```

## Query rules

- Pass an absolute recursive scope, for example `scope="C:\Work\project"`. The server composes the 1.4-compatible boundary `"C:\Work\project\"`.
- Do not use `path:"C:\Work\project"` as a directory boundary. `path:` performs a partial match on the full path and can also match `C:\Work\project-backup`.
- Exclude noisy folders when useful: `!node_modules !.git !dist !build !.venv !__pycache__ !reports`.
- Use `ext:` for extension filtering, for example `ext:json;yml;yaml`.
- Use `dm:` or `dc:` for modified or created dates.
- Use `file:` or `folder:` when the object type matters.
- Use exact quotes for literal paths or phrases.
- Use space for AND, `|` for OR, `!` for NOT, and `< >` for grouping. Everything evaluates OR before AND by default.
- Quote regular expressions containing operators or spaces, for example `regex:"gr(a|e)y" ext:txt`.
- Use `everything_syntax_help` when syntax is uncertain.

## Result limits

- Default `limit <= 25`.
- Hard cap is `100`.
- If a query would exceed the cap, refine instead of raising the limit.
- Return paths first.
- Metadata is opt-in. Ask for metadata only when size, date, attributes, or sorting matter.

## Content hand-off

Everything has content functions, but file contents are not indexed and content search is slow. Avoid content search by default.

The 1.4 content functions and known 1.5 aliases such as `ansi-content:`, `ascii-content:`, `binary-content:`, `byte-stream-content:`, ADS content functions, and `from-disk:` require both a non-root scope and a separate indexed filter. The same rule applies to literal-tail forms such as `content*:` and nested modifier chains such as `binary:content:` or `dot-all:regex:content:`. Prefer this pattern instead:

```text
Everything: C:\Work\project\ ext:py;md dm:thismonth
Then: grep, ast-grep, read, or LSP on selected paths
```

Hand off by need:

- Use `read` for exact file content.
- Use `grep` for plain text matches inside known paths or a scoped project.
- Use `ast-grep` for syntax-aware code patterns.
- Use LSP for definitions, references, workspace symbols, rename safety, and diagnostics.

## Safety policy

The MVP is read-only. Never suggest or perform delete, move, rename, quarantine, dedupe cleanup, index mutation, full-drive dump, HTTP enabling, or Everything configuration writes.

If a user asks for cleanup or mutation, explain that this skill can only locate candidate paths. Stop at discovery and decline mutation inside this read-only MVP.

## Backend expectations

The local runtime should use backend adapters in this order:

1. SDK/IPC as the primary local backend.
2. ES CLI as a fallback when `es.exe` is available.
3. HTTP JSON only when the user has explicitly enabled Everything HTTP outside this workflow.

The skill behavior stays the same no matter which backend is active.
