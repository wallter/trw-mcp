---
name: trw-exec-plan
description: >-
  Decompose a groomed PRD into an execution plan with micro-tasks, file paths, test names, verification commands, and dependency graphs. Invoke this skill during the internal phase of plan generation, which is automatically triggered by /trw-prd-ready and /trw-prd-new.
user-invocable: false
argument-hint: "[PRD-ID or file path]"
---

> Portable adapter note: use the active client instruction file and available MCP tools. If a step mentions a client-specific workflow, use the equivalent tool/manual flow for the current harness.
<!-- ultrathink -->

# Execution Plan Generation Skill

Generate a structured execution plan that bridges a groomed PRD to concrete implementation micro-tasks. The execution plan decomposes each FR into actionable steps with file paths, test names, verification commands, and dependency graphs — so agents can execute without self-decomposing.

## Research Basis

- Agent half-life ~35 min (Ord 2025): micro-tasks must fit within reliability window
- Plan granularity mismatch (Sprint 34 lesson): file-level planning misses secondary read paths; function-level inventory required for cross-cutting changes
- Execution plans reduce self-decomposition variance by providing pre-computed task graphs
- Vertical tracer bullets reduce integration risk: prefer end-to-end behavioral slices over broad horizontal layer churn
- Deep modules reduce cognitive load: plan around stable interfaces and information hiding, not just file edits

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate PRDs. Execution plans are stored in a sibling `exec-plans/` directory.


## Pre-Implementation Checklist (PRD-QUAL-056-FR03)

Before writing the execution plan, confirm the PRD is valid, source/test seams are identified, and the project-native verification command is known or explicitly uncertain. Record checklist completion in the plan metadata so downstream review can verify planning discipline.

## Workflow

### Step 1: Resolve PRD

Check `$ARGUMENTS` for a PRD ID or file path:
- If a PRD ID (e.g., `PRD-CORE-020`), resolve to file path via `prds_relative_path`
- If a file path, use directly
- Read the full PRD file

### Step 2: Validate PRD Readiness

Call `trw_prd_validate(prd_path)` to check quality using the `total_score` field (0-100 scale). Do NOT use `completeness_score` (a deprecated 0-1 float):
- If `total_score < 85` (below APPROVED tier): abort with "PRD is not sprint-ready (total_score: {total_score}). Run /trw-prd-ready {PRD-ID} first."
- If `total_score >= 85` (APPROVED tier): continue

### Step 3: Research Context

- Call `trw_recall` with keywords from the PRD Problem Statement
- Use Grep/Glob to find existing code patterns, interfaces, seams, data contracts, and test conventions in files mentioned by the Technical Approach
- Read related PRDs from the traceability section
- Infer the project's language, framework, file extensions, and test runner from config and existing examples; do not assume Python unless the PRD is Python-specific

### Step 4: Decompose FRs into Micro-Tasks

For each FR in the PRD:
1. **Identify affected files** — source files to create/modify (from Technical Approach + Grep)
2. **Identify affected interfaces/seams** — APIs, CLI commands, schemas, events, components, hooks, jobs, queues, or data contracts touched by the FR
3. **List symbol-level changes** — specific functions, classes, components, modules, commands, schemas, or adapters to add, modify, or wire
4. **Define test cases** — framework-appropriate test names and what they assert
5. **Write verification command** — the exact project-specific test/build command to verify this FR (e.g., `pytest`, `vitest`, `npm test`, `cargo test`, `go test`, `make ...`, or a bash script)
6. **Choose slice shape** — prefer a vertical tracer-bullet task that proves behavior end-to-end; horizontal/layer-only tasks require a rationale and an explicit integration follow-up
7. **Map dependencies** — which other FRs or micro-tasks must complete first

Target: each micro-task should be completable in <35 minutes (agent half-life threshold).

### Step 5: Build Task Dependency Graph

Create an ASCII DAG showing:
- Which tasks can run in parallel (no dependencies between them)
- Which tasks must run sequentially (dependency chain)
- Critical path identification

### Step 6: Generate Wave Plan

