---
name: trw-project-health
description: >
  Audit TRW health in the current or target project. Summarizes tool usage,
  ceremony compliance, hook enforcement, active runs, learnings, and issues.
  Use: /project-health [path]
user-invocable: true
argument-hint: "[optional project path]"
---

# Project Health Skill

Generate a comprehensive TRW health report for a project, covering tool usage, ceremony compliance, hook enforcement, learnings, and active issues.

## Path Resolution

- If `$ARGUMENTS` provides a path, use it as the project root
- Otherwise, use the current working directory
- Verify the project has a `.trw/` directory; if not, report "TRW not initialized"

## Workflow

1. **Gather data** — Read the following files (skip any that don't exist):

   | Source | Path | Data extracted |
   |--------|------|----------------|
   | Analytics | `.trw/context/analytics.yaml` | Session count, learning count, sync count, success rate |
   | Hook log | `.trw/context/hook-executions.log` | Event counts by type, block rates |
   | Learnings | `.trw/learnings/entries/*.yaml` | Total count, substantive vs auto-generated, tag distribution |
   | Active runs | `{task_root}/*/runs/*/meta/run.yaml` | Run ID, task, phase, status, PRD scope |
   | Event logs | `{task_root}/*/runs/*/meta/events.jsonl` | Event counts, checkpoint frequency, ceremony completion |
   | Telemetry log | `.trw/logs/trw-mcp-*.jsonl` | File size, line count, TRW event counts vs noise |
   | Config | `.trw/config.yaml` | Task root, telemetry settings, debug mode |

2. **Analyze ceremony compliance** per active run:
   - Count ceremony events: `run_init`, `checkpoint`, `shard_start/complete`, `reflection_complete`, `trw_deliver_complete`
   - Check for premature delivery pattern: `reflection_complete` immediately after `run_init` (< 60s gap)
   - Calculate checkpoint frequency: checkpoints per hour of work
   - Check if delivery was called after meaningful work

3. **Analyze hook enforcement**:
   - Count by event type: SessionStart, Stop, SubagentStart, TaskCompleted, PostToolUse
   - Calculate block rate per type: `blocked / total`
   - Flag types with > 50% block rate as potential over-enforcement
   - Flag Stop hooks with 0% pass rate (likely a bug)

4. **Analyze learnings**:
   - Count substantive learnings (those with detail > 100 chars and impact >= 0.6)
   - Count auto-generated learnings (IDs matching `repeated-operation-*` or `success-*` patterns)
   - Check for promoted learnings (`promoted_to_claude_md: true`)
   - List top 5 by impact score

5. **Check for known issues**:
   - LLM call failures in telemetry (`"event": "llm_call_failed"`)
   - Excessive deliver calls (> 3 per run suggests stop hook frustration)
   - Event name mismatches in hooks
   - Missing ceremony events in completed runs
   - Log file size > 10MB (suggests noise suppression issue)

6. **Generate report** — Output a structured markdown report:

```markdown
## TRW Project Health Report

**Project**: {path}
**Report date**: {date}
**TRW version**: {framework version from latest run.yaml}

### Summary

| Metric | Value | Status |
|--------|-------|--------|
| Sessions tracked | {N} | -- |
| Active runs | {N} | -- |
| Total learnings | {N} | -- |
| Ceremony success rate | {N}% | OK/WARN/FAIL |
| Hook block rate (avg) | {N}% | OK/WARN/FAIL |

### Active Runs

| Run | Task | Phase | Events | Checkpoints | Ceremony |
|-----|------|-------|--------|-------------|----------|
| {id} | {task} | {phase} | {N} | {N} | Complete/Pending/Premature |

### Hook Enforcement

| Hook | Fires | Blocked | Pass Rate | Assessment |
|------|-------|---------|-----------|------------|
| Stop | {N} | {N} | {N}% | {OK/Over-blocking/Bug} |
| TaskCompleted | {N} | {N} | {N}% | {OK/Over-blocking} |
| ... | ... | ... | ... | ... |

### Tool Usage (from telemetry)

| Tool | Calls | Assessment |
|------|-------|------------|
| trw_checkpoint | {N} | {Good/Low/Excessive} |
| trw_deliver | {N} | {Good/Excessive} |
| ... | ... | ... |

### Learnings

- **Substantive**: {N} (impact >= 0.6, detail > 100 chars)
- **Auto-generated**: {N} (repeated ops, success patterns)
- **Promoted to CLAUDE.md**: {N}
- **Top learnings**: {list top 5 by impact}

### Issues Found

{Bulleted list of detected issues with severity}

### Recommendations

{Actionable items based on the analysis}
```

## Assessment Thresholds

| Metric | OK | WARN | FAIL |
|--------|-----|------|------|
| Ceremony success rate | >= 80% | 50-79% | < 50% |
| Hook block rate (avg) | < 20% | 20-50% | > 50% |
| Stop hook pass rate | > 0% | -- | = 0% |
| Checkpoint frequency | >= 2/hr | 1-2/hr | < 1/hr |
| Deliver calls per run | <= 3 | 4-6 | > 6 |
| Log file size | < 10 MB | 10-50 MB | > 50 MB |

## Notes

- This skill is read-only — it never modifies project files
- Works on the current project or any project with a `.trw/` directory
- For cross-project monitoring, pass the target project path as an argument
- Pairs well with `/trw-memory-audit` for deeper learning analysis
