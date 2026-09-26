<!-- Canonical human-reference source for the TRW tool lifecycle.
     Run scripts/sync-instruction-surfaces.py after edits; renderers load the
     bundled mirror and `trw-mcp instructions sync` propagates its hash-stamped gate
     section into supported client instruction files. -->

# TRW Tool Lifecycle

## Core Mandates

**MUST call `trw_session_start()` as your absolute first action.** It loads prior learnings, active run state, and the operational protocol; without it you start from zero.

## Mandatory Tool Lifecycle

| Tool | When | Requirement |
|------|------|-------------|
| `trw_session_start()` | **First Action** | **MANDATORY.** Loads prior learnings and active run state. |
| `trw_learn(summary, detail)` | On discoveries | **REQUIRED** for non-obvious technical insights or gotchas. |
| `trw_checkpoint(message)` | After milestones | **REQUIRED.** Saves resume point for context compaction. |
| `trw_deliver()` | Completed-work acceptance | **REQUIRED for delivery**, under the existing gate below; not required merely to stop. |

## Session boundaries

For material unfinished work, preserve progress, observed checks, residual risks and the next action in a checkpoint or durable native handoff with a next-read pointer. Stopping is not acceptance. If nothing material needs preservation, do not manufacture an artifact or learning. Already captured learnings remain persisted.

## Tool surface (PRD-CORE-218)

The tool surface is flat. Every session sees the kernel plus every capability pack whose config flag is on; there is no
per-task pack resolution and no phase-based hiding.

- **Kernel — always, every phase**: `trw_session_start`, `trw_init`, `trw_status`, `trw_recall`, `trw_learn`, `trw_checkpoint`, `trw_deliver`, `trw_build_check`, `trw_review`, `trw_prd_validate`, `trw_code`. The `run_maintenance` pack is also always on.
- **Flag-gated packs**: `trw_send`/`trw_inbox` need `comms_enabled` (default true); `trw_dispatch` needs `dispatch_tools_exposed` (default false, required in every mode including `tool_resolution_mode: all`); `trw_assess` needs `assess_enabled` (default false). Turn one on by setting the flag to true in `.trw/config.yaml`.
- `tool_resolution_mode: all` turns on the comms and assess packs too, never dispatch.

A call to an off tool returns `tool_not_in_surface` with an `enable_with` hint naming the flag to set. See the resolved
surface with `trw_status(detail="surface")` (or the CLI `trw-mcp profile explain [--json]`).

## Delegation

Delegate only for work that is genuinely independent and parallelizable — a wide multi-file investigation, or shards with disjoint file ownership. Keep routine self-checks in your own loop. Required independent review is separate from routine self-checks; preserve the framework's risk/tier-appropriate review and fallback rules. If one helper suffices, use one. When the harness cannot delegate, run the same shards sequentially — delegation is an optimization, and the invariant is focused context, explicit ownership, persisted findings, and final integration by the orchestrator.

## Deliver Gate (v26.2)

Do NOT call `trw_deliver` unless at least one of:
- (a) `trw_build_check` reported `tests_passed=true` and `static_checks_clean=true` (or omitted), with a non-zero `test_count` and a non-empty `scope`, **or**
- (b) `allow_unverified=true` and `unverified_reason` contains a valid, unexpired
  acceptable-failure record with `failed_command`, `residual_risk`, `owner`, and
  `expiry_iso`, **or**
- (c) an authorized operator/config override is recorded with technical rationale.

A review-verdict label or free-text reason alone is not an acceptable-failure record.
Under the default `deliver_gate_mode: block_coding` a missing build check blocks when the task type expects a build artifact (`coding`, `rca`, `eval`) OR when the session recorded modifications to at least `deliver_gate_unclassified_change_threshold` distinct files — so an unclassified or misclassified run that changed code still blocks. A run that modified nothing surfaces the missing-build warning as an advisory without requiring an exception record.
