# TRW Audit Framework — Shared Protocol

Shared reference document for the `trw-audit` skill, `trw-auditor`, and `trw-adversarial-auditor`. Every audit surface reads this file so the audit scaffolding (rubrics, taxonomies, NFR checklist, wave-pause heuristic, finding schema, severity ladder, verdict criteria, report schema, delivery rule) lives in one place and stays consistent across the skill path and both auditor variants.

**This document is the sole owner of the audit protocol.** No agent, skill, or projection may restate an element defined here — reference it by section instead. `scripts/check_audit_protocol_single_source.py` enforces that: a *reference* is legal, a second *definition* is not.

This document is a reference (not an agent file) and is not bound by the 350-LOC agent limit.

---

## Section A. Evidence Tier Rubric

Every finding anchors to evidence at one of three tiers. Choose the lowest tier that honestly applies; do not upgrade evidence to strengthen a claim.

| Tier | Meaning | Example |
|------|---------|---------|
| **direct** | Evidence is the code itself or a deterministic command output (file:line, test output, grep result) cited in the finding | `src/foo.py:42: returns None when spec requires bool` |
| **inferential** | Evidence combines multiple direct observations via a stated chain of reasoning | "Field missing in model → absent from API response → spec requires it" |
| **speculative** | Evidence is a hypothesis grounded in a pattern, not a committed observation | "Likely race condition — no test exercises concurrent writers" |

Guidance:
- P0/P1 findings should cite direct or inferential evidence. Pure speculation is a P2 or a flagged open question, not a blocker.
- Record the tier alongside each finding so downstream reviewers can re-verify quickly.

---

## Section B. 5-Category Root-Cause Taxonomy

Each finding uses one of these five root-cause categories. The taxonomy is stable across PRDs so learnings aggregate cleanly.

| Category | Description | Phase Affinity |
|----------|-------------|---------------|
| `spec_gap` | PRD acceptance criteria are ambiguous or incomplete | plan, implement |
| `impl_gap` | Code does not match spec — wrong behavior, missing feature, wrong file placement | implement |
| `test_gap` | Tests validate the implementation rather than the specification | implement, validate |
| `integration_gap` | Code works in isolation but is not wired into the production system | implement |
| `traceability_gap` | PRD traceability matrix has stale or incorrect entries | implement, deliver |

### Legacy Category Mapping

For backward compatibility with older audit outputs, map legacy labels to the 5 canonical categories. Retain the original label under `legacy_category` on the finding so historical analytics continue to work.

| Legacy label | Maps to `category` |
|---|---|
| `prd-ambiguity` | `spec_gap` |
| `spec-gap` | `spec_gap` |
| `type-safety` | `impl_gap` |
| `dry` | `impl_gap` |
| `error-handling` | `impl_gap` |
| `observability` | `impl_gap` |
| `test-quality` | `test_gap` |
| `integration` | `integration_gap` |

Example: a PRD ambiguity finding emits `category: spec_gap` and `legacy_category: prd-ambiguity`.

---

## Section C. NFR Checklist (11 items)

Run every item against every endpoint/component in scope. Do not skip items. Do not assume compliance without evidence.

| # | NFR | Check | Common Miss |
|---|-----|-------|-------------|
| 1 | **Input limits** | Max sizes enforced (collections, strings, numeric ranges), defaults present | Unlimited sizes accepted |
| 2 | **Input validation** | Request/params validated, oversized rejected, types checked | No size limits on arrays/strings |
| 3 | **Auth enforcement** | Every protected endpoint/route returns appropriate error for unauthorized access | Only 1-2 endpoints tested |
| 4 | **Error handling** | Non-critical failures wrapped, no crash on bad input | Exception crashes request |
| 5 | **Response completeness** | Response/output contains all specified fields with correct types | Status tested, body ignored |
| 6 | **Negative testing** | Invalid credentials fail, suspended entities blocked, not-found handled | Only happy path tested |
| 7 | **Rate limiting** | Rate limits applied where specified, appropriate retry guidance on throttle | Rate limit exists but untested |
| 8 | **Data consistency** | Timestamps correct, IDs match, no orphaned references | Timestamps not verified |
| 9 | **Idempotency** | Duplicate operations handled safely where specified | No idempotency key or dedup |
| 10 | **Logging/Audit** | Security-relevant actions logged, no sensitive data in logs | Logging exists but not tested |
| 11 | **Property reachability** (safety properties only: redaction / sanitization / validation / egress filtering) | For each asserted safety property, trace every external **sink** (LLM prompt, user-facing artifact, persisted store, log, network egress) back to **all** its sources; confirm each source→sink path crosses the gate AND that the gate's output is consumed by production code. Require an adversarial fixture in **every** injected input channel, not only the gated one. | Gate is mechanically correct and 100%-tested in isolation, but its output is consumed by no production code while an unguarded upstream object reaches the sink (a "Potemkin gate"). Tests pass; the property is false by construction. |

