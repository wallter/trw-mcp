---
name: trw-sprint-finish
description: >
  Complete a sprint. Validates deliverables, runs build gate,
  updates PRD statuses, archives sprint doc, runs delivery ceremony.
  Use: /trw-sprint-finish
user-invocable: true
disable-model-invocation: true
---

# Sprint Completion Skill

Complete an active sprint by validating deliverables, running the build gate, archiving the sprint document, and executing the full delivery ceremony.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD and sprint directories. Sprint docs and archives are siblings of the prds directory under the same parent.

## Workflow

1. **Find active sprint**: Look for sprint docs in the `sprints/active/` subdirectory (sibling of `prds_relative_path`). Also check `sprints/planned/` and `sprints/completed/` if no active sprint found. If multiple exist, ask the user which sprint to close.

2. **Read sprint doc**: Extract assigned PRDs, goals, and completion criteria.

3. **Check PRD statuses (read-only)**: For each assigned PRD, read its YAML frontmatter status (the `status:` field is nested under the `prd:` key, e.g., `prd:\n  status: draft`):
   - Expected: `done` or `implemented`
   - If any PRD is still `draft`, `review`, `approved`, or `implemented`, report which PRDs need updating
   - If any PRD is genuinely incomplete (work not done), ask the user whether to update it anyway or exclude it from the sprint
   - **Security PRD escalation (PRD-QUAL-044-FR04)**: If any PRD has `tags: [security]` or title contains "security"/"hardening"/"vulnerability" and its status is NOT `done`, escalate it to P0 — security PRDs cannot be deferred or left incomplete
   - **Do NOT update PRD statuses yet** — wait until the build gate passes (step 5)

3a. **Spawn adversarial auditor (PRD-QUAL-044-FR01/FR02)**: Before proceeding to exit criteria, spawn a `trw-adversarial-auditor` agent for each sprint PRD (or batch them into one audit pass covering all PRDs). The auditor independently verifies spec compliance — this is mandatory, not optional.
   - If the auditor reports **any P0 findings**: BLOCK sprint completion — status cannot be set to "done"
   - If the auditor reports **P1 findings**: report them but allow completion with user acknowledgement
   - If the auditor is unavailable (e.g., agent spawn fails): log a warning and proceed, but flag it in the report as "audit_skipped"

3b. **Check for partial completion (PRD-QUAL-044-FR03)**: If the user passes `--partial` flag or the sprint doc has `status: partial` in frontmatter:
   - Allow sprint to close with status "partial" instead of "done"
   - Require a completion table in the sprint doc listing which PRDs are done vs. remaining
   - Remaining PRDs will be carried forward to the next sprint

4. **Parse exit criteria checkboxes**: Spec reconciliation (`trw_review(mode='reconcile')`) should have been run during the REVIEW phase for each governing PRD. Extract all `- [ ]` and `- [x]` lines from the "Exit Criteria" section of the sprint doc.
   - For each **unchecked** (`- [ ]`) item, classify it:
     - **Auto-verifiable**: build passes, coverage >= N%, mypy clean, PRD status = done — verify these now and report pass/fail
     - **Manual**: pen testing completed, docs published, deployment verified — require explicit user waiver (`WAIVE: criterion text`)
   - **`verify:` command support (PRD-QUAL-045-FR04)**: If an exit criterion has an inline `verify: <command>` field, run the command (with a 60s timeout) and use its exit code to determine pass/fail. This allows automated verification of criteria like "grep -c pattern file".
   - If **any criterion is unchecked AND not auto-verified AND not waived**: BLOCK sprint completion
   - Report all criteria with their verification status: `[CHECKED]`, `[VERIFIED]`, `[WAIVED]`, or `[BLOCKED]`
   - If the sprint doc has YAML frontmatter with `exit_criteria:` list, parse that instead (machine-readable format takes precedence over markdown checkboxes)

4a. **Wave completion check (PRD-INFRA-036-FR04)**: If the sprint doc contains a wave manifest (YAML `waves:` block), check that each wave has at least one checkpoint with a matching `wave_id`. Warn (do not block) if any wave lacks a completion checkpoint — this indicates incomplete tracking, not necessarily incomplete work.

