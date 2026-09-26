---
prd:
  id: PRD-QUAL-076
  title: Truthfulness and Overclaim Audit for Eval Iterations
  version: '1.0'
  status: draft
  priority: P0
  category: QUAL
  risk_level: low
  ip_tier: public
  functionality_level: partial
  partially_implemented_frs: [FR-01, FR-02, FR-04]
  audit_note: |
    Sprint 104 backlog-maintenance audit (2026-04-30): FR-03 (stale meta-log
    detection) shipped via src/audit/analysis/meta_log_check.py
    (119 LOC) with test_meta_log_check.py (2 tests pass) and CLI subcommand
    `trw-eval meta-log-check`. Manual claim audits exist as one-shot artifacts
    (e.g., docs/audit/iter-11-claims-audit.md) but are not automated.

    NOT YET IMPLEMENTED (3 of 4 FRs):
    - FR-01: Claim ledger module (no truth_audit.py / claim_ledger.py).
    - FR-02: Overclaim heuristic detector (no test_truth_audit_overclaims.py).
    - FR-04: Retraction/retirement tracking in result-doc template.
  stubs:
    - location: src/audit/analysis/truth_audit.py
      description: "FR-01 claim ledger module not present."
      activation_gate: FR-01
      upgrade_prd: self
    - location: src/audit/analysis/overclaim_detector.py
      description: "FR-02 heuristic checks for proves/demonstrates/winner-without-evidence claims."
      activation_gate: FR-02
      upgrade_prd: self
    - location: docs/audit/templates/iter-result-template.md
      description: "FR-04 result-doc template needs Retired Claims section + status field."
      activation_gate: FR-04
      upgrade_prd: self
  evidence:
    level: strong
    sources:
      - docs/CONSTITUTION.md
      - docs/audit/META-TUNE-LOG.md
      - docs/audit/iter-notes/iter-26-design.md
  confidence:
    implementation_feasibility: 0.86
    requirement_clarity: 0.88
    estimate_confidence: 0.75
    test_coverage_target: 0.85
  traceability:
    implements:
      - docs/CONSTITUTION.md#truthfulness
    depends_on:
      - PRD-CORE-151
---

# PRD-QUAL-076: Truthfulness and Overclaim Audit for Eval Iterations

## 1. Problem Statement

Eval notes can drift from raw evidence. The stale iter-26 draft framed iter-25 as a hard plateau and assumed unvalidated model/parser readiness. The Constitution requires truthfulness, evidence labels, and explicit uncertainty, but there is no standard audit artifact that checks claims against result data and prior retractions.

## 2. Goals & Non-Goals

### Goals
- Add a standard truthfulness/overclaim audit to every eval iteration.
- Force claims to be labeled Observed, Verified, Inferred, or Unknown.
- Identify contradicted prior claims, retired hypotheses, and stale draft sections.

### Non-Goals
- Replacing human review.
- Using an LLM as the sole judge of truth.
- Blocking exploratory notes that are clearly labeled draft/unknown.

## 3. Functional Requirements

### FR-01 — Claim ledger
**EARS**: WHEN an iteration result doc is finalized, it MUST include a claim ledger with evidence labels and source paths for every primary claim.

### FR-02 — Overclaim checks
**EARS**: WHEN a claim uses terms such as "proves", "demonstrates", "winner", or "plateau", the audit MUST require supporting n, effect size, uncertainty, and scope language.

### FR-03 — Stale meta-log detection
**EARS**: WHEN `trw-eval meta-log-check` runs, it MUST flag missing statuses and multiple non-superseded entries for the same iteration.

### FR-04 — Retraction/retirement tracking
**EARS**: WHEN a hypothesis is contradicted or scoped down, the result doc MUST list the retired claim and the evidence that changed it.

## 4. Acceptance Criteria

- A fixture with an unlabeled "proved" claim fails the audit.
- A fixture with Observed/Verified/Inferred/Unknown labels passes.
- `META-TUNE-LOG.md` status check runs in CI or pre-delivery scripts.

## 5. Traceability

| FR | Implementation target | Test target |
|---|---|---|
| FR-01 | future `trw_eval.analysis.truth_audit` | `test_truth_audit_claim_ledger.py` |
| FR-02 | truth audit heuristics | `test_truth_audit_overclaims.py` |
| FR-03 | `trw_eval.analysis.meta_log_check` | `test_meta_log_check.py` |
| FR-04 | result-doc template | `test_retired_claims_section.py` |
