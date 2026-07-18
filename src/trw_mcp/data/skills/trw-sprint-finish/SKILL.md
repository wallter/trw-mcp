---
name: trw-sprint-finish
description: >
  Complete a sprint. Validates deliverables, runs build gate,
  validates PRD lifecycle, archives sprint doc, runs delivery ceremony.
  Use: /trw-sprint-finish
user-invocable: true
disable-model-invocation: true
---

# Sprint Completion

Use when: closing an active sprint after deliverables and evidence are ready for validation.

Close a sprint only when its PRDs, evidence, archive, and delivery state agree. This skill validates and archives; it never bypasses the PRD lifecycle state machine.

## 1. Resolve the active contract

1. Read `prds_relative_path` from `.trw/config.yaml` and locate the sprint document using the project's configured/conventional sibling paths.
2. Require exactly one active sprint document for the requested sprint. If active/completed duplicates exist, stop and report them; do not delete ambiguous files.
3. Read assigned PRDs, exit criteria, run path, coverage policy, and manual obligations from the sprint document.

## 2. Validate lifecycle and requirements

For every assigned PRD:

- read the nested `prd.status`, requirements, acceptance criteria, and evidence;
- require the lifecycle state expected by the project's completed-sprint policy (normally `done`);
- treat `draft`, `review`, `approved`, or `implemented` as incomplete unless the sprint contract explicitly defines a non-completion outcome;
- run the applicable spec-vs-code audit and resolve blocking findings.

Do **not** edit a non-terminal PRD directly to `done`. Normal phase/delivery progression must apply validated transitions (`draft -> review -> approved -> implemented -> done`) and their guards; only an eligible `implemented -> done` step may close completion. If a PRD cannot reach the required state through that path, block completion and report the exact state/evidence gap.

## 3. Verify exit criteria

Classify each criterion:

- **Observed/automatic:** run its named project-native command or inspect its durable artifact now. Execute a stored command only when it comes from trusted project-owned configuration or has explicit operator approval, using the normal sandbox.
- **Manual:** require explicit evidence or a user-authorized waiver that names residual risk and owner.
- **Missing/failed:** block completion.

Never mark a checkbox from narrative confidence. Record the command/artifact and observed result beside every verified criterion.

## 4. Run the pre-archive eligibility gate

1. Run the project-native full validation appropriate to the changed packages.
2. Record only observed results with `trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope="<exact command>")`; include `command_results` when evidence enforcement requires it.
3. Read coverage gates from project config and explicit accepted requirements. If they conflict, stop and surface the conflict. If no coverage threshold is configured, report measured coverage as informational and do not invent a percentage.
4. Complete the required substantive review. Any unresolved P0/blocking finding stops closure.

## 5. Archive safely

Only after lifecycle, exit criteria, validation, and review pass:

1. Update the sprint document's own status and completion date.
2. Atomically move that exact selected active document to the project's completed-sprint location.
3. Re-scan for duplicates. If another copy exists, report it for explicit reconciliation rather than deleting it blindly.
4. Preserve the run/evidence links in the archived document.
5. Treat the status/date/archive mutations as invalidating any earlier build or review evidence they postdate.

Do not archive an incomplete sprint as completed. A partial or cancelled sprint remains explicitly non-complete under the project's chosen location/status convention.

## 6. Deliver and report

After the last archive mutation, run project-native validation appropriate to the final tree and record a fresh
`trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope="<exact command>")`;
include typed `command_results` when enforced. Refresh substantive review if the archive mutation is inside its bound
scope. Never reuse the pre-archive build or review result as if it covered later edits.

Check `trw_status().deliver_gate_summary` before delivery. If the post-archive gate fails, do not deliver or leave the
sprint represented as successfully complete: atomically restore or reclassify it under the project's non-complete
convention and report the failure. When the gate is ready, ensure all filesystem changes are complete.
Call `trw_deliver()` as the last TRW action. Follow the framework's three-path delivery gate; a review label alone is not an
acceptable-failure record.

Report:

- PRD lifecycle status and audit result per PRD;
- exit criterion, evidence, and disposition;
- exact validation commands, counts, failures, static checks, and applicable coverage gate;
- review result;
- archive path and duplicate check;
- delivery result and residual risks.
