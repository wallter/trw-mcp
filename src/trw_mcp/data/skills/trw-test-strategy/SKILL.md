---
name: test-strategy
description: >
  Audit test coverage and strategy. Identifies untested modules,
  coverage gaps, and suggests test improvements. Use before or
  during IMPLEMENT phase.
  Use: /test-strategy [module or 'all']
user-invocable: true
argument-hint: "[module or 'all']"
allowed-tools: Read, Glob, Grep, Bash, mcp__trw__trw_build_check
---

# Test Strategy Audit Skill

Audit test coverage and strategy for the project codebase. Identifies untested modules, coverage gaps, and suggests targeted test improvements.

## Workflow

1. **Determine scope**: Parse `$ARGUMENTS`:
   - If a specific module path (e.g., `tools/learning.py`), focus on that module
   - If `all` or empty, discover the project's source directory (look for `src/`, `lib/`, or top-level Python packages)

2. **Run coverage**: Call `trw_build_check(scope="pytest")` to get current test results and coverage.

3. **Analyze test structure**:
   - Glob for test files: `tests/test_*.py` or `test_*.py` (discover the project's test directory)
   - For each source module in scope, check if a corresponding test file exists
   - Grep test files for function/class names from the source module

4. **Coverage gap analysis**:
   - Parse coverage report for uncovered lines per module
   - Identify functions/methods with 0% coverage
   - Identify branches with no coverage (if/else paths)

5. **Convention check**:
   - Verify test files use fixtures from `conftest.py` (not ad-hoc setup)
   - Check for `@pytest.mark.unit` / `@pytest.mark.integration` markers
   - Verify async tests use `async def` (asyncio_mode=auto handles the rest)
   - Check that PRD traceability comments exist in test files

6. **Report**:
   ```
   ## Test Strategy Report

   ### Coverage Summary
   - Overall: {X}% (threshold: 80%)
   - Tests: {N} total, {N} passed, {N} failed

   ### Module Coverage
   | Module | Coverage | Untested Functions |
   |--------|----------|--------------------|
   | {module} | {X}% | {func1, func2} |

   ### Missing Tests
   - {module}:{function} — no test coverage
   - {module}:{class.method} — no test coverage

   ### Convention Issues
   - {file}: missing pytest markers
   - {file}: not using conftest fixtures

   ### Recommendations
   1. {highest priority test to add}
   2. {next priority}
   3. {next priority}
   ```

## Notes

- This skill is read-only — it identifies gaps but does not write tests
- Use the recommendations to guide test writing during IMPLEMENT phase
- Coverage threshold is 80% (enforced in pyproject.toml)
- Focus on testing edge cases and error paths, not just happy paths
