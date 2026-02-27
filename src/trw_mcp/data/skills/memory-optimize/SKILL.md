---
name: memory-optimize
description: >
  Optimize learning memory. Prunes stale entries, consolidates duplicates,
  rebalances tags. Interactive — confirms before deleting.
  Use: /memory-optimize
user-invocable: true
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, mcp__trw__trw_recall, mcp__trw__trw_learn_update, mcp__trw__trw_claude_md_sync
---

# Memory Optimization Skill

Optimize the TRW self-learning layer by pruning low-value entries, consolidating duplicates, and rebalancing tags. Interactive — always confirms before making destructive changes.

## Workflow

1. **Audit first**: Run the same analysis as `/memory-audit`:
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

## Constraints

- NEVER delete learning YAML files — mark as `obsolete` status instead
- ALWAYS present the plan and get user confirmation before any changes
- ALWAYS preserve high-impact (>= 0.7) entries unless clearly outdated
- ALWAYS run `trw_claude_md_sync` after changes to keep CLAUDE.md current

## Notes

- Optimal learning layer size: 20-40 active entries
- Run after major milestones (strip-downs, sprint completions) when many entries may be stale
- Use `/memory-audit` first for a read-only preview