Group micro-tasks into waves:
- **Within a wave**: all tasks are independent (parallelizable)
- **Between waves**: sequential (wave N+1 depends on wave N)
- Target: each wave completes in <35 min equivalent work

### Step 7: File Ownership Mapping

Map each FR to:
- Source files (who writes them)
- Test files (who tests them)
- Integration points (who verifies the wiring)

### Step 8: Write Execution Plan

Write to `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`:

````markdown
# EXECUTION PLAN: {PRD-ID}

## Metadata
- PRD: {PRD-ID} ({title})
- PRD Version: {version from frontmatter}
- PRD Validation Score: {score}
- Generated: {ISO 8601 timestamp}
- Agent half-life target: <35 min per wave

## 1. FR Decomposition

### FR01: {FR title}

**Micro-tasks:**
1. {task description} — `{file_path}:{symbol_or_interface}` — slice: vertical tracer bullet | horizontal prerequisite (rationale: {why})
2. {task description} — `{file_path}:{symbol_or_interface}` — slice: vertical tracer bullet | horizontal prerequisite (rationale: {why})

**Test cases:**
- `test_{fr_id}_happy` — asserts {what}
- `test_{fr_id}_edge` — asserts {what}
- `test_{fr_id}_error` — asserts {what}

**Verification:**
```bash
# Use the project's actual test/build command.
{exact project-specific verification command}
```

**Dependencies:** None | FR02, FR03

---

### FR02: {FR title}
{same structure}

## 2. Task Dependency Graph

```
FR01 ──┐
       ├── FR03 ──┐
FR02 ──┘          ├── FR05 (integration)
       FR04 ──────┘
```

## 3. Wave Plan

| Wave | Tasks | Est. Time | Parallel? |
|------|-------|-----------|-----------|
| 1 | FR01, FR02, FR04 | 25 min | Yes (independent) |
| 2 | FR03 | 15 min | Blocked by Wave 1 |
| 3 | FR05 (integration) | 20 min | Blocked by Wave 2 |

## 4. File Ownership Mapping

| FR | Source Files | Test Files | Integration Points |
|----|-------------|------------|-------------------|
| FR01 | src/{domain}/{module}.{ext} | {test_path}.{test_ext} | -- |
| FR02 | packages/{pkg}/{component}.{ext} | {test_path}.{test_ext} | -- |
| FR05 | {module_a}, {module_b} | {integration_test_path} | module_a → module_b data flow |

## 5. Verification Checklist

| FR | Acceptance Criterion | Test Name | Verification Command | Expected Result |
|----|---------------------|-----------|---------------------|-----------------|
| FR01 | {criterion} | {framework-appropriate test name} | {exact command} | PASSED |

## 6. Known Risks

| FR | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| FR03 | Depends on FR01 interface | Medium | High | Define interface contract before implementation |
````

### Step 9: Generate Test Skeletons

Generate spec-first test stubs so tests exist BEFORE implementation. All tests SHOULD FAIL before code is written (TDD).

For each FR in the execution plan:

1. **Create test stubs** using the project's naming convention (for example `test_{fr_id}_{case_type}`, `it("FR01 ...")`, `#[test] fn fr01_...`, or table-driven `TestFR01...`)
   - `_happy` — primary acceptance criterion
   - `_edge` — boundary/edge cases from acceptance criteria
   - `_error` — error paths and negative cases

2. **Include docstring/comment/block comment** with the exact acceptance criterion text from the PRD

3. **Add a framework-appropriate failing placeholder assertion** (for example `assert False`, `expect(...).toBe(...)` with TODO, `panic!`, `t.Fatalf`, or a pending/skipped marker only if the framework treats pending as expected-fail)

4. **Parametrize** similar cases where multiple inputs share the same assertion pattern

Write test skeleton to `docs/requirements-aare-f/test-skeletons/TEST-SKELETON-{PRD-ID}.{ext}` where `{ext}` matches the project's test language (e.g., `.py`, `.ts`, `.rs`).

If the repo has no safe standard skeleton format for the target language/framework, write a framework-neutral `TEST-SKELETON-{PRD-ID}.md` containing the test names, acceptance criteria, fixtures/setup, and exact verification commands instead of fabricating executable code.

