<!-- Canonical human-reference source for the TRW tool lifecycle.
     Renderer wire-up (auto-propagation into root CLAUDE.md’s trw:start/trw:end
     block via trw_instructions_sync) is deferred to PRD-QUAL-076; edits here
     do NOT yet auto-sync into rendered client surfaces. Mirror changes into
     trw-mcp/src/trw_mcp/state/claude_md/_static_sections.py until QUAL-076 lands. -->

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

## Delegation

Delegate to focused helpers when the harness supports it and file ownership is clear. When it does not, run the same shards sequentially. Delegation is an optimization — the invariant is focused context, explicit ownership, persisted findings, and final integration by the orchestrator.

## Deliver Gate (v26)

Do NOT call `trw_deliver` unless at least one of:
- (a) `trw_build_check` returned `build_check_result=pass`, **or**
- (b) a `review_verdict` carries an explicit `acceptable-failure` label, **or**
- (c) an explicit override justification is included in the deliver message.

For task types `coding`, `rca`, `eval` the gate blocks by default (`deliver_gate_mode: block_coding`). Docs, research, planning, and unknown types remain advisory.