> **Item 11 rationale — the Potemkin-gate defect class** (operator report `sub_zAfRqZYYq2KtF72d`). A fail-closed redaction gate passed 60+ unit/contract tests at claimed 100% branch coverage, yet the only content reaching the LLM prompt and the user-facing artifact bypassed it entirely — every test exercised the gate in isolation; nothing verified what the sink actually consumes. Unit/contract tests structurally cannot catch this; only sink-to-source data-flow can. Treat "gate output consumed by nothing in production" as an **automatic FAIL**, regardless of coverage. This failure mode is over-represented in LLM-authored code, because the implementing model optimizes the gate's tests rather than the property.

An `N/A` verdict is valid only when the NFR category genuinely does not apply to the feature. When in doubt, investigate before marking N/A. Item 11 is `N/A` only when the component asserts no redaction/sanitization/validation/egress property at all.

---

## Section D. Per-Wave Coverage Prompts

Audits run in waves. Each wave carries one coverage prompt below — a question
that targets the gap class that wave is most likely to miss. Answer it as part
of the wave's work, in the wave's findings.

These are coverage questions, not a self-verification pass: there is no separate
re-read step, and re-checking work you have already done is not one of the
audit's phases.

### Coverage Prompts (one per wave)

- Wave 1 (spec extraction): Is every requirement captured? Are there implicit requirements the spec assumes but doesn't state?
- Wave 2 (implementation & wiring): Any gaps? Any "tests exist but implementation doesn't" patterns? Any extracted modules never imported? For any safety property (redaction/sanitization/validation/egress), does the gate's output actually reach the sink, or is there an unguarded path that does the real work (Potemkin gate — NFR item 11)?
- Wave 3 (functional correctness): For every PARTIAL verdict, is it really partial, or are you being generous? Re-read the acceptance criterion literally.
- Wave 4 (code quality): Patterns of type unsafety, or isolated incidents? Patterns indicate systemic issues worth escalating.
- Wave 5 (error handling): Are there gaps the NFR checklist catches that Phase 5 missed, or vice versa?
- Wave 6 (NFR grid): Any N/A verdicts that should actually be FAIL?
- Wave 7 (synthesis): Any P1 that is secretly P0 because it blocks a downstream FR?

---

## Section E. Finding Schema

Every finding uses this structure so downstream tooling (analytics, learning capture, PR review) can parse audits uniformly.

```yaml
severity: P0|P1|P2
category: spec_gap|impl_gap|test_gap|integration_gap|traceability_gap
legacy_category: prd-ambiguity|spec-gap|type-safety|dry|error-handling|observability|test-quality|integration|null
evidence_tier: direct|inferential|speculative
location: "path/to/file.py:42"   # or "PRD-CORE-123 FR05" for spec findings
issue: "One-line description of the gap"
evidence: "What the code does vs. what the spec requires — include command output or cited line"
fix: "Specific recommendation with file path and line"
```

### Severity Criteria

| Severity | Criteria | Examples |
|----------|----------|----------|
| P0 | FR completely missing, fundamentally broken, or security vulnerability | Endpoint not implemented, auth not enforced, type-unsafe cast causes data loss |
| P1 | FR partially implemented, key behavior missing, or significant quality gap | Pagination exists but no max limit, response missing required fields, blanket error suppression |
| P2 | Minor gap, edge case not covered, or style/quality nit | Missing negative test, cosmetic field wrong, minor type imprecision |

**Security PRD escalation (PRD-QUAL-044-FR04)**: If the PRD has `tags: [security]` or its title contains "security"/"hardening"/"vulnerability", any FAIL or MISSING verdict is automatically escalated to P0. Security PRDs cannot be left incomplete.

### Audit Verdict Criteria (overall)

| Verdict | Criteria | Action |
|---------|----------|--------|
| **PASS** | Zero P0 findings AND zero P1 findings AND all FRs have verdict PASS or PARTIAL-with-justification | PRD advances to DELIVER |
| **CONDITIONAL** | Zero P0 findings AND 1-2 P1 findings that are fixable without architectural change | PRD holds; implementer fixes P1s; re-audit only affected FRs |
| **FAIL** | Any P0 finding OR 3+ P1 findings OR any FR with verdict MISSING | PRD reverts to IMPLEMENT; full review required |

