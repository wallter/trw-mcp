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

## TRW Behavioral Protocol

→ See [`../docs/documentation/tool-lifecycle.md`](../docs/documentation/tool-lifecycle.md) and [`../docs/documentation/memory-routing.md`](../docs/documentation/memory-routing.md).

