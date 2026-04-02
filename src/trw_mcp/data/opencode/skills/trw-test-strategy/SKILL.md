---
name: trw-test-strategy
description: >
  Audit tests and coverage strategy for the current module or project.
  Use: /trw-test-strategy [module or 'all']
user-invocable: true
argument-hint: "[module or 'all']"
allowed-tools: Read, Glob, Grep, Bash, mcp__trw__trw_build_check
---

# OpenCode Test Strategy Skill

1. Determine the module or project scope.
2. Call `trw_build_check(scope="pytest")` when available.
3. Inspect source and test files for missing coverage, weak assertions, and missing edge cases.
4. Report coverage gaps and the highest-priority tests to add next.

Constraints:
- Read-only analysis only.
- Prefer concrete missing cases over generic advice.
