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

## Recording progress is not planning admission

During already authorized work, record observations, failed or missing proof,
evidence pointers and the next already-reviewed task in the existing Execution
plan. Do not invoke readiness, request another planning review or require full
PRD validation solely to preserve these observations. This does not apply to
initial planning or substantive plan changes, and never expands write authority.
Explicit project, legacy or experiment restrictions still govern their targets;
a historical one-transition receipt is not upgraded by this default.

Compare cumulative changes with the reviewed substantive baseline, not just the
last diff. Preserve intent, requirements, owners, interfaces, strategy and proof
standards. Renew independent review before advancing changed scope or acceptance,
changed owners/interfaces/strategy/proof, or new facts invalidating a material
assumption. Recording a blocker is allowed; advancing past it is not. When the
boundary is unclear, preserve observations in the existing authorized handoff
without editing disputed scope, then resolve the uncertainty.

Use the byte-preserving update procedure below for an authorized section edit;
check current bytes and competing authority, preserve other sections and failed
proof, and retain the resulting digest. A changed whole-document hash does not
extend an old approval or acceptance manifest to the new bytes. Status is an
observation, not acceptance: independent evidence assessment and actual delivery
gates remain required. No new receipt, score, public tool or recurring ceremony.

The readiness and admission sections below govern initial planning, substantive
changes and explicitly restricted legacy/experiment routes, not this ordinary
progress-recording path.

## Readiness gate

Resolve the PRD from the supplied ID/path and project configuration. Call full
`trw_prd_validate(prd_path)`. Legacy mode continues only with
`validation_partial: false`, `valid: true`, and risk-scaled
`quality_tier: approved`; `total_score` is diagnostic. Embedded mode instead
requires full valid/nonpartial validation and either retained invocation-creation
provenance for initial drafting or the independent scoped admission evidence below; document score/tier is diagnostic, not an approved-tier prerequisite.
Otherwise return actual result fields and blockers to `trw-prd-ready`.

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

For initial planning, create the smallest cohesive tasks per requirement that can be owned and
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

## Embedded plan (resolved readiness mode)

Use the resolved selected mode from `trw-prd-ready`: new feature descriptions
default to embedded; existing inputs without an explicit option retain their
existing artifact authority and legacy route. Explicit project/operator separate-artifact
requirements take precedence. Do not infer mode or authoring permission from a
missing flag, missing plan, draft status, or the generated path alone. The compatibility
`--embedded-plan` option remains supported by the readiness owner. This is a skill instruction,
not a new MCP argument, schema, automatic executor, or demonstrated efficacy claim.
Legacy gates remain unchanged. Embedded admission replaces only the document-tier
prerequisite; independent substantive review remains mandatory.

### Initial draft — invocation-created embedded input only
With this invocation's successful creation result bound to the exact output path and
original authoring scope, draft the plan in the SAME unreviewed PRD after groom's
substantive requirements assessment. No prior planning review is required on this branch.
Do not infer authority from draft status, existing ID/path, missing plan or failed creation.
Reuse the checklist/decomposition below; deficient requirements return to groom within
original scope, while new user-scope uncertainty stops. Full whole-artifact validation
must pass before ready requests ONE independent requirements+plan review per candidate.
For this successfully created input, up to two NEEDS WORK repair cycles are permitted
within the original user scope, directed by the independent reviewer's findings.
Creation provenance is retained; every revision invalidates prior review and requires
full validation plus fresh author-independent review of exact revised bytes.
After READY this branch is read-only: no planner rewrite, approval insertion or status edit.
New scope, BLOCK, missing evidence, review/tool failure or exhausted cycles stops.
No code execution permission is added by initial drafting or repair.

### Existing input / scoped planning updates

These admission rules apply to planning mutations and explicitly restricted
legacy/experiment updates, not ordinary progress recording defined above.
Before planning mutation on other embedded inputs, require an author-independent READY
receipt. Existing inputs do not acquire the new-creation repair allowance; returning to
groom then requires separately authorized repair scope. Preserve existing V1/reconciliation
contracts; no retrospective upgrade. The receipt records exact PRD path/version,
input SHA256, source root, validation receipt; requirement IDs, accepted intent, allowed paths/actions and excluded actions;
exact digests of load-bearing linked intent, policy/experiment recipe and skills;
author-independent reviewer identity/context, inspected trace provenance and no blockers.
The requirements, slice or implementation author cannot self-review under another label.
Policy approval alone is not admission; arbitrary trace tokens are not inspected provenance.

The receipt authorizes either:
- **V1 (restricted route):** one named planning/artifact-mutation transition. Require
  same-input/link-digest READY; subsequent slices/resumes need renewed review
  bound to current bytes. Missing explicit reconciliation permission retains V1;
  an existing receipt cannot be upgraded retrospectively.
- **Reconciliation-only scope:** the initial independent review explicitly permits
  subsequent evidence/status reconciliation. It binds exact starting PRD bytes,
  frozen bytes outside Execution plan, existing tasks/owners/interfaces, allowed
  actions/exclusions, proof standards and the exact dependency digests above.
  Reuse this substantive review only for the bounded updates below; do not rerun
  substantive admission merely because a permitted reconciliation changed bytes.

For every scoped update, inspect current PRD and supporting receipts and require full
valid/nonpartial validation. Check exact reviewed dependency digests; changed
links stop reuse, not an actor judgment that a change is harmless. Newly linked
observations are evidence, not new authority. In V1 also check the exact input
digest. In reconciliation mode compare frozen outside-section bytes and cumulative
section changes against the reviewed baseline, not just the last diff; unexplained
edits or competing authority stop reuse.

