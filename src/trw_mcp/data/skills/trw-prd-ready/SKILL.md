---
name: trw-prd-ready
description: >
  Prepare new requirements and execution plan together before review; preserve existing PRD routes.
  Accepts a feature description ("Add rate limiting") or a PRD ID (PRD-CORE-020).
  Use: /trw-prd-ready "Add rate limiting to the API" or /trw-prd-ready PRD-CORE-020
user-invocable: true
argument-hint: "[feature description or PRD-ID]"
---

# PRD Ready — Full Lifecycle Skill

Use when: turning a feature description or existing PRD into a groomed, reviewed, execution-ready PRD.

This explicitly invoked requirements workflow prepares new requirements and planning together before review. Keep small work small: ordinary tasks do not require a PRD or sprint.

## Progress-only requests

An observation, failed-proof record or next-task handoff during already authorized
work is not a readiness request. Use exec-plan's "Recording progress is not planning
admission" boundary without running CREATE/GROOM/REVIEW again. Preserve explicit
legacy/experiment restrictions; substantive changes still use the readiness route.
Never promote observations to accepted requirements or invent execution authority.

## Input Detection

Recognize and remove the exact standalone `--embedded-plan` option before
classifying `$ARGUMENTS`; preserve the remaining PRD/path/feature text. Require
nonempty remaining input; classify the original remaining input once. This is
skill routing, not a new MCP parameter. Never pass the option to `trw_prd_create`
or `trw_prd_validate`, nor add a selected-mode parameter to their schemas.

- **PRD ID**: the entire remaining argument identifies one PRD ID
  (`PRD-[A-Z]+-\d+`, whole-input match), not an ID mentioned in prose.
- **File path**: the entire remaining argument is an explicit document path;
  use that file directly, not any path mentioned in a feature request.
- **Feature description**: a request describing new work, including references
  to existing IDs or paths; create a new PRD after the duplicate check.

Mentioning an ID or path inside a feature description does not select existing input.
For example, `Add validation to scripts/check_exec_plan_paths.py` and
`Add export support compatible with PRD-CORE-020` are feature descriptions,
not document selections. If an explicitly selected existing path is missing, stop and report it;
do not infer new-creation authority or silently switch routes.

New feature descriptions default to embedded mode: requirements and plan in one
artifact before review. `--embedded-plan` remains a compatibility option for
explicit selection, including existing-input scoped admission.
Existing PRD ID/path input without the option preserves its existing
artifact authority and legacy readiness behavior; a missing plan or draft status does not authorize migration.
Do not reclassify a newly created path as existing input after creation.
Explicit project/operator requirements for separate artifacts take precedence:
report that governing exception and select the separate route rather than silently
converting it. Conflicting instructions or competing artifact authority stop for resolution.
Resolve and retain the selected mode here, then forward it internally with the
original input classification and successful creation provenance when available.
Example: `/trw-prd-ready "Add rate limiting"` defaults to embedded;
`/trw-prd-ready PRD-CORE-020` preserves existing-input routing.

## Pipeline Phases

Diagram shows legacy routing for existing inputs or governing separate-artifact requirements.
Default new embedded invocation: CREATE → GROOM requirements →
DRAFT plan → full artifact validation → ONE combined review per candidate → read-only READY handoff. Existing-input
embedded admission retains the explicit scoped branches below.

```
 ┌───────────┐     ┌─────────┐     ┌─────────┐     ┌────────┐     ┌───────────┐
 │ PREFLIGHT │ ──▶ │ CREATE  │ ──▶ │  GROOM  │ ──▶ │ REVIEW │ ──▶ │ EXEC PLAN │
 │ (if new)  │     │ (if new)│     │(APPROVED)│    │(READY) │     │ (output)  │
 └───────────┘     └─────────┘     └────┬────┘     └───┬────┘     └───────────┘
                                        │              │
                                        │   NEEDS WORK │
                                        ◀──────────────┘
                                        (max 2 refinement loops)
```

Each phase has clear entry/exit criteria. The skill automatically skips phases that are already satisfied.

---

### Phase 0: PREFLIGHT (conditional)

**Entry**: `$ARGUMENTS` is a feature description (not an existing PRD ID or file path) AND the request is vague, high-impact, cross-cutting, missing success criteria, or likely to affect multiple modules.
**Skip if**: `$ARGUMENTS` is an existing PRD ID/file path, or the feature is small and sufficiently specified.

1. Before framing questions or assumptions, reuse relevant prior evidence already in context; otherwise make a task-specific `trw_recall` query from the feature description. Treat retrieved claims as evidence, not authority: check them against current docs/code/related PRDs. Carry material sources, caveats and applied/rejected/stale/unavailable disposition into the existing decision tree, not a new artifact. Answer obvious questions from that evidence.
2. Ask unresolved questions **one at a time**. Each question must include:
   - why the answer matters,
   - the recommended/default answer,
   - the consequence of choosing differently.
