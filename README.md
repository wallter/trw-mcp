# trw-mcp

TRW Framework MCP Server — orchestration, requirements engineering, and self-learning tools for Claude Code.

Part of the [TRW (The Real Work) Framework](../FRAMEWORK.md) v17.1_TRW.

## Quick Start

```bash
# Install (from source with dev tools)
cd trw-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Optional: LLM-augmented features (requires Claude Agent SDK)
pip install -e ".[ai]"
```

### Configure in Claude Code

Add to your project's `.mcp.json`:

```json
{
  "mcpServers": {
    "trw": {
      "command": "/path/to/trw-mcp/.venv/bin/trw-mcp",
      "args": ["--debug"]
    }
  }
}
```

Or use the Claude CLI:

```bash
claude mcp add trw -- /path/to/trw-mcp/.venv/bin/trw-mcp --debug
```

## Tools (17)

### Orchestration (7)

| Tool | Purpose |
|------|---------|
| `trw_init` | Bootstrap `.trw/`, run directories, `run.yaml`, `events.jsonl` |
| `trw_status` | Return current run state — phase, wave progress, confidence |
| `trw_phase_check` | Validate exit criteria before advancing phases |
| `trw_wave_validate` | Post-wave output contract validation |
| `trw_resume` | Classify shard state on session reconnect |
| `trw_checkpoint` | Create atomic state snapshot for interrupt-safe recovery |
| `trw_event` | Log structured event to `events.jsonl` audit trail |

### Self-Learning (7)

| Tool | Purpose |
|------|---------|
| `trw_reflect` | Extract learnings from events (LLM-augmented when available) |
| `trw_learn` | Record a discovery to `.trw/learnings/` |
| `trw_learn_update` | Update learning status, impact, tags (lifecycle management) |
| `trw_learn_prune` | Review and retire stale learnings (LLM-augmented) |
| `trw_recall` | Search accumulated knowledge before starting tasks |
| `trw_script_save` | Save reusable scripts to `.trw/scripts/` |
| `trw_claude_md_sync` | Promote active high-impact learnings to CLAUDE.md |

### AARE-F Requirements (3)

| Tool | Purpose |
|------|---------|
| `trw_prd_create` | Generate AARE-F compliant PRD from requirements text |
| `trw_prd_validate` | Validate PRD against quality gates |
| `trw_traceability_check` | Verify requirement-to-implementation-to-test coverage |

## Debug Mode

Enable file logging and DEBUG-level output:

```bash
# Via CLI flag
trw-mcp --debug

# Via environment variable
TRW_DEBUG=true trw-mcp
```

Debug mode writes daily log files to `.trw/logs/trw-mcp-YYYY-MM-DD.jsonl` and enables DEBUG-level structured JSON logging on stderr.

## Configuration

All settings are configurable via environment variables (prefix `TRW_`) or `.trw/config.yaml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `TRW_DEBUG` | `false` | Enable debug logging |
| `TRW_LLM_ENABLED` | `true` | Allow LLM calls when SDK available |
| `TRW_LLM_DEFAULT_MODEL` | `haiku` | Default model for LLM features |
| `TRW_PARALLELISM_MAX` | `10` | Max concurrent shards |
| `TRW_TIMEBOX_HOURS` | `8` | Default task timebox |
| `TRW_LEARNING_MAX_ENTRIES` | `500` | Max learning entries before pruning |
| `TRW_LEARNING_PROMOTION_IMPACT` | `0.7` | Min impact for CLAUDE.md promotion |

See `src/trw_mcp/models/config.py` for the full configuration reference.

## Architecture

```
src/trw_mcp/
  server.py              # FastMCP entry point, CLI, logging setup
  models/
    config.py            # TRWConfig (pydantic-settings, env vars)
    run.py               # RunState, ShardCard, WaveManifest, etc.
    learning.py          # LearningEntry, LearningStatus, Pattern, etc.
    requirements.py      # PRDFrontmatter, ValidationResult, etc.
  tools/
    orchestration.py     # 7 orchestration tools
    learning.py          # 7 self-learning tools
    requirements.py      # 3 AARE-F requirements tools
  clients/
    llm.py               # LLMClient — Claude Agent SDK abstraction
  state/
    persistence.py       # FileStateReader/Writer (YAML/JSONL)
    validation.py        # Phase checks, contract validation
  resources/             # MCP resources (config, learnings, run state)
  prompts/               # MCP prompts (AARE-F templates)
  exceptions.py          # Custom exception hierarchy
```

## Development

```bash
# Run tests with coverage
.venv/bin/python -m pytest tests/ -v --cov=trw_mcp --cov-report=term-missing

# Type checking (strict mode)
.venv/bin/python -m mypy --strict src/trw_mcp/

# Current: 168 tests, 88% coverage, mypy --strict clean
```

### Optional Dependencies

| Extra | Package | Purpose |
|-------|---------|---------|
| `[dev]` | pytest, mypy, etc. | Testing and type checking |
| `[ai]` | `claude-agent-sdk` | LLM-augmented tool features |
| `[otel]` | OpenTelemetry | Distributed tracing (future) |

## Recommended Workflow

```
1. trw_init(task, objective)     # Bootstrap project
2. trw_recall(query)             # Check prior knowledge
3. trw_event() / trw_checkpoint()  # Audit trail during work
4. trw_reflect()                 # Extract learnings after sessions
5. trw_learn() / trw_learn_update()  # Record and manage discoveries
6. trw_learn_prune()             # Retire stale learnings
7. trw_claude_md_sync()          # Promote to CLAUDE.md at delivery
```

## Known Issues

See `.trw/learnings/` for accumulated project knowledge, or use `trw_recall(query)`.

Open PRDs in `docs/requirements-aare-f/prds/`:
- **PRD-FIX-002**: `trw_learn_prune` age heuristic ineffective for new projects
- **PRD-FIX-005**: `trw_status` stale version warning

## License

MIT
