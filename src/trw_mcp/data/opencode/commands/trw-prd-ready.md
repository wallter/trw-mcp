---
name: trw-prd-ready
description: Take a PRD or feature idea to a reviewed execution plan
---

Run the complete `trw-prd-ready` skill contract, including review, bounded
refinement, and execution-plan artifact generation.

Workflow:
1. Detect whether the input is a PRD ID, file path, or feature description.
2. Call `trw_recall()` for related learnings.
3. If needed, call `trw_prd_create()`.
4. Run full `trw_prd_validate()` and read `validation_partial`, `valid`, `quality_tier`, and `total_score`.
5. Readiness requires a non-partial, valid, risk-scaled `approved` result; use `total_score` only for progress.
6. If the predicate fails, groom directly from repository evidence and revalidate. Stop on explicit blockers rather
   than falling through after convergence.
7. Perform the skill's read-only review pass and route `NEEDS WORK` findings
   through bounded refinement; stop on `BLOCK`.
8. On `READY`, execute the inline exec-plan contract with verified paths,
   ownership, dependencies, integration, exact proof commands, and applicable
   migration/rollback concerns.
9. Write/report `EXECUTION-PLAN-{PRD-ID}.md`, the review verdict, result fields,
   blockers, and the next implementation step.

Fallbacks:
- If research tooling is limited, rely on local repo evidence and document the missing context.
- If the PRD cannot reach readiness without new user input, stop and list the blocking questions.

Constraints:
- Do not assume helpers are available.
- Prefer direct tool orchestration over client-specific workflow shortcuts.
