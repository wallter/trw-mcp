# `_compat_baseline.txt` — Provenance

## Source Commit

Originally generated from the tree state of commit `3613eedc2` (the commit
immediately preceding PRD-CORE-141 landing in `7273de366`). The current list
excludes tests intentionally retired with their proven-dead production paths;
those deviations are recorded below.

## Caveat — Three Files Were Modified In-Place

During PRD-CORE-141 Wave 3, the following three listed files were modified to
adapt their `monkeypatch` lambdas to the new `context=` kwarg on pin helpers
(`find_active_run`, `resolve_run_path`):

- `trw-mcp/tests/test_review_tool.py`
- `trw-mcp/tests/test_run_report.py`
- `trw-mcp/tests/test_tools_report.py`

These edits are **signature-only** — each lambda was widened from
`lambda: run_dir` → `lambda **_: run_dir` (or equivalent) so the existing
stubs accept an unused `context=` kwarg.  The tests still exercise the same
behavior paths and still assert the same post-conditions.

If you need a TRUE pre-PRD copy of these three files to validate behavioral
backward compatibility, recover them with:

```bash
git show 3613eedc2:trw-mcp/tests/test_review_tool.py > /tmp/test_review_tool.pre.py
# (repeat for the other two)
```

Run the recovered copies against current `src/` — they will fail on the
lambda signatures (the new code passes `context=` positionally as a kwarg),
which is the expected FR15 boundary: legacy stdio callers pass no ctx; the
monkeypatch lambdas in unit tests were the only consumers forced to update.

## Intentional Dead-Test Retirements

- `trw-mcp/tests/test_llm_helpers.py` — removed when the abandoned event-to-learning helper was retired.
- `trw-mcp/tests/test_reflection_state.py` — removed with the abandoned full-reflect module; its live FR06
  follow-through coverage moved to `trw-mcp/tests/test_reflection_followthrough.py`.

These paths are omitted rather than left as missing entries that make the FR15 command fail before collection.

## FR15 Check Protocol

```bash
xargs -n 30 .venv/bin/python -m pytest --tb=line -q < trw-mcp/tests/_compat_baseline.txt
```

All files in the baseline must pass against HEAD's `src/`.
