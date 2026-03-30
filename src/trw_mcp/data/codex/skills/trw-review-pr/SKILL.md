---
name: trw-review-pr
description: "Structured code review using the FRAMEWORK.md rubric. Scores correctness, tests, security, performance, maintainability, and completeness. Use before merging. Use: /trw-review-pr [branch or PR number]\n"
---

> Codex-specific skill: this version is authored for Codex. Follow Codex-native skill and subagent flows, and ignore Claude-only references if any remain.

# Structured Code Review Skill

Perform a structured code review using the FRAMEWORK.md quality rubric. Scores each dimension, provides an overall verdict, and suggests improvements.

## Workflow

1. **Determine scope**: Parse `$ARGUMENTS`:
   - If a PR number, use `gh pr diff {number}` to get the diff
   - If a branch name, use `git diff main...{branch}` to get changes
   - If `HEAD` or empty, use `git diff HEAD~1` for the latest commit
   - If no argument, use `git diff main...HEAD` for all branch changes

2. **Gather context**:
   - Run `git log --oneline main..HEAD` to understand the commit history
   - Read any PRD files referenced in commit messages or changed files
   - Call `trw_recall` with keywords from the change to find relevant learnings

3. **Run build verification**: Call `trw_build_check(scope="full")` to confirm tests pass and type checking is clean.

4. **Review each rubric dimension**:

   ### Correctness (35%)
   - Does the code do what it claims to do?
   - Are edge cases handled?
   - Are error paths correct (not swallowed, properly propagated)?
   - Does it match the requirements in the linked PRD?

   ### Tests (20%)
   - Are new/changed functions covered by tests?
   - Do tests verify behavior, not implementation details?
   - Are edge cases and error paths tested?
   - Do tests use shared project fixtures (e.g., conftest.py, test helpers)?
   - **Spec-based test review**: For each FR in the linked PRD:
     - Does at least one test assert the acceptance criterion (Given/When/Then)?
     - Does the test check response bodies, not just status codes or `is not None`?
     - Would removing the FR cause the test to fail? (If not, test is tautological)
     - Are negative cases from acceptance criteria tested?

   ### Security (15%)
   - No command injection (subprocess with shell=True)?
   - No path traversal vulnerabilities?
   - No hardcoded secrets or credentials?
   - Input validation at system boundaries?

   ### Performance (10%)
   - No unnecessary file I/O in hot paths?
   - No O(n^2) patterns on potentially large inputs?
   - Appropriate use of caching?
   - No blocking operations in async contexts?

   ### Maintainability (10%)
   - Clear variable and function names?
   - Follows existing codebase patterns?
   - No unnecessary abstractions or over-engineering?
   - Type annotations present and correct?

   ### Completeness (10%)
   - All acceptance criteria from the PRD addressed?
   - CHANGELOG.md updated for user-visible changes?
   - Documentation updated if needed?
   - No TODO items left unaddressed?

5. **Score and verdict**:
   - Score each dimension 0-100%
   - Calculate weighted total: `correctness*0.35 + tests*0.20 + security*0.15 + performance*0.10 + maintainability*0.10 + completeness*0.10`
   - Verdict: **APPROVE** (>= 80%), **REQUEST CHANGES** (60-79%), **BLOCK** (< 60%)

6. **Report**:
   ```
   ## Code Review Report

   ### Summary
   - Branch: {branch}
   - Commits: {count}
   - Files changed: {count}
   - Lines: +{added} -{removed}
   - Build: {pass/fail}

   ### Rubric Scores

   | Dimension | Weight | Score | Notes |
   |-----------|--------|-------|-------|
   | Correctness | 35% | {0-100}% | {brief note} |
   | Tests | 20% | {0-100}% | {brief note} |
   | Security | 15% | {0-100}% | {brief note} |
   | Performance | 10% | {0-100}% | {brief note} |
   | Maintainability | 10% | {0-100}% | {brief note} |
   | Completeness | 10% | {0-100}% | {brief note} |
   | **Total** | | **{weighted}%** | |

   ### Verdict: {APPROVE / REQUEST CHANGES / BLOCK}

   ### Findings
   - [{severity}] {file:line} — {description}

   ### Suggestions
   - {improvement suggestion}
   ```

## Notes

- This skill is read-only for code — it reviews but does not modify source files
- Uses `gh` CLI for PR-based reviews; falls back to `git diff` for local branches
- Build verification runs as part of the review to catch regressions
- The rubric weights match FRAMEWORK.md gate criteria
