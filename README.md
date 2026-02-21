# trw-mcp

TRW Framework MCP Server — orchestration, engineering memory, build verification, and requirements tools for Claude Code.

Part of the [TRW (The Real Work) Framework](../README.md) v24.0_TRW.

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
- `.trw/` — learning memory, run state, configuration
- `.mcp.json` — MCP server connection for Claude Code
- `CLAUDE.md` — project instructions with TRW ceremony protocol
- `.claude/hooks/` — 9 ceremony enforcement hooks
- `.claude/skills/` — 10 workflow automation skills
- `.claude/agents/` — 5 specialized sub-agents

After setup, edit `.trw/config.yaml` to set your project's `source_package_name`, `source_package_path`, and `tests_relative_path`.

## Tools (14)

| Category | Tool | Purpose |
|----------|------|---------|
| **Ceremony** | `trw_session_start` | Load high-impact learnings + check run status |
| | `trw_deliver` | Batched delivery: reflect, checkpoint, CLAUDE.md sync, index sync |
| **Learning** | `trw_recall` | Search accumulated knowledge by keyword or tag |
| | `trw_learn` | Record a discovery, gotcha, or pattern to `.trw/learnings/` |
| | `trw_claude_md_sync` | Promote high-impact learnings to CLAUDE.md |
| **Orchestration** | `trw_init` | Bootstrap run directory with `run.yaml` and `events.jsonl` |
| | `trw_status` | Return current run state — phase, progress, last checkpoint |
| | `trw_checkpoint` | Create atomic state snapshot for interrupt-safe recovery |
| **Requirements** | `trw_prd_create` | Generate an AARE-F compliant PRD from requirements text |
| | `trw_prd_validate` | Validate PRD against quality gates (100-point scale) |
| **Build** | `trw_build_check` | Run pytest + mypy, cache results for phase gate verification |
| **Reporting** | `trw_run_report` | Single-run metrics (events, checkpoints, build status) |
| | `trw_analytics_report` | Cross-run ceremony scores, build pass rates, trends |
| | `trw_usage_report` | LLM token usage and cost estimates by model |

## Skills (10)

Skills are user-invocable workflows in `.claude/skills/` — zero tokens until triggered.

| Skill | Phase | Description |
|-------|-------|-------------|
| `/sprint-init` | PLAN | Initialize a sprint: select PRDs, create sprint doc, bootstrap run |
| `/sprint-finish` | DELIVER | Complete a sprint: validate, build gate, archive, deliver |
| `/prd-new` | PLAN | Create an AARE-F PRD from a feature description |
| `/prd-groom` | PLAN | Groom a PRD to sprint-ready quality (>= 0.85 completeness) |
| `/prd-review` | PLAN | Read-only quality review with READY/NEEDS WORK/BLOCK verdict |
| `/deliver` | DELIVER | Pre-flight build check + full delivery ceremony |
| `/commit` | ANY | Convention-enforced git commit with type(scope): msg format |
| `/memory-audit` | ANY | Read-only learning health report |
| `/memory-optimize` | REVIEW | Prune stale learnings, consolidate duplicates |
| `/test-strategy` | IMPLEMENT | Audit test coverage gaps, suggest improvements |
| `/framework-check` | ANY | Check ceremony compliance, phase gate status, run health |

## Agents (5)

Specialized sub-agents in `.claude/agents/` spawned via `Task()`.

| Agent | Model | Purpose |
|-------|-------|---------|
| `code-simplifier` | Sonnet | Simplify code for clarity and maintainability |
| `prd-groomer` | Sonnet | Research and draft PRD sections to sprint-ready quality |
| `requirement-reviewer` | Sonnet | Assess PRD quality with per-dimension scores and verdict |
| `requirement-writer` | Sonnet | Draft EARS-compliant requirements with confidence scores |
| `traceability-checker` | Haiku | Verify bidirectional traceability between PRDs, code, and tests |

## Configuration

Settings via environment variables (prefix `TRW_`) or `.trw/config.yaml`:

| Variable | Default | Description |
|----------|---------|-------------|
| `TRW_DEBUG` | `false` | Enable debug logging to `.trw/logs/` |
| `TRW_TELEMETRY` | `false` | Detailed per-tool telemetry to `.trw/logs/tool-telemetry.jsonl` |
| `TRW_TELEMETRY_ENABLED` | `true` | Tool invocation events in `events.jsonl` (kill switch) |
| `TRW_SOURCE_PACKAGE_NAME` | `trw_mcp` | Python package name for `--cov=` |
| `TRW_SOURCE_PACKAGE_PATH` | `trw-mcp/src` | Source directory for mypy/pytest |
| `TRW_TESTS_RELATIVE_PATH` | `trw-mcp/tests` | Test directory for pytest |
| `TRW_LLM_ENABLED` | `true` | Allow LLM calls via anthropic SDK |
| `TRW_LEARNING_PROMOTION_IMPACT` | `0.7` | Minimum impact score for CLAUDE.md promotion |

