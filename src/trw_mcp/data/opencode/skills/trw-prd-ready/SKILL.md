---
name: trw-prd-ready
description: >
  Create or refine a PRD until it is implementation-ready in an OpenCode-safe workflow.
  Use: /trw-prd-ready [feature description or PRD-ID]
user-invocable: true
argument-hint: "[feature description or PRD-ID]"
allowed-tools: Read, Grep, Glob, Edit, Write, Bash, mcp__trw__trw_recall, mcp__trw__trw_prd_create, mcp__trw__trw_prd_validate, mcp__trw__trw_learn
---

# OpenCode PRD Ready Skill

## Implementation-Readiness Guardrails

Treat **implementation-readiness** as the load-bearing signal, not a license to
chase a score.
Before advancing, confirm the PRD makes **control points**, **testability**,
proof-oriented tests / verification commands, **migration** / rollback
semantics, and completion evidence explicit.
Treat **score-gaming** or density-chasing as failure modes; add prose only when
it improves implementability, traceability, or proof quality.

1. Detect whether the input is a feature description, PRD ID, or file path.
2. Call `trw_recall()` for related learnings and likely duplicate PRDs. If a likely duplicate exists, stop creation and ask whether to reuse/groom it.
3. For ambiguous new feature descriptions, run a lightweight preflight before creation: answer from code/docs when possible, otherwise ask one question at a time with a recommended default and tradeoff. Capture affected modules/interfaces/seams, deep-module opportunity, vertical tracer-bullet proof path, non-goals, and open assumptions.
4. If needed, call `trw_prd_create()` with the preflight decision tree included in the input.
5. Run full `trw_prd_validate()` and read `validation_partial`, `valid`, `quality_tier`, and `total_score`.
6. Readiness requires `validation_partial: false`, `valid: true`, and the risk-scaled `quality_tier: approved`. Use
   `total_score` only for progress/reporting; a partial result never passes.
7. If the predicate fails, improve the PRD directly from repository evidence, then revalidate. Stop when it passes or
   when blockers are explicit; never fall through on convergence.
8. Run a read-only review pass against category-specific expected sections,
   evidence, singular/testable requirements, traceability, interfaces, risks,
   and relevant NFRs. Use an independent reviewer when the client exposes one;
   otherwise label independence unavailable and keep review findings distinct
   from grooming. Verdict: `READY | NEEDS WORK | BLOCK`.
9. On `NEEDS WORK`, route specific findings through one bounded refinement and
   revalidate/review. On `BLOCK`, stop for missing evidence or operator input.
10. On `READY`, execute the `trw-exec-plan` contract inline: discover real
    paths/interfaces, decompose requirements into behavior-sized tasks, assign
    non-overlapping source/test ownership, map dependencies/integration, and
    name exact project-native verification plus migration/rollback concerns.
11. Write `docs/requirements-aare-f/exec-plans/EXECUTION-PLAN-{PRD-ID}.md`
    (or the configured sibling path) and report validation fields, review
    verdict, blockers, decision tree, plan path, and next implementation step.

Constraints:
- Do not assume subagents are available.
- Do not fabricate file paths or requirements.
- Do not claim a reviewed execution plan if either review or planning was skipped.
