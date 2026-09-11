---
name: trw-sprint-init
description: >
  Initialize an explicitly requested sprint. Lists draft PRDs, creates sprint doc,
  bootstraps run directory, sets up tracking.
  Use: /trw-sprint-init "Sprint 16: Skills Architecture"
user-invocable: true
argument-hint: "[sprint name]"
---
<!-- ultrathink -->

# Sprint Initialization

Use when: the user explicitly requests a sprint from scoped PRDs and an explicit sprint goal.

Create a sprint coordination contract from selected PRDs and bootstrap a resumable run only when a sprint is explicitly requested. Ordinary work does not require a sprint. Do not start implementation inside this skill.

## 1. Resolve project state

1. Read `prds_relative_path` from `.trw/config.yaml`; default to `docs/requirements-aare-f/prds` only when absent.
2. Treat `INDEX.md` and `sprints/{active,completed}` as siblings of that PRD directory when those paths exist. Follow project-native alternatives instead of creating duplicate catalogues.
3. Find existing sprint numbers in active and completed locations. Use the user-supplied number when present; otherwise choose the next unused number.
4. If a prior or parallel sprint is active, report its PRDs and exact overlapping files. Do not infer safety from a universal overlap percentage.

## 2. Select verified candidates

Read the catalogue and candidate PRD frontmatter/body. Present ID, title, lifecycle status, priority, problem, and known file ownership. Source searches may reveal likely implementation, but identifier existence is not proof of completion; label it as inspection evidence and recommend an audit when lifecycle state appears stale.

Ask the user to confirm the sprint scope unless the invocation already names exact PRDs. Do not silently include every draft.

## 3. Reuse governing plans; coordinate only the sprint

Read each selected PRD's accepted execution plan, embedded or project-required separate artifact. Reference its path and task IDs or section anchors; do not recreate per-PRD tasks, status, ownership, dependencies, or verification commands in the sprint document.

If a plan is missing or unaccepted, identify the gap and use the existing governing artifact and readiness workflow before scheduling that work as ready. Sprint selection does not grant approval. Surface conflicting plans or ownership for resolution rather than silently choosing or overwriting an authority.

Add only cross-PRD ordering, shared-file coordination, integration ownership, and aggregate/manual acceptance not already governed by those plans. Use their references to resolve the next task and its verification after resume:

- shared files or ordered interfaces stay sequential;
- disjoint work may run concurrently only when the active harness and project policy allow delegation;
- a single-session sequential plan is always valid.

Do not launch helpers automatically. Execution begins after scope/ownership approval.

## 4. Write the sprint document

Write one active sprint document using the project's convention. Preserve project-required separate formats and existing sprint artifacts; do not auto-migrate them. Keep the lifecycle metadata and aggregate closure obligations below, then link governing plans and add only the sprint-specific coordination from step 3:

```yaml
sprint: <number>
name: <name>
run_task_name: <safe <=128-char slug, e.g. sprint-16-skills-architecture>
status: active
prd_ids: [<PRD-ID>]
run_path: <filled after trw_init>
coverage_threshold: null  # Populate only from project config or an accepted requirement.
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
3. Checkpoint the selected PRDs, governing-plan references, sprint-specific coordination, execution mode, and open risks; keep per-PRD progress in its governing artifact.
4. Report the sprint path, run path, scope, ownership conflicts, and next approval/action.

Initialization is complete when the sprint contract and run are resumable—not when implementation has been launched.
