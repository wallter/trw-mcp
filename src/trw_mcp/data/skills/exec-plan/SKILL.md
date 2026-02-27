---
name: exec-plan
description: >
  Generate an execution plan from a groomed PRD. Decomposes FRs into micro-tasks
  with file paths, test names, verification commands, and dependency graphs.
  Bridges PRDs to concrete implementation steps.
  Use: /exec-plan PRD-CORE-020
user-invocable: true
argument-hint: "[PRD-ID or file path]"
allowed-tools: Read, Grep, Glob, Write, Bash, mcp__trw__trw_recall, mcp__trw__trw_prd_validate
---

# Execution Plan Generation Skill

Generate a structured execution plan that bridges a groomed PRD to concrete implementation micro-tasks. The execution plan decomposes each FR into actionable steps with file paths, test names, verification commands, and dependency graphs — so agents can execute without self-decomposing.

## Research Basis

- Agent half-life ~35 min (Ord 2025): micro-tasks must fit within reliability window
- Plan granularity mismatch (Sprint 34 lesson): file-level planning misses secondary read paths; function-level inventory required for cross-cutting changes
- Execution plans reduce self-decomposition variance by providing pre-computed task graphs

## Path Discovery

Read `prds_relative_path` from `.trw/config.yaml` (default: `docs/requirements-aare-f/prds`) to locate PRDs. Execution plans are stored in a sibling `exec-plans/` directory.

## Workflow

### Step 1: Resolve PRD

Check `$ARGUMENTS` for a PRD ID or file path:
- If a PRD ID (e.g., `PRD-CORE-020`), resolve to file path via `prds_relative_path`
- If a file path, use directly
- Read the full PRD file

### Step 2: Validate PRD Readiness

Call `trw_prd_validate(prd_path)` to check quality:
- If score < 0.85: abort with "PRD is not sprint-ready (score: {score}). Run /prd-groom first."
- If score >= 0.85: continue

### Step 3: Research Context

- Call `trw_recall` with keywords from the PRD Problem Statement
- Use Grep/Glob to find existing code patterns in files mentioned by the Technical Approach
- Read related PRDs from the traceability section

### Step 4: Decompose FRs into Micro-Tasks

For each FR in the PRD:
1. **Identify affected files** — source files to create/modify (from Technical Approach + Grep)
2. **List function-level changes** — specific functions to add, modify, or wire
3. **Define test cases** — test function names and what they assert
4. **Write verification command** — the exact pytest/bash command to verify this FR
5. **Map dependencies** — which other FRs or micro-tasks must complete first

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
1. {task description} — `{file_path}:{function_name}`
2. {task description} — `{file_path}:{function_name}`

**Test cases:**
- `test_{fr_id}_happy` — asserts {what}
- `test_{fr_id}_edge` — asserts {what}
- `test_{fr_id}_error` — asserts {what}

**Verification:**
```bash
.venv/bin/python -m pytest tests/test_{module}.py::test_{fr_id}_happy -v
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
| FR01 | src/module_a.py | tests/test_module_a.py | -- |
| FR02 | src/module_b.py | tests/test_module_b.py | -- |
| FR05 | src/module_a.py, src/module_b.py | tests/test_integration.py | module_a → module_b data flow |

## 5. Verification Checklist

| FR | Acceptance Criterion | Test Name | Verification Command | Expected Result |
|----|---------------------|-----------|---------------------|-----------------|
| FR01 | {criterion} | test_fr01_happy | pytest ... -v | PASSED |

## 6. Known Risks

| FR | Risk | Likelihood | Impact | Mitigation |
|----|------|-----------|--------|------------|
| FR03 | Depends on FR01 interface | Medium | High | Define interface contract before implementation |
````

### Step 9: Report

Output a summary:
- PRD ID and title
- FR count and micro-task count
- Wave count and estimated total time
- File ownership summary
- Execution plan file path

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The PRD is clear enough, I don't need to research the codebase" | Execution plans without codebase evidence have wrong file paths and missing dependencies | Agents waste time looking for files that don't exist or missing integration points |
| "Function-level decomposition is overkill" | File-level planning misses secondary read paths — Sprint 34's #1 lesson | Cross-cutting changes need function-level inventory or review discovers gaps |
| "I'll estimate the wave timing later" | Without timing estimates, waves exceed the agent half-life threshold | Agents degrade after ~35 min — oversized waves produce lower quality work |

## Constraints

- NEVER fabricate file paths — use Grep/Glob to verify files exist
- NEVER skip PRD validation — sub-0.85 PRDs produce unreliable execution plans
- ALWAYS include verification commands with each FR (not "verify manually")
- ALWAYS map dependencies — missing dependencies cause wave failures
- If a FR is too large for one micro-task (>35 min), decompose it further