**Framework-neutral skeleton pattern:**

```text
TEST SUITE: {PRD-ID}: {PRD title}
SOURCE: generated from PRD acceptance criteria; executable tests should fail before implementation.

FR01: {FR title}
  CASE happy
    Given {precondition}
    When {action}
    Then {expected result}
    Placeholder: failing assertion/pending marker according to the project's test framework

  CASE edge
    Given {edge case}
    When {action}
    Then {expected boundary behavior}
    Placeholder: failing assertion/pending marker according to the project's test framework

  CASE error
    Given {error condition}
    When {action}
    Then {error response}
    Placeholder: failing assertion/pending marker according to the project's test framework

  CASE table_driven
    Inputs/expected outputs: {case list}
    Placeholder: framework-appropriate parametrized/table-driven assertion

FR02: {FR title}
  ...repeat for each FR...
```

Adapt the skeleton to the project's language and test framework (examples include pytest, Vitest/Jest, Rust test functions, Go table tests, Ruby specs, shell bats tests, or another project-native harness).

Write manifest to `docs/requirements-aare-f/test-skeletons/MANIFEST-{PRD-ID}.yaml`:

```yaml
prd_id: PRD-{CATEGORY}-{SEQ}
prd_title: "{title}"
generated: "{ISO 8601 timestamp}"
skeleton_file: "test-skeletons/TEST-SKELETON-{PRD-ID}.{ext}"
fr_coverage:
  - fr_id: FR01
    test_count: 3
    tests:
      - test_fr01_happy
      - test_fr01_edge
      - test_fr01_error
  - fr_id: FR02
    test_count: 3
    tests:
      - test_fr02_happy
      - test_fr02_edge
      - test_fr02_error
total_tests: 6
status: all_failing  # Expected — implementation has not started
```

### Step 10: Report

Output a summary:
- PRD ID and title
- FR count and micro-task count
- Wave count and estimated total time
- File ownership summary
- Execution plan file path
- Test skeleton file path (if generated)

Call `trw_learn(summary="Exec plan generated: {PRD-ID} — {n} FRs → {m} micro-tasks in {w} waves", tags=["prd-workflow", "exec-plan"])`

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The PRD is clear enough, I don't need to research the codebase" | Execution plans without codebase evidence have wrong file paths and missing dependencies | Agents waste time looking for files that don't exist or missing integration points |
| "Function-level decomposition is overkill" | File-level planning misses secondary read paths — Sprint 34's #1 lesson | Cross-cutting changes need function-level inventory or review discovers gaps |
| "I'll estimate the wave timing later" | Without timing estimates, waves exceed the agent half-life threshold | Agents degrade after ~35 min — oversized waves produce lower quality work |
| "I'll make all data/model/UI layers first and integrate later" | Horizontal slices defer the first proof of behavior | Integration bugs accumulate until the end and force rework |
| "The interface is obvious; I'll list files only" | File lists miss seams and contract ownership | Agents change internals without preserving the stable module boundary |

## Assertion Verification in Tasks (PRD-CORE-086)

When generating execution plan tasks for FRs that include assertions, add assertion verification steps:

- Include `Assert: grep_present "pattern" in "target"` lines in task verification commands
- These serve as machine-checkable acceptance criteria the implementer can run after making changes

## Constraints

- NEVER fabricate file paths — use Grep/Glob to verify files exist
- NEVER skip PRD validation — PRDs with `total_score < 85` (below APPROVED tier) produce unreliable execution plans
- ALWAYS include verification commands with each FR (not "verify manually")
- ALWAYS map dependencies — missing dependencies cause wave failures
- ALWAYS infer language/framework/test runner from the project rather than copying Python examples
- SHOULD prefer vertical tracer-bullet slices; horizontal prerequisite tasks need rationale and explicit integration proof
- SHOULD identify stable interfaces/seams so implementers know what must remain load-bearing
- If a FR is too large for one micro-task (>35 min), decompose it further

<!-- compliance: implementation-readiness, control points, testability, migration, score-gaming -->
