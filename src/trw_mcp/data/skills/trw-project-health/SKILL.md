---
name: trw-project-health
description: >
  Audit TRW health in the current or target project. Summarizes tool usage,
  ceremony compliance, hook enforcement, active runs, learnings, and issues.
  Use: /trw-project-health [path]
user-invocable: true
argument-hint: "[optional project path]"
---

# Project Health

Use when: producing an evidence-backed health assessment for a TRW-enabled project.

Produce a read-only, evidence-backed TRW operational snapshot for the current or requested project.

## 1. Resolve evidence sources

1. Resolve the project root and require `.trw/`; otherwise report `not initialized`.
2. Call `trw_status()` first. When available, also use `trw_pipeline_health()` and `trw_mcp_security_status()` rather than recreating their logic.
3. Read run state with `trw_status()`, then resolve the path from the caller, current session-start result (`run.active_run`), or runtime pins. Status itself does not return a path. The canonical layout is `.trw/runs/<task>/<run>/`; inspect only explicitly resolved paths.
4. Treat every file source as optional and schema-check it before use:
   - `.trw/context/analytics.yaml`, `build-status.yaml`, `session-events.jsonl`, and dated `events-*.jsonl`;
   - resolved run `meta/*.yaml` and `reports/*.md`;
   - `.trw/security/` counters when security health is in scope;
   - client-specific hook logs only when the active client actually emits them.

Do not treat a missing optional log as zero activity. Mark it `UNKNOWN/NOT EMITTED` and name the absent source.

## 2. Assess without folklore

### Run and ceremony

- Report active/stale/completed state, phase, tier/profile, last activity, checkpoints/reversions, build evidence, and substantive review evidence.
- Judge compliance against the resolved tier and configured gates, not raw event counts or elapsed-time folklore.
- Separate session-level events from run-level artifacts and avoid attributing another concurrent session's evidence to this run.

### Hooks and tools

- Derive hook fires/blocks/errors from the actual event/log schema found.
- Report raw counts and rates with sample size/source. Compare against configured policy or project history only when available; do not impose universal block-rate/pass-rate thresholds.
- Distinguish `not installed`, `not emitted`, and observed zero.

### Learning memory

- Use bounded `trw_recall`/`trw_session_start` output or `/trw-memory-audit`; do not assume `.trw/learnings/entries/*.yaml` is the active store.
- Report retrieval availability, assertion failures when exposed, stale/duplicate candidates, and domain gaps. Learnings surface through recall/session start, not promotion into client instruction files.

### Runtime integrity

- Compare deployed framework/version evidence with the bundled/source version when available.
- In the framework repository, run the project-native runtime-integrity diagnostic if requested/appropriate and keep source parity separate from deployed state.

## 3. Report provenance

```markdown
## TRW Project Health
- Project: <absolute path>
- Active run/session: <id/path or none>
- Evidence window: <timestamps>

| Area | Status | Evidence source | Observation |
|---|---|---|---|
| runtime/framework | | | |
| run/ceremony | | | |
| validation/review | | | |
| hooks/tools | | | |
| learning retrieval | | | |
| security/pipeline | | | |

### Unknown or unavailable evidence
- <source and impact>

### Issues
- <severity, observed fact, risk>

### Recommended next actions
1. <project-native action>
```

Use `PASS`, `WARN`, `BLOCK`, `N/A`, or `UNKNOWN`. Never convert absence, small samples, or fixed generic percentages into a confident verdict.
