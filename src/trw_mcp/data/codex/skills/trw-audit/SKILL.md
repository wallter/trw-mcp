---
name: trw-audit
description: >
  Adversarial spec-vs-code audit. Verifies implementation against PRD acceptance
  criteria, runs mandatory NFR checklist, assesses test quality. Independent from
  the implementer to break confirmation bias.
  Use: /trw-audit PRD-CORE-055
---

> Codex adaptation: `AGENTS.md` is the primary instruction file. If a step mentions legacy Claude-specific workflow, follow the equivalent Codex skill/subagent flow instead.

Run the audit with a reviewer independent from the implementer when subagents are available. Otherwise perform a
separate evidence pass and disclose that independence was unavailable.

# Adversarial Spec-vs-Code Audit Skill

Use when: adversarially checking implementation behavior against a PRD before declaring it done.

Verify that implementation code matches PRD acceptance criteria. This is NOT a code quality review; use the packaged `trw-reviewer` helper or a client-native code-review workflow for that. This audit answers one question: **does the code do what the PRD says it should?**

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate PRDs.


## Preflight Verification Contract

Check `events.jsonl` for `pre_implementation_checklist_complete` and `pre_audit_self_review`. Include these fields in audit output when applicable:

```yaml
preflight_verification: present|missing|not_applicable
self_review_alignment: matches|underreported|missing
prior_learning_verification: checked|missing|not_applicable
```

## Workflow

### Step 1: Resolve PRD

Check `$ARGUMENTS` for a PRD ID or file path:
- If a PRD ID (e.g., `PRD-CORE-055`), resolve to file path via `prds_relative_path`
- If a file path, use directly
- Read the full PRD file
- Extract ALL functional requirements (FRs) with their acceptance criteria
- Extract ALL non-functional requirements (NFRs) with verification methods

### Step 2: Validate Readiness

Call `trw_prd_validate(prd_path)` in full mode and record `total_score`, `quality_tier`, `valid`, and
`validation_partial`. Use `total_score` only for reporting; do not gate on the deprecated `completeness_score`.
- If validation is partial, rerun in full mode or disclose the skipped checks.
- If the PRD is invalid or below the risk-scaled `approved` tier, record the specification risk and continue when the
  requirements remain auditable. Mark ambiguous criteria uncertain. Do not abort an adversarial audit solely because
  the PRD score or tier is weak.

Verify implementation exists:
- Use Grep/Glob to find source files referenced in the PRD's Technical Approach
- Infer source/test roots and test naming from repo config and existing files; do not assume `src/` + `tests/` unless that is the scoped package convention
- If no implementation files found: abort with "No implementation found for {PRD-ID}. Nothing to audit."

### Step 2a: AC Keyword Extraction (PRD-QUAL-045-FR01/FR02)

From each FR's acceptance criteria, regardless of requirement syntax or verification method, extract useful search terms:
- Function/class/method/component/command/schema/event/API names mentioned in the spec
- Field names, status codes, error messages, boundary values
- Configuration keys and thresholds

Use these keywords as search hints for implementation and verification evidence. Naming overlap is not behavioral
proof: never assign a verdict or severity from a keyword-match percentage.

### Step 3: Locate Code and Tests

For each FR in the PRD:
1. **Find implementation** — Grep for the public symbols, interfaces, commands, endpoints, schemas, components, events, or files named by the FR
2. **Find tests** — Grep for framework-appropriate tests that reference the FR, acceptance ID, public interface, or behavior
3. **Build mapping** — FR → implementation files → test files

If a FR has NO implementation: mark as MISSING (P0) immediately.
Apply the PRD's declared verification method. Machine-observable behavior without an executable behavioral test is
UNTESTED (P1) unless the PRD explicitly justifies another method; evaluate Analysis, Inspection, or Demonstration
evidence on its own terms.

### Step 3a: Wiring Check (PRD-QUAL-045-FR03)

For each new public symbol, exported component, command, endpoint, schema, event, or adapter defined in the implementation:
1. Verify it is actually wired through at least one production caller, route, registry, export, command table, or integration path
2. Use Grep/Glob across repo-detected production roots, then inspect tests separately
3. Public definitions that are never wired → P1 "dead code — defined but not wired"
4. Exclude private helpers called only within the same file (these are OK)
5. Test-only reachability is not production wiring; report it as unwired or uncertain according to the available evidence

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

Evaluate every checklist item for each audited surface. Mark non-applicable items `NA` only with concrete justification.

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
  # ... one row per checklist item above

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

Call `trw_learn` only when findings reveal a non-obvious reusable pattern, not for routine audit status.

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
- NEVER use PARTIAL to soften a failed acceptance criterion; PARTIAL requires concrete implemented behavior plus an explicit remaining gap
- NEVER skip NFR checklist items — mark NA with justification if truly not applicable
- NEVER downgrade severity to avoid blocking — P0 is P0
- ALWAYS read implementation code directly — tests are not a proxy for behavior
- ALWAYS provide fix recommendations — findings without fixes are complaints, not audits
- If PRD acceptance criteria are ambiguous, note as category: prd-ambiguity

<!-- compliance: implementation-readiness, control points, testability, migration, score-gaming -->
