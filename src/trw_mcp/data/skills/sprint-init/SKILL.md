---
name: sprint-init
description: >
  Initialize a new sprint. Lists draft PRDs, creates sprint doc,
  bootstraps run directory, sets up tracking.
  Use: /sprint-init "Sprint 16: Skills Architecture"
user-invocable: true
argument-hint: "[sprint name]"
allowed-tools: Read, Grep, Glob, Write, Edit, Bash
---

# Sprint Initialization Skill

Initialize a new sprint by selecting PRDs, creating a sprint planning document, and bootstrapping the TRW run directory.

## Workflow

1. **Survey draft PRDs**: Read `docs/requirements-aare-f/INDEX.md` to find all draft PRDs. Read each draft PRD's Problem Statement and Goals sections to understand scope.

2. **Present PRD candidates**: Show the user a summary table of available draft PRDs with:
   - PRD ID, title, priority
   - Brief problem statement (1-2 sentences)
   - Estimated complexity (based on section count and requirement density)

3. **Select PRDs**: Ask the user which PRDs to include in this sprint (or accept all drafts).

4. **Create sprint document**: Write a sprint planning doc to `docs/requirements-aare-f/sprints/active/` using the template below. Use the sprint name from `$ARGUMENTS` or generate one.

5. **Bootstrap run**: Call `trw_init(task_name="$ARGUMENTS", prd_scope=[selected_prd_ids])` to create the run directory with event tracking.

6. **Checkpoint**: Call `trw_checkpoint(message="Sprint initialized: $ARGUMENTS")`.

7. **Report**: Output sprint doc path, run directory, selected PRDs, and suggested next steps (groom PRDs, begin implementation tracks).

## Sprint Document Template

```markdown
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

## Completion Criteria

- [ ] All assigned PRDs reach done status
- [ ] Build gate passes: pytest + mypy clean
- [ ] Coverage >= 80%
- [ ] Delivery ceremony completed (/deliver)
```

## Notes

- Sprint docs live in `docs/requirements-aare-f/sprints/active/` while active
- On sprint completion (`/sprint-finish`), the doc moves to `archive/sprints/`
- Each sprint can have multiple tracks (A, B, C) for parallel work streams
