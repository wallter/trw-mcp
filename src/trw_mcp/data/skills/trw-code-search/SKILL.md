---
name: trw-code-search
description: >
  Use local TRW code indexing, lexical code search, and symbol lookup before
  broad file reads. Use: /trw-code-search [query or symbol]
user-invocable: true
argument-hint: "[query or symbol]"
---

# TRW Code Search Skill

Use when: You need token-efficient code context from a repository without reading whole files.


## Workflow

1. Refresh the local manifest with `trw_code_index_update(repo_root, force=false, paths=null)`.
2. Search behavior with `trw_code_search(repo_root, query, top_k=10)`.
3. Locate declarations with `trw_code_symbol(repo_root, symbol, top_k=10)`.
4. Read only the returned paths and line ranges that are relevant.

## Safety and fallback behavior

- Lexical search works in the base install and does not require parser or
  embedding extras.
- Search is lexical. There is no semantic mode and no embedding extra to
  install; passing `mode` is refused by the tool's input schema.
- Result snippets are capped and intended for triage, not as full-file output.
- Missing index, invalid repo, and unsafe path filters return structured
  failures instead of unhandled exceptions.

## Verification

Check the search results against the repository you are working in, using that
repository's own toolchain — this skill makes no assumption about its language,
test runner, or build system.

1. Round trip: after `trw_code_index_update`, a symbol you can see in a file you
   have open must come back from `trw_code_symbol` with that file and a line
   range that actually contains it.
2. Stale index: edit an indexed file without re-running
   `trw_code_index_update`, and confirm the hit you get back still points at a
   line range you can read, so you re-index rather than trusting the snippet.
3. Negative cases: an invalid repo root and a path filter outside the repo must
   return structured failures, not partial results.
4. Then run the target project's own checks — read its task file or manifest
   (`Makefile`, `pyproject.toml`, `package.json`, `go.mod`, `Cargo.toml`, and so
   on) for the commands it defines. Never assume a command this skill did not
   read from the project.
