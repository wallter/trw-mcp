---
name: trw-prd-ready
description: "Full PRD lifecycle in one command: create (or pick up existing) → groom → review → refine → execution plan. Accepts a feature description (\"Add rate limiting\") or a PRD ID (PRD-CORE-020). Use: /trw-prd-ready \"Add rate limiting to the API\" or /trw-prd-ready PRD-CORE-020\n"
---

> Codex-specific skill: this version is authored for Codex. Follow Codex-native skill and subagent flows, and ignore Claude-only references if any remain.

# PRD Ready — Full Lifecycle Skill

Take a requirement from idea to sprint-ready execution plan in a single invocation. This skill orchestrates the entire PRD pipeline so users never need to remember which steps come next.

## Implementation-Readiness Guardrails

Treat **implementation-readiness** as the load-bearing signal, not a license to
chase a score.
Before advancing, confirm the PRD makes **control points**, **testability**,
proof-oriented tests / verification commands, **migration** / rollback
semantics, and completion evidence explicit.
Treat **score-gaming** or density-chasing as failure modes; add prose only when
it improves implementability, traceability, or proof quality.

## Input Detection

Parse `$ARGUMENTS` to determine the entry point:

- **PRD ID** (matches `PRD-[A-Z]+-\d+`): Pick up an existing PRD wherever it is in the pipeline.
- **File path** (contains `/` or `.md`): Use the file directly.
- **Feature description** (anything else): Create a new PRD first.

## Pipeline Phases

```
 ┌─────────┐     ┌─────────┐     ┌────────┐     ┌───────────┐
 │ CREATE  │ ──▶ │  GROOM  │ ──▶ │ REVIEW │ ──▶ │ EXEC PLAN │
 │ (if new)│     │ (≥0.85) │     │(READY) │     │ (output)  │
 └─────────┘     └────┬────┘     └───┬────┘     └───────────┘
                      │              │
                      │   NEEDS WORK │
                      ◀──────────────┘
                      (max 2 refinement loops)
```

Each phase has clear entry/exit criteria. The skill automatically skips phases that are already satisfied.

---

### Phase 1: CREATE (conditional)

**Entry**: `$ARGUMENTS` is a feature description (not a PRD ID or file path).
**Skip if**: `$ARGUMENTS` is an existing PRD ID or file path.

1. Call `trw_recall` with keywords from the feature description to find related learnings and prior work.
2. Read `INDEX.md` in the PRD parent directory (read `prds_relative_path` from `.trw/config.yaml`, default: `docs/requirements-aare-f/prds`) to verify no duplicate PRD exists.
3. Call `trw_prd_create(input_text="$ARGUMENTS")` to generate an AARE-F skeleton.
4. Read the generated PRD file to confirm creation.
5. Default category is CORE. Use FIX for bugs, INFRA for infrastructure, QUAL for quality.

**Exit**: PRD file exists with a valid PRD ID. Report:
> "Created {PRD-ID} — skeleton tier. Proceeding to groom..."

**Capture**: Set `$PRD_ID` and `$PRD_PATH` for subsequent phases.

---

### Phase 2: GROOM

**Entry**: PRD file exists. May be skeleton, draft, or partially groomed.
**Skip if**: `trw_prd_validate` returns score >= 0.85.

Delegate to a **trw-prd-groomer** subagent (`subagent_type: "trw-prd-groomer"`) for focused grooming work:

1. Resolve PRD path (from Phase 1 output or `$ARGUMENTS`).
2. Read PRD and call `trw_prd_validate(prd_path)` for baseline score.
3. If score >= 0.85, skip to Phase 3.
4. Research phase:
   - Call `trw_recall` with keywords from the PRD Background section
   - Use Grep/Glob to find relevant codebase patterns, interfaces, and data structures
   - Read related PRDs from traceability section
5. Draft missing/weak sections following AARE-F 12-section guidance:
   - Problem Statement, Goals & Non-Goals, User Stories, Functional Requirements (EARS patterns + confidence scores), Non-Functional Requirements, Technical Approach, Test Strategy, Rollout Plan, Success Metrics, Dependencies & Risks, Open Questions, Traceability Matrix
6. Validation loop (max 3 iterations):
   a. Write updated PRD
   b. Call `trw_prd_validate(prd_path)`
   c. If >= 0.85, exit with success
   d. If < 5% improvement after iteration, exit (convergence)
   e. Parse failures and draft fixes

**Constraints:**
- NEVER fabricate requirements not grounded in Background or codebase evidence
- NEVER remove existing content — additive only
- ALWAYS use EARS patterns for functional requirements
- ALWAYS include confidence scores
- If 0.85 requires inventing ungrounded content, document gaps in Open Questions

**Exit**: Score >= 0.85 OR convergence reached. Report:
> "Groomed {PRD-ID} to {score}. Proceeding to review..."

**Gate failure**: If score < 0.70 after 3 iterations, STOP. Report what's blocking and suggest the user provide more context. Do NOT proceed to review.

---

### Phase 3: REVIEW

**Entry**: PRD score >= 0.85 from Phase 2.