See `src/trw_mcp/models/config.py` for the full configuration reference.

### Debug Mode

```bash
trw-mcp --debug                 # CLI flag
TRW_DEBUG=true trw-mcp          # environment variable
```

Writes daily log files to `.trw/logs/trw-mcp-YYYY-MM-DD.jsonl` with structured JSON logging.

## Architecture

```
src/trw_mcp/
  server.py                # FastMCP entry point, CLI, tool registration
  bootstrap.py             # init-project: deploy TRW to target repos
  scoring.py               # Utility scoring (Q-learning + Ebbinghaus decay)
  models/
    config.py              # TRWConfig (pydantic-settings, env vars)
    run.py                 # RunState, ShardCard, WaveManifest
    learning.py            # LearningEntry, LearningStatus
    requirements.py        # PRDFrontmatter, ValidationResult
    build.py               # BuildStatus, BuildResult
  tools/
    ceremony.py            # trw_session_start, trw_deliver
    learning.py            # trw_recall, trw_learn, trw_claude_md_sync
    orchestration.py       # trw_init, trw_status, trw_checkpoint
    requirements.py        # trw_prd_create, trw_prd_validate
    build.py               # trw_build_check
    report.py              # trw_run_report, trw_analytics_report
    usage.py               # trw_usage_report
    telemetry.py           # @log_tool_call decorator
  state/
    persistence.py         # FileStateReader/Writer (atomic YAML/JSONL)
    validation.py          # PRD quality gate validation
    claude_md.py           # CLAUDE.md generation and sync
    recall_search.py       # Learning search and ranking
    reflection.py          # Post-run reflection extraction
    analytics.py           # Learning analytics and metrics
    analytics_report.py    # Cross-run ceremony scoring
    prd_utils.py           # PRD parsing and status management
    index_sync.py          # INDEX.md/ROADMAP.md synchronization
    llm_helpers.py         # LLM call abstractions
    _paths.py              # Path resolution utilities
  middleware/
    ceremony.py            # CeremonyMiddleware (session-start enforcement)
  data/                    # Bundled hooks, skills, agents for init-project
```

## Development

```bash
# 863 tests, 86% coverage, mypy --strict clean
.venv/bin/python -m pytest tests/ -v --cov=trw_mcp --cov-report=term-missing

# Type checking (strict mode)
.venv/bin/python -m mypy --strict src/trw_mcp/

# Targeted testing during development
.venv/bin/python -m pytest tests/test_tools_learning.py -k "test_recall" -v
```

### Optional Dependencies

| Extra | Packages | Purpose |
|-------|----------|---------|
| `[dev]` | pytest, mypy, coverage, etc. | Testing and type checking |
| `[ai]` | anthropic | LLM-augmented features (reflect, groom, prune) |
| `[otel]` | OpenTelemetry | Distributed tracing (future) |

LLM features require `pip install -e ".[ai]"`. Without it, LLM-augmented tools gracefully degrade to non-LLM fallbacks.

## Recommended Workflow

### Full Run (sprints, features)

```
trw_session_start()                    # 1. Load learnings + check run state
  → trw_init(task_name, prd_scope)     # 2. Bootstrap run directory
  → work + trw_checkpoint(message)     # 3. Implement with periodic snapshots
  → trw_learn(summary, detail)         # 4. Record discoveries along the way
  → trw_build_check(scope="full")      # 5. Verify tests + types pass
  → trw_deliver()                      # 6. Reflect, sync CLAUDE.md, close run
```

### Quick Task (no run directory)

```
trw_session_start()                    # 1. Load learnings
  → work                               # 2. Do the task
  → trw_learn(summary, detail)         # 3. Record any discoveries
  → trw_deliver()                      # 4. Sync learnings to CLAUDE.md
```

## MCP Resources

| URI | Description |
|-----|-------------|
| `trw://config` | Current TRWConfig values |
| `trw://framework` | Bundled FRAMEWORK.md text |
| `trw://learnings` | Learning index from `.trw/` |
| `trw://patterns` | Discovered patterns index |
| `trw://run-state` | Current run state (latest `run.yaml`) |

## License

[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) — see [LICENSE](../LICENSE).
