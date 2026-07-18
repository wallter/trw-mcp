---
name: trw-prd-ready
description: >
  Full PRD lifecycle in one command: create (or pick up existing) → groom → review → refine → execution plan.
  Accepts a feature description ("Add rate limiting") or a PRD ID (PRD-CORE-020).
  Use: /trw-prd-ready "Add rate limiting to the API" or /trw-prd-ready PRD-CORE-020
---

> Codex adaptation: `AGENTS.md` is the primary instruction file. If a step mentions legacy Claude-specific workflow, follow the equivalent Codex skill/helper flow instead.

# PRD Ready — Full Lifecycle Skill

Use when: turning a feature description or existing PRD into a groomed, reviewed, execution-ready PRD.

Take a requirement from idea to sprint-ready execution plan in a single invocation. This skill orchestrates the entire PRD pipeline so users never need to remember which steps come next.

## Input Detection

Parse `$ARGUMENTS` to determine the entry point:

- **PRD ID** (matches `PRD-[A-Z]+-\d+`): Pick up an existing PRD wherever it is in the pipeline.
- **File path** (contains `/` or `.md`): Use the file directly.
- **Feature description** (anything else): Create a new PRD first.

## Pipeline Phases

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

1. Search docs/code/related PRDs first; answer obvious questions from evidence.
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

1. Call `trw_recall` with keywords from the feature description to find related learnings and prior work.
2. Read `INDEX.md` in the PRD parent directory (read `prds_relative_path` from `.trw/config.yaml`, default: `docs/requirements-aare-f/prds`) to verify no duplicate PRD exists. If a likely duplicate exists, STOP creation, report the matching PRD(s), and ask whether to reuse/groom the existing PRD instead of silently spawning a new one.
3. Call `trw_prd_create(input_text="$ARGUMENTS")` to generate an AARE-F skeleton. If Phase 0 ran, include the decision tree and assumptions in the input text or immediately patch the generated PRD so they are visible.
4. Read the generated PRD file to confirm creation.
5. Default category is CORE. Use FIX for bugs, INFRA for infrastructure, QUAL for quality.

**Exit**: PRD file exists with a valid PRD ID. Report:
> "Created {PRD-ID} — skeleton tier. Proceeding to groom..."

**Capture**: Set `$PRD_ID` and `$PRD_PATH` for subsequent phases.

---

### Phase 2: GROOM

**Entry**: PRD file exists. May be skeleton, draft, or partially groomed.
**Skip if**: full `trw_prd_validate` returns `validation_partial: false`, `valid: true`, and risk-scaled
`quality_tier: approved`. Use `total_score` (0-100) only for progress/reporting; never gate on deprecated
`completeness_score`.

Invoke the packaged internal `trw-prd-groom` contract with the PRD ID/path. If the current client cannot invoke
hidden skills but the contract is installed, execute it inline; do not substitute a weaker grooming loop. After it
returns, call full `trw_prd_validate(prd_path)` and proceed only when the readiness predicate above passes.
On a REVIEW loop-back, invoke it with the reviewer's specific findings as refinement context, not only the PRD path.

**Exit to review**: the readiness predicate passes. Report:
> "Groomed {PRD-ID} to {total_score} ({quality_tier}). Proceeding to review..."

**Gate failure**: If the predicate still fails after 3 iterations or convergence, stop, report the result fields and
blockers, and request missing context. Do not proceed to review.

---

### Phase 3: REVIEW

**Entry**: Phase 2 produced a full, valid, risk-scaled `approved` result.

Invoke the packaged internal `trw-prd-review` contract with the PRD ID/path and preserve its independent,
read-only assessment. If the current client cannot invoke hidden skills but the contract is installed, execute it inline
and disclose that same-context review could not provide independent-agent separation. Do not replace review with the
grooming validator or a weaker summary.

**Exit routing:**

| Verdict | Action |
|---------|--------|
| **READY** | Proceed to Phase 4 (Exec Plan) |
| **NEEDS WORK** | Increment refinement counter. If < 2 refinements done, return to Phase 2 (Groom) with the reviewer's specific findings as targeted guidance. If 2 refinements already done, STOP and report. |
| **BLOCK** | STOP immediately. Report blocking issues — these require user/stakeholder input. |

**On NEEDS WORK loop-back**: Pass the reviewer's findings to Phase 2 so the groomer targets specific weaknesses rather than re-running the full groom.

---

### Phase 4: EXEC PLAN

**Entry:** the independent review verdict is READY.

Invoke the packaged internal `trw-exec-plan` contract with the PRD ID/path. If
the current client cannot invoke hidden skills, execute that contract inline; do
not substitute a weaker summary. Require verified paths/interfaces, behavior-
sized tasks, source and test ownership, dependency/integration ordering, exact
project-native proof commands, and migration/rollback concerns where applicable.

The phase produces `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`
(or the configured sibling path). Optional test skeletons are created only when
the project convention and caller request make them useful; unconditional failing
or broadly skipped skeletons are not readiness evidence.

If the exec-plan contract reports fabricated/UNKNOWN critical paths, ownership
conflicts, or missing proof commands, stop and report the blockers rather than
claiming the PRD is execution-ready.

---

## Final Report

After all phases complete, output a consolidated summary:

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
