---
name: trw-sprint-finish
description: >
  Complete a sprint. Validates deliverables, runs build gate,
  updates PRD statuses, archives sprint doc, runs delivery ceremony.
  Use: /sprint-finish
user-invocable: true
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Sprint Completion Skill

Complete an active sprint by validating deliverables, running the build gate, archiving the sprint document, and executing the full delivery ceremony.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD and sprint directories. Sprint docs and archives are siblings of the prds directory under the same parent.

## Workflow

1. **Find active sprint**: Look for sprint docs in the `sprints/active/` subdirectory (sibling of `prds_relative_path`). If multiple exist, ask the user which sprint to close.

2. **Read sprint doc**: Extract assigned PRDs, goals, and completion criteria.

3. **Check PRD statuses**: For each assigned PRD, read its frontmatter status:
   - Expected: `done` or `implemented`
   - If any PRD is still `draft`, `review`, or `approved`, report which PRDs are incomplete and ask the user whether to proceed anyway

4. **Build gate**: Call `trw_build_check(scope="full")` to run pytest + mypy.
   - If build **fails**: Report failures, do NOT proceed. The sprint cannot be completed with a failing build.
   - If build **passes**: Continue.

5. **Archive sprint doc**: Move the sprint doc from `sprints/active/` to the `archive/sprints/` subdirectory. Update its status to "Completed" with the completion date.

6. **Delivery ceremony**: Call `trw_deliver()` for full delivery (reflect, checkpoint, claude_md_sync, index_sync).

7. **Report**:
   - Completed PRDs (count and IDs)
   - Test results (total, passed, coverage)
   - mypy status
   - Sprint doc archive path
   - Learnings promoted
   - Suggested next steps

## Notes

- A sprint cannot be completed if the build gate fails
- PRDs that are not yet `done` will be flagged — the user can override to close anyway
- The archived sprint doc preserves the full sprint history for retrospectives
- After sprint-finish, use `/sprint-init` to start the next sprint
