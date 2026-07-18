---
name: trw-deliver
description: "Persist the session's work with validation evidence, durable learnings, and client instruction sync. Use: /trw-deliver"
---

# TRW Deliver

Run the delivery ceremony before ending a work session.

## Steps

1. Inspect the current diff and active requirements.
2. **Traceability pre-flight** (advisory — not a hard block): Delegate to the **trw-traceability-checker** agent or equivalent to verify bidirectional PRD→code→test links for the PRDs in scope. Surface any untraced FRs, orphan implementations, or stale matrix entries as warnings in the delivery report. Proceed even if gaps are found — record them as residual risks and use `trw_learn` to capture patterns if the gap is systemic. Skip this step if no PRDs are in scope for the session.
3. Run the narrowest meaningful validation that proves the change, then broaden if risk warrants it.
4. Call `trw_build_check(...)` with the validation result.
5. Record any durable gotchas with `trw_learn(...)`.
6. If helpers or separate workstreams contributed, consolidate their findings and resolve duplicates before delivery.
7. Call `trw_deliver()` only through one of the three gate paths below.
8. Report completed work, validation, residual risks (including any untraced FRs from step 2), and committed/uncommitted paths.

## Guardrails

- Do not record routine status as a learning.
- **Deliver gate — no fourth path:** require at least one of three sanctioned paths: (1) a passing `trw_build_check` bound to post-edit
  project-native validation; (2) `allow_unverified=true` with a valid, unexpired structured `unverified_reason`
  containing `failed_command`, `residual_risk`, `owner`, and `expiry_iso`; or (3) an authorized operator/config
  override recorded with technical rationale.
- Free-text limitations and review-verdict labels are not acceptable-failure records. If no gate path exists, stop and
  report delivery blocked rather than claiming success.
- Do not mix unrelated dirty files into the delivery commit.