Reconciliation may record actual observations, evidence pointers and qualified
states of existing tasks, or explain the next already-reviewed task. It may not
add tasks, change owners/interfaces, reinterpret acceptance or proof standards,
authorize a new command, revise implementation strategy or grant privileges.
Compression preserves material decisions, failed/missing proof and exclusions.
Renew substantive review for changed intent/acceptance/non-goals, scope/ownership,
interfaces, proof interpretation, reviewed dependencies, ambiguous authority or new
critical facts contradicting a material assumption or revealing a safety/correctness
blocker. Recording a newly found blocker is allowed; advancing past it is not.
Uncertainty stops reuse: report the concern without mutating ambiguous scope.

Preserve pre/post bytes. Admission is planning/artifact scope, not production implementation,
automatic execution, new privileges, lifecycle promotion or delivery. Report
`experimental slice admitted` separately from actual document tier/status; never
relabel either as approved. This is instruction-level inspectable provenance,
not authenticated review identity, semantic-diff enforcement or concurrency-proof
writes. Implementation permissions, security and build/delivery gates are unchanged.

### Byte-preserving update procedure

Before editing, inspect the PRD and configured separate-plan location. If a
separate plan already exists, STOP and report the competing authority paths;
do not overwrite, delete, or silently migrate it. Duplicate embedded sections,
conflicting task ownership, or an ambiguous authority also block the update.
Preserve accepted requirements, acceptance criteria, PRD ID/version, and all
content outside the one named `## Execution plan` section. Match that heading
as a whole line outside fenced code; its boundary is the next level-two heading
or end of document. Append it only if absent: write one complete section directly after the same-byte
prewrite check, preserving all prior bytes. Do not create a placeholder or call the
replacement-only helper for initial append. For an existing section, update in place;
never append a second plan. When installed, use
`trw_mcp.state.prd_sections.update_execution_plan` for that replacement with the complete
section and `expected_sha256` of starting bytes; retain pre/post evidence and output digest.
The helper does not perform admission, semantic review, competing-plan checks or full validation.
Its sibling `.lock` and temporary-file writes must fit the authorized fixture
scope. Do not invoke it under a target-only write restriction without resolving
that scope. If unavailable, disclose the manual fallback and verify preservation.
Re-read before writing: immediately compare current bytes
with this editor's starting snapshot; mismatch stops the write.
Cooperative locking/atomic replacement are not atomic CAS against uncooperative editors.
If planning reveals a needed requirement change, return to grooming and
review under the applicable branch authority rather than changing accepted intent here.

Initial planning uses the compact shape below. Reconciliation preserves the
reviewed decomposition, ownership, verification and risks; it does not regenerate
tasks or reset existing statuses:

```markdown
## Execution plan
### Decision context
- User outcome, non-goals, accepted PRD version, readiness/review evidence
- Repository, verified seams, Pre-Implementation Checklist: complete
- Memory: query and IDs (or unavailable reason); applied/rejected/stale/unavailable
- For each relevant memory: disposition, current evidence, decision affected
### Requirement-linked work
| Task / requirement | Outcome rationale | Owned paths/symbols | Consumer/interface | Proof command and expected evidence | Dependencies / owner | Status / evidence |
|---|---|---|---|---|---|---|
### Integration and risks
- Sequential order or safe waves; shared contracts and integration owner
- Security/privacy/performance and migration/rollback concerns as applicable
### Handoff
- Last verified result and evidence path; unresolved risks and next outcome-linked task
- Drift decision and rationale; validation/review remaining
```

Use a bounded, task-specific `trw_recall` before selecting the next work slice,
including when resuming an existing PRD. Reuse a still-relevant recorded query
and inspected evidence instead of recalling mechanically. Memory is evidence,
never authority: inspect current sources before applying it; mark stale or
rejected advice with the reason, and disclose unavailable retrieval without
pretending an empty result proves there is no relevant memory. Do not copy
sensitive memory content into the PRD. Follow existing tool failure policy;
missing critical evidence blocks readiness, not every unavailable memory query.

A drift check is triggered by a work-to-outcome mismatch: the proposed next
slice cannot explain how it advances an accepted requirement and user outcome,
or repeatedly expands a local edge case without resolving an outcome blocker.
Pause that slice, preserve the unresolved risk, and select a requirement-linked
slice or seek an explicit scope decision. Do not trigger by elapsed turns,
periodic reminders, number of tools called, or passing test count.

Task statuses here are authored progress notes, not RequirementRegistry scheduling
or AcceptanceManifest acceptance. Edits change whole-PRD digests; a loadable old
manifest is evidence for its recorded source, not current approval.

New tasks start planned; distinguish in-progress, blocked, and verified, with
actual evidence for verified work. Independent evidence assessment is required
before promoting consequential work to verified; a passing command alone does not
certify the requirement. Section existence is not verification.
Commands are proposed proof, not permission to run them automatically. This
planning phase does not execute implementation or bypass validation/review.
Re-run full PRD validation on the whole artifact after drafting or a scoped planning update.
Ordinary progress recording follows its separate boundary above. Initial drafts
may be repaired within original creation scope or its bounded finding-directed repair cycles. For existing scoped updates,
invalid/partial output stops further reuse until corrected under separately reviewed scope, never a silent
second write. Do not invent validator support for this section.
In this mode no extra sprint or separate execution-plan artifact is required;
optional test skeletons below remain caller-requested, not an extra gate.

## Plan contract (legacy default)

When the resolved mode is separate, write `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md` (or the
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

In embedded mode report `{prd_path}#execution-plan` instead of a separate plan
path, and report the selected route and substantive admission. Otherwise report the
execution-plan path. In both modes report the PRD path, task/dependency count, parallelism
assumptions, ownership conflicts, verification commands, generated optional
artifacts, and blockers. Do not claim the plan is executable when paths,
interfaces, or proof commands remain fabricated or UNKNOWN.
