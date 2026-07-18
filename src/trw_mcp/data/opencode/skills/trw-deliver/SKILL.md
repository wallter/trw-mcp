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
2. Run project-native validation, then record its observed result with `trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope="<exact command>")`.
3. If the build fails, stop unless a structured acceptable-failure record or authorized operator/config override actually satisfies a gate path; never fabricate a passing build check.
4. Call `trw_deliver()` only through one of the three gate paths below.
5. Summarize the build result, delivery result, and any remaining risks.

Constraints:
- Do not assume team task APIs exist.
- Delivery is handled through `trw_deliver()`.
- **Deliver gate — no fourth path:** require at least one of three sanctioned paths: (1) a passing `trw_build_check` bound to post-edit
  project-native validation; (2) `allow_unverified=true` with a valid, unexpired structured `unverified_reason`
  containing `failed_command`, `residual_risk`, `owner`, and `expiry_iso`; or (3) an authorized operator/config
  override recorded with technical rationale.
- Free-text limitations and review-verdict labels are not acceptable-failure records. Without a gate path, report blocked.
