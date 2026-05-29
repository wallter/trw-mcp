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
5. Validate the PRD with `trw_prd_validate()`.
6. If it is not ready, improve the PRD directly in the current thread using repository evidence.
7. Re-validate and stop either when the PRD is ready or the blocking questions are explicit.
8. Report the score, blockers, decision tree, and next implementation step.

Constraints:
- Do not assume subagents are available.
- Do not fabricate file paths or requirements.
