---
name: trw-memory-optimize
model: sonnet
description: >
  Optimize learning memory. Prunes stale entries, consolidates duplicates,
  rebalances tags. Interactive — confirms before deleting.
  Use: /trw-memory-optimize
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, mcp__trw__trw_recall, mcp__trw__trw_learn_update, mcp__trw__trw_claude_md_sync
---

# Memory Optimization Skill

Optimize the TRW self-learning layer by pruning low-value entries, consolidating duplicates, and rebalancing tags. Interactive — always confirms before making destructive changes.

## Workflow

1. **Audit first**: Run the same analysis as `/trw-memory-audit`:
   - Call `trw_recall('*', compact=true)` for all learnings
   - Read `.trw/learnings/index.yaml`
   - Analyze tags, impact, staleness, duplicates

2. **Build optimization plan**:
   - **Prune candidates**: Entries with impact < 0.4, entries tagged `repeated` with count suffix, entries referencing removed features
   - **Consolidate candidates**: Near-duplicate entries that can be merged into a single compendium entry
   - **Tag cleanup**: Orphan tags to remove, inconsistent tag names to normalize
   - **Impact recalibration**: Entries whose impact scores seem miscalibrated based on actual utility

3. **Present plan**: Show the user:
   - Entries to delete (with summary and current impact)
   - Entries to consolidate (showing which merge into what)
   - Tag changes proposed
   - Ask for confirmation before proceeding

4. **Execute** (only after user confirmation):
   - For deletions: Read each entry YAML file, update status to `obsolete` (do not delete the file — TRW tracks obsolete entries)
   - For consolidations: Create a new compendium entry via `trw_learn`, then mark originals as `obsolete`
   - For tag cleanup: Edit entry YAML files to update tags

5. **Sync**: Call `trw_claude_md_sync()` to update CLAUDE.md with the optimized learning set.

6. **Report**: Before/after summary:
   - Active entries: before → after
   - Entries made obsolete
   - Entries consolidated
   - Tags normalized
   - CLAUDE.md updated

## Sizing Guidelines

The optimal learning count scales with project complexity — do NOT use a fixed target.

**Formula**: Target = (distinct domain count) × 3-5 entries per domain, with a floor of 20.

**How to calculate**:
1. Identify distinct topic clusters (e.g., "hallucination", "testing", "ollama", "transcription")
2. Each cluster should consolidate to 3-5 entries depending on depth:
   - Simple domain (few gotchas): 2-3 entries
   - Complex domain (many patterns, edge cases): 5-8 entries
3. A project with 12 domains should target ~50-70 active entries, not 30

**Consolidation depth limit**: Never merge more than 10-15 entries into a single compendium. If a topic has 60+ entries, create 5-8 sub-topic compendiums (e.g., "hallucination-grounding", "hallucination-detection", "hallucination-mitigation") rather than one mega-entry.

**Domain coverage rule**: Every distinct domain MUST retain at least 1 detailed entry after optimization. If consolidation would leave a domain with 0 entries, it's too aggressive.

## Constraints

- NEVER delete learning YAML files — mark as `obsolete` status instead
- ALWAYS present the plan and get user confirmation before any changes
- ALWAYS preserve high-impact (>= 0.7) entries unless clearly outdated
- ALWAYS run `trw_claude_md_sync` after changes to keep CLAUDE.md current
- NEVER collapse all entries in a domain into a single compendium — maintain sub-topic granularity

## Assertion Verification Wave (PRD-CORE-086)

After standard pruning and consolidation, run an assertion verification wave:

1. **Collect**: Identify all learnings with non-empty assertions via `trw_recall(query="*", max_results=0)` and filter for entries with `assertion_status`
2. **Verify**: For each entry with failing assertions, spawn a subagent to investigate:
   - Read the referenced files in the codebase
   - Determine root cause: Is the learning outdated? Is the assertion pattern wrong? Is the code violating the convention?
   - Recommend one of:
     - `UPDATE_LEARNING`: Learning text needs revision (provide new text)
     - `UPDATE_ASSERTION`: Assertion pattern is wrong (provide corrected pattern)
     - `RETIRE_LEARNING`: Knowledge is obsolete (provide reason)
     - `CODE_VIOLATION`: Code is wrong, learning is right (flag for human review)
3. **Apply**: For each recommendation, use `trw_learn_update()` to apply changes (with user confirmation for retirements)
4. **Report**: Summary table of verification results — passing, fixed, retired, flagged

## Notes

- Run after major milestones (strip-downs, sprint completions) when many entries may be stale
- Use `/trw-memory-audit` first for a read-only preview
