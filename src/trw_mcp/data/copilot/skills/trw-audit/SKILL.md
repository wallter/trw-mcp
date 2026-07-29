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

Use when: adversarially checking implementation behavior against a PRD before declaring it done.

Verify that implementation code matches PRD acceptance criteria. This is NOT a code quality review; use the packaged `trw-reviewer` helper or a client-native code-review workflow for that. This audit answers one question: **does the code do what the PRD says it should?**

## Shared Protocol

This skill is an invocation adapter. It owns argument resolution, the readiness
gate, the search procedure, and dispatch — **not** the audit protocol. The audit
protocol is defined once, in the sibling `audit-framework.md` that ships in this
skill directory: evidence tiers (Section A), root-cause taxonomy (Section B),
the NFR checklist (Section C), the wave-pause heuristic (Section D), the finding
schema plus severity ladder and overall verdict criteria (Section E), the
severity-to-impact mapping (Section F), the audit report schema (Section G), and
the report delivery rule (Section H). Read it before Step 1 and follow it
verbatim; this file never restates it.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate PRDs.


## Prior-Learning Contract

Call `trw_recall(query='<prd-domain> audit-finding')` before auditing and verify
whether each known pattern was addressed here. Record the result under the
`prior_learning_verification:` key of the report schema in `audit-framework.md`
Section G, which defines its shape.

Do **not** audit the implementer's self-report. Self-attested checklist events
are caller-controlled and are therefore not evidence; verify the implementation
against the spec directly. Never record a process-gap finding for the absence of
a self-review artifact.

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

Run every item in the `audit-framework.md` Section C checklist against each
audited surface. Section C is the only list; do not work from a remembered or
abbreviated copy.

Mark non-applicable items `NA` only with concrete justification, per the Section C `N/A` rule.

### Step 6: Severity Assignment

Assign P0/P1/P2 using the severity criteria in `audit-framework.md` Section E,
including its security-PRD escalation rule. Section E is the only severity
ladder.

### Step 7: Report the Audit

Emit the report schema defined in `audit-framework.md` Section G, and deliver it
per Section H. Assign the overall verdict from the Section E verdict criteria
table. This file defines none of those three; read them.

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

Call `trw_learn` only when findings reveal a non-obvious reusable pattern, not for routine audit status.

## Assertion Verification (PRD-CORE-086)

When auditing FRs that include `Assertions:` blocks, use them as objective evidence:

1. Read the assertion definitions from the FR
2. Mentally evaluate (or run `verify_assertions()` if available) whether the patterns would match
3. Use assertion results to ground your verdict — "grep_present for X in Y: PASSING" is stronger evidence than "I saw X in the code"
4. If assertions are present and FAILING, this is strong evidence of incomplete implementation
5. Report assertion pass/fail status in your FR-by-FR analysis

## Constraints

- NEVER modify code files — this audit is read-only
- NEVER accept "tests pass" as evidence of spec compliance
- NEVER use PARTIAL to soften a failed acceptance criterion; PARTIAL requires concrete implemented behavior plus an explicit remaining gap
- NEVER skip NFR checklist items — mark NA with justification if truly not applicable
- NEVER downgrade severity to avoid blocking — P0 is P0
- ALWAYS read implementation code directly — tests are not a proxy for behavior
- ALWAYS provide fix recommendations — findings without fixes are complaints, not audits
- If PRD acceptance criteria are ambiguous, note as category: prd-ambiguity

<!-- compliance: implementation-readiness, control points, testability, migration, score-gaming -->