Maximum audit cycles before escalation: 3. The value's owner is the `max_audit_cycles` field declared in `trw_mcp/models/config/_fields_ceremony.py` and overridable in `.trw/config.yaml`; the number above restates that field's declared default and nothing else may restate it. After that many consecutive FAIL verdicts, escalate to the orchestrator for replan or scope reduction.

---

## Section F. Severity-to-Impact Mapping for Learning Capture

When calling `trw_learn()` for P0/P1 findings, use these impact values so learnings rank consistently across audits:

| Severity | Impact | Rationale |
|---|---|---|
| P0 | 0.8 | Blocking failures future agents must avoid |
| P1 | 0.6 | Recurring quality gaps |
| P2 | — | Do not create learnings for P2 findings (noise) |

Tags should include `audit-finding`, the PRD id, and the finding category. Type is `incident` and confidence is `verified` — both are top-level arguments. `phase_affinity` is taken from the taxonomy table in Section B and travels inside the `metadata` argument, as a list: `metadata={"phase_affinity": ["IMPLEMENT"]}`. Passing it as a top-level argument is rejected and the finding is lost.

---

## Section G. Audit Report Schema

This is the one definition of the audit report. Every audit surface — the `/trw-audit` skill, `trw-auditor`, and `trw-adversarial-auditor` — emits exactly this structure. Finding entries use the Section E finding schema; `category` and `legacy_category` use the Section B vocabularies; `overall_verdict` uses the Section E verdict criteria.

```yaml
audit_id: A-{task-id}
prd_id: PRD-{CATEGORY}-{SEQ}
prd_title: "{title}"
auditor: "{your-name}"
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
        category: spec_gap|impl_gap|test_gap|integration_gap|traceability_gap
        legacy_category: prd-ambiguity|spec-gap|type-safety|dry|error-handling|observability|test-quality|integration|null
        evidence_tier: direct|inferential|speculative
        location: "path/to/file.py:42"
        issue: "Description of the gap"
        evidence: "What the code does vs. what the spec requires"
        fix: "Specific recommendation with file path and line"
    test_quality:
      seeds_meaningful_data: true|false
      checks_response_body: true|false
      covers_negative_cases: true|false
      would_catch_regression: true|false

traceability:
  total_requirements: 0
  traced_to_source: 0
  traced_to_tests: 0
  overall_coverage_pct: 0
  untraced_requirements: []
  orphan_implementations: []
  stale_traces: []

code_quality:
  type_safety: { suppressions_found: 0, untyped_containers: 0, cross_file_mismatches: 0, verdict: PASS|FAIL }
  dry: { duplicated_blocks: 0, magic_literals: 0, verdict: PASS|FAIL }
  error_handling: { silent_swallows: 0, missing_context: 0, resource_leaks: 0, verdict: PASS|FAIL }
  observability: { unlogged_operations: 0, pii_in_logs: 0, missing_correlation: false, verdict: PASS|FAIL }
  todos_remaining: 0

nfr_audit:
  - nfr: "Input limits"
    verdict: PASS|FAIL|NA
    evidence: "Specific code reference"
    finding: "Description if FAIL"
  # ... one row per applicable Section C checklist item

integration:
  orphan_modules: []
  unwired_exports: []
  stale_config: []
  missing_migrations: []
  unresolved_todos: []

prior_learning_verification:
  known_patterns: []
  verified_patterns: []
  missed_patterns: []

summary:
  total_frs: 5
  pass: 2
  partial: 1
  fail: 1
  missing: 1
  p0_count: 1
  p1_count: 2
  p2_count: 0
  audit_angles_completed: [spec, vision, types, dry, errors, observability, integration, tests, traceability]
  overall_verdict: PASS|CONDITIONAL|FAIL
  # PASS: zero P0, zero P1, and every FR is PASS or PARTIAL-with-justification
  # CONDITIONAL: zero P0 and 1-2 P1 findings fixable without architectural change
  # FAIL: any P0, 3+ P1 findings, or any FR verdict MISSING
  status_recommendation: "advance|hold|revert"
```

The `prior_learning_verification` mapping is populated from the prior-learning recall step: `known_patterns` are the patterns recall surfaced, `verified_patterns` are those you explicitly confirmed against this implementation, and `missed_patterns` are those the implementation still exhibits.

---

## Section H. Report Delivery

The audit report is **returned as your final message**, leading with any P0 finding. Write it to a file only when the caller supplied a path; auditors are read-only and must not invent an artifact location or assume a coordination surface exists to receive the report.

If a caller-supplied path is present, write the same Section G structure there and still return it as the final message — the file is a convenience copy, never a substitute for the response.
