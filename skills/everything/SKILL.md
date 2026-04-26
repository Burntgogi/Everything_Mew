---
name: everything
compatibility: opencode
description: Use when locating files or folders by name, path, extension, date, size, attributes, or other Everything-indexed Windows filesystem metadata.
---

# Everything

Use this skill when a user needs to find candidate files or folders on Windows by metadata that Everything indexes. Everything is a fast path discovery layer, not a file reader, code search engine, cleanup tool, or project mapper.

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

## Default workflow

1. Decide whether the task is metadata discovery. If it is content or code understanding, use `read`, `grep`, `ast-grep`, or LSP instead.
2. Build a scoped Everything query. Prefer a known project path, extension, date, size, and noisy-directory exclusions.
3. Count before broad search. If the query is broad, ambiguous, or unscoped, call `everything_count` first.
4. Refine until the result size is useful.
5. Call `everything_search` with path-first output and `metadata=false` unless metadata is required.
6. Inspect only selected candidate paths with `read`, `grep`, `ast-grep`, or LSP.

Preferred chain:

```text
everything_count -> everything_search -> read/grep/ast-grep/LSP
```

## Query rules

- Scope to a known root, for example `C:\Work\project\ ext:md`.
- Exclude noisy folders when useful: `!node_modules !.git !dist !build !.venv !__pycache__ !reports`.
- Use `ext:` for extension filtering, for example `ext:json;yml;yaml`.
- Use `dm:` or `dc:` for modified or created dates.
- Use `file:` or `folder:` when the object type matters.
- Use exact quotes for literal paths or phrases.
- Use `everything_syntax_help` when syntax is uncertain.

## Result limits

- Default `limit <= 25`.
- Hard cap is `100`.
- If a query would exceed the cap, refine instead of raising the limit.
- Return paths first.
- Metadata is opt-in. Ask for metadata only when size, date, attributes, or sorting matter.

## Content hand-off

Everything has `content:`, but file contents are not indexed and content search is slow. Avoid `content:` by default.

Only use `content:` when heavily scoped with strong filters such as path, extension, date, or file type. Prefer this pattern instead:

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

The local MCP should use backend adapters in this order:

1. SDK/IPC as the primary local backend.
2. ES CLI as a fallback when `es.exe` is available.
3. HTTP JSON only when the user has explicitly enabled Everything HTTP outside this workflow.

The skill behavior stays the same no matter which backend is active.
