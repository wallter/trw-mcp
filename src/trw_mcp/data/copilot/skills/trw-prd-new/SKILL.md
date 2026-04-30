---
name: trw-prd-new
description: >
  Create a new PRD from a feature description, then automatically run the full
  readiness pipeline (groom → review → execution plan) so the user gets a
  sprint-ready PRD with micro-tasks in one command.
  Use: /trw-prd-new "Add rate limiting to the API"
user-invocable: true
argument-hint: "[feature description]"
---

# PRD Creation + Full Pipeline

Use when: starting from a feature description and creating a fresh PRD plus readiness pipeline artifacts.

Create a new PRD from a feature description, then automatically run it through the full readiness pipeline (groom → review → execution plan).

## Phase 0: Drill / Ambiguity Preflight

Before generating a PRD, make ambiguity explicit instead of letting the PRD tool feel "magic".

Run this preflight when the feature is vague, high-impact, cross-cutting, missing success criteria, or likely to affect multiple modules. Keep it lightweight for small, obvious work.

1. **Answer from evidence first**: Search docs/code/related PRDs and answer any obvious questions yourself.
2. **Ask one question at a time** for unresolved decisions. Each question includes:
   - why the answer matters,
   - a recommended answer/default,
   - the tradeoff if the user chooses differently.
3. **Cover the minimum decision set**:
   - affected modules, interfaces, seams, data contracts, or workflows,
   - user-visible behavior and success metric,
   - the vertical tracer-bullet path that proves the feature end-to-end,
   - deep-module opportunity (what complexity should be hidden behind a smaller stable interface),
   - explicit non-goals and rollout/test expectations.
4. **Record the decision tree** in the PRD Background, Open Questions, or Notes section:
   - resolved decisions,
   - assumptions made because evidence was sufficient,
   - remaining open questions that block sprint readiness.

If the user is unavailable and the evidence is strong enough, proceed with explicit assumptions rather than stopping; mark low-confidence assumptions in Open Questions.

## Phase 1: Create

1. **Search context**: Call `trw_recall` with keywords from `$ARGUMENTS` to find related learnings and prior work.

2. **Check existing PRDs**: Read `INDEX.md` in the PRD parent directory (read `prds_relative_path` from `.trw/config.yaml`, default: `docs/requirements-aare-f/prds`) to verify no duplicate PRD exists for this feature. If a likely duplicate exists, STOP creation, report the matching PRD(s), and ask whether to reuse/groom the existing PRD instead of silently spawning a new one.

3. **Generate PRD**: Call `trw_prd_create(input_text="$ARGUMENTS")` to generate an AARE-F-compliant PRD skeleton. Include the Phase 0 decision tree or assumptions in the input text when preflight ran. The tool auto-increments the sequence number per category.

4. **Read generated PRD**: Read the generated PRD file to confirm it was created successfully.

5. **Validate**: Call `trw_prd_validate(prd_path)` to get the initial quality score.

6. **Report creation**: Output PRD ID, file path, quality score. Then:
   > "Created {PRD-ID}. Proceeding to groom → review → execution plan..."

- Default category is CORE. If the feature is clearly a bug fix, use FIX; if infrastructure, use INFRA; if quality, use QUAL.

## Phase 2: Continue with /trw-prd-ready pipeline

After creation, **automatically proceed** with the full pipeline by following the `/trw-prd-ready` skill workflow for the newly created PRD ID. Do NOT stop after creation — the whole point is that the user gets a sprint-ready PRD with an execution plan from a single command.

Pipeline phases (the `/trw-prd-ready` skill defines the full gates and constraints):
- **GROOM** — research and iteratively draft until quality score ≥ 0.85
- **REVIEW** — 5-dimension quality review yielding READY / NEEDS WORK / BLOCK verdict
- **Refinement loop** — if NEEDS WORK, re-groom with targeted findings (max 2 loops)
- **EXEC PLAN** — decompose FRs into micro-tasks with file paths, tests, and verification commands

## Also triggers when...

This skill (or `/trw-prd-ready`) should be suggested when a user:
- Asks for a new feature ("add X", "we need Y", "build Z")
- Describes a requirement without using skill invocations
- Says "create a PRD for..."
- Asks to "groom", "review", or "plan" a PRD (use `/trw-prd-ready {PRD-ID}` for existing PRDs)
