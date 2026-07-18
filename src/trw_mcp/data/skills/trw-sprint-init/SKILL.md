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

# Sprint Initialization

Use when: starting a sprint from approved, scoped PRDs and an explicit sprint goal.

Create a sprint contract from selected PRDs, establish ownership, and bootstrap a resumable run. Do not start implementation inside this skill.

## 1. Resolve project state

1. Read `prds_relative_path` from `.trw/config.yaml`; default to `docs/requirements-aare-f/prds` only when absent.
2. Treat `INDEX.md` and `sprints/{active,completed}` as siblings of that PRD directory when those paths exist. Follow project-native alternatives instead of creating duplicate catalogues.
3. Find existing sprint numbers in active and completed locations. Use the user-supplied number when present; otherwise choose the next unused number.
4. If a prior or parallel sprint is active, report its PRDs and exact overlapping files. Do not infer safety from a universal overlap percentage.

## 2. Select verified candidates

Read the catalogue and candidate PRD frontmatter/body. Present ID, title, lifecycle status, priority, problem, and known file ownership. Source searches may reveal likely implementation, but identifier existence is not proof of completion; label it as inspection evidence and recommend an audit when lifecycle state appears stale.

Ask the user to confirm the sprint scope unless the invocation already names exact PRDs. Do not silently include every draft.

## 3. Plan ownership and execution mode

Group work by actual dependencies and file ownership:

- shared files or ordered interfaces stay in one sequential track;
- disjoint tracks may run concurrently only when the active harness and project policy allow delegation;
- a single-session sequential plan is always valid;
- every track must name owned paths, inputs/outputs, validation, and handoff evidence.

Do not launch helpers automatically. Sprint initialization prepares the contract; execution begins after scope/ownership approval.

## 4. Write the sprint document

Write one active sprint document using the project's convention. Keep the schema compact:

```yaml
sprint: <number>
name: <name>
run_task_name: <safe <=128-char slug, e.g. sprint-16-skills-architecture>
status: active
prd_ids: [<PRD-ID>]
run_path: <filled after trw_init>
coverage_threshold: null  # Populate only from project config or an accepted requirement.
tracks:
  - id: A
    prd_ids: [<PRD-ID>]
    owned_paths: [<path>]
    depends_on: []
exit_criteria:
  - id: prd-lifecycle
    description: Assigned PRDs reached their evidence-backed terminal sprint state
    verified: false
  - id: project-validation
    description: Project-native validation passed and observed results were recorded
    verified: false
  - id: review
    description: Required substantive review completed
    verified: false
completion_actions:
  - trw_deliver  # Final TRW action after completion files are settled.
```

Derive `run_task_name` deterministically from the display name, then require it to match
`^[a-zA-Z0-9][a-zA-Z0-9_-]*$` and contain at most 128 characters. Reject and report an empty, invalid, or ambiguous
slug rather than silently stripping it into a different sprint identity. Keep `name` as the human-facing title.

If no coverage threshold is configured or explicitly accepted, keep it null and omit any coverage pass/fail criterion; do not invent a percentage. Add project-specific exit criteria only when the PRDs or repository define them. Do not copy framework-internal incident checklists into unrelated projects.

## 5. Bootstrap and report

1. Call `trw_init(task_name=<run_task_name>, objective=<display sprint name>, prd_scope=[...])`; never pass the display title directly as `task_name`.
2. Insert the returned run path into the sprint document.
3. Checkpoint the selected PRDs, track ownership, dependencies, execution mode, and open risks.
4. Report the sprint path, run path, scope, ownership conflicts, and next approval/action.

Initialization is complete when the sprint contract and run are resumable—not when implementation has been launched.
