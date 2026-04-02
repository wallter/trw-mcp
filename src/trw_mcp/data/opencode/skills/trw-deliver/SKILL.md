---
name: trw-deliver
description: >
  Run build verification and persist the current TRW session for OpenCode.
  Use: /trw-deliver
user-invocable: true
allowed-tools: Read, Bash, mcp__trw__trw_build_check, mcp__trw__trw_deliver, mcp__trw__trw_status
---

# OpenCode Delivery Skill

1. Call `trw_status()` to confirm the current run state.
2. Call `trw_build_check(scope="full")`.
3. If the build fails, stop and report the failing checks.
4. If the build passes, call `trw_deliver()`.
5. Summarize the build result, delivery result, and any remaining risks.

Constraints:
- Do not assume team task APIs exist.
- Delivery is handled through `trw_deliver()`.