5. **Build gate with coverage threshold**: Extract coverage target from exit criteria (pattern: `coverage >= X%` or `coverage_threshold: X` in YAML frontmatter). Default to 80% if not specified.
   - Call `trw_build_check(scope="full")` to run tests + type-check
   - If coverage is below the threshold: BLOCK with message showing actual vs required
   - If build **fails**: Report failures, do NOT proceed. The sprint cannot be completed with a failing build.
   - If build **passes** and coverage meets threshold: Continue to step 5a.

   5a. **Update PRD statuses to `done`**: Now that the build gate has passed, update all non-done PRDs. For each PRD that is not yet `done`, use the Edit tool to change `status: <current>` to `status: done` under the `prd:` frontmatter key. Sprint completion certifies the work — the state machine's incremental transitions are for in-flight work, not for the sprint closure ceremony.

6. **Move sprint doc to completed and clean up duplicates**:
   a. Copy the sprint doc to the `sprints/completed/` subdirectory (if not already there).
   b. Update the sprint doc's `**Status**:` line to `Done` with the completion date.
   c. **Remove all copies from other directories** — check `sprints/planned/` and `sprints/active/` for copies of the same sprint doc and delete them. This prevents duplicate sprint docs that cause confusion in future sprint planning.
   ```bash
   # Example (adjust filename):
   cp "sprints/planned/sprint-39-name.md" "sprints/completed/sprint-39-name.md"
   rm -f "sprints/planned/sprint-39-name.md" "sprints/active/sprint-39-name.md"
   ```
   This step is REQUIRED — sprint docs left in `planned/` or `active/` after completion cause confusion in future sprint planning (this was identified as a recurring framework bug).

7. **Delivery ceremony**: Call `trw_deliver()` for full delivery (reflect, checkpoint, claude_md_sync, index_sync).

8. **Report**:
   - Exit criteria verification table (criterion | status)
   - Completed PRDs (count and IDs)
   - Test results (total, passed, coverage vs threshold)
   - mypy status
   - Sprint doc archive path
   - Learnings promoted
   - Suggested next steps

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "Some PRDs are still draft but the build passes, so let's close the sprint" | Draft PRDs mean unfinished work — closing the sprint archives them as implicitly done | Future sprints won't pick up the draft PRDs, creating permanent gaps in the requirements catalogue |
| "The build gate failed but it's just a flaky test" | Flaky tests mask real failures — the gate exists to catch exactly this | Shipping past a failed gate has caused 3 production regressions in past sprints |
| "I'll skip the delivery ceremony, the commit is enough" | Delivery ceremony syncs learnings, updates INDEX.md, and checkpoints the run | Skipping trw_deliver means the next session starts with zero knowledge from this sprint |
| "The exit criteria checkboxes are just formatting, not contracts" | Each checkbox represents a stakeholder commitment — checking them without verification is the #1 cause of false sprint completions | Sprint 29 was falsely reported complete because an agent checked boxes without verifying the work was actually done |
| "I can check the boxes because the work was 'partially' done" | Partial completion is not completion — a checkbox means 100% done, verified | Checking a box for partial work hides gaps that compound across sprints |
| "Coverage is close enough to the threshold, rounding is fine" | The threshold exists precisely to prevent gradual erosion — 79.9% is not 80% | Each sprint that ships below threshold normalizes the gap until coverage is meaningfully degraded |
| "I'll skip moving the sprint doc, it's just housekeeping" | Sprint docs in `planned/` signal unfinished work to future agents — leaving a completed sprint there wastes planning time and causes confusion | Sprint 39 was completed but left in `planned/`, requiring a manual cleanup pass |
| "I'll update PRD statuses before running the build gate" | If the build gate fails, PRDs are stuck at terminal `done` with no rollback — `done` is a terminal status in the state machine | Always run the build gate FIRST, then update PRDs only after it passes (step 5a) |
| "The PRDs will get updated to 'done' eventually by auto-progression" | Auto-progression relies on run prd_scope and valid state machine transitions — PRDs stuck in 'draft' can NEVER auto-progress to 'done' because draft→done is not a valid transition | Sprints 45, 48, and 51 completed with all 12 PRDs left as 'draft', requiring manual reconciliation |
| "I only need to remove the sprint doc from its current directory" | Sprint docs can exist in multiple directories simultaneously (planned/ AND completed/) if previous sprint attempts copied but didn't clean up | Sprints 48 and 51 had duplicate docs in both planned/ and completed/, confusing future sprint planning |

## Notes

- A sprint cannot be completed if the build gate fails
- A sprint cannot be completed if exit criteria are unchecked and unwaived
- PRDs that are not yet `done` will be flagged — the user can override to close anyway
- The archived sprint doc preserves the full sprint history for retrospectives
- After sprint-finish, use `/trw-sprint-init` to start the next sprint
