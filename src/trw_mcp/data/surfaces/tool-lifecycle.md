<!-- Canonical human-reference source for the TRW tool lifecycle.
     Run scripts/sync-instruction-surfaces.py after edits; renderers load the
     bundled mirror and trw_instructions_sync propagates its hash-stamped gate
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
| `trw_deliver()` | **Last Action** | **MANDATORY.** Persists your discoveries for future agents. |

## Tool surface (PRD-CORE-218)

`tool_resolution_mode` (default `standard`) is the sole tool-exposure authority. Under `standard` each session exposes:

- **Kernel — always, 9 tools**: `trw_session_start`, `trw_status`, `trw_recall`, `trw_learn`, `trw_checkpoint`, `trw_deliver`, `trw_skill_discovery`, `trw_request_tool_access`, `trw_profile_explain`.
- **Task packs — selected by the active run's `task_type`**: `coding` → verification + code_navigation; `research` → code_navigation + memory_management; `docs` → requirements + verification; `eval` → verification; `rca` → code_navigation + verification; `planning` → requirements; `unknown` / no run → kernel only.
- **Always exposed regardless of task or mode**: the RIGID lifecycle gates `trw_session_start`, `trw_build_check`, `trw_deliver` plus bootstrap `trw_init`. The deliver-gate tools (`trw_build_check` and `trw_deliver`) are therefore always callable, and a bounded surface can never brick a session.

Tools outside the resolved surface are masked, not deregistered. A denial names the pack(s) that contain the tool and the remedy: call `trw_request_tool_access(tool_name=..., reason=...)` for a single-use grant, or set `tool_resolution_mode='all'` to expose the full registered surface (the operator escape).

## Delegation

Delegate to focused helpers when the harness supports it and file ownership is clear. When it does not, run the same shards sequentially. Delegation is an optimization — the invariant is focused context, explicit ownership, persisted findings, and final integration by the orchestrator.

## Deliver Gate (v26.1)

Do NOT call `trw_deliver` unless at least one of:
- (a) `trw_build_check` returned `build_check_result=pass`, **or**
- (b) `allow_unverified=true` and `unverified_reason` contains a valid, unexpired
  acceptable-failure record with `failed_command`, `residual_risk`, `owner`, and
  `expiry_iso`, **or**
- (c) an authorized operator/config override is recorded with technical rationale.

A review-verdict label or free-text reason alone is not an acceptable-failure record.
For task types `coding`, `rca`, `eval` the gate blocks by default (`deliver_gate_mode: block_coding`). Docs, research, planning, and unknown types remain advisory and surface the missing-build warning without requiring an exception record.
