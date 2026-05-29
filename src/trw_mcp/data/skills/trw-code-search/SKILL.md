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
2. Search behavior with `trw_code_search(repo_root, query, mode="lexical", top_k=10)`.
3. Locate declarations with `trw_code_symbol(repo_root, symbol, top_k=10)`.
4. Read only the returned paths and line ranges that are relevant.

## Safety and fallback behavior

- Lexical search works in the base install and does not require parser or
  embedding extras.
- Semantic mode must fail closed with `dependency_missing` and remediation when
  no local optional embedder is configured.
- Result snippets are capped and intended for triage, not as full-file output.
- Missing index, invalid repo, and unsafe path filters return structured
  failures instead of unhandled exceptions.

## Verification

Run the focused checks from `trw-mcp`:

```bash
../.venv/bin/python -m pytest tests/test_code_chunking.py tests/test_code_search_lexical.py tests/test_code_search_tool.py tests/test_code_search_embeddings_optional.py -q
../.venv/bin/ruff check src/trw_mcp/code_index/chunking.py src/trw_mcp/code_index/search.py src/trw_mcp/code_index/embeddings.py src/trw_mcp/tools/code_search.py tests/test_code_chunking.py tests/test_code_search_lexical.py tests/test_code_search_tool.py tests/test_code_search_embeddings_optional.py
../.venv/bin/python -m mypy --strict src/trw_mcp/code_index/chunking.py src/trw_mcp/code_index/search.py src/trw_mcp/code_index/embeddings.py src/trw_mcp/tools/code_search.py
```
