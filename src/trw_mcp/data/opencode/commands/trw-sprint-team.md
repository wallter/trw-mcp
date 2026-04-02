---
name: trw-sprint-team
description: Plan multi-track implementation work for a sprint
---

Run a lightweight sprint-planning workflow for OpenCode.

Workflow:
1. Read the sprint document or active PRD list.
2. Identify tracks, dependencies, and likely file ownership boundaries.
3. Produce a concise implementation plan with sequencing and validation steps.
4. If explicit delegation support is unavailable, keep the plan in the main thread instead of trying to spawn background teammates.

Fallbacks:
- If there is no sprint doc, ask for the target PRDs or create a short plan from the provided scope.
- If multi-agent execution is unsupported, provide a manual track plan rather than failing.

Constraints:
- Do not assume Agent Teams, background tasks, or teammate panels exist.
- Make unsupported behavior explicit instead of implying it will happen automatically.