Delegate to a **trw-requirement-reviewer** subagent (`subagent_type: "trw-requirement-reviewer"`) for independent review:

1. Perform 5-dimension quality assessment:
   - **Structure** — AARE-F section completeness and formatting
   - **Content Quality** — substantive depth vs. placeholder content
   - **Requirements Quality** — EARS compliance, confidence scores, testability
   - **Evidence & Confidence** — source citations, confidence calibration
   - **Traceability** — bidirectional links to code and tests
2. Return per-dimension scores (0-100%) and overall verdict: **READY** / **NEEDS WORK** / **BLOCK**

**Exit routing:**

| Verdict | Action |
|---------|--------|
| **READY** | Proceed to Phase 4 (Exec Plan) |
| **NEEDS WORK** | Increment refinement counter. If < 2 refinements done, return to Phase 2 (Groom) with the reviewer's specific findings as targeted guidance. If 2 refinements already done, STOP and report. |
| **BLOCK** | STOP immediately. Report blocking issues — these require user/stakeholder input. |

**On NEEDS WORK loop-back**: Pass the reviewer's findings to Phase 2 so the groomer targets specific weaknesses rather than re-running the full groom.

---

### Phase 4: EXEC PLAN

**Entry**: Review verdict is READY.

**Execution model**: Execute directly (no subagent delegation). The exec plan phase requires codebase exploration and file writing that benefit from the orchestrator's full context.

Generate the execution plan:

1. Call `trw_recall` with keywords from the PRD Problem Statement.
2. Use Grep/Glob to find existing code patterns mentioned in Technical Approach.
3. For each FR, decompose into micro-tasks:
   - **Affected files** — verified via Grep/Glob (never fabricated)
   - **Function-level changes** — specific functions to add/modify/wire
   - **Test cases** — `test_{fr_id}_{happy|edge|error}` with acceptance criterion docstrings
   - **Verification command** — exact pytest/bash command
   - **Dependencies** — which FRs/tasks must complete first
   - Target: each micro-task completable in <35 min (agent half-life)
4. Build task dependency graph (ASCII DAG).
5. Generate wave plan (parallelizable groups).
6. Create file ownership mapping.
7. Write execution plan to `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`.
8. Generate test skeletons to `docs/requirements-aare-f/test-skeletons/TEST-SKELETON-{PRD-ID}.py`.
9. Generate manifest to `docs/requirements-aare-f/test-skeletons/MANIFEST-{PRD-ID}.yaml`.

**Execution plan structure:**

```markdown
# EXECUTION PLAN: {PRD-ID}

## Metadata
- PRD: {PRD-ID} ({title})
- PRD Version: {version}
- PRD Validation Score: {score}
- Review Verdict: READY
- Generated: {ISO 8601}
- Agent half-life target: <35 min per wave

## 1. FR Decomposition
### FR01: {title}
**Micro-tasks:**
1. {description} — `{file_path}:{function_name}`

**Test cases:**
- `test_fr01_happy` — asserts {what}
- `test_fr01_edge` — asserts {what}

**Verification:** `pytest tests/test_{module}.py::test_fr01_happy -v`
**Dependencies:** None | FR02, FR03

## 2. Task Dependency Graph
## 3. Wave Plan
## 4. File Ownership Mapping
## 5. Verification Checklist
## 6. Known Risks
```

**Constraints:**
- NEVER fabricate file paths — verify with Grep/Glob
- ALWAYS include verification commands (not "verify manually")
- ALWAYS map dependencies
- If a FR exceeds 35 min, decompose further

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
- Test Skeletons: `docs/requirements-aare-f/test-skeletons/TEST-SKELETON-{PRD-ID}.py`

**Next step**: `/trw-sprint-team` to assign agents, or implement directly.
```

Call `trw_learn(summary="PRD ready pipeline: {PRD-ID} — {score}", tags=["prd-workflow"])` to record the outcome.

---

## Rationalization Watchlist

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "Skip the review, the groom score is high enough" | Review catches structural issues that validation scoring misses (ambiguity, untestable requirements) | Agents implement ambiguous requirements differently, causing rework |
| "The PRD is NEEDS WORK but close, just proceed to exec plan" | Execution plans built on weak PRDs have wrong file paths and missing dependencies | Agents waste time on incorrect decompositions |
| "I'll fabricate file paths to complete the exec plan faster" | Fabricated paths cause agents to create wrong files or search fruitlessly | Implementation variance and rework cycles |
| "One refinement loop is enough, the review found minor issues" | Minor review issues often mask structural gaps that surface during implementation | Better to fix now (minutes) than during implementation (hours) |

## Error Recovery

- **MCP tool failure** (`[Errno 2]`): The MCP server may have died. Report to user: "MCP server unavailable — run `/mcp` to reconnect, then retry `/trw-prd-ready`."
- **PRD create fails**: Check if PRD ID already exists. Report and suggest using the existing PRD ID.
- **Groom convergence at < 0.70**: Stop and report. The feature description likely needs more detail from the user.
- **Review returns BLOCK**: Stop and report blocking issues. These require human decisions.
- **Exec plan hits unverifiable files**: Flag in Known Risks section rather than fabricating.
