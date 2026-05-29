---
name: trw-team-playbook
description: "Generate portable coordination playbooks with file ownership and interface contracts for sprint workstreams. Works for local sequential work, human handoff, or any client-supported helper. Use: /trw-team-playbook [sprint-doc-path]"
---

# TRW Coordination Playbook

Use when sprint work needs explicit ownership, contracts, and verification instructions before implementation.

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
5. Write `scratch/sprint-coordination/file_ownership.yaml` when persistence is useful.
6. Write interface contracts for shared boundaries.
7. Write one playbook per workstream with:
   - mission
   - owned files
   - acceptance criteria
   - verification commands
   - coordination notes
   - output contract: changed paths, tests run, risks
8. Optionally inject relevant `trw_recall` findings when the harness supports it.

## Output

Report generated artifact paths and any unresolved ownership risks.
