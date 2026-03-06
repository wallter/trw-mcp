# trw-mcp

Engineering memory MCP server for Claude Code -- patterns, gotchas, and project knowledge that persist across sessions.

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: CC BY-NC-SA 4.0](https://img.shields.io/badge/License-CC_BY--NC--SA_4.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc-sa/4.0/)

## Getting Started

See [Developer Quickstart](../docs/TRW_README.md) for installation and first-run instructions.

## What It Does

TRW-MCP is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives Claude Code persistent engineering memory. It records what you learn during development sessions -- patterns, gotchas, architecture decisions -- and recalls relevant knowledge at the start of every new session. Over time, your AI assistant accumulates project-specific expertise instead of starting from scratch.

The server also manages structured run tracking (phases, checkpoints, events), build verification (pytest + mypy), requirements engineering (AARE-F PRDs), and CLAUDE.md auto-generation from high-impact learnings.

## Quick Start

```bash
# Install from source
cd trw-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Deploy TRW to a project (must be a git repo)
trw-mcp init-project /path/to/your/repo

# Or add the MCP server to Claude Code manually
claude mcp add trw -- /path/to/trw-mcp/.venv/bin/trw-mcp --debug
```

### Deploy to a Project

`trw-mcp init-project` bootstraps the full TRW framework in any git repository:

```bash
trw-mcp init-project .              # current directory
trw-mcp init-project /path/to/repo  # specific project
trw-mcp init-project . --force      # overwrite existing files
```

This creates:
- `.trw/` -- learning memory, run state, configuration
- `.mcp.json` -- MCP server connection for Claude Code
- `CLAUDE.md` -- project instructions with TRW ceremony protocol
- `.claude/hooks/` -- ceremony enforcement hooks
- `.claude/skills/` -- workflow automation skills
- `.claude/agents/` -- specialized sub-agents

## Available Tools (11)

| Category | Tool | Purpose |
|----------|------|---------|
| **Ceremony** | `trw_session_start` | Load high-impact learnings + check run status |
| | `trw_deliver` | Batched delivery: reflect, checkpoint, CLAUDE.md sync, index sync |
| **Learning** | `trw_recall` | Search accumulated knowledge by keyword or tag |
| | `trw_learn` | Record a discovery, gotcha, or pattern |
| | `trw_learn_update` | Update or retire existing learnings |
| | `trw_claude_md_sync` | Promote high-impact learnings to CLAUDE.md |
| **Orchestration** | `trw_init` | Bootstrap run directory with `run.yaml` and `events.jsonl` |
| | `trw_status` | Return current run state -- phase, progress, last checkpoint |
| | `trw_checkpoint` | Create atomic state snapshot for interrupt-safe recovery |
| **Requirements** | `trw_prd_create` | Generate an AARE-F compliant PRD from requirements text |
| | `trw_prd_validate` | Validate PRD against quality gates (100-point scale) |
| **Build** | `trw_build_check` | Run pytest + mypy, cache results for phase gate verification |
| **Review** | `trw_review` | Structured code review findings with pass/warn/block verdict |
| **Checkpoint** | `trw_pre_compact_checkpoint` | Safety checkpoint before context compaction |
| **Reporting** | `trw_run_report` | Single-run metrics (events, checkpoints, build status) |
| | `trw_analytics_report` | Cross-run ceremony scores, build pass rates, trends |
| | `trw_usage_report` | LLM token usage and cost estimates by model |

## Configuration

Settings via environment variables (prefix `TRW_`) or `.trw/config.yaml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `TRW_DEBUG` | `false` | Enable debug logging to `.trw/logs/` |
| `TRW_TELEMETRY` | `false` | Detailed per-tool telemetry |
| `TRW_TELEMETRY_ENABLED` | `true` | Tool invocation events (kill switch) |
| `TRW_SOURCE_PACKAGE_NAME` | `trw_mcp` | Python package name for `--cov=` |
| `TRW_SOURCE_PACKAGE_PATH` | `trw-mcp/src` | Source directory for mypy/pytest |
| `TRW_TESTS_RELATIVE_PATH` | `trw-mcp/tests` | Test directory for pytest |
| `TRW_LLM_ENABLED` | `true` | Allow LLM calls via anthropic SDK |
| `TRW_LEARNING_PROMOTION_IMPACT` | `0.7` | Min impact for CLAUDE.md promotion |

See `src/trw_mcp/models/config.py` for the full configuration reference.

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests (2600+ tests, >=80% coverage required)
.venv/bin/python -m pytest tests/ -v --cov=trw_mcp --cov-report=term-missing

# Type checking (strict mode)
.venv/bin/python -m mypy --strict src/trw_mcp/

# Targeted testing during development
.venv/bin/python -m pytest tests/test_tools_learning.py -k "test_recall" -v

# Fast unit tests only
.venv/bin/python -m pytest tests/ -m unit
```

### Optional Dependencies

| Extra | Packages | Purpose |
|-------|----------|---------|
| `[dev]` | pytest, mypy, coverage, etc. | Testing and type checking |
| `[ai]` | anthropic | LLM-augmented features |
| `[otel]` | OpenTelemetry | Distributed tracing (future) |

LLM features require `pip install -e ".[ai]"`. Without it, LLM-augmented tools gracefully degrade to non-LLM fallbacks.

## Architecture

```
src/trw_mcp/
  server.py              # FastMCP entry point, CLI, tool registration
  bootstrap.py           # init-project: deploy TRW to target repos
  scoring.py             # Utility scoring (Q-learning + Ebbinghaus decay)
  models/                # Pydantic v2 models (config, run, learning, etc.)
  tools/                 # MCP tool implementations (10 modules)
  state/                 # State management (28 modules)
  middleware/            # FastMCP middleware (ceremony enforcement)
  data/                  # Bundled hooks, skills, agents for init-project
```

## License

[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) -- see [LICENSE](../LICENSE).
