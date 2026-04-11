---
name: trw-audit
context: fork
agent: general-purpose
description: >
  Adversarial spec-vs-code audit. Verifies implementation against PRD acceptance
  criteria, runs mandatory NFR checklist, assesses test quality. Independent from
  the implementer to break confirmation bias.
  Use: /trw-audit PRD-CORE-055
user-invocable: true
argument-hint: "[PRD-ID or file path]"
---

# Adversarial Spec-vs-Code Audit Skill

Verify that implementation code matches PRD acceptance criteria. This is NOT a code quality review (use `/trw-review-pr` for that). This audit answers one question: **does the code do what the PRD says it should?**

## Why This Exists

Sprint 29 proved that "all tests pass" is insufficient:
1. Agents who write code also write tests → confirmation bias
2. Self-reported exit criteria mask gaps → agents declare FRs "done" when tests pass
3. NFRs (pagination, auth, error handling) are consistently skipped
4. Tests validate status codes but not response bodies, happy paths but not edge cases

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate PRDs. Audit reports are stored in `scratch/audits/`.

## Workflow

### Step 1: Resolve PRD

Check `$ARGUMENTS` for a PRD ID or file path:
- If a PRD ID (e.g., `PRD-CORE-055`), resolve to file path via `prds_relative_path`
- If a file path, use directly
- Read the full PRD file
- Extract ALL functional requirements (FRs) with their acceptance criteria
- Extract ALL non-functional requirements (NFRs) with verification methods

### Step 2: Validate Readiness

Call `trw_prd_validate(prd_path)` to check PRD quality:
- If score < 0.85: abort with "PRD is not sprint-ready (score: {score}). Run /trw-prd-ready {PRD-ID} first."

Verify implementation exists:
- Use Grep/Glob to find source files referenced in the PRD's Technical Approach
- If no implementation files found: abort with "No implementation found for {PRD-ID}. Nothing to audit."

### Step 2b: Preflight Verification (PRD-QUAL-056-FR03/FR05)

- Check `events.jsonl` for `pre_implementation_checklist_complete` and `pre_audit_self_review`
- If present, record whether the implementer logged the checklist and what the self-review claimed
- Cross-check the self-review claims against your audit findings; missing or under-reported self-review results are audit evidence, not a waiver
- Record the results in the audit report as:
  - `checklist_logged: true|false`
  - `self_review_logged: true|false`
  - `self_review_alignment: matches|underreported|missing`
  - `notes: []`

### Step 2a: AC Keyword Extraction (PRD-QUAL-045-FR01/FR02)

From each FR's acceptance criteria (Given/When/Then), extract key technical terms:
- Function/class/method names mentioned in the spec
- Field names, status codes, error messages, boundary values
- Configuration keys and thresholds

Use these keywords to grep the implementation and test code. Report a "keyword match score" per FR:
- `keywords_found / total_keywords` as a percentage
- Score < 50% → P1 finding "Acceptance criteria keywords not reflected in implementation"

### Step 3: Locate Code and Tests

For each FR in the PRD:
1. **Find implementation** — Grep for function/class/endpoint names from the FR
2. **Find tests** — Grep for test functions that reference the FR or its functionality
3. **Build mapping** — FR → implementation files → test files

If a FR has NO implementation: mark as MISSING (P0) immediately.
If a FR has NO tests: mark as UNTESTED (P1) immediately.

### Step 3a: Wiring Check (PRD-QUAL-045-FR03)

For each new function/class defined in the implementation:
1. Verify it is actually CALLED from at least one other module or test
2. Use Grep: `grep -rn "function_name" src/ tests/`
3. Functions defined but never called → P1 "dead code — defined but not wired"
4. Exclude private helpers called only within the same file (these are OK)

### Step 4: Audit Each FR

For each FR, answer three questions by reading the actual code:

**Q1: Does the code implement the acceptance criterion?**
- Read the implementation function/endpoint
- Compare behavior against the Given/When/Then from the PRD
- Check: all fields returned? All conditions handled? All states covered?
- Verdict: PASS | PARTIAL | FAIL | MISSING

**Q2: Does the test verify the spec or just the implementation?**
- Does the test seed meaningful data (not empty fixtures)?
- Does the test check response bodies (not just status codes)?
- Does the test cover negative cases (not just happy path)?
- Would removing the FR cause the test to fail?

**Q3: Are edge cases covered?**
- Boundary values from acceptance criteria
- Empty collections, null/missing fields
- Error conditions specified in the PRD

### Step 5: NFR Checklist

Run EVERY item against EVERY endpoint/component. Do NOT skip items.

| # | NFR | Check | Common Miss |
|---|-----|-------|-------------|
| 1 | **Pagination limits** | Max limit enforced, offset >= 0, defaults present | Unlimited `?limit=999999` |
| 2 | **Input validation** | Body/params validated, oversized rejected | No size limits |
| 3 | **Auth enforcement** | Protected endpoints return 401/403 | Only 1-2 tested |
| 4 | **Error handling** | Non-critical failures wrapped, no crash | Logging exception crashes request |
| 5 | **Response completeness** | All specified fields present with correct types | Status code only |
| 6 | **Negative testing** | Revoked creds fail, 404 on not-found | Happy path only |
| 7 | **Rate limiting** | Applied where specified, Retry-After on 429 | Exists but untested |
| 8 | **Data consistency** | Timestamps correct, IDs match | Not verified |
| 9 | **Idempotency** | Duplicate requests safe where specified | No dedup |
| 10 | **Logging/Audit** | Security actions logged, no PII | Not tested |

