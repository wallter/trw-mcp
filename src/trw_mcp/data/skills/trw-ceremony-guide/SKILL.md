---
name: trw-ceremony-guide
model: claude-sonnet-4-6
description: >
  Load the full TRW ceremony reference: tool lifecycle table, execution
  phases, and example flows. Use when you need to know which tool to call
  and when. Invoke: /trw-ceremony-guide
user-invocable: true
argument-hint: ""
allowed-tools: []
---

# TRW Ceremony Guide

Complete reference for TRW lifecycle tools, execution phases, and workflow patterns.

## Execution Phases

```
RESEARCH -> PLAN -> IMPLEMENT -> VALIDATE -> REVIEW -> DELIVER
```

- **RESEARCH**: Discover context, audit codebase, register findings
- **PLAN**: Design implementation approach, identify dependencies
- **IMPLEMENT**: Execute work with periodic checkpoints, shard self-review before completing
- **VALIDATE**: Run trw_build_check, verify coverage, lead checks shard integration
- **REVIEW**: Review diff for quality (DRY/KISS/SOLID), fix gaps, record learnings
- **DELIVER**: Sync artifacts, checkpoint, close run

## Tool Lifecycle

| Phase | Tool | When to Use | What It Does | Example |
|-------|------|-------------|--------------|---------|
| Start | `trw_session_start` | At session start -- loads learnings + run state (pass query for focused recall) | Recall learnings + check run status | `trw_session_start(query='task domain')` |
| Start | `trw_recall` | Quick tasks -- retrieves relevant prior learnings | Search learnings by query | `trw_recall('*', min_impact=0.7)` |
| Start | `trw_status` | When resuming -- shows phase, progress, next steps | Show run state and phase | `trw_status()` |
| RESEARCH | `trw_init` | New tasks -- creates run directory for tracking | Bootstrap run directory + events | `trw_init(task_name='...')` |
| Any | `trw_learn` | On errors/discoveries -- saves for future sessions | Record learning entry | `trw_learn(summary='...', impact=0.8)` |
| Any | `trw_checkpoint` | After milestones -- preserves progress across compactions | Atomic state snapshot | `trw_checkpoint(message='...')` |
| PLAN | `trw_prd_create` | When defining requirements | Generate AARE-F PRD | `trw_prd_create(input_text='...')` |
| PLAN | `trw_prd_validate` | Before implementation | PRD quality gate | `trw_prd_validate(prd_path='...')` |
| VALIDATE | `trw_build_check` | After implementation -- runs pytest + mypy, verifies integration | Run pytest + mypy | `trw_build_check(scope='full')` |
| REVIEW | `review diff` | After VALIDATE -- check quality (DRY/KISS/SOLID), fix gaps, record learnings | Review diff, fix incomplete integrations | `Read diff, fix gaps, trw_learn(summary='...')` |
| DELIVER | `trw_claude_md_sync` | At delivery -- promotes learnings to CLAUDE.md | Promote learnings to CLAUDE.md | `trw_claude_md_sync()` |
| DELIVER | `trw_deliver` | At task completion -- persists everything in one call | reflect+sync+checkpoint+index | `trw_deliver()` |

## Example Flows

**Quick Task** (no run needed):
```
trw_session_start -> work -> trw_learn (if discovery) -> trw_deliver()
```

**Full Run**:
```
trw_session_start -> trw_init(task_name, prd_scope)
  -> work + trw_checkpoint (periodic) + trw_learn (discoveries)
  -> trw_build_check(scope='full')           [VALIDATE]
  -> review diff, fix gaps, trw_learn         [REVIEW]
  -> trw_deliver()
```

## Ceremony Tiers (PRD-CORE-060)

TRW adapts ceremony depth based on task complexity:

| Tier | When | Ceremony |
|------|------|----------|
| MINIMAL | Trivial tasks (1-2 files, bug fix) | trw_session_start + trw_learn (if discovery) |
| STANDARD | Normal tasks (3-5 files, feature) | Full lifecycle: init, checkpoint, build_check, deliver |
| COMPREHENSIVE | Complex tasks (6+ files, sprint) | Agent Teams, playbooks, full phase gates |

See `/trw-sprint-init` for sprint-level orchestration.

## Rationalization Watchlist

If you catch yourself thinking any of these, stop and follow the process:

| Thought | Why it's wrong | Consequence |
|---------|---------------|-------------|
| "This is too simple for ceremony" | Simple tasks compound into gaps when 10 agents skip in parallel | You skip checkpoint, context compacts, you re-implement from scratch |
| "I'll checkpoint/deliver after I finish this part" | Context compaction erases uncheckpointed work permanently | Past agents who skipped trw_deliver lost all session learnings |
| "I already know the codebase" | Prior learnings contain gotchas for exactly this area | Sprint 26 had 6 P0/P1 defects from agents who skipped recall |
| "I can implement directly, delegation is overhead" | Subagent implementation has 3x fewer P0 defects | Your focused context is valuable -- subagents get deeper context per task |
| "The build check can wait until the end" | Late build failures cascade into multi-file rework | 2x rework when caught at DELIVER vs catching at VALIDATE |

### Rigid Tools (never skip, unconditional)

- `trw_session_start()` -- always, first action
- `trw_deliver()` -- always, last action
- `trw_build_check()` -- always at VALIDATE and DELIVER
- Completion artifacts -- always before marking task complete

### Flexible Tools (must happen, you pick timing)

- `trw_checkpoint()` -- at milestones (you judge which)
- `trw_learn()` -- on discoveries/gotchas/errors
- `trw_recall()` -- recommended at start, skippable for repeat-domain
