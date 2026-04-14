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
2. Call `trw_recall()` for related learnings.
3. If needed, call `trw_prd_create()`.
4. Validate the PRD with `trw_prd_validate()`.
5. If it is not ready, improve the PRD directly in the current thread using repository evidence.
6. Re-validate and stop either when the PRD is ready or the blocking questions are explicit.
7. Report the score, blockers, and next implementation step.

Constraints:
- Do not assume subagents are available.
- Do not fabricate file paths or requirements.
