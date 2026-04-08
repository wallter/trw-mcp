---
name: trw-self-review
description: >
  Pre-audit self-review checklist. Run BEFORE requesting formal adversarial audit
  to catch 60%+ of typical findings. Covers assertion verification, wiring checks,
  NFR mini-checklist, and test quality spot-check.
user-invocable: true
argument-hint: "[PRD-ID]"
---

# Pre-Audit Self-Review Skill (PRD-QUAL-056-FR05)

Run this checklist BEFORE requesting formal adversarial audit (`/trw-audit`). This catches 60%+ of typical audit findings at zero adversarial cost.

## Why This Exists

Analysis of 55 audit-fix commits found the same categories of findings recur: test names mismatched with PRD FRs, functions defined but never wired, mocks used for testable dependencies, traceability matrix entries stale after fix cycles. A structured pre-audit self-review catches these patterns before the auditor runs.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD.

## Workflow

### Step 1: Resolve PRD

- If `$ARGUMENTS` contains a PRD ID, resolve to file path
- Read the full PRD
- Extract all FRs with their assertions and acceptance criteria

### Step 2: Assertion Verification

For each FR that has machine-verifiable assertions (`grep_present`, `grep_absent`, `glob_exists`, `command_succeeds`):

1. Run each assertion command via Bash or Grep
2. Record PASS / FAIL for each
3. If any assertion FAILS, note the specific mismatch

```bash
# Example assertion verification:
grep -q 'def _score_file_path_coverage' trw-mcp/src/trw_mcp/state/validation/_prd_scoring.py && echo 'PASS' || echo 'FAIL'
```

### Step 3: Wiring Check

For each new file created during implementation:

1. Grep the codebase for imports of that file from production (non-test) modules
2. A file that is not imported from any production module is likely unwired
3. Exclude `__init__.py` re-exports — they count as wiring

```bash
# Example: check if new module is imported anywhere in production code
grep -r "from trw_mcp.scoring._io_boundary import" trw-mcp/src/ --include="*.py" | grep -v test
```

### Step 4: NFR Mini-Checklist

Check the 5 highest-frequency NFR findings from historical audits:

1. **Input validation**: New endpoints/entry points have input validation
2. **Error handling**: Non-critical failures wrapped (no bare `except:` without justification comment)
3. **Structured logging**: Significant operations have `structlog` calls with outcome field
4. **Type annotations**: No `# type: ignore` without justification comment on same line
5. **No stale TODOs**: No `TODO` or `FIXME` markers in committed production code

For each item, grep the modified files and report PASS/FAIL.

### Step 5: Test Quality Spot-Check

For each FR, verify:

1. **Test name matches**: Test function name matches the PRD traceability matrix entry
2. **Non-trivial data**: Test seeds actual data (not empty strings/dicts/lists)
3. **Response body checked**: Test asserts on actual values (not just `assert result is not None`)
4. **Spec-anchored**: Test docstring references the FR it validates

### Step 6: Log Results

Log the self-review results as a `pre_audit_self_review` event:

```python
# Event payload structure:
{
    "event": "pre_audit_self_review",
    "prd_id": "PRD-QUAL-056",
    "passed": N,     # assertions that passed
    "failed": M,     # assertions that failed
    "skipped": K,    # assertions not applicable
    "wiring_issues": [],  # unwired files
    "nfr_issues": [],     # NFR checklist failures
    "test_issues": [],    # test quality findings
}
```

### Step 7: Report

Output a structured report:

```markdown
## Pre-Audit Self-Review: {PRD-ID}

### Assertion Verification: {passed}/{total} PASS
| FR | Assertion | Result |
|---|---|---|
| FR01 | grep_present "def foo" in src/bar.py | PASS |
| FR02 | grep_absent "TODO" in src/baz.py | FAIL — found on line 42 |

### Wiring Check: {issues_count} issues
- {file} is not imported from any production module

### NFR Mini-Checklist: {pass_count}/5
- [x] Input validation
- [x] Error handling
- [ ] Structured logging — missing in src/new_module.py
- [x] Type annotations
- [x] No stale TODOs

### Test Quality: {pass_count}/{total}
- FR01: test name matches, non-trivial data, body checked
- FR02: test name MISMATCH — expected test_fr02_happy, found test_feature_two

### Recommendation
{READY FOR AUDIT | FIX {N} ISSUES FIRST}
```

## Constraints

- This is a SELF-review — it runs the same checks the auditor will run
- Do NOT skip assertions — the auditor WILL catch what you miss
- Log results to events.jsonl so the auditor can cross-reference
- If >3 assertions fail, fix them before requesting audit
