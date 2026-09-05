---
name: trw-team-playbook
description: "Generate portable coordination playbooks with file ownership and interface contracts for sprint workstreams. Works for local sequential work, human handoff, or any client-supported helper. Use: /trw-team-playbook [sprint-doc-path]"
---

# TRW Coordination Playbook

**Use when:** sprint work needs explicit ownership, contracts, and verification instructions before implementation.

## Rules

- Do not assume any provider-specific helper, background worker, or peer messaging feature.
- Test files are owned files. A test file MUST NOT be assigned to multiple writers.
- Shared interfaces need one writer and a written contract.
- Playbooks are instructions for a workstream, not proof of completion.

## Steps

1. Read the sprint doc and referenced PRDs/exec plans.
2. Build workstreams from requirements and file boundaries.
3. Derive exclusive ownership:
   - `owns`: source/config/docs files the workstream may edit
   - `test_owns`: test files the workstream may edit
   - `does_not_own`: files owned by other workstreams
4. Stop on overlap. Resolve by splitting files or assigning a single owner.
5. Record the result as a formation manifest: `trw_init(advanced={"formation": {...}})`
   writes `formation.yaml` under the orchestrator run, whose `owned_paths` /
   `test_owned_paths` are the ownership declaration every enforcement surface reads.
6. Write interface contracts for shared boundaries.
7. Write one playbook per workstream with:
   - mission
   - owned files
   - acceptance criteria
   - verification commands
   - coordination notes
   - output contract: changed paths, tests run, risks
8. Optionally inject relevant `trw_recall` findings when the harness supports it.

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

Report generated artifact paths and any unresolved ownership risks.
