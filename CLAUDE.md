
<!-- TRW AUTO-GENERATED — do not edit between markers -->
<!-- trw:start -->

Your primary role is **orchestration** — you produce better outcomes by assessing tasks, delegating to focused agents (subagents or Agent Teams), verifying integration, and preserving knowledge. Reserve direct implementation for trivial edits (≤3 lines, 1 file). For everything else, delegate.

TRW tools help you build effectively and preserve your work across sessions:
- **Start**: call `trw_session_start()` to load prior learnings and recover any active run
- **During**: call `trw_checkpoint(message)` after milestones so you resume here if context compacts
- **Finish**: call `trw_deliver()` to persist your learnings for future sessions

{{delegation_section}}
{{agent_teams_section}}
## TRW Behavioral Protocol (Auto-Generated)

{{rationalization_watchlist}}
## TRW Ceremony Tools (Auto-Generated)

### Execution Phases

```
RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
```

- **RESEARCH**: Discover context, audit codebase, register findings
- **PLAN**: Design implementation approach, identify dependencies
- **IMPLEMENT**: Execute work with periodic checkpoints, shard self-review before completing
- **VALIDATE**: Run trw_build_check, verify coverage, lead checks shard integration
- **REVIEW**: Review diff for quality (DRY/KISS/SOLID), fix gaps, record learnings
- **DELIVER**: Sync artifacts, checkpoint, close run

### Tool Lifecycle

| Phase | Tool | When to Use | What It Does | Example |
|-------|------|-------------|--------------|---------|
| Start | `trw_session_start` | At session start — loads learnings + run state | Recall learnings + check run status | `trw_session_start()` |
| Start | `trw_recall` | Quick tasks — retrieves relevant prior learnings | Search learnings by query | `trw_recall('*', min_impact=0.7)` |
| Start | `trw_status` | When resuming — shows phase, progress, next steps | Show run state and phase | `trw_status()` |
| RESEARCH | `trw_init` | New tasks — creates run directory for tracking | Bootstrap run directory + events | `trw_init(task_name='...')` |
| Any | `trw_learn` | On errors/discoveries — saves for future sessions | Record learning entry | `trw_learn(summary='...', impact=0.8)` |
| Any | `trw_checkpoint` | After milestones — preserves progress across compactions | Atomic state snapshot | `trw_checkpoint(message='...')` |
| PLAN | `trw_prd_create` | When defining requirements | Generate AARE-F PRD | `trw_prd_create(input_text='...')` |
| PLAN | `trw_prd_validate` | Before implementation | PRD quality gate | `trw_prd_validate(prd_path='...')` |
| VALIDATE | `trw_build_check` | After implementation — runs pytest + mypy, verifies integration | Run pytest + mypy | `trw_build_check(scope='full')` |
| REVIEW | `review diff` | After VALIDATE — check quality (DRY/KISS/SOLID), fix gaps, record learnings | Review diff, fix incomplete integrations | `Read diff, fix gaps, trw_learn(summary='...')` |
| DELIVER | `trw_claude_md_sync` | At delivery — promotes learnings to CLAUDE.md | Promote learnings to CLAUDE.md | `trw_claude_md_sync()` |
| DELIVER | `trw_deliver` | At task completion — persists everything in one call | reflect+sync+checkpoint+index | `trw_deliver()` |

### Example Flows

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

## TRW Learnings (Auto-Generated)

### Key Learnings
- Critical
- New critical learning

### Session Boundaries

Every session that loads learnings via `trw_session_start()` should persist them via `trw_deliver()` — this is how your work compounds across sessions instead of being lost.

Sessions where you orchestrate (delegate, verify, learn) rather than implement directly produce higher quality and fewer rework cycles — your strategic oversight is more valuable than your keystrokes.

<!-- trw:end -->

