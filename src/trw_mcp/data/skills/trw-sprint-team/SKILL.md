---
name: trw-sprint-team
description: "Plan sprint-scale coordination from a sprint document. Produces a simple file-ownership and helper-assignment plan that works with any client or harness; does not assume beta peer-team features. Use: /trw-sprint-team [sprint-doc-path]"
---

# TRW Sprint Coordination

Use when a sprint plan exists and you need to divide work safely across one or more humans, subagents, or sequential local passes.

## Rules

- Do not assume a specific client, model, background worker, or peer messaging surface.
- If the active harness supports helpers, use them only after file ownership is explicit.
- If helpers are unavailable, execute the same assignments sequentially.
- Keep the orchestrator responsible for final integration, validation, and delivery.

## Steps

1. Read the sprint document and referenced PRDs/exec plans.
2. Extract requirements, likely source files, likely test files, dependencies, and validation commands.
3. Propose a coordination plan:
   - workstream name
   - owner type: `local`, `human`, `subagent`, or `external-helper`
   - owned source files
   - owned test files
   - dependencies/blockers
   - validation command
4. Validate zero overlap across source and test file ownership.
5. Ask for user approval before launching helpers or making broad edits.
6. Generate/update coordination artifacts under `scratch/sprint-coordination/` when persistence is useful:
   - `file_ownership.yaml`
   - `workstreams.yaml`
   - optional `playbooks/{workstream}.md`
7. Execute or delegate in the smallest safe batches.
8. Integrate results, run validation, record `trw_build_check`, and deliver.

## Packaged specialists

When the harness supports helpers, these are the role agents TRW packages.
Confirm which are actually installed for the active client — thin profiles ship
a subset — then assign each workstream to the one whose boundary matches. Do not
invent a role, and never assign production edits to a read-only agent.

| agent | owns | writes files? |
|---|---|---|
| `trw-implementer` | production code and its tests, within an assigned boundary | yes |
| `trw-tester` | tests only — verifies requirements the implementer wrote code for | yes (tests) |
| `trw-researcher` | investigation of code and external sources; returns findings | no |
| `trw-reviewer` | correctness/security/test-quality review of a diff | no |
| `trw-auditor` | spec-vs-code audit of a requirement set, with traceability | no |
| `trw-adversarial-auditor` | independent red-team pass over an audit's verdicts | no |
| `trw-traceability-checker` | requirement→source→test link verification | no |
| `trw-prd-groomer` | PRD authoring and grooming | yes (PRDs) |
| `trw-requirement-writer` | requirement text for a caller to apply | no |
| `trw-requirement-reviewer` | independent PRD readiness verdict | no |

A sub-agent generally cannot spawn another sub-agent, so a helper you assign
work to will execute it directly rather than delegating further.

## Output

Report:

- sprint doc path
- workstream table
- file ownership artifact path, if written
- validation commands
- next action for the orchestrator
