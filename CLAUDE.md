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
- `tools/` — 24 MCP tool implementations
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

Your primary role is **orchestration** — delegate to focused agents for better outcomes than direct implementation. Reserve self-implementation for trivial edits (≤3 lines, 1 file).

Start every session with `trw_session_start()`, save progress with `trw_checkpoint()` after milestones, and close with `trw_deliver()` to persist your work across sessions.

## TRW Behavioral Protocol (Auto-Generated)

| Tool | When | What |
|------|------|------|
| `trw_session_start()` | First action | Load prior learnings + recover active run |
| `trw_learn(summary, detail)` | On discoveries | Persist knowledge for all future agents |
| `trw_checkpoint(message)` | After milestones | Save progress (survives context compaction) |
| `trw_deliver()` | Last action | Persist learnings + sync CLAUDE.md + close session |

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

### Framework Reference

Read `.trw/frameworks/FRAMEWORK.md` at session start — it defines phase gates, exit criteria, quality rubrics, and formation selection. Re-read after context compaction.

### Session Boundaries

Every session that loads learnings via `trw_session_start()` should persist them at session end — this is how your work compounds across sessions instead of being lost.

<!-- trw:end -->

