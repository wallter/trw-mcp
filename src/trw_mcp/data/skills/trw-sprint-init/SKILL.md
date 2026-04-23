---
name: trw-sprint-init
description: >
  Initialize a new sprint. Lists draft PRDs, creates sprint doc,
  bootstraps run directory, sets up tracking.
  Use: /trw-sprint-init "Sprint 16: Skills Architecture"
user-invocable: true
argument-hint: "[sprint name]"
---
<!-- ultrathink -->

# Sprint Initialization Skill

Use when: starting a new sprint and you need to select draft PRDs, create the sprint doc, and bootstrap the run directory.

Initialize a new sprint by selecting PRDs, creating a sprint planning document, and bootstrapping the TRW run directory.

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate the PRD directory. The INDEX.md and sprints directories are siblings of the prds directory under the same parent.

## Sprint Number Auto-Detection

Automatically determine the next sprint number — never ask the user for it:

1. List all sprint docs: `ls sprints/active/sprint-*.md sprints/completed/sprint-*.md archive/sprints/sprint-*.md 2>/dev/null`
2. Extract sprint numbers from filenames using regex: `sprint-(\d+)`
3. Take the maximum number found and add 1 → that's the new sprint number
4. If the user provides a sprint number in `$ARGUMENTS` (e.g., `/trw-sprint-init "Sprint 76: Feature X"`), use that instead

This eliminates the manual step of scanning directories to find the next number.

## Pre-flight: Prior Sprint Verification

Before creating a new sprint, check that the prior sprint (N-1) has been properly archived:
1. Look for `sprint-{N-1}*.md` in `sprints/completed/` or `archive/sprints/`
2. If the prior sprint doc is still in `sprints/active/` or `sprints/planned/`, **warn** the user: "Sprint N-1 has not been completed yet. Run `/trw-sprint-finish` first, or acknowledge this gap."
3. This is a **warning**, not a blocker -- the user can proceed if they acknowledge.

## Parallel Sprint Detection

When another sprint is active, automatically assess file overlap:

1. Read the active sprint doc(s) from `sprints/active/`
2. Extract the PRD IDs from each active sprint's "PRD Assignments" table
3. Read the Key Files table from each active sprint's PRDs
4. Compare against the candidate PRDs' Key Files
5. Report overlap:
   - **0-5% overlap**: "Safe to run in parallel — independent file ownership"
   - **5-20% overlap**: "Caution — partial overlap in: {files}. Consider sequential execution."
   - **>20% overlap**: "High conflict risk — these sprints modify the same modules. Recommend completing Sprint N before starting."
6. This is advisory — the user decides whether to proceed.

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

## Pre-Implementation FPI Checklist (2026-04-18)

Before any PRD in this sprint moves to `status: implemented`, the sprint MUST meet the 11 Framework Process Improvements (FPIs) from
[`docs/research/agentic-hpo/DISTILLERY-DEFECT-LEDGER-2026-04-18.md`](../../../docs/research/agentic-hpo/DISTILLERY-DEFECT-LEDGER-2026-04-18.md).
The DIST-001/002/003/004 post-mortem found 15 defects + 12 hidden stubs that every unit test passed through — the FPIs prevent recurrence.

### FPI Gating (all P0 — block `status: implemented`)

| # | FPI | How to verify |
|---|---|---|
| 1 | Real-data integration test per cross-module FR | Each PRD's acceptance section names at least one integration test that exercises a real artifact (live git repo, live SQLite db, etc.) — not a synthetic fixture |
| 2 | CLI `--format json` parses end-to-end | Test pipes stdout through `json.loads()` + schema-validates |
| 3 | Detector FPR ceiling declared in NFR | Every `FR-5`-style pattern detector carries an explicit NFR: `false_positive_rate <= X%` on a non-X corpus |
| 4 | Pipeline adapter contract codified as FR | When stage N feeds stage N+1, the data-flow contract (field names + types) is a first-class FR of BOTH stages |
| 5 | Stderr/stdout discipline | New CLIs configure `structlog.PrintLoggerFactory(file=sys.stderr)` at import time — never interleave with stdout JSON |
| 6 | Run-on-monorepo before `implemented` | Any CLI-facing code MUST be exercised end-to-end against THIS repo (real diffs, real conventions) and a human samples the output before the PRD flips status |
| 7 | `functionality_level` + `stubs[]` frontmatter | Enforced by `trw_prd_validate` — see [`docs/requirements-aare-f/CLAUDE.md`](../../../docs/requirements-aare-f/CLAUDE.md) §Functionality-Level Frontmatter |
| 8 | Dispatcher reachability tests | When a PRD declares N detector/extractor families, a regression test asserts the production dispatcher invokes all N |
| 9 | Config-resolution E2E tests | A PRD config field gets a test exercising the full chain: source → CLI → orchestrator → actual request |
| 10 | CLI live-path smoke test | Before `status: implemented` the CLI's subcommands are exercised against a real/dockerized backing service in CI |
| 11 | Env-file cwd behaviour documented | Pydantic-settings `env_file` is cwd-relative — any PRD adding a config source documents OR implements upward search |

### How to use during sprint-init

Include this table verbatim (or by reference) in the sprint planning doc. When a track owner requests review / promotion to `status: implemented`, the reviewer walks the table and rejects the promotion on the first unmet row. This is the Wave-5 lesson from the distillery defect ledger: catching the gap at review is cheaper than discovering it in a real run.

## Notes

- Sprint docs live in the `sprints/active/` subdirectory while active
- On sprint completion (`/trw-sprint-finish`), the doc moves to `sprints/completed/` or `archive/sprints/`
- Each sprint can have multiple tracks (A, B, C) for parallel work streams
- The YAML frontmatter `exit_criteria` section enables machine-readable verification by `sprint-finish`
