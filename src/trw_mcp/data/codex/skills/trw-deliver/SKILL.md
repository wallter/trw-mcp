---
name: trw-deliver
description: "Persist the session's work with validation evidence, durable learnings, and client instruction sync. Use: /trw-deliver"
---

# TRW Deliver

Run the delivery ceremony before ending a work session.

## Steps

1. Inspect the current diff and active requirements.
2. Run the narrowest meaningful validation that proves the change, then broaden if risk warrants it.
3. Call `trw_build_check(...)` with the validation result.
4. Record any durable gotchas with `trw_learn(...)`.
5. If helpers or separate workstreams contributed, consolidate their findings and resolve duplicates before delivery.
6. Call `trw_deliver()`.
7. Report completed work, validation, residual risks, and committed/uncommitted paths.

## Guardrails

- Do not record routine status as a learning.
- Do not claim completion without validation evidence or an explicit limitation.
- Do not mix unrelated dirty files into the delivery commit.
