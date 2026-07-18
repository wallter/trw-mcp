---
name: trw-memory-optimize
description: "Optimize learning memory. Prunes stale entries, consolidates duplicates, rebalances tags. Interactive — confirms before deleting. Use: /trw-memory-optimize\n"
---

> Codex-specific skill: this version is authored for Codex. Follow Codex-native skill and subagent flows, and ignore Claude-only references if any remain.

# Memory Optimization

Interactive, evidence-backed maintenance of learning memory. Audit first, present exact proposed mutations, and obtain confirmation before changing any entry.

## 1. Build a grounded plan

1. Run `/trw-memory-audit` and retain its provenance/coverage limitations.
2. For each candidate, recall the full current entry and nearby semantic matches.
3. Classify the proposed action:
   - `RETAIN`: distinct and still useful;
   - `UPDATE`: correct the summary/detail/tags/assertions/status with current evidence;
   - `CONSOLIDATE`: choose a survivor, preserve unique constraints/provenance, then obsolete true duplicates;
   - `OBSOLETE`: retire knowledge proven stale or superseded;
   - `INVESTIGATE`: evidence is incomplete or conflicts with current code.
4. Record exact learning IDs, before/after fields, evidence, uncertainty, and rollback/recovery approach.

Optimize retrieval usefulness and domain coverage—not a fixed global count, entries-per-domain formula, impact threshold, or compendium size.

The optional `trw-distill maintain optimize` workflow may generate a machine-readable plan when installed. Use it for planning only: verify its version/command, review the dry-run output under the same rules, and never treat optional LLM scoring as authoritative evidence. Do not invoke `trw-distill maintain optimize --apply`; the current CLI rebuilds an unbound plan instead of applying an immutable reviewed receipt. Apply confirmed IDs and fields narrowly through `trw_learn_update`.

## 2. Confirm destructive/semantic changes

Present the full plan. Require explicit user confirmation before obsoleting entries, merging meaning, renaming tags broadly, or applying an external batch plan. Do not interpret approval of one candidate as approval of the batch.

Checkpoint the accepted plan before mutation when a run is active.

## 3. Apply narrowly

- Prefer `trw_learn_update` for explicit per-entry changes.
- Retire with `status="obsolete"`; do not hard-delete learning storage.
- Apply consolidations in a recoverable order: update/create the survivor, verify it, then obsolete duplicates.
- Re-read an entry immediately before mutation so a stale plan does not overwrite concurrent changes.

`trw_instructions_sync` is not a memory-index refresh and does not place the optimized learning set into client instructions. Do not call it for that purpose. Learnings surface through `trw_session_start` and `trw_recall`.

## 4. Verify

Re-run bounded recall/audit queries and confirm:

- updated/surviving entries are retrievable for intended queries;
- obsolete entries no longer appear in default active recall;
- unique constraints and domain coverage remain;
- assertions and referenced paths are valid or explicitly queued for investigation;
- no unrelated entries changed.

If only a sampled audit is possible, label the before/after comparison partial.

## Report

```markdown
## TRW Memory Optimization
- Audit source/coverage: <...>
- Confirmed plan: <receipt/checkpoint>

| ID(s) | Action | Before -> after | Evidence | Verification |
|---|---|---|---|---|

### Retrieval checks
- <query>: <observed result>

### Deferred/uncertain candidates
- <ID, missing evidence, owner/next action>
```

Record a new learning only if this maintenance reveals a non-obvious reusable system behavior; routine optimization results belong in the run/report.
