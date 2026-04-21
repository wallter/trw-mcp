# trw-mcp

MCP server for AI coding agents — part of [TRW Framework](https://trwframework.com).

**Public repo**: [github.com/wallter/trw-mcp](https://github.com/wallter/trw-mcp) | **PyPI**: `pip install trw-mcp`

## Build & Test

```bash
pip install -e ".[dev]"                                       # Dev install
pytest tests/test_specific_file.py -v                         # Single file (preferred)
pytest tests/ -m unit                                         # Unit tests only
mypy --strict src/trw_mcp/                                    # Type check
ruff check src/                                               # Lint
```

## Key Architecture

- `server/` — FastMCP entry point, middleware chain (ceremony, observation masking, response optimizer)
- `tools/` — 14 MCP tool implementations
- `state/` — State management (persistence uses `YAML(typ="safe")` for all reads)
- `middleware/` — Observation masking (`ContextBudgetMiddleware`), ceremony enforcement
- `telemetry/` — Constants (inlined from trw-shared), pipeline, sender
- `data/` — Bundled agents, skills, hooks for `init-project`

## Security Notes

- All YAML reads use `YAML(typ="safe")` — round-trip loader only for writes
- Shell hooks use `_json_escape()` for all interpolated values
- `sqlite-vec` is optional (`[vectors]` extra) — degrades gracefully when absent
<!-- TRW AUTO-GENERATED — do not edit between markers -->
<!-- trw:start -->

Your primary role is **orchestration** — delegate to focused agents when a task benefits from its own context window. Focused subagents get deeper context per task than the parent session can hold; subagent results return with tighter scope and less distraction. Reserve self-implementation for trivial edits (≤3 lines, 1 file).

**Your first action in every session must be `trw_session_start()`.**

This single call loads everything you need: prior learnings from hundreds of past sessions, any active run state you can resume, and the full operational protocol (delegation guidance, phase gates, quality rubrics). Without it, you start from zero — with it, you start from the team’s accumulated experience.

After `trw_session_start()`, save progress with `trw_checkpoint()` after milestones, and close with `trw_deliver()` so your discoveries persist for future agents.

## TRW Behavioral Protocol (Auto-Generated)

| Tool | When | Why |
|------|------|-----|
| `trw_session_start()` | First action | Loads prior learnings so you don't repeat solved problems or rediscover known gotchas |
| `trw_learn(summary, detail)` | On discoveries | Saves your finding so no future agent repeats your mistake |
| `trw_checkpoint(message)` | After milestones | If context compacts, you resume here instead of re-implementing from scratch |
| `trw_deliver()` | Last action | Persists your session's discoveries for future agents — without it, your learnings die with your context window |

Full tool lifecycle: `/trw-ceremony-guide`

### Memory Routing

Default to `trw_learn()` for knowledge. Use native auto-memory only for personal preferences.

| | `trw_learn()` | Native auto-memory |
|---|---|---|
| Search | `trw_recall(query)` — semantic + keyword | Filename scan only |
| Visibility | All agents, subagents, teammates | Primary session only |
| Lifecycle | Impact-scored, auto-promotes to CLAUDE.md | Static until manually edited |
| Scale | Hundreds of entries, auto-pruned by staleness | 200-line index cap |

Gotcha or error pattern → `trw_learn()`. User’s preferred commit style → native memory. Build trick that saves time → `trw_learn()`. Communication preference → native memory.

### Session Boundaries

Every session that loads learnings via `trw_session_start()` should persist them at session end — this is how your work compounds across sessions instead of being lost.

<!-- trw:end -->

