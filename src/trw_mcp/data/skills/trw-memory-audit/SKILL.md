---
name: trw-memory-audit
model: claude-sonnet-4-6
context: fork
agent: Explore
description: >
  Audit learning memory health. Shows tag distribution, impact spread,
  staleness, duplicate candidates, and recommendations. Read-only.
  Use: /trw-memory-audit
user-invocable: true
allowed-tools: Read, Glob, Grep, mcp__trw__trw_recall
---

# Memory Audit Skill

Analyze the health of TRW's self-learning memory layer and provide actionable recommendations. This skill is read-only — it never modifies learning entries.

## Workflow

1. **Retrieve all learnings**: Call `trw_recall('*', compact=true)` to get the full learning index.

2. **Read index file**: Read `.trw/learnings/index.yaml` for the complete index with metadata.

3. **Analyze dimensions**:

   **Tag distribution**:
   - Count entries per tag
   - Identify orphan tags (used by only 1 entry)
   - Identify missing coverage areas (phases or topics with no learnings)

   **Impact distribution**:
   - Histogram: how many entries at each impact level (0.0-0.3, 0.3-0.5, 0.5-0.7, 0.7-0.9, 0.9-1.0)
   - Flag entries with impact >= 0.9 that may be over-rated
   - Flag entries with impact < 0.3 that provide little value

   **Staleness analysis**:
   - Entries older than 30 days with no recent access
   - Entries referencing removed tools or deprecated features
   - Entries tagged with `repeated` or `auto-discovered` that may be noise

   **Duplicate detection**:
   - Entries with similar summaries (fuzzy match on keywords)
   - Compendium entries that overlap with individual entries
   - Multiple entries about the same topic that could be consolidated

4. **Generate recommendations** (top 5):
   - Entries to prune (low value, stale, or noise)
   - Entries to consolidate (near-duplicates)
   - Tags to retire or rename
   - Coverage gaps to fill

5. **Report**: Output structured report with:
   - Total active/obsolete/resolved counts
   - Tag distribution table
   - Impact histogram
   - Staleness warnings
   - Duplicate candidates
   - Top 5 recommendations

## Sizing Guidelines

The optimal learning count scales with project complexity — do NOT use a fixed target.

**Formula**: Target = (distinct domain count) × 3-5 entries per domain, with a floor of 20.

When reporting, include:
- Distinct domain/topic count identified
- Calculated target range based on formula
- Current overshoot (active - target max)
- Per-domain entry counts vs per-domain target

A project with 12 distinct domains and 448 entries should target ~50-70, not 30.

## Assertion Health Analysis (PRD-CORE-086)

When auditing memory health, include an assertion analysis section:

1. Query `trw_session_start` for the `assertion_health` field (if present)
2. Report: total learnings with assertions, passing count, failing count, stale count
3. For failing assertions: list the learning ID, summary, and which assertions fail
4. Recommend actions:
   - Failing >30 days: suggest retirement via `trw_learn_update(status="obsolete")`
   - Failing recently: investigate: is the code wrong or the learning outdated?
   - Learnings referencing specific files but lacking assertions: candidates for assertion addition
5. Include an "Assertion Coverage" metric: % of learnings that have at least one assertion

## Notes

- This skill is read-only — use `/trw-memory-optimize` to act on recommendations
- Run periodically (every few sprints) to keep the learning layer healthy
