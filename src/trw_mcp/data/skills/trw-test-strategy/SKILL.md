---
name: trw-test-strategy
context: fork
agent: Explore
description: >
  Audit test coverage and strategy. Identifies untested modules,
  coverage gaps, and suggests test improvements. Use before or
  during IMPLEMENT phase.
  Use: /trw-test-strategy [module or 'all']
user-invocable: true
argument-hint: "[module or 'all']"
---

# Test Strategy Audit Skill

Use when: auditing test/coverage strategy for a module or project before or during implementation.

Audit test coverage and strategy for the project codebase. Identifies untested modules, coverage gaps, and suggests targeted test improvements.

## Workflow

1. **Determine scope**: Parse `$ARGUMENTS`:
   - If a specific module path (e.g., `tools/learning.py`), focus on that module
   - If `all` or empty, discover source roots from the repo layout and config: `src/`, `lib/`, `packages/`, `apps/`, `cmd/`, `crates/`, service directories, or top-level language packages/modules

2. **Run coverage/test signal**: Use the project's configured test command when known (`make test`, `pytest`, `vitest`, `npm test`, `cargo test`, `go test`, etc.). Capture the exact command, scope, outcome, counts, and coverage provenance. Do not call `trw_build_check` from this read-only audit: return the evidence to the owning orchestrator, which may record it only when it satisfies the active validation plan. If no safe command is evident, report likely commands and continue with static analysis rather than inventing one.

3. **Analyze test structure**:
   - Glob for test files using project conventions: `tests/**`, `*.test.*`, `*.spec.*`, `*_test.go`, `*_test.rs`, `test_*.py`, integration/e2e folders, or configured test globs
   - For each source module in scope, check if a corresponding test file exists
   - Grep test files for functions, classes, components, commands, schemas, API routes, events, or acceptance IDs from the source module/PRD

4. **Coverage gap analysis**:
   - Parse coverage report for uncovered lines per module
   - Identify symbols, handlers, components, or commands with no direct or behavioral coverage
   - Identify branches with no coverage (conditionals, error paths, boundary cases, retries, fallbacks)
   - Identify whether any vertical tracer-bullet/e2e slice proves the main behavior through its real integration path

5. **Convention check**:
   - Verify test files use shared fixtures or setup helpers (not ad-hoc setup)
   - Check for tier markers/tags (unit/integration/e2e/slow/network) according to the project's test runner config
   - Verify async/concurrency tests follow the framework's async test pattern (for example pytest-asyncio, Vitest/Jest async tests, Go context/timeouts, Rust async runtimes)
   - Check that PRD traceability comments exist in test files
   - Check deep-module boundaries: tests should assert public/stable interfaces first and avoid overfitting private internals unless characterization is required

6. **Report**:
   ```
   ## Test Strategy Report

   ### Coverage Summary
   - Overall: {X}% (configured gate: {Y}% | not configured)
   - Tests: {N} total, {N} passed, {N} failed

   ### Module Coverage
   | Module | Coverage | Untested Functions |
   |--------|----------|--------------------|
   | {module} | {X}% | {func1, func2} |

   ### Missing Tests
   - {module}:{function} — no test coverage
   - {module}:{class.method} — no test coverage

	   ### Convention Issues
	   - {file}: missing project tier marker/tag
	   - {file}: duplicates setup instead of using shared fixtures/helpers

	   ### Slice and Boundary Coverage
	   - Vertical slice: {present/missing} — {path through system}
	   - Deep-module boundary tests: {public interface covered / only private internals covered}

	   ### Recommendations
   1. {highest priority test to add}
   2. {next priority}
   3. {next priority}
   ```

## Notes

- This skill is read-only — it identifies gaps but does not write tests
- Use the recommendations to guide test writing during IMPLEMENT phase
- Use the coverage threshold enforced by project config or an explicit accepted requirement. If no coverage threshold is configured, report `not configured`; do not invent a percentage.
- Focus on testing edge cases and error paths, not just happy paths
- Prefer recommendations that prove a thin vertical slice before asking for broad horizontal layer coverage
