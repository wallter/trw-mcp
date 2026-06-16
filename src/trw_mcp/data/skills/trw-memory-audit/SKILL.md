---
name: trw-memory-audit
context: fork
agent: Explore
description: >
  Audit learning memory health. Shows tag distribution, impact spread,
  staleness, duplicate candidates, and recommendations. Read-only.
  Use: /trw-memory-audit
user-invocable: true
---

# Memory Audit Skill

Use when: you need a read-only summary of learning-memory health (tag distribution, staleness, duplicates).

Analyze the health of TRW's self-learning memory layer and provide actionable recommendations. This skill is read-only — it never modifies learning entries.

## Workflow

> **Optional accelerator — `trw-distill`.** The fastest path uses the
> `trw-distill maintain audit` command for the heavy analysis. `trw-distill`
> is a separate, optional TRW package; if it is **not installed** (the command
> is not on your `PATH`), skip Step 1 and use the MCP-tool-only fallback in
> Step 1b — every audit dimension below is also derivable from `trw_recall`
> and the `assertion_health` field of `trw_session_start`, no extra tooling
> required.

### Step 1: Run trw-distill maintain audit (if available)

If `trw-distill` is installed, the heavy analysis runs locally via `trw-distill maintain audit`:

```bash
# Full audit with assertion verification (slower, requires codebase)
trw-distill maintain audit --trw-dir .trw --no-llm

# Quick audit without verification
trw-distill maintain audit --trw-dir .trw --no-llm --no-verify

# Machine-readable output for further processing
trw-distill maintain audit --trw-dir .trw --no-llm --format json
```

This produces a structured report covering:
- **Tag distribution**: counts, orphan tags (1 entry), hot tags (>30%)
- **Impact histogram**: 5 buckets from 0.0 to 1.0
- **Staleness**: never-accessed, stale at 30/60/90 day thresholds
- **Assertion health**: coverage %, passing/failing counts
- **Domain sizing**: topic clusters, target range, overshoot
- **Recommendations**: algorithmic suggestions for improvement

### Step 1b: MCP-tool-only fallback (no trw-distill required)

When `trw-distill` is unavailable, build the same picture using only the
built-in MCP tools:

1. **Inventory**: `trw_recall(query="*", max_results=0)` to list every active
   learning with its tags, impact, and access metadata.
2. **Tag distribution**: count entries per tag from the inventory; flag orphan
   tags (1 entry) and hot tags (>30% of entries).
3. **Impact spread**: bucket the `impact` scores into 5 ranges (0.0–1.0).
4. **Staleness**: use each entry's last-access/created timestamps to flag
   never-accessed and 30/60/90-day-stale entries.
5. **Assertion health**: read the `assertion_health` field from
   `trw_session_start` (see "Assertion Health Analysis" below).

Present the same recommendation set as the trw-distill path — the only
difference is the analysis is done in-session rather than by the external CLI.

### Step 2: Run assertion verification (optional, requires trw-distill)

For deeper assertion health analysis:

```bash
trw-distill maintain verify --trw-dir .trw --workers 4
```

Reports per-entry pass/fail with specific failing assertions.

### Step 3: Interpret and present results

Read the audit output and present to the user:
- Highlight the most actionable recommendations
- Flag entries with failing assertions for investigation
- Compare domain sizing against the target formula
- Identify coverage gaps (domains with fewer than 3 entries)

## Sizing Guidelines

The optimal learning count scales with project complexity — do NOT use a fixed target.

**Formula**: Target = (distinct domain count) × 3-5 entries per domain, with a floor of 20.

When reporting, include:
- Distinct domain/topic count identified
- Calculated target range based on formula
- Current overshoot (active - target max)
- Per-domain entry counts vs per-domain target

A project with 12 distinct domains and 448 entries should target ~50-70, not 30.

## Observability Check

As part of the audit, verify logging and observability health:
- Confirm storage operations emit structured log events with `component`, `op`, and `outcome` fields
- Check that error paths include sufficient context for diagnosis (error type, operation, affected resource)
- Verify no sensitive data (API keys, tokens, credentials) appears in learning summaries or details
- Flag learning entries that reference file paths no longer present in the codebase

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
