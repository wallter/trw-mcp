---
name: trw-framework-check
context: fork
agent: Explore
description: >
  Check TRW framework compliance. Reports ceremony adherence,
  phase gate status, and active run health. Use when unsure if
  framework obligations are being met.
  Use: /trw-framework-check
user-invocable: true
---

# Framework Compliance Check

Use when: checking current TRW ceremony obligations, gates, and run evidence.

Read-only check of current TRW obligations and evidence. Report observed state separately from missing/unknown evidence; do not mutate runs or invent health thresholds.

## Workflow

1. **Resolve live state**
   - Call `trw_status()` for run state. Resolve artifact paths only from an explicit caller path or this session's `trw_session_start().run.active_run`; status itself does not return a path. It may resolve its own session pin internally, but never inspect pin files or scan runs to reconstruct that path. When no verified path was returned, rely on status and mark path-bound checks `UNKNOWN`.
   - If no run is active, report which session-level obligations are observable and mark run-specific checks N/A.
   - Do not discover runs through guessed `{task_root}/*/runs/*` globs.

2. **Check tier/phase obligations**
   - Read the resolved ceremony profile/tier when available.
   - Compare completed evidence with only the phases required for that tier.
   - Treat `trw_status()` fields `build_gate_ready`, `review_gate_ready`, and `deliver_gate_summary` as primary gate evidence when present. Verify session start, applicable validation/build evidence, substantive review at STANDARD+, completion artifacts, and delivery state.
   - Use the resolved run's `meta/run.yaml`, `meta/review.yaml`, reports, and other artifacts only when their run/session binding is verified. The project-global `.trw/context/build-status.yaml` may support diagnostics but cannot prove run/session compliance. Missing or unbound evidence is `UNKNOWN`, not automatic pass or fail.

3. **Check framework deployment**
   - Compare the version/hash reported by `trw_status()` with the deployed compact framework under `.trw/frameworks/`.
   - In the framework repository, prefer the project-native runtime-integrity check when present; distinguish authoring-source parity from deployed-runtime integrity.
   - Report stale deployment explicitly rather than copying files in this read-only skill.

4. **Check learning availability**
   - Use focused `trw_recall` or a bounded wildcard sample to confirm retrieval works.
   - Use `/trw-memory-audit` for a full memory-health assessment. Do not classify health from a universal active-entry count or index timestamp.

5. **Check governing requirements when present**
   - Resolve `prds_relative_path` from config and inspect only PRDs in the active scope.
   - Validate lifecycle state against current phase and evidence. Age alone does not prove a PRD is stuck.

## Report

```markdown
## TRW Framework Compliance
- Project/run: <path or no active run>
- Tier/phase: <observed>
- Framework deployment: MATCH | STALE | UNKNOWN

| Obligation | PASS/WARN/BLOCK/N/A/UNKNOWN | Evidence |
|---|---|---|
| session start | | |
| required phases | | |
| validation/build | | |
| substantive review | | |
| completion/delivery | | |
| learning retrieval | | |
| scoped PRD lifecycle | | |

### Gaps and next actions
- <gap, authority, safest action>
```

A compliance report is diagnostic evidence, not permission to bypass a gate.
