---
name: trw-deliver
description: >
  Persist your session's work so future agents inherit your discoveries.
  Runs build check (catches bugs before they ship), synthesizes teammate
  learnings (resolves duplicates/conflicts), then trw_deliver. Use: /trw-deliver
user-invocable: true
model: claude-sonnet-4-6
disable-model-invocation: true
allowed-tools: Read, Bash, mcp__trw__trw_build_check, mcp__trw__trw_deliver, mcp__trw__trw_recall, mcp__trw__trw_status, mcp__trw__trw_learn_update, mcp__trw__trw_learn
---

# Enhanced Delivery Skill

Run a complete delivery ceremony with pre-flight build verification and post-team learning synthesis. This ensures the codebase is clean and that any duplicate or conflicting learnings from teammate agents are consolidated before delivery artifacts are generated.

## Workflow

1. **Pre-flight build check**: Call `trw_build_check(scope="full")` to run pytest + mypy.

2. **Gate check**:
   - If build **fails**: Report failures with details (test count, failures, mypy errors). Do NOT proceed to delivery. Suggest fixing the issues first.
   - If build **passes**: Continue to step 3.

3. **Team learning synthesis** (run before delivery):

   a. **Check for active team**: Run `ls ~/.claude/teams/` via Bash. If the directory is empty or does not exist, skip to step 4.

   b. **If a team was active**, synthesize learnings:
      - Call `trw_recall("*", max_results=200)` to retrieve all current learnings.
      - Identify learnings created during this session by comparing their timestamps against the run start time (from `trw_status()` or the active run's `run.yaml`).
      - **Group by topic**: Cluster session learnings where summaries share >60% word overlap and at least one common tag.
      - **Resolve duplicates** (same topic, same conclusion): For each duplicate group, keep the entry with the highest impact score. Call `trw_learn_update(learning_id, status="resolved")` on each lower-impact duplicate.
      - **Reconcile conflicts** (same topic, different conclusions): For each conflict group, create one consolidated learning via `trw_learn()` that includes the highest-impact detail plus an "Alternative finding: ..." note for the differing conclusion. Then call `trw_learn_update(learning_id, status="resolved")` on all originals in the group.
      - **Report synthesis results**: "Synthesized N teammate learnings -> M consolidated (X duplicates resolved, Y conflicts reconciled)". If no session learnings were found, report "No team learnings to synthesize."

   c. If no team was active: skip synthesis, continue to step 4.

4. **Run delivery ceremony**: Call `trw_deliver()` which executes:
   - `trw_reflect` — extract learnings from session events
   - `trw_checkpoint` — atomic state snapshot
   - `trw_claude_md_sync` — promote high-impact learnings to CLAUDE.md
   - `trw_index_sync` — update INDEX.md and ROADMAP.md from PRD frontmatter

5. **Report summary**:
   - Test results: total tests, passed, failed
   - Coverage: percentage and pass/fail vs threshold
   - mypy: clean or error count
   - Team learning synthesis results (if a team was active)
   - Delivery steps completed
   - Learnings promoted (if any)
   - INDEX.md/ROADMAP.md sync status

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "The build passed earlier, I can skip the pre-flight check" | Build state changes between VALIDATE and DELIVER — new code may have been added | Shipping with a stale build check is the #1 cause of broken deliveries |
| "Team learning synthesis is optional, I'll skip it" | Duplicate/conflicting learnings from teammates pollute future recall | Next session loads contradictory advice — agents get confused and make worse decisions |
| "I'll just call trw_deliver directly, the skill is overkill" | The skill adds build verification + team synthesis that raw trw_deliver skips | Manual delivery skips validation steps — 3x more defects in audits |

## Notes

- This is the recommended way to end any implementation session, especially after Agent Team runs
- Combines `trw_build_check` + team learning synthesis + `trw_deliver` into a single workflow
- If you only want the delivery ceremony without build verification, call `trw_deliver()` directly
- Learning synthesis is a best-effort pass — if timestamps or run state are unavailable, synthesize all recalled learnings from the session rather than skipping
