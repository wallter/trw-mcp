---
name: trw-sprint-init
model: claude-opus-4-6
description: >
  Initialize a new sprint. Lists draft PRDs, creates sprint doc,
  bootstraps run directory, sets up tracking.
  Use: /trw-sprint-init "Sprint 16: Skills Architecture"
user-invocable: true
argument-hint: "[sprint name]"
allowed-tools: Read, Grep, Glob, Write, Edit, Bash, mcp__trw__trw_init, mcp__trw__trw_checkpoint
---
<!-- ultrathink -->

# Sprint Initialization Skill

Initialize a new sprint by selecting PRDs, creating a sprint planning document, and bootstrapping the TRW run directory.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD directory. The INDEX.md and sprints directories are siblings of the prds directory under the same parent.

## Pre-flight: Prior Sprint Verification

Before creating a new sprint, check that the prior sprint (N-1) has been properly archived:
1. Look for `sprint-{N-1}*.md` in `sprints/completed/` or `archive/sprints/`
2. If the prior sprint doc is still in `sprints/active/` or `sprints/planned/`, **warn** the user: "Sprint N-1 has not been completed yet. Run `/trw-sprint-finish` first, or acknowledge this gap."
3. This is a **warning**, not a blocker -- the user can proceed if they acknowledge.

## Workflow

1. **Survey draft PRDs**: Read `INDEX.md` in the PRD parent directory (sibling of the configured `prds_relative_path`) to find all draft PRDs. Read each draft PRD's Problem Statement and Goals sections to understand scope.

2. **Present PRD candidates**: Show the user a summary table of available draft PRDs with:
   - PRD ID, title, priority
   - Brief problem statement (1-2 sentences)
   - Estimated complexity (based on section count and requirement density)

3. **Pre-implementation state check**: For each candidate PRD, verify whether its FRs are already implemented:
   a. Read each FR's Description and Acceptance sections.
   b. Extract key identifiers (function names, class names, endpoint paths) from backtick-wrapped terms.
   c. Grep the codebase for each identifier.
   d. If >80% of identifiers for a PRD already exist: flag as `LIKELY IMPLEMENTED` in the candidate table.
   e. If >50% exist: flag as `PARTIALLY IMPLEMENTED`.
   f. Present the implementation status alongside each candidate.
   This prevents wasting agent sessions on already-implemented PRDs. Sprint 64 caught 3 PRDs that were already done but still marked "groomed".

4. **Select PRDs**: Ask the user which PRDs to include in this sprint (or accept all drafts).

5. **Create sprint document**: Write a sprint planning doc to the `sprints/active/` subdirectory (sibling of `prds_relative_path`) using the template below. Use the sprint name from `$ARGUMENTS` or generate one.

6. **Bootstrap run**: Call `trw_init(task_name="$ARGUMENTS", prd_scope=[selected_prd_ids])` to create the run directory with event tracking.

7. **Checkpoint**: Call `trw_checkpoint(message="Sprint initialized: $ARGUMENTS")`.

8. **Report**: Output sprint doc path, run directory, selected PRDs, and suggested next steps (groom PRDs, begin implementation tracks).

## Sprint Document Template

Sprint docs should include YAML frontmatter for machine-readable exit criteria. The `sprint-finish` skill reads this frontmatter for automated verification.

````markdown
---
sprint: {N}
coverage_threshold: 80
exit_criteria:
  - id: prd-status
    description: All assigned PRDs reach done status
    type: auto
    verified: false
  - id: build-gate
    description: Build gate passes -- tests pass + type-check clean
    type: auto
    command: "trw_build_check(scope='full')"
    verified: false
  - id: coverage
    description: "Coverage >= {coverage_threshold}%"
    type: auto
    verified: false
  - id: delivery
    description: Delivery ceremony completed (/trw-deliver)
    type: auto
    verified: false
---

# {Sprint Name}

**Created**: {date}
**Status**: Active
**Run**: {run_path}

## Goals

{1-3 sprint goals derived from selected PRDs}

## PRD Assignments

| Track | PRD | Title | Priority | Owner |
|-------|-----|-------|----------|-------|
| A | {PRD-ID} | {title} | {priority} | -- |

## Timeline

- Sprint start: {date}
- Mid-sprint review: --
- Sprint end: --

## Exit Criteria

- [ ] All assigned PRDs reach done status
- [ ] Build gate passes: tests pass + type-check clean
- [ ] Coverage >= {coverage_threshold}%
- [ ] Delivery ceremony completed (/trw-deliver)
````

## After Grooming: Auto-Parallel Implementation

Once PRDs are groomed and approved, proceed to implementation automatically:

1. **Analyze file overlap**: For each PRD, identify the modules/files it touches
2. **Group into tracks**: PRDs with <5% file overlap go in separate tracks (parallelizable). PRDs sharing modules go in the same track (sequential)
3. **Launch parallel subagents**: One subagent per track (not per PRD). Each subagent implements its track's PRDs sequentially, writes tests, and validates
4. **Final gate**: After all tracks complete, run `trw_build_check(scope="full")` to verify no cross-track regressions

The user does not need to direct parallelism -- this is the default behavior after PRD approval.

## Notes

- Sprint docs live in the `sprints/active/` subdirectory while active
- On sprint completion (`/trw-sprint-finish`), the doc moves to `sprints/completed/` or `archive/sprints/`
- Each sprint can have multiple tracks (A, B, C) for parallel work streams
- The YAML frontmatter `exit_criteria` section enables machine-readable verification by `sprint-finish`
