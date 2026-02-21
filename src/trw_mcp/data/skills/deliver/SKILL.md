---
name: deliver
description: >
  Enhanced delivery with pre-flight build verification.
  Runs build check first, then trw_deliver, then reports summary.
  Use: /deliver
user-invocable: true
allowed-tools: Read, Bash
---

# Enhanced Delivery Skill

Run a complete delivery ceremony with pre-flight build verification. This ensures the codebase is in a clean state before delivery artifacts are generated.

## Workflow

1. **Pre-flight build check**: Call `trw_build_check(scope="full")` to run pytest + mypy.

2. **Gate check**:
   - If build **fails**: Report failures with details (test count, failures, mypy errors). Do NOT proceed to delivery. Suggest fixing the issues first.
   - If build **passes**: Continue to step 3.

3. **Run delivery ceremony**: Call `trw_deliver()` which executes:
   - `trw_reflect` — extract learnings from session events
   - `trw_checkpoint` — atomic state snapshot
   - `trw_claude_md_sync` — promote high-impact learnings to CLAUDE.md
   - `trw_index_sync` — update INDEX.md and ROADMAP.md from PRD frontmatter

4. **Report summary**:
   - Test results: total tests, passed, failed
   - Coverage: percentage and pass/fail vs threshold
   - mypy: clean or error count
   - Delivery steps completed
   - Learnings promoted (if any)
   - INDEX.md/ROADMAP.md sync status

## Notes

- This is the recommended way to end any implementation session
- Combines `trw_build_check` + `trw_deliver` into a single workflow
- If you only want the delivery ceremony without build verification, call `trw_deliver()` directly
