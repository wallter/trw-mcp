---
name: trw-exec-plan
description: >-
  Convert an approved PRD into a repository-grounded execution plan with
  behavior-sized tasks, verified paths/interfaces, tests or other proof,
  dependencies, ownership, migration concerns, and exact project-native
  verification. Internal phase used by trw-prd-ready and trw-prd-new.
user-invocable: false
argument-hint: "[PRD-ID or file path]"
---

# Execution Plan Generation

Use when: an implementation-ready PRD needs a concrete plan before coding.

## Readiness gate

Resolve the PRD from the supplied ID/path and project configuration. Call full
`trw_prd_validate(prd_path)`. Continue only with `validation_partial: false`,
`valid: true`, and risk-scaled `quality_tier: approved`; `total_score` is
diagnostic. Otherwise return the result fields and blockers to `trw-prd-ready`.

Treat **implementation-readiness** as the load-bearing signal. The plan must
make control points, testability, proof, migration/rollback where applicable,
and completion evidence explicit. Treat score-gaming and density-chasing as
failure modes.

## Pre-Implementation Checklist (PRD-QUAL-056-FR03)

Before writing the plan, confirm:

- acceptance criteria, non-goals, applicable NFRs, and unresolved decisions;
- repository root plus existing source, test, interface, and configuration
  seams discovered with Read/Grep/Glob rather than guessed paths;
- project language, framework, test/verification conventions, and generated
  projections;
- the exact project-native verification command, or explicit uncertainty when
  no safe command is evident.

Record checklist completion in plan metadata.

## Decompose by evidence boundary

For each requirement, create the smallest cohesive tasks that can be owned and
verified independently. Each task states:

1. behavior/acceptance criterion and requirement ID;
2. verified files plus symbols, interfaces, schemas, events, commands, jobs, or
   data contracts affected;
3. implementation change and production consumer/wiring point;
4. tests and negative/boundary/integration cases when behavior is
   machine-observable, or another objective verification method;
5. exact project-native command and expected evidence (for example `PASSED`),
   without inventing a runner;
6. dependencies, shared interfaces, ownership, and integration owner;
7. vertical proof slice, or rationale and follow-up for a horizontal
   prerequisite;
8. relevant security, privacy, performance, migration, rollback, and
   observability concerns.

Do not split solely by line count, fixed duration, or file count. Split when
ownership, dependency, risk, or verification boundaries differ. Do not create
parallel tasks that write the same path.

## Plan contract

Write `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md` (or the
project-configured sibling directory) with:

```markdown
# EXECUTION PLAN: {PRD-ID}

## Metadata
- PRD/version/readiness result
- Pre-Implementation Checklist: complete
- Repository and sizing/evidence basis

## Requirement decomposition
### {FR-ID}: {behavior}
| Task | Owned paths/symbols | Consumer/interface | Proof | Dependencies |
|---|---|---|---|---|

## Dependency DAG and critical integration path
## Safe waves or sequential order
## File ownership and shared-interface contracts
## Project-native verification checklist
## Migration/rollback and known risks
## Open decisions and blocked evidence
```

Generate test skeletons only when the project convention, acceptance criteria,
and caller request make them useful. Skeletons must represent meaningful
behavior and must not be committed as unconditional failures or broad skips.
If generated, place them in the configured planning artifact area and include a
manifest linking every skeleton to its requirement, owner, and verification.

## Completion

Report the PRD path, execution-plan path, task/dependency count, parallelism
assumptions, ownership conflicts, verification commands, generated optional
artifacts, and blockers. Do not claim the plan is executable when paths,
interfaces, or proof commands remain fabricated or UNKNOWN.
