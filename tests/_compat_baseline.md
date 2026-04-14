# `_compat_baseline.txt` — Provenance

## Source Commit

Generated from the tree state of commit `3613eedc2` (the commit immediately
preceding PRD-CORE-141 landing in `7273de366`).  The file list is byte-for-byte
identical to `git ls-files --with-tree=3613eedc2 'trw-mcp/tests/test_*.py' | sort`.

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

## FR15 Check Protocol

```bash
cd trw-mcp
xargs -n 30 .venv/bin/python -m pytest --tb=line -q < tests/_compat_baseline.txt
```

All files in the baseline must pass against HEAD's `src/`.
