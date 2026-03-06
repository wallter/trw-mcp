---
name: trw-sprint-finish
description: >
  Complete a sprint. Validates deliverables, runs build gate,
  updates PRD statuses, archives sprint doc, runs delivery ceremony.
  Use: /trw-sprint-finish
user-invocable: true
allowed-tools: Read, Grep, Glob, Write, Edit, Bash, mcp__trw__trw_build_check, mcp__trw__trw_deliver
---

# Sprint Completion Skill

Complete an active sprint by validating deliverables, running the build gate, archiving the sprint document, and executing the full delivery ceremony.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD and sprint directories. Sprint docs and archives are siblings of the prds directory under the same parent.

## Workflow

1. **Find active sprint**: Look for sprint docs in the `sprints/active/` subdirectory (sibling of `prds_relative_path`). Also check `sprints/planned/` and `sprints/completed/` if no active sprint found. If multiple exist, ask the user which sprint to close.

2. **Read sprint doc**: Extract assigned PRDs, goals, and completion criteria.

3. **Check PRD statuses**: For each assigned PRD, read its frontmatter status:
   - Expected: `done` or `implemented`
   - If any PRD is still `draft`, `review`, or `approved`, report which PRDs are incomplete and ask the user whether to proceed anyway

4. **Parse exit criteria checkboxes**: Spec reconciliation (`trw_review(mode='reconcile')`) should have been run during the REVIEW phase for each governing PRD. Extract all `- [ ]` and `- [x]` lines from the "Exit Criteria" section of the sprint doc.
   - For each **unchecked** (`- [ ]`) item, classify it:
     - **Auto-verifiable**: build passes, coverage >= N%, mypy clean, PRD status = done — verify these now and report pass/fail
     - **Manual**: pen testing completed, docs published, deployment verified — require explicit user waiver (`WAIVE: criterion text`)
   - If **any criterion is unchecked AND not auto-verified AND not waived**: BLOCK sprint completion
   - Report all criteria with their verification status: `[CHECKED]`, `[VERIFIED]`, `[WAIVED]`, or `[BLOCKED]`
   - If the sprint doc has YAML frontmatter with `exit_criteria:` list, parse that instead (machine-readable format takes precedence over markdown checkboxes)

5. **Build gate with coverage threshold**: Extract coverage target from exit criteria (pattern: `coverage >= X%` or `coverage_threshold: X` in YAML frontmatter). Default to 80% if not specified.
   - Call `trw_build_check(scope="full")` to run tests + type-check
   - If coverage is below the threshold: BLOCK with message showing actual vs required
   - If build **fails**: Report failures, do NOT proceed. The sprint cannot be completed with a failing build.
   - If build **passes** and coverage meets threshold: Continue.

6. **Move sprint doc to completed**: Move the sprint doc from its current location (`sprints/planned/` or `sprints/active/`) to the `sprints/completed/` subdirectory. Update the sprint doc's `**Status**:` line to `Done` with the completion date. This step is REQUIRED — sprint docs left in `planned/` or `active/` after completion cause confusion in future sprint planning.
   ```bash
   # Example (adjust filename):
   mv "sprints/planned/sprint-39-name.md" "sprints/completed/sprint-39-name.md"
   ```

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

## Notes

- A sprint cannot be completed if the build gate fails
- A sprint cannot be completed if exit criteria are unchecked and unwaived
- PRDs that are not yet `done` will be flagged — the user can override to close anyway
- The archived sprint doc preserves the full sprint history for retrospectives
- After sprint-finish, use `/trw-sprint-init` to start the next sprint
