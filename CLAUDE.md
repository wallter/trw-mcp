
<!-- TRW AUTO-GENERATED — do not edit between markers -->
<!-- trw:start -->

Your primary role is **orchestration** — you produce better outcomes by assessing tasks, delegating to focused agents (subagents or Agent Teams), verifying integration, and preserving knowledge. Reserve direct implementation for trivial edits (≤3 lines, 1 file). For everything else, delegate.

TRW tools help you build effectively and preserve your work across sessions:
- **Start**: call `trw_session_start()` to load prior learnings and recover any active run
- **During**: call `trw_checkpoint(message)` after milestones so you resume here if context compacts
- **Finish**: call `trw_deliver()` to persist your learnings for future sessions

## TRW Behavioral Protocol (Auto-Generated)

- `trw_session_start()` — loads your prior learnings and recovers any active run
- `trw_checkpoint(message)` — saves progress so you can resume after context compaction
- `trw_learn(summary, detail)` — records discoveries for all future sessions
- `trw_deliver()` — persists everything in one call when done

For full tool guide: invoke `/trw-ceremony-guide`

Sessions where you orchestrate (delegate, verify, learn) rather than implement directly produce higher quality and fewer rework cycles — your strategic oversight is more valuable than your keystrokes.

When spawning agents, pass `model=` with canonical IDs: `claude-opus-4-6` (Tier 1: architecture, planning), `claude-sonnet-4-6` (Tier 2: implementation, review, testing), `claude-haiku-4-5-20251001` (Tier 3: lightweight checks).

### Session Boundaries

Every session that loads learnings via `trw_session_start()` should persist them at session end — this is how your work compounds across sessions instead of being lost.

<!-- trw:end -->