### Step 6: Severity Assignment

| Severity | Criteria | Examples |
|----------|----------|----------|
| P0 | FR completely missing or fundamentally broken | Endpoint not implemented, auth not enforced |
| P1 | FR partially implemented, key behavior missing | Pagination exists but no max limit, response missing fields |
| P2 | Minor gap, edge case not covered | Missing negative test, cosmetic field wrong |

**Security PRD escalation (PRD-QUAL-044-FR04)**: If the PRD has `tags: [security]` or its title contains "security"/"hardening"/"vulnerability", any FAIL or MISSING verdict is automatically escalated to P0. Security PRDs cannot be left incomplete.

### Step 7: Write Audit Report

Write to `scratch/audits/AUDIT-{PRD-ID}.yaml`:

```yaml
audit_id: AUDIT-{PRD-ID}
prd_id: PRD-{CATEGORY}-{SEQ}
prd_title: "{title}"
timestamp: "{ISO 8601}"

fr_verdicts:
  - fr_id: FR01
    title: "{FR title}"
    acceptance_criterion: "{exact text from PRD}"
    verdict: PASS|PARTIAL|FAIL|MISSING
    implementation_file: "path/to/file:line"
    test_file: "path/to/test_file:test_name"
    findings:
      - severity: P0|P1|P2
        issue: "Description of the gap"
        evidence: "What code does vs. what spec requires"
        fix: "Specific recommendation"
    test_quality:
      seeds_meaningful_data: true|false
      checks_response_body: true|false
      covers_negative_cases: true|false
      would_catch_regression: true|false

nfr_audit:
  - nfr: "Pagination limits"
    verdict: PASS|FAIL|NA
    evidence: "Specific code reference"
    finding: "Description if FAIL"
  # ... all 10 items

prior_learning_verification:
  known_patterns: []
  verified_patterns: []
  missed_patterns: []

preflight_verification:
  checklist_logged: true|false
  self_review_logged: true|false
  self_review_alignment: matches|underreported|missing
  notes: []

summary:
  total_frs: 5
  pass: 2
  partial: 1
  fail: 1
  missing: 1
  p0_count: 1
  p1_count: 2
  p2_count: 0
  overall_verdict: PASS|CONDITIONAL|FAIL
```

Overall verdict rules:
- **PASS**: All FRs pass, no P0/P1 findings
- **CONDITIONAL**: No P0, <=2 P1 findings (fixable without replan)
- **FAIL**: Any P0 OR >2 P1 findings

### Step 7.5: Spec Reconciliation

1. Call `trw_review(mode="reconcile", prd_ids=["PRD-{ID}"])` with the audited PRD ID
2. If mismatches found: include mismatched identifiers as P1 findings with `update_spec` recommendation, note as spec drift
3. If clean: note "Spec reconciliation: clean" in the summary

### Step 8: Summary

Output a markdown summary:
- PRD ID and title
- FR count with verdict breakdown (pass/partial/fail/missing)
- NFR checklist results (pass/fail counts)
- Severity summary (P0/P1/P2 counts)
- Overall verdict with rationale
- Top 3 most critical findings with fix recommendations
- Audit report file path

If findings exist, call `trw_learn` to record the pattern for future sessions.

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The tests pass, so this FR is fine" | Tests validate the implementation, not the specification — they confirm the code works as written, not as specified | Sprint 29: 12 gaps survived "all tests pass" |
| "This NFR isn't relevant to this type of endpoint" | NFR checklist is cross-cutting — skipping items is how gaps accumulate across sprints | Pagination, auth, error handling skipped on "simple" endpoints |
| "I'll mark this PARTIAL instead of FAIL to avoid blocking" | Accuracy matters more than velocity — downgraded findings ship to production | Accurate findings get fixed; downgraded findings become tech debt |

## Assertion Verification (PRD-CORE-086)

When auditing FRs that include `Assertions:` blocks, use them as objective evidence:

1. Read the assertion definitions from the FR
2. Mentally evaluate (or run `verify_assertions()` if available) whether the patterns would match
3. Use assertion results to ground your verdict — "grep_present for X in Y: PASSING" is stronger evidence than "I saw X in the code"
4. If assertions are present and FAILING, this is strong evidence of incomplete implementation
5. Report assertion pass/fail status in your FR-by-FR analysis

## Constraints

- NEVER modify code files — this skill is read-only (except writing the audit report)
- NEVER accept "tests pass" as evidence of spec compliance
- NEVER skip NFR checklist items — mark NA with justification if truly not applicable
- NEVER downgrade severity to avoid blocking — P0 is P0
- ALWAYS read implementation code directly — tests are not a proxy for behavior
- ALWAYS provide fix recommendations — findings without fixes are complaints, not audits
- If PRD acceptance criteria are ambiguous, note as category: prd-ambiguity
