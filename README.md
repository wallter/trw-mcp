# trw-mcp

**Persistent engineering memory for AI coding agents** — an MCP server for cross-session recall, evidence-backed delivery, and spec-driven development. Part of [TRW Framework](https://trwframework.com).

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://python.org)
[![License: BSL 1.1](https://img.shields.io/badge/License-BSL_1.1-orange.svg)](https://trwframework.com/license)
[![MCP](https://img.shields.io/badge/MCP-compatible-green)](https://modelcontextprotocol.io/)
[![Docs](https://img.shields.io/badge/docs-trwframework.com-blue)](https://trwframework.com/docs)

> **Release status:** Alpha and source-available under BSL 1.1. The current
> package is suitable for evaluation and dogfooding, but it does not claim a
> production-stable API or support SLA.

> Coding-agent sessions are usually stateless. TRW keeps project knowledge in `.trw/` and recalls relevant learnings when the next session starts.

**[Quick start](#quick-start)** · **[Core tools](#mcp-tools)** · **[Configuration](#configuration)** · **[Security and network behavior](#telemetry--network-behavior)** · **[Development](#development)**

## How it fits

trw-mcp is the MCP server component of [TRW (The Real Work)](https://trwframework.com) — a methodology layer for AI-assisted development that turns each coding session's discoveries into permanent institutional knowledge. It works alongside [trw-memory](https://github.com/wallter/trw-memory), the standalone memory engine.

- **trw-mcp** (this repo): MCP server with <!-- inv:tools -->46<!-- /inv --> tools, <!-- inv:skills -->28<!-- /inv --> skills, <!-- inv:agents -->12<!-- /inv --> agents
- **[trw-memory](https://github.com/wallter/trw-memory)**: Standalone memory engine with hybrid retrieval, scoring, and lifecycle

## What it does

trw-mcp is a [Model Context Protocol](https://modelcontextprotocol.io/) server that gives AI coding agents **persistent engineering memory**. It records what you learn during development sessions — patterns, gotchas, architecture decisions — and recalls relevant knowledge at the start of every new session. Over time, your AI coding assistant **accumulates captured learnings** in `.trw/` and recalls them at session start. *Whether this yields measurable task-completion lift is an open empirical question; early SWE-bench single-shot measurements (n=40/47) showed null. See the [verification docs](https://trwframework.com/docs/verification) for the current methodology and evidence posture.*

Beyond memory, the server provides:

- **Run lifecycle** — phases, checkpoints, events, resumable state, and delivery records.
- **Verification gates** — project-native build evidence and structured review/delivery checks.
- **Requirements workflows** — [AARE-F PRDs](https://trwframework.com/docs), validation, and requirement-to-code traceability.
- **Client integration** — generated instruction files, hooks, skills, and capability-aware tool exposure for supported coding clients.
- **Code intelligence** — lexical/symbol search, before-edit context, dependency relationships, and risk signals.

**Dogfooding scale**: thousands of tests across hundreds of PRDs, dogfooded across the TRW monorepo (coverage gate enforced at 80%, 90% target for new code). This codebase was built by AI agents using TRW. *Scale proves the framework is usable at volume; whether it improves outcomes vs baseline is measured via the eval bench, not inferred from these counts.*

## Quick Start

Requires Python 3.10+ and a Git repository. The installer supports Claude Code, Codex, Cursor, OpenCode, Copilot, and Antigravity; use `--ide all` when a repository is shared across clients. See the [full quickstart guide](https://trwframework.com/docs/quickstart) for client-specific setup.

```bash
# Recommended: install TRW
curl -fsSL https://trwframework.com/install.sh | bash

# Bootstrap the current repository (client is auto-detected)
cd /path/to/your/repo
trw-mcp init-project .

# Confirm the installation and resolved client surfaces
trw-mcp doctor .
```

### Manual / advanced install

```bash
# Install from PyPI
pip install trw-mcp

# Or install from source
git clone https://github.com/wallter/trw-mcp.git
cd trw-mcp
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### Deploy to a Project

`trw-mcp init-project` bootstraps the full TRW framework in any git repository. Full configuration reference at [trwframework.com/docs/config](https://trwframework.com/docs/config).

```bash
trw-mcp init-project .              # current directory
trw-mcp init-project /path/to/repo  # specific project
trw-mcp init-project . --ide codex  # force Codex bootstrap
trw-mcp init-project . --force      # overwrite existing files
```

Every installation creates `.trw/` plus the Claude-compatible baseline used by the core bootstrap (`.mcp.json`, `CLAUDE.md`, and `.claude/` hooks, skills, and agent definitions). The selected client integration then adds its own instruction, MCP, hook, skill, and agent surfaces where supported. Bundled skills and agent definitions are runtime inputs to `init-project` and `update-project`, not examples that can be discarded. Managed updates preserve user-authored content where the target format supports safe merging; review `--force` before using it in a customized repository.

### Configuration

Settings via environment variables (prefix `TRW_`) or `.trw/config.yaml`. Full reference at [trwframework.com/docs/config](https://trwframework.com/docs/config).

```yaml
# .trw/config.yaml — top settings (all optional, shown with defaults)
embeddings_enabled: true           # Vector search on by default (install the [vectors] extra to use it)
learning_max_entries: 500          # Max learnings before auto-pruning
build_check_enabled: true          # Run pytest+mypy on trw_build_check
deliver_gate_mode: "block_coding"  # Block delivery for coding/rca/eval tasks without a passing build record;
                                   # set to "advisory" to restore warn-only posture (changed 2026-06-10)
observation_masking: true          # Reduce verbosity in long sessions
ceremony_mode: "full"              # "full" or "light"
```

## Telemetry & network behavior

trw-mcp is **local-first**: with the default configuration it persists everything under your project's `.trw/` directory and makes **no outbound network calls** except the optional embedding-model download described below. There is no built-in usage tracking, phone-home, or content upload unless you explicitly enable it.

### What can touch the network, when, and how to turn it off

| Surface | When | Default | Opt-out / control |
|---------|------|---------|-------------------|
| **Embedding model download** | First vector operation downloads `all-MiniLM-L6-v2` from huggingface.co (only when the `[vectors]`/`[embeddings]` extra is installed) | `embeddings_enabled: true` | `TRW_OFFLINE=1` (or `HF_HUB_OFFLINE=1`) suppresses the download and degrades to keyword-only recall; a disclosure log line is emitted before any fetch |
| **Usage telemetry** | Only if explicitly enabled | **off** (gated by `platform_telemetry_enabled`, default `false`) | leave `platform_telemetry_enabled=false`; see PRD-SEC-004 |
| **Learning-content publishing** | Only if explicitly enabled | **off** (gated by `learning_sharing_enabled`, default `false`) | leave `learning_sharing_enabled=false`; learning content is never published off-box by default |

With `TRW_OFFLINE=1` set, `session_start` makes **zero** huggingface.co calls — a testable invariant for air-gapped deployments.

### Environment-variable inventory

| Variable | Purpose | Default |
|----------|---------|---------|
| `TRW_OFFLINE` | Master offline switch — blocks the huggingface.co embedding-model download | unset (online) |
| `HF_HUB_OFFLINE` | Upstream huggingface_hub offline switch — also honored by trw-mcp | unset |
| `TRW_PROBE_ENABLED` | Enables the optional sandboxed `trw_probe` experiment tool | unset (probe disabled) |
| `ENABLE_TOOL_SEARCH` | Force-enable/disable MCP tool-search auto-deferral (`true`/`false`) | auto-detected |
| `TRW_LOG_LEVEL` | Explicit log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`) | derived from `--debug` / defaults |
| `TRW_PLATFORM_API_KEY` | Platform credential (PRD-SEC-005) — read from the environment, kept out of git-tracked config | unset |
| `TRW_CONFIG_STRICT` | Fail **closed** on a malformed `.trw/config.yaml` instead of reverting to defaults | unset (fail-open, but loud) |
| `MEMORY_*` | trw-memory engine knobs (see the [trw-memory README](https://github.com/wallter/trw-memory)) | per-field |

A malformed `.trw/config.yaml` always emits a `WARNING` (and a stderr notice) rather than being silently discarded; set `TRW_CONFIG_STRICT=1` to make the load fail closed so security overrides are never dropped unnoticed.

### Security defaults

| Capability | Default | Notes |
|-----------|---------|-------|
| Field-level encryption | **off** | opt-in via trw-memory `encryption_enabled` |
| Secret redaction in logs | **on** | API keys, tokens, and secret-named fields are masked in log output by default |
| PII detection (memory content) | **warn** | PII (emails, API keys, etc.) is detected and logged but stored as-is by default (`pii_action: warn`); set `pii_action: block` to reject such writes, or `redact` to mask them |
| Recall output filtering | **redact** | SEC-001 recall filter masks flagged values returned by recall (`recall_filter_mode: redact`) |
| Memory poisoning detection | **observe** | detects and records statistical anomalies, does not quarantine, by default |
| Remote sync / publishing | **off** | `learning_sharing_enabled=false`, `platform_telemetry_enabled=false` |
| `.trw/` directory permissions | `0700` | state/secret dirs are owner-only |
| `memory.db` / secret files | `0600` | owner read/write only (consistent with `pins.json`) |

### Enterprise hardening recipe

For an air-gapped or compliance-sensitive deployment:

```bash
export TRW_OFFLINE=1            # no huggingface.co egress; keyword-only recall
export TRW_CONFIG_STRICT=1      # malformed config fails closed, never silently reverts
# Leave telemetry + learning-sharing at their secure defaults:
#   platform_telemetry_enabled: false
#   learning_sharing_enabled:   false
```

Then verify: `.trw/` dirs are `0700`, `memory.db` is `0600`, and no outbound connection is attempted at `session_start`.

<a id="mcp-tools"></a>

## MCP Tools (<!-- inv:tools -->46<!-- /inv -->)

The table below covers the most-used tools out of the full <!-- inv:tools -->46<!-- /inv -->. For the complete, always-current list run `trw-mcp config-reference` or browse the [tool reference docs](https://trwframework.com/docs).

| Category | Tools | Purpose |
|----------|-------|---------|
| **Session** | `session_start`, `init`, `status`, `checkpoint`, `pre_compact_checkpoint`, `heartbeat`, `adopt_run` | Run lifecycle, progress tracking, and pin/liveness management |
| **Learning** | `learn`, `learn_update`, `recall`, `instructions_sync` | Knowledge capture, retrieval, and instruction-file refresh |
| **Quality** | `build_check`, `review`, `deliver` | Verification and delivery |
| **Requirements** | `prd_create`, `prd_validate`, `prd_diff` | [Spec-driven development](https://trwframework.com/docs) with AARE-F PRDs |
| **Code intelligence** | `code_search`, `code_symbol`, `code_index_update`, `before_edit_hint`, `codebase_risk_report`, `entity_risk_map` | Repo-aware search, symbol lookup, and risk signals |
| **Observability** | `query_events`, `surface_diff`, `mcp_security_status` | Event history, surface diffs, and security status |

## Skills (<!-- inv:skills -->28<!-- /inv -->)

Slash-command workflows — zero tokens until triggered. Full skill reference at [trwframework.com/docs](https://trwframework.com/docs).

**Sprint & Delivery**: `/trw-sprint-init` · `/trw-sprint-finish` · `/trw-sprint-team` · `/trw-deliver` · `/trw-commit` · `/trw-reflect`

**Requirements**: `/trw-prd-new` · `/trw-prd-ready` · `/trw-prd-groom` · `/trw-prd-review` · `/trw-exec-plan`

**Quality**: `/trw-audit` · `/trw-self-review` · `/trw-delegate` · `/trw-simplify` · `/trw-dry-check` · `/trw-security-check` · `/trw-test-strategy`

**Framework**: `/trw-framework-check` · `/trw-project-health` · `/trw-memory-audit` · `/trw-memory-optimize`

## Agents (<!-- inv:agents -->12<!-- /inv -->)

Optional specialized agent definitions for clients and harnesses that support delegation. TRW does not require multi-agent execution; the same lifecycle works sequentially.

| Role | Agent | Purpose |
|------|-------|---------|
| **Core Team** | trw-lead, trw-implementer, trw-tester, trw-researcher, trw-reviewer, trw-auditor, trw-adversarial-auditor | Orchestration, TDD, testing, research, review, audit, spec-vs-code audit |
| **Requirements** | trw-prd-groomer, trw-requirement-writer, trw-requirement-reviewer | PRD lifecycle specialists |
| **Quality** | trw-traceability-checker, trw-code-simplifier | Traceability and code health |

## The 6-Phase Model

TRW implements a structured execution lifecycle: **RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER** with phase gates, build checks, adversarial audits, and delivery ceremony. See [FRAMEWORK.md](FRAMEWORK.md) for the full specification, or read the [lifecycle overview at trwframework.com/docs/lifecycle](https://trwframework.com/docs/lifecycle).

## CLI Commands

```bash
trw-mcp init-project .                # Deploy TRW to a project
trw-mcp update-project .              # Update existing installation
trw-mcp doctor .                      # Diagnose environment and client setup
trw-mcp check-instructions .          # Validate instruction-tool parity (exit 1 on mismatch)
trw-mcp audit .                       # Audit TRW configuration
trw-mcp config-reference              # Print all TRW_ environment variables
trw-mcp version-status                # Compare package, framework, and live-server versions
trw-mcp export --format json          # Export learnings
trw-mcp uninstall .                   # Remove TRW from a project
```

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
  bootstrap/          # init-project: deploy TRW to target repos
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
