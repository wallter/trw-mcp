---
name: trw-sprint-team
description: "Plan exclusive workstream ownership and handoff contracts from a PRD, embedded execution plan, sprint or explicit scope. Planning only unless execution is separately authorized. Use: /trw-sprint-team [work-artifact-or-scope]"
---

# TRW Coordination

**Use when:** work needs explicit ownership and interface contracts before it can
be divided safely. A sprint document is optional; do not create one just to use
this workflow. One local pass is a valid outcome when splitting adds no value.

## Planning contract

First decide whether coordination is needed. If one local owner can perform the
work sequentially without an unresolved cross-owner interface, preserve the
existing tasks and acceptance methods and report that no workstream split is
needed. Do not invent coordinator/implementer/tester pseudo-teams or expand a
small request into an exhaustive plan. Produce only an explicitly requested
handoff and material unresolved decisions; stop before the decomposition below.

1. Read the selected governing work artifact and relevant source/test interfaces.
   For explicit scope without an artifact, use an appropriate existing issue/plan
   or the requested handoff location; do not manufacture a sprint or full PRD.
   Reuse its requirements, embedded tasks and acceptance methods; do not create
   a parallel requirements or status authority. Infer source/test ownership from
   repo-detected layout and project configuration, not language-specific templates.
2. Reuse relevant memory already inspected; query missing evidence with trw_recall
   only when it could change the division or handoff. Carry material caveats into
   the existing work artifact.
3. Record each workstream there: mission, requirement/task references, owner,
   exclusive source/config/docs and test paths, dependencies, interface contract,
   project-native verification commands and required return evidence. Every shared
   interface has one writer. Resolve overlapping source or test ownership before
   any writer starts; name exclusions where boundaries would otherwise be ambiguous.
4. Select only roles actually available in the active harness. A read-only reviewer
   cannot own production edits. If helpers are unavailable, keep the same bounded
   work local/sequential; do not assume background workers or nested delegation.
5. Return the workstream map, unresolved dependencies and next action. A plan,
   generated brief or completion marker is not acceptance evidence.

## Execution boundary

Default is planning only: do not launch helpers, edit production code, initialize
a formation or call delivery just because this skill was invoked. Preserve any
existing plan-only restriction, including the trw-team-playbook compatibility entry.
An explicit execution request may continue within its authorized scope; ask only
for missing authority, not approval already granted.

For authorized coordinated execution, use the existing formation manifest as
the live ownership authority. Inspect an existing formation before updating it;
do not create a second one. Otherwise use trw_init advanced formation/join_formation
through its current schema. Validate owned_paths/test_owned_paths before dispatch.
Generate an ownership brief with trw-mcp formation brief <member_id>, accompanied
by precise references to the workstream's mission, interface contract, acceptance
methods and verification commands in the governing artifact. The brief does not
contain those task-specific instructions by itself.

Do not require a separately maintained playbook per worker. Supply missing context
in the handoff; produce standalone copies only when requested or required by the
recipient, and identify their governing source. Retain any project-required
reporting protocol, but never manufacture a completion promise before verification.

The orchestrator integrates changed paths and returned evidence, resolves risks,
and applies the existing validation/review/delivery gates. Helper completion alone
does not satisfy them.
