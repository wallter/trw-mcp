# trw-mcp

MCP server for AI coding agents — part of [TRW Framework](https://trwframework.com).

**Public repo**: [github.com/wallter/trw-mcp](https://github.com/wallter/trw-mcp) | **PyPI**: `pip install trw-mcp`

## Build & Test

```bash
pip install -e ".[dev]"                # Dev install
pytest tests/test_specific_file.py -v  # Single file (preferred)
pytest tests/ -m unit                  # Unit tests only
mypy --strict src/trw_mcp/             # Type check
ruff check src/                        # Lint
```

## Key Architecture

- `server/` — FastMCP entry, boot path, middleware chain, CLI subcommands
- `tools/` — MCP tool implementations (registered in `server/_tools.py::_register_tools`)
- `state/` — phases, ceremony, nudge engine, `trw-memory` adapter, claude_md generation, pin isolation (`YAML(typ="safe")` reads)
- `security/`, `meta_tune/` — MCP trust boundary + self-modification safety gates
- `scoring/` — utility scoring, Q-learning + outcome correlation, decay, adaptive ceremony, CLEAR scorer
- `bootstrap/`, `client_profiles/`, `agents/` — multi-host installer (8 profiles) + capability-tier resolver (`frontier|balanced|local-large|local-small`)
- `middleware/`, `telemetry/`, `data/` — observation masking + ceremony; telemetry pipeline; bundled agents/skills/hooks

## Security Notes

- All YAML reads use `YAML(typ="safe")`; round-trip loader only for writes
- Shell hooks use `_json_escape()` for interpolated values
- `sqlite-vec` is optional (`[vectors]`) — degrades gracefully when absent

## Releasing

CI-driven on a `v*` tag push (Trusted Publishing via OIDC); no manual upload/token. Release `trw-memory` first; tag the subtree-split commit. Full runbook: [`../docs/deployment/CLAUDE.md`](../docs/deployment/CLAUDE.md).

## TRW Behavioral Protocol

→ See [`../docs/documentation/tool-lifecycle.md`](../docs/documentation/tool-lifecycle.md) and [`../docs/documentation/memory-routing.md`](../docs/documentation/memory-routing.md).
