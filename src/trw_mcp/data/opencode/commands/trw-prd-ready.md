---
name: trw-prd-ready
description: Take a PRD or feature idea to a reviewed execution plan
---

Run the PRD readiness pipeline.

Workflow:
1. Detect whether the input is a PRD ID, file path, or feature description.
2. Call `trw_recall()` for related learnings.
3. If needed, call `trw_prd_create()`.
4. Validate the PRD with `trw_prd_validate()`.
5. If the PRD is weak, groom it directly in the current thread using repo evidence.
6. Re-validate until it is ready or the remaining blockers are explicit.
7. Summarize the result and the next implementation step.

Fallbacks:
- If research tooling is limited, rely on local repo evidence and document the missing context.
- If the PRD cannot reach readiness without new user input, stop and list the blocking questions.

Constraints:
- Do not assume subagents or Agent Teams are available.
- Prefer direct tool orchestration over client-specific workflow shortcuts.