3. Cover, at minimum:
   - affected modules, interfaces, seams, data contracts, or workflows,
   - user-visible behavior and success metric,
   - the vertical tracer-bullet path that proves the behavior end-to-end,
   - deep-module opportunity (where complexity should be hidden behind a smaller stable interface),
   - explicit non-goals, rollout expectations, and test strategy.
4. Summarize the visible decision tree before creation:
   - resolved decisions,
   - evidence-backed assumptions,
   - open questions that should appear in the PRD.

If the user is unavailable and evidence is strong enough, proceed with explicit assumptions. Do not hide uncertainty; low-confidence assumptions belong in Open Questions.

**Exit**: Decision tree is ready to feed into PRD creation, or preflight is explicitly skipped with rationale.

---

### Phase 1: CREATE (conditional)

**Entry**: `$ARGUMENTS` is a feature description (not a PRD ID or file path).
**Skip if**: `$ARGUMENTS` is an existing PRD ID or file path.

1. Reuse the inspected preflight evidence. If preflight was skipped and relevant prior evidence is not already available, call `trw_recall` with feature keywords before creation. Retrieve again only for a new evidence gap or stale result, not merely because the phase changed. Preserve material sources and caveats in the PRD; do not copy raw memory wholesale.
2. Read `INDEX.md` in the PRD parent directory (read `prds_relative_path` from `.trw/config.yaml`, default: `docs/requirements-aare-f/prds`) to verify no duplicate PRD exists. If a likely duplicate exists, STOP creation, report the matching PRD(s), and ask whether to reuse/groom the existing PRD instead of silently spawning a new one.
3. Call `trw_prd_create(input_text="$ARGUMENTS")` to generate an AARE-F skeleton. If Phase 0 ran, include the decision tree and assumptions in the input text or immediately patch the generated PRD so they are visible.
4. Read the generated PRD file to confirm creation.
5. Default category is CORE. Use FIX for bugs, INFRA for infrastructure, QUAL for quality.

**Exit**: PRD file exists with a valid PRD ID. Report:
> "Created {PRD-ID} — skeleton tier. Proceeding to groom..."

**Capture**: Set `$PRD_ID` and `$PRD_PATH` for subsequent phases.
For embedded creation, retain the successful creation result and its exact output
path as this invocation's initial-authoring provenance. Never infer permission
from draft status, a missing plan, an existing ID/path, or failed/uncertain creation.
This permits initial requirements through groom and an embedded plan through exec-plan
in the same artifact, not code execution. Creation provenance is retained, not consumed as a one-use editing permission.
Only this successfully created new input permits up to two NEEDS WORK repair cycles,
directed by the independent reviewer's findings within the original user scope.
A failed or uncertain creation, existing input, or missing plan grants no such authority.


---

### Phase 2: GROOM

**Legacy skip if:** full validation returns `validation_partial: false`, `valid: true`,
`quality_tier: approved`; `total_score` is diagnostic.

Only for legacy or authorized embedded authoring, invoke the packaged internal `trw-prd-groom` contract (inline if unavailable).
Forward creation provenance/repair scope; legacy uses approved target; embedded creation with successful Phase 1
provenance uses initial-authoring target. The owner alone handles research
and substantive assessment; never substitute a local loop.

**Embedded route:** otherwise call full `trw_prd_validate(prd_path)`
read-only; invalid/partial existing input stops without mutation. All paths require valid/nonpartial output.
Tier remains diagnostic. For invocation-created embedded input, invoke exec-plan's
initial-draft branch NOW with the resolved selected mode and creation provenance, then validate the whole requirements+plan artifact before Phase 3.
Existing-input authority remains scoped, not inferred from draft status.

**Legacy route:** require approved; supply the reviewer's specific findings as refinement context
on loop-back. Stop on owner blockers/exhaustion. Existing-input embedded repair still requires its separately authorized scope.

---

### Phase 3: REVIEW

**Entry:** full valid/nonpartial output; legacy additionally approved.
For newly created input only: Every revision invalidates prior review reuse; require full validation plus fresh author-independent review of exact revised bytes.
Existing-input reconciliation retains its scoped reuse and renewal rules below.

**Legacy review:** Invoke the packaged internal `trw-prd-review` contract;
inline fallback discloses same-context review.

