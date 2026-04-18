---
name: trw-memory-optimize
description: >
  Optimize learning memory. Prunes stale entries, consolidates duplicates,
  rebalances tags. Interactive — confirms before deleting.
  Use: /trw-memory-optimize
user-invocable: true
---

# Memory Optimization Skill

Optimize the TRW self-learning layer by pruning low-value entries, consolidating duplicates, and rebalancing tags. Interactive — always confirms before making destructive changes.

## Workflow

### Step 1: Audit first

Run `/trw-memory-audit` or the trw-maintain audit command to understand the current state:

```bash
trw-maintain audit --trw-dir .trw --no-llm
```

### Step 2: Build optimization plan

Use `trw-maintain optimize` to build a structured plan:

```bash
# Dry-run: show what would change
trw-maintain optimize --trw-dir .trw --no-impact

# With LLM-powered impact assessment (requires Ollama + gemma4)
trw-maintain optimize --trw-dir .trw --model gemma4:e2b

# Machine-readable plan
trw-maintain optimize --trw-dir .trw --format json
```

The plan identifies:
- **Prune candidates**: Low impact + stale, noise patterns, very old entries
- **Consolidation groups**: Semantically similar entries (requires embedding model)
- **Tag cleanup**: Orphan tags, near-duplicate tags, hot tags
- **Impact adjustments**: Entries with miscalibrated scores (requires LLM)

### Step 3: Present plan to user

Show the plan and ask for confirmation before proceeding. The user should review:
- Entries proposed for pruning (are any valuable?)
- Consolidation groups (are they truly duplicates?)
- Tag renames (are the canonical forms correct?)

### Step 4: Execute (after confirmation)

```bash
# Apply the optimization plan
trw-maintain optimize --trw-dir .trw --apply
```

Or apply selectively using `trw_learn_update()` for individual entries.

### Step 5: Sync

Call `trw_instructions_sync()` to refresh the client instruction file (CLAUDE.md / AGENTS.md / etc.) with the optimized learning set context.

### Step 6: Report

Run `trw-maintain audit` again and compare before/after:
- Active entries: before → after
- Entries made obsolete
- Tags normalized
- Assertion coverage change

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
- ALWAYS run `trw_instructions_sync` after changes to keep the client instruction file current
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
