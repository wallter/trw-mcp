---
name: trw-memory-audit
description: "Audit learning memory health. Shows tag distribution, impact spread, staleness, duplicate candidates, and recommendations. Read-only. Use: /trw-memory-audit\n"
---

> Codex-specific skill: this version is authored for Codex. Follow Codex-native skill and subagent flows, and ignore Claude-only references if any remain.

# Memory Audit

Assessment of learning retrieval quality, redundancy, staleness, assertion health, and domain coverage. It never changes learning content/status; disclose that recall/session-start retrieval updates access telemetry. Report the evidence window and sampling limits; do not optimize for a universal entry count.

## Evidence paths

1. Prefer a verified read-only primary-store diagnostic. If the optional `trw-distill` package is installed, `maintain audit --no-llm --format json` may provide that inventory; verify the command/version and label the source.
2. Reuse existing `trw_session_start(verbose=true)` diagnostics when available. Its assertion health is aggregate only and does not identify failing learning IDs.
3. Use targeted, bounded `trw_recall` only to inspect retrieval utility or candidate duplicates. Recall updates access counts/timestamps, so record the audit start time and never use post-recall access metadata as staleness evidence.
4. Never run wildcard `max_results=0` inside the model context; it can overload context and alters access metadata for every match.
5. Do not assume learning YAML files or an instruction file are the active store.

Without a verified full read-only diagnostic, label the audit `SAMPLED/PARTIAL`; mark corpus-wide staleness, duplicate, and tag counts `UNKNOWN`.

## Analysis

For the observed inventory:

- **Retrieval utility:** identify entries that answer current domains, conflict, or repeatedly fail to surface.
- **Redundancy:** group semantically overlapping entries, but retain distinct constraints, provenance, scope, and counterexamples.
- **Staleness:** use access/creation time, assertion results, referenced-path validity, and current code evidence. Age alone is not proof of obsolescence.
- **Impact calibration:** compare impact with demonstrated consequence/reuse; do not protect or demote solely by a fixed score.
- **Domain coverage:** identify missing or overrepresented topics relative to the project's actual architecture/work, not a fixed entries-per-domain formula.
- **Assertion health:** report aggregate categories from session diagnostics; list learning IDs only when a full diagnostic actually returns them. A failure may mean stale knowledge, a bad assertion, or a code regression.
- **Safety:** flag secrets, credentials, PII, or overly specific machine-local data in summaries/details.

Do not modify learning content, status, tags, or assertions in this skill.

## Report

```markdown
## TRW Memory Audit
- Source(s): <tool/command/version>
- Inventory coverage: FULL | SAMPLED/PARTIAL
- Entries observed / total reported: <n / unknown>
- Evidence window: <timestamps>

### Retrieval and domain coverage
- useful/missing/overrepresented: <evidence>

### Candidate actions
| Learning IDs | Action | Evidence | Risk/uncertainty |
|---|---|---|---|
| | RETAIN / UPDATE / CONSOLIDATE / OBSOLETE / INVESTIGATE | | |

### Assertion health
| ID | Status | Evidence | Next check |
|---|---|---|---|

### Safety findings
- <finding or none observed>

### Limitations
- <sampling, missing fields, unavailable optional tools>
```

Recommend `/trw-memory-optimize` only when the evidence supports a concrete change plan.
