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

- `server/` — FastMCP entry point, boot path, middleware chain (MCP security, ceremony, progressive disclosure, observation masking, response optimizer), CLI subcommands
- `tools/` — MCP tool implementations: 32 defined, 24 registered in the default boot path (`server/_tools.py::_register_tools`); `report.py`/`usage.py`/`ceremony_feedback.py` tools are defined-but-not-registered
- `state/` — State management (persistence uses `YAML(typ="safe")` for all reads): phases, ceremony, nudge engine, memory adapter onto `trw-memory`, claude_md instruction-file generation, analytics, pin isolation
- `security/`, `meta_tune/` — MCP-server trust boundary (signed registry, capability scope, anomaly detector) and self-modification safety gates (sandbox, promotion gate, eval-gaming detector, hash-chained audit, rollback)
- `scoring/` — utility-based learning scoring, Q-learning + outcome correlation, Ebbinghaus decay, adaptive ceremony depth, proximal-reward detection, CLEAR 5-dim scorer
- `bootstrap/`, `client_profiles/` — `init-project`/`update-project` multi-host installer (8 client profiles) with smart-merge marker sections + version migration
- `middleware/` — Observation masking (`ContextBudgetMiddleware`), ceremony enforcement
- `telemetry/` — Constants (inlined from trw-shared), pipeline, sender
- `data/` — Bundled agents, skills, hooks for `init-project`
- `agents/` — Per-client capability-tier resolver (PRD-INFRA-104). Translates the framework's
  tier vocabulary (`frontier|balanced|local-large|local-small`) into the concrete model
  identifiers each client harness accepts. `bootstrap/_init_project_skills.py::_install_agents`
  applies it on every Claude Code install; `scripts/sync-agents.py` applies it for the dev
  repo's `.claude/agents/`. New client adapters add an entry to `_CLIENT_MAPS` in
  `agents/tier_resolver.py`.

## Security Notes

- All YAML reads use `YAML(typ="safe")` — round-trip loader only for writes
- Shell hooks use `_json_escape()` for all interpolated values
- `sqlite-vec` is optional (`[vectors]` extra) — degrades gracefully when absent

## TRW Behavioral Protocol

→ See [`../docs/documentation/tool-lifecycle.md`](../docs/documentation/tool-lifecycle.md) and [`../docs/documentation/memory-routing.md`](../docs/documentation/memory-routing.md).
<!-- TRW AUTO-GENERATED — do not edit between markers -->
<!-- trw:start -->

Your primary role is **orchestration** — delegate to focused agents when a task benefits from its own context window. Focused subagents get deeper context per task than the parent session can hold; subagent results return with tighter scope and less distraction. Reserve self-implementation for trivial edits (≤3 lines, 1 file).

**Your first action in every session must be `trw_session_start()`.**

This single call loads everything you need: 0 learnings from 6 prior sessions, any active run state you can resume, and the full operational protocol (delegation guidance, phase gates, quality rubrics). Without it, you start from zero — with it, you start from the team’s accumulated experience.

After `trw_session_start()`, save progress with `trw_checkpoint()` after milestones, and close with `trw_deliver()` so your discoveries persist for future agents.

## TRW Behavioral Protocol (Auto-Generated)

| Tool | When | Why |
|------|------|-----|
| `trw_session_start()`<br><sub>e.g. `trw_session_start(query='task domain')`</sub> | First action — loads prior learnings + recovers active run state | Start from accumulated knowledge instead of zero — prior agents already found gotchas for your area |
| `trw_learn(summary, detail)`<br><sub>e.g. `trw_learn(summary='...', impact=0.8)`</sub> | On errors, discoveries, or gotchas | Saves your finding so no future agent repeats your mistake — this is how institutional knowledge grows |
| `trw_checkpoint(message)`<br><sub>e.g. `trw_checkpoint(message='...')`</sub> | After milestones — preserves progress across context compactions | Your resume point if context compacts — uncheckpointed work is permanently lost |
| `trw_deliver()` | Last action — persists everything in one call | Without this, your session's learnings are invisible to future agents — they start from scratch |

Full tool lifecycle: `/trw-ceremony-guide`

### Memory Routing

Default to `trw_learn()` for knowledge. Use native auto-memory only for personal preferences.

| | `trw_learn()` | Native auto-memory |
|---|---|---|
| Search | `trw_recall(query)` — semantic + keyword | Filename scan only |
| Visibility | All sessions and configured helpers | Primary session only |
| Lifecycle | Impact-scored, recalled at session start | Static until manually edited |
| Scale | 0 learnings across 6 sessions, auto-pruned by staleness | 200-line index cap |

Gotcha or error pattern → `trw_learn()`. User’s preferred commit style → native memory. Build trick that saves time → `trw_learn()`. Communication preference → native memory.

### Session Boundaries

Every session that loads learnings via `trw_session_start()` should persist them at session end — this is how your work compounds across sessions instead of being lost.

### Troubleshooting

If MCP tools fail with 'fetch failed', use the local CLI fallback:
- `trw-mcp local init --task NAME` to create a run directory
- `trw-mcp local checkpoint --message MSG` to save progress

<!-- trw:end -->