**Embedded review routing:** do not invoke the legacy document-READY reviewer.
New invocation: ONE combined review boundary per candidate of the whole requirements+plan artifact,
including intent coverage, tasks, ownership, interfaces and proof, not score.
Bind review to exact current PRD path/SHA256, full-validation receipt and
load-bearing intent/dependency digests.
Existing input: use exec-plan's selected admission/renewal checks;
only explicit reconciliation-only scope permits reuse without fresh substantive review.
V1 receipts stay one-transition; never upgrade retrospectively.

Full validation means the existing PRD checks over the combined artifact bytes,
not an automated plan-coverage guarantee. Independent review must assess
plan presence, uniqueness, task/requirement coverage and proof. Do not add a
second parser/validator or infer acceptance from a passing structural check.

**Embedded review:** when required, use an author-independent helper/human;
no inline author self-review fallback. For newly created input, NEEDS WORK returns to
the relevant groom/plan owner for a finding-directed repair within the two-cycle limit;
then run full validation and a fresh independent review. No lifetime single-review limit
may prevent these permitted corrections. Existing-input NEEDS WORK retains its scoped stop.
New scope or competing ownership stops; BLOCK, review/tool failure, missing evidence, or exhausted cycles stops
without inventing authority or a passing verdict;
never reinterpret a legacy NEEDS WORK as slice approval. Policy approval alone is not admission.

**Legacy routing:**
| Verdict | Action |
|---|---|
| **READY** | Proceed to Phase 4. |
| **NEEDS WORK** | If < 2 refinements done, return to Phase 2; otherwise STOP. |
| **BLOCK** | STOP immediately. |

---

### Phase 4: EXEC PLAN / HANDOFF

**Entry:** independent review verdict is READY under selected admission
(including its permitted reuse checks).

For invocation-created embedded work, this phase is read-only handoff: no postreview
planner rewrite, approval insertion or status edit. Recheck reviewed bytes and dependency
digests; STOP if changed. Return the reviewed artifact unchanged.
Existing-input embedded and legacy routes invoke the
packaged internal `trw-exec-plan` contract (inline if unavailable); forward the resolved selected mode explicitly.
The owner handles preservation and scoped mutation; no retrospective authority upgrade.

Legacy output: configured separate plan. Embedded output: `{prd_path}#execution-plan`;
no extra sprint or separate plan is required. Report actual tier/status and substantive
admission separately. Security, delivery and existing-input legacy gates are unchanged.

Existing user implementation authorization plus readiness suffices to begin the reviewed
work; do not add a redundant implementation ceremony. Planning-only requests never imply
execution. Stop on fabricated/UNKNOWN paths, ownership conflicts or missing proof.
Optional test skeletons remain caller-requested. Return actual validation/review results.

---

## Final Report

After all phases complete, output a consolidated summary. For resolved embedded mode,
replace the separate Execution Plan artifact below with `{prd_path}#execution-plan`
and report the selected route and its authority. The next step is the next requirement-linked task in
that section; no sprint artifact or sprint command is required in embedded mode.
For new embedded work report CREATE → requirements+plan drafting → whole-artifact
validation → one combined review per candidate (report repair cycles) → read-only READY handoff. Show actual tier and substantive admission
separately; do not label the PRD production-ready. Existing-input reports retain their
actual scoped route, not this new-invocation provenance.
Report actual gate results and unresolved evidence, not efficacy claims:

```
## PRD Ready: {PRD-ID}

**Pipeline**: {CREATE →} GROOM → REVIEW → EXEC PLAN  ✓

| Phase      | Result                          |
|------------|---------------------------------|
| Create     | {PRD-ID} created / skipped      |
| Groom      | Score: {score} ({iterations} iterations) |
| Review     | READY ({refinement_loops} refinement loops) |
| Exec Plan  | {n} FRs → {m} micro-tasks in {w} waves |

**Artifacts:**
- PRD: `{prd_path}`
- Execution Plan: `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`
- Test Skeletons: `{path}` (include only when created)

**Next step**: `/trw-sprint-team` to assign agents, or implement directly.
```

Record the outcome in the run artifact; reserve `trw_learn` for non-obvious reusable discoveries.

---

## Error Recovery

- **TRW MCP tool failure**: report the exact failure, reconnect through the client's supported MCP flow when available,
  and retry once per TRW policy. If it remains unavailable, stop at the current gate.
- **PRD create fails**: Check if PRD ID already exists. Report and suggest using the existing PRD ID.
- **Groom convergence before readiness**: Stop and report the result fields and missing context; never fall through to review.
- **Review returns BLOCK**: Stop and report blocking issues. These require human decisions.
- **Exec plan hits unverifiable files**: Flag in Known Risks section rather than fabricating.

<!-- compliance: implementation-readiness, control points, testability, migration, score-gaming -->
