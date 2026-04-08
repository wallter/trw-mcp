# trw-mcp

**MCP server for AI coding agents** — persistent engineering memory, knowledge compounding, and spec-driven development workflows. Part of [TRW Framework](https://trwframework.com).

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL_1.1-orange.svg)](https://trwframework.com/license)
[![MCP](https://img.shields.io/badge/MCP-compatible-green)](https://modelcontextprotocol.io/)
[![Docs](https://img.shields.io/badge/docs-trwframework.com-blue)](https://trwframework.com/docs)

> Every AI coding tool resets to zero. TRW is the one that doesn't.

## Part of TRW Framework

trw-mcp is the MCP server component of [TRW (The Real Work)](https://trwframework.com) — a methodology layer for AI-assisted development that turns each coding session's discoveries into permanent institutional knowledge. It works alongside [trw-memory](https://github.com/wallter/trw-memory), the standalone memory engine.

- **trw-mcp** (this repo): MCP server with <!-- inv:tools -->24<!-- /inv --> tools, <!-- inv:skills -->24<!-- /inv --> skills, <!-- inv:agents -->12<!-- /inv --> agents
- **[trw-memory](https://github.com/wallter/trw-memory)**: Standalone memory engine with hybrid retrieval, scoring, and lifecycle

## What It Does

trw-mcp is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI coding agents **persistent engineering memory**. It records what you learn during development sessions — patterns, gotchas, architecture decisions — and recalls relevant knowledge at the start of every new session. Over time, your AI coding assistant accumulates project-specific expertise instead of starting from scratch every time.

The server also manages structured run tracking (phases, checkpoints, events), build verification (pytest + mypy), [spec-driven development](https://trwframework.com/docs) with AARE-F PRDs, and CLAUDE.md auto-generation from high-impact learnings.

**[Knowledge compounding](https://trwframework.com/docs) in practice**: 225 PRDs, 64+ sprints, 8,000+ tests, 91% coverage (trw-memory). The dogfooding is the proof — this codebase was built by AI agents using TRW.

## Quick Start

See the [full quickstart guide](https://trwframework.com/docs/quickstart) for Claude Code, Cursor, opencode, and Codex setup.

```bash
# Install from PyPI
pip install trw-mcp

# Or install from source
git clone https://github.com/wallter/trw-mcp.git
cd trw-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Deploy TRW to a project (must be a git repo)
trw-mcp init-project /path/to/your/repo

# Or add the MCP server to Claude Code manually
claude mcp add trw -- trw-mcp --debug
```

### Deploy to a Project

`trw-mcp init-project` bootstraps the full TRW framework in any git repository. Full configuration reference at [trwframework.com/docs/configuration](https://trwframework.com/docs/configuration).

```bash
trw-mcp init-project .              # current directory
trw-mcp init-project /path/to/repo  # specific project
trw-mcp init-project . --ide codex  # force Codex bootstrap
trw-mcp init-project . --force      # overwrite existing files
```

This creates:
- `.trw/` — learning memory, run state, configuration
- `.mcp.json` — MCP server connection for Claude Code
- `CLAUDE.md` — project instructions with TRW ceremony protocol
- `.claude/hooks/` — ceremony enforcement hooks
- `.claude/skills/` — workflow automation skills
- `.claude/agents/` — specialized sub-agents

### Configuration

Settings via environment variables (prefix `TRW_`) or `.trw/config.yaml`. Full reference at [trwframework.com/docs/configuration](https://trwframework.com/docs/configuration).

```yaml
# .trw/config.yaml — top settings (all optional, shown with defaults)
embeddings_enabled: false          # Enable vector search (requires [vectors] extra)
learning_max_entries: 5000         # Max learnings before auto-pruning
build_check_enabled: true          # Run pytest+mypy on trw_build_check
observation_masking: true          # Reduce verbosity in long sessions
progressive_disclosure: false      # Show tools progressively
ceremony_mode: "full"              # "full", "light", or "off"
```

## MCP Tools (24)

24 tools covering the full AI coding assistant memory lifecycle. See [tool reference docs](https://trwframework.com/docs) for detailed parameter documentation.

| Category | Tools | Purpose |
|----------|-------|---------|
| **Session** | `session_start`, `init`, `status`, `checkpoint`, `pre_compact_checkpoint`, `progressive_expand` | Run lifecycle and progress tracking |
| **Learning** | `learn`, `learn_update`, `recall`, `knowledge_sync`, `claude_md_sync` | Knowledge capture and retrieval |
| **Quality** | `build_check`, `review`, `trust_level`, `quality_dashboard`, `deliver` | Verification and delivery |
| **Requirements** | `prd_create`, `prd_validate` | [Spec-driven development](https://trwframework.com/docs) with AARE-F PRDs |
| **Ceremony** | `ceremony_status`, `ceremony_approve`, `ceremony_revert` | Workflow compliance |
| **Reporting** | `run_report`, `analytics_report`, `usage_report` | Metrics and cost tracking |

## Skills (24)

Slash-command workflows — zero tokens until triggered. Full skill reference at [trwframework.com/docs](https://trwframework.com/docs).

**Sprint & Delivery**: `/trw-sprint-init` · `/trw-deliver` · `/trw-commit`

**Requirements**: `/trw-prd-new` · `/trw-prd-ready` · `/trw-prd-groom` · `/trw-prd-review` · `/trw-exec-plan`

**Quality**: `/trw-audit` · `/trw-review-pr` · `/trw-simplify` · `/trw-dry-check` · `/trw-security-check` · `/trw-test-strategy`

**Framework**: `/trw-framework-check` · `/trw-project-health` · `/trw-memory-audit` · `/trw-memory-optimize`

## Agents (18)

Specialized sub-agents for Agent Teams — parallel execution with coordinated handoffs:

| Role | Agent | Purpose |
|------|-------|---------|
| **Core Team** | trw-lead, trw-implementer, trw-tester, trw-researcher, trw-reviewer, trw-adversarial-auditor | Orchestration, TDD, testing, research, review, spec-vs-code audit |
| **Requirements** | trw-prd-groomer, trw-requirement-writer, trw-requirement-reviewer | PRD lifecycle specialists |
| **Quality** | trw-traceability-checker, trw-code-simplifier | Traceability and code health |

## The 6-Phase Model

TRW implements a structured execution lifecycle: **RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER** with phase gates, build checks, adversarial audits, and delivery ceremony. See [FRAMEWORK.md](FRAMEWORK.md) for the full specification, or read the [framework overview at trwframework.com/docs/framework](https://trwframework.com/docs/framework).

## Configuration

Settings via environment variables (prefix `TRW_`) or `.trw/config.yaml`. Full configuration reference at [trwframework.com/docs/configuration](https://trwframework.com/docs/configuration).

| Variable | Default | Description |
|----------|---------|-------------|
| `TRW_DEBUG` | `false` | Enable debug logging to `.trw/logs/` |
| `TRW_TELEMETRY_ENABLED` | `true` | Tool invocation events (kill switch) |
| `TRW_SOURCE_PACKAGE_NAME` | auto | Python package name for `--cov=` |
| `TRW_LLM_ENABLED` | `true` | Allow LLM calls via anthropic SDK |
| `TRW_LEARNING_PROMOTION_IMPACT` | `0.7` | Min impact for CLAUDE.md promotion |
| `TRW_OBSERVATION_MASKING` | `true` | Reduce verbosity in long sessions |

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v --cov=trw_mcp --cov-report=term-missing

# Type checking (strict mode)
mypy --strict src/trw_mcp/

# Targeted testing during development
pytest tests/test_tools_learning.py -k "test_recall" -v
```

## Architecture

```
src/trw_mcp/
  server/             # FastMCP entry point, middleware chain
  bootstrap.py        # init-project: deploy TRW to target repos
  models/             # Pydantic v2 models (config, run, learning, etc.)
  tools/              # MCP tool implementations
  state/              # State management (persistence, validation, analytics)
  middleware/         # FastMCP middleware (ceremony, observation masking, response optimizer)
  telemetry/          # Telemetry pipeline (models, sender, anonymizer)
  data/               # Bundled hooks, skills, agents for init-project
```

## Troubleshooting

**MCP connection error: "[Errno 2] No such file or directory"**
The MCP server process crashed. In Claude Code, type `/mcp` to reconnect. For other clients, restart your CLI tool.

**`trw_session_start()` returns "No learnings found"**
This is normal on first use — learnings accumulate as you work. Call `trw_learn()` to save discoveries, then `trw_deliver()` to persist them.

**stale `.trw/` state after upgrading**
Run `trw-mcp update-project .` to migrate your project state to the latest schema. If issues persist, backup and re-initialize with `trw-mcp init-project . --force`.

**Embeddings not working despite `embeddings_enabled=true`**
Embeddings require the `[vectors]` extra: `pip install 'trw-mcp[vectors]'`. Without it, vector search silently degrades to keyword-only.

### Debugging

Enable debug logging:

```bash
trw-mcp --debug serve              # Debug mode with file logging
TRW_LOG_LEVEL=DEBUG trw-mcp serve  # Via environment variable
```

Logs are written to `.trw/logs/trw-mcp-YYYY-MM-DD.jsonl`.

## License

[Business Source License 1.1](https://trwframework.com/license) — source-available, free for non-competing use. Converts to Apache 2.0 on 2030-03-21. See the [full license terms](https://trwframework.com/license).

---

Built by [Tyler Wall](http://tylerrwall.com) · [TRW Framework](https://trwframework.com) · [Documentation](https://trwframework.com/docs) · [License](https://trwframework.com/license)
