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
2. Call `trw_recall("*", max_results=25)` when broader learning context is needed.
3. Inspect framework and instruction files relevant to the current project.
4. Report ceremony status, run status, learning-layer health, and actionable next steps.

Constraints:
- Read-only only.
- Report gaps; do not try to auto-fix them here.
