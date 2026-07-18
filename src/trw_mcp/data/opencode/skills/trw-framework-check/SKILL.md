---
name: trw-framework-check
description: >
  Check TRW framework compliance and run health in an OpenCode-safe workflow.
  Use: /trw-framework-check
user-invocable: true
allowed-tools: Read, Glob, Grep, mcp__trw__trw_status, mcp__trw__trw_recall
---

# OpenCode Framework Check Skill

1. Call `trw_status()` to inspect the current run.
2. Prefer its `build_gate_ready`, `review_gate_ready`, and `deliver_gate_summary` fields. Without an explicit caller path or this session's session-start path, mark path-bound checks `UNKNOWN`; never inspect active-pin files or treat project-global `build-status.yaml` as run proof.
3. Call `trw_recall("*", max_results=25)` when broader learning context is needed.
4. Inspect framework and instruction files relevant to the current project.
5. Report ceremony status, run status, learning-layer health, and actionable next steps.

Constraints:
- Read-only only.
- Report gaps; do not try to auto-fix them here.
