---
name: trw-prd-new
description: >-
  Compatibility entry for creating a PRD through trw-prd-ready. Forwards the original
  feature input and selected mode to the single readiness workflow.
---

> Codex adaptation: `AGENTS.md` is the primary instruction file. If a step mentions legacy Claude-specific workflow, follow the equivalent Codex skill/subagent flow instead.

# PRD Creation — compatibility entry

Use when: starting from a feature description through the existing full readiness workflow.

Invoke the installed `trw-prd-ready` skill with the **original `$ARGUMENTS`**, including
any explicit `--embedded-plan` option. Keep feature text as feature text; do not
create a PRD first and substitute its ID. This preserves creation provenance and
lets one contract own the entire journey.

`trw-prd-ready` owns preflight, targeted recall, duplicate detection, category
selection, creation, grooming, independent review and execution planning. Follow
`/trw-prd-ready`'s risk-scaled readiness contract and selected mode; this alias
neither adds a gate nor changes the default plan location, lifecycle or permissions.
Do not call the creation tool here or repeat these phases locally.

If skill invocation is unavailable but the installed contract is readable, execute
that contract inline with the same original input. If the contract is unavailable,
report the missing dependency and stop; do not manufacture a partial substitute.
Do not stop merely because a skeleton was created: return the owning workflow's
actual outcome, including blockers and incomplete validation/review/planning.

For an existing PRD ID or path, the same owner resolves that input without forcing
creation of another PRD. Recommend `/trw-prd-ready` as the primary entry; retain
`/trw-prd-new` for compatibility rather than maintaining a second pipeline.
