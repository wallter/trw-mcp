<!-- last-verified: 2026-03-04 -->
# TRW Framework — Developer Quickstart & Usage Guide

> **Version**: <!-- inv:framework_version -->v24.6_TRW<!-- /inv --> | **MCP Tools**: <!-- inv:tools -->25<!-- /inv --> | **Skills**: <!-- inv:skills -->24<!-- /inv --> | **Agents**: <!-- inv:agents -->12<!-- /inv --> | **Hooks**: <!-- inv:hooks -->13<!-- /inv --> | **Python**: 3.10+

This is the hands-on guide for using TRW (The Real Work) in your projects. It covers installation, first-run setup, daily usage, configuration, and observability.

See also: [TRW Comprehensive Guide](TRW-COMPREHENSIVE-GUIDE.md) for in-depth architecture and design decisions.

---

## Table of Contents

1. [What Is TRW?](#1-what-is-trw)
2. [Quick Start](#2-quick-start)
3. [Installation](#3-installation)
4. [Post-Install: First Session](#4-post-install-first-session)
5. [Daily Usage](#5-daily-usage)
6. [Configuration](#6-configuration)
7. [Observability & Telemetry](#7-observability--telemetry)
8. [MCP Tool Reference](#8-mcp-tool-reference)
9. [Skills Reference](#9-skills-reference)
10. [Hooks](#10-hooks)
11. [The Self-Learning System](#11-the-self-learning-system)
12. [Requirements Engineering (AARE-F)](#12-requirements-engineering-aare-f)
13. [Troubleshooting](#13-troubleshooting)
14. [Next Steps](#14-next-steps)

---

## 1. What Is TRW?

TRW is a **prompt-based operational framework** that gives Claude Code structured workflows, persistent memory, and quality gates. It transforms AI coding agents from stateless executors into a self-improving engineering system.

| Capability | How |
|-----------|-----|
| **Memory** | A persistent knowledge store (`.trw/`) that survives across sessions, with Q-learning scoring |
| **Structure** | A six-phase workflow (RESEARCH → DELIVER) that prevents agents from shipping untested code |
| **Self-Improvement** | Discoveries become learnings, get scored, and promote into agent context automatically |
| **Quality Gates** | Requirements validation (AARE-F), build verification, and behavioral ceremony enforcement |

TRW consists of three layers:

1. **FRAMEWORK.md** — A ~617-line orchestration blueprint injected into agent context at session start
2. **trw-mcp** — A Python MCP server exposing 25 tools that Claude Code calls for run management, learning, build verification, and delivery
3. **.trw/** — A directory of YAML/JSONL files persisting learnings, configuration, and run state across sessions

---

## 2. Quick Start

### Prerequisites

| Requirement | Minimum | Recommended | Install |
|-------------|---------|-------------|---------|
| **Python** | 3.10 | 3.11+ | `brew install python` (macOS) / `sudo apt install python3` (Ubuntu/Debian/WSL2) |
| **pip** | 22.0+ | latest | `python -m pip install --upgrade pip` |
| **Claude Code CLI** | latest | latest | `npm install -g @anthropic-ai/claude-code` |
| **git** | 2.30+ | latest | `brew install git` (macOS) / `sudo apt install git` (Ubuntu/Debian/WSL2) |

Verify your prerequisites:

```bash
python3 --version   # 3.10+
pip --version        # 22.0+
claude --version     # any recent version
git --version        # 2.30+
```

### Fast Path (simplest — no install needed)

Add TRW to your MCP config for instant setup. Edit `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "trw": {
      "command": "uvx",
      "args": ["trw-mcp"]
    }
  }
}
```

Then start Claude Code in any git repository and TRW is active.

### Team Path (recommended — per-project)

```bash
# 1. Install the package
pip install trw-mcp

# 2. Bootstrap TRW in your project
cd /path/to/your/project
trw-mcp init-project .

# 3. Start Claude Code — TRW is now active
claude
```

That's it. Claude Code detects the MCP server via `.mcp.json`, loads all 25 tools, and the session-start hook fires automatically.

---

## 3. Installation

### Monorepo Package Overview

| Package | Stack | What It Does |
|---------|-------|-------------|
| `trw-mcp/` | Python, FastMCP, Pydantic v2 | MCP server -- 25 tools, 6 resources, 24 skills, 18 agents |
| `trw-memory/` | Python, SQLite, sqlite-vec | Standalone memory engine -- hybrid retrieval, knowledge graph |
| `backend/` | FastAPI, SQLAlchemy, Alembic | Platform API -- 17 routers, JWT auth, rate limiting |
| `platform/` | Next.js 15, TypeScript, Tailwind | Frontend -- admin dashboard, marketing site, docs |

### Option A: From source (recommended -- you're on the same machine)

```bash
cd /path/to/trw-framework/trw-mcp
pip install -e ".[dev]"
```

The `-e` (editable) install means changes to the framework source take effect immediately without reinstalling. Use this when TRW and your target project are on the same machine.

### Option B: pip install (when distributing)

```bash
pip install trw-mcp
```

### Bootstrap your project

```bash
cd /path/to/your/project    # Must be a git repo
trw-mcp init-project .
```

This creates the following structure in your project:

```
your-project/
├── .mcp.json                  # MCP server connection (auto-detected by Claude Code)
├── CLAUDE.md                  # Project instructions with TRW ceremony protocol
├── .trw/
│   ├── config.yaml            # Framework configuration (edit this)
│   ├── frameworks/
│   │   └── FRAMEWORK.md       # Orchestration blueprint
│   ├── learnings/
│   │   ├── index.yaml         # Learning index
│   │   └── entries/           # Individual learning files
│   ├── context/
│   │   └── behavioral_protocol.yaml
│   └── templates/
├── .claude/
│   ├── settings.json          # Hook registrations
│   ├── hooks/                 # 11 hook scripts
│   │   ├── session-start.sh
│   │   ├── pre-compact.sh
│   │   ├── post-tool-event.sh
│   │   ├── stop-ceremony.sh
│   │   └── ...
│   ├── skills/                # 21 workflow skills
│   │   ├── deliver/
│   │   ├── sprint-init/
│   │   ├── prd-new/
│   │   ├── project-health/
│   │   ├── learn/
│   │   └── ...
│   └── agents/                # 18 specialized sub-agents
│       ├── trw-implementer.md
│       ├── trw-reviewer.md
│       ├── trw-tester.md
│       ├── trw-researcher.md
│       ├── prd-groomer.md
│       ├── code-simplifier.md
│       └── ...
```

### Verify installation

```bash
which trw-mcp              # Should print the installed path
ls .trw/                   # Should show config.yaml, frameworks/, learnings/, etc.
ls .claude/hooks/          # Should show 11 hook scripts
```

### Verification Checklist

Before your first session, confirm these four items:

- [ ] `which trw-mcp` prints an installed path (not "not found")
- [ ] `.trw/config.yaml` exists in your project root
- [ ] `.mcp.json` exists and contains a `trw` entry with `"command": "trw-mcp"` (portable, no absolute paths)
- [ ] `.claude/hooks/session-start.sh` exists and is executable (`chmod +x .claude/hooks/*.sh`)

If any check fails, re-run `trw-mcp init-project .` in your project root.

### Update an existing project

When TRW gets new hooks, skills, agents, or framework improvements, update a project without losing its configuration:

```bash
trw-mcp update-project /path/to/your/project
```

This selectively updates framework-managed files while preserving user customizations:

| Updated (always) | Preserved (never overwritten) |
|---|---|
| hooks, skills, agents | `.trw/config.yaml` |
| FRAMEWORK.md, behavioral_protocol.yaml | `.trw/learnings/` |
| settings.json, claude_md template | `.mcp.json` |
| CLAUDE.md (TRW section only) | CLAUDE.md (user sections) |

Stale artifacts (renamed/removed hooks, skills, agents) are automatically cleaned up.

---

## 4. Post-Install: First Session

### Step 1: Configure for your project

Edit `.trw/config.yaml` — the critical fields for a new project:

```yaml
# .trw/config.yaml
task_root: docs                        # Where run directories are created
source_package_name: your_package      # For pytest --cov= (your Python package name)
source_package_path: src               # Where your source code lives
tests_relative_path: tests             # Where your tests live
debug: false                           # Set true for verbose logging
```

**If you skip this**, `trw_build_check` will fail because the defaults point to `trw-mcp/src` (the framework's own source).

### Step 2: Edit CLAUDE.md

Replace the placeholder sections in the generated `CLAUDE.md`:

```markdown
# CLAUDE.md

## What This Is
[Describe your project — what it does, what language/framework]

## Build & Test Commands
[Your actual build and test commands]

## Project Conventions
[Your coding conventions, commit format, etc.]

<!-- TRW auto-generated section below — don't edit between markers -->
```

The section between `<!-- trw:start -->` and `<!-- trw:end -->` is auto-managed by `trw_claude_md_sync`.

### Step 3: Start Claude Code

```bash
claude
```

The session-start hook fires automatically and displays:

```
TRW PROTOCOL — tools that help you build effectively:
- trw_session_start(): loads learnings + recovers active run
- trw_checkpoint(): saves progress so you resume after compaction
- trw_learn(): records discoveries for future sessions
- trw_deliver(): persists everything in one call when done

SESSION START: Call trw_session_start() to load your learnings and any active run state.
```

Claude Code will call `trw_session_start()` and begin working with the full TRW lifecycle.

---

## 5. Daily Usage

### Quick Task (bug fix, small change)

No run directory needed. The agent:

```
trw_session_start()        → loads prior learnings
work                       → fix the bug, make the change
trw_learn(...)             → record any discoveries (optional)
trw_deliver()              → sync learnings to CLAUDE.md
```

### Full Run (feature, refactor, multi-file change)

```
trw_session_start()        → loads learnings + checks for active runs
trw_init(task_name="...")  → creates run directory with events.jsonl
  RESEARCH                 → explore codebase, gather context
  PLAN                     → design approach, optionally create PRD
  IMPLEMENT                → write code + trw_checkpoint() at milestones
  VALIDATE                 → trw_build_check(scope="full"), verify integration
  REVIEW                   → review diff for quality, fix gaps, trw_learn()
  DELIVER                  → trw_deliver()
```

### Resuming after interruption

If a session is interrupted (context compaction, timeout, crash):

```
trw_session_start()        → auto-detects the active run
trw_status()               → shows phase, last checkpoint, events logged
continue from checkpoint   → no need to re-plan or restart
```

The `pre-compact.sh` hook saves run state before compaction, and the `session-start.sh` hook recovers it on resume with the exact run path, phase, and last checkpoint message.

### Prompting an instance for autonomous work

For sprints and planned features, give the instance a single prompt:

```
Implement PRD-CORE-031 (Observability & Telemetry Pipeline).

Read the PRD at docs/requirements-aare-f/prds/PRD-CORE-031.md.
Follow the TRW lifecycle: plan → implement → validate → deliver.
```

The agent handles the full cycle. Your only intervention point is plan approval (if you're using plan mode).

---

## 6. Configuration

### `.trw/config.yaml`

All fields have defaults. Override only what you need.

```yaml
# === Project paths (REQUIRED for non-TRW projects) ===
task_root: docs                        # Base dir for run auto-detection
source_package_name: my_package        # Python package name (for --cov=)
source_package_path: src               # Source directory
tests_relative_path: tests             # Test directory
prds_relative_path: docs/prds          # PRD catalogue location

# === Behavioral tuning ===
parallelism_max: 10                    # Max concurrent shards
timebox_hours: 8                       # Session time budget
recall_max_results: 25                 # Max learnings per recall
build_check_coverage_min: 85.0         # Test coverage floor
claude_md_max_lines: 300               # Max CLAUDE.md auto-section length
learning_promotion_impact: 0.7         # Min impact for CLAUDE.md promotion

# === Observability ===
debug: false                           # Debug-level logging to .trw/logs/
telemetry: false                       # Detailed per-tool telemetry records
telemetry_enabled: true                # Tool invocation events (kill switch)
```

### Configuration precedence

Environment variables (`TRW_*`) > `.trw/config.yaml` > built-in defaults.

Every field in config.yaml can be overridden via environment variable with the `TRW_` prefix:

```bash
TRW_DEBUG=true TRW_TELEMETRY=true claude    # Enable all observability
```

### `.mcp.json`

Generated by `init-project`. Points Claude Code at the MCP server:

```json
{
  "mcpServers": {
    "trw": {
      "command": "trw-mcp",
      "args": ["--debug"]
    }
  }
}
```

Remove `"--debug"` for quieter operation. If using a virtualenv, replace `"trw-mcp"` with the full path (e.g., `"/home/user/.venv/bin/trw-mcp"`).

---

## 7. Observability & Telemetry

TRW captures telemetry at three levels. All data stays local in `.trw/`.

### Level 1: Event log (always on)

Every run writes to `{run-dir}/meta/events.jsonl`:

```jsonl
{"ts": "2026-02-21T00:21:33Z", "event": "run_init", "task": "add-auth"}
{"ts": "...", "event": "checkpoint", "message": "middleware complete"}
{"ts": "...", "event": "file_modified", "tool": "Edit", "file": "src/auth.py"}
{"ts": "...", "event": "session_start", "learnings_recalled": 12}
{"ts": "...", "event": "build_check_complete", "tests_passed": true}
```

When no run is active, events go to `.trw/context/session-events.jsonl`.

### Level 2: Tool invocation events (default on)

When `telemetry_enabled: true` (the default), the `@log_tool_call` decorator logs every MCP tool call to `events.jsonl`:

```jsonl
{"ts": "...", "event": "tool_invocation", "tool": "trw_checkpoint", "duration_ms": 45, "success": true}
```

Disable with `TRW_TELEMETRY_ENABLED=false`.

### Level 3: Detailed telemetry (opt-in)

When `telemetry: true`, per-call records are written to `.trw/logs/tool-telemetry.jsonl`:

```jsonl
{"tool": "trw_learn", "args_hash": "a3f7b2c1", "duration_ms": 120, "result_summary": "{'learning_id': 'L-...", "success": true}
```

Enable with `TRW_TELEMETRY=true`.

### Querying telemetry

Use MCP tools to query collected data:

```python
# Cross-run ceremony scores, build pass rates, trends
trw_analytics_report(since="2026-02-01")

# Single run deep-dive
trw_run_report(run_path="docs/my-task/runs/20260221T.../")

# LLM token usage and cost estimates
trw_usage_report()
```

### Ceremony scoring

`trw_analytics_report` computes a 0-100 ceremony score per run:

| Component | Points |
|-----------|--------|
| `session_start` event present | 30 |
| `reflection_complete` event | 30 |
| At least 1 checkpoint | 20 |
| Any learning recorded | 10 |
| Build check completed | 10 |

The aggregate report includes `avg_ceremony_score`, `build_pass_rate`, `avg_learnings_per_run`, and a `ceremony_trend` time series.

### Debug logging

```bash
trw-mcp --debug              # or TRW_DEBUG=true
```

Writes structured JSON logs to `.trw/logs/trw-mcp-YYYY-MM-DD.jsonl`.

---

## 8. MCP Tool Reference

25 tools organized into 9 categories.

### Session Lifecycle (3)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_session_start` | Every session start | Loads high-impact learnings + checks for active runs |
| `trw_deliver` | Task completion | Combined: reflect + checkpoint + CLAUDE.md sync + index sync |
| `trw_pre_compact_checkpoint` | Before compaction | Creates safety checkpoint before context window compresses |

### Engineering Memory (5)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_recall` | Before new work | Searches learnings by query, ranked by utility score |
| `trw_learn` | On discovery/error | Records a learning entry with summary, detail, impact, tags |
| `trw_learn_update` | Maintaining knowledge | Updates, resolves, or retires existing learning entries |
| `trw_claude_md_sync` | At delivery | Promotes high-impact learnings (>=0.7) to CLAUDE.md |
| `trw_knowledge_sync` | Periodic | Auto-generates topic documents from tag clusters in the learning store |

### Run Orchestration (3)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_init` | New tasks | Creates run directory with `run.yaml`, `events.jsonl` |
| `trw_status` | Anytime | Shows current phase, run state, last checkpoint |
| `trw_checkpoint` | Every milestone | Atomic state snapshot — preserves progress across compactions |

### Requirements (2)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_prd_create` | Requirements definition | Generates AARE-F compliant PRD from description |
| `trw_prd_validate` | Pre-implementation | Validates PRD against quality gates (100-point scale) |

### Build Verification (1)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_build_check` | Before delivery | Runs pytest + mypy, caches results |

### Code Review (2)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_preflight_log` | Before implementation/audit | Records preflight checklist and self-review evidence for a run |
| `trw_review` | After validation | Produces structured review findings with pass/warn/block verdict |

### Reporting (3)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_run_report` | After a run | Single-run metrics (events, checkpoints, build status) |
| `trw_analytics_report` | Anytime | Cross-run ceremony scores, trends, build pass rates |
| `trw_usage_report` | Anytime | LLM token usage and cost estimates by model |

### Observability (2)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_quality_dashboard` | Anytime | Summarizes readiness, quality, and delivery signals across recent runs |
| `trw_ceremony_status` | Anytime | Returns a compact live ceremony status line and nudge context |

### Administration (4)

| Tool | When | What It Does |
|------|------|-------------|
| `trw_ceremony_approve` | Human oversight | Records explicit ceremony approval for blocked transitions |
| `trw_ceremony_revert` | Recovery | Reverts a run to an earlier phase with rationale and event logging |
| `trw_trust_level` | Governance | Reports or updates trust-level state used by ceremony policies |
| `trw_progressive_expand` | Tool UX | Expands deferred tool descriptions when the client asks for more detail |

---

## 9. Skills Reference

TRW includes 21 workflow skills triggered with `/skill-name` in Claude Code. Zero tokens until triggered. Key skills:

| Skill | Phase | Description |
|-------|-------|-------------|
| `/sprint-init` | PLAN | Initialize a sprint: select PRDs, create sprint doc, bootstrap run |
| `/sprint-finish` | DELIVER | Complete a sprint: validate, build gate, archive, deliver |
| `/prd-new` | PLAN | Create an AARE-F PRD from a feature description |
| `/prd-groom` | PLAN | Groom a PRD to sprint-ready quality (>= 0.85 completeness) |
| `/prd-review` | PLAN | Read-only quality review with READY/NEEDS WORK/BLOCK verdict |
| `/deliver` | DELIVER | Pre-flight build check + full delivery ceremony |
| `/commit` | ANY | Convention-enforced git commit with type(scope): msg format |
| `/learn` | ANY | Record a critical learning (quality-gated, dedup-checked) |
| `/memory-audit` | ANY | Read-only learning health report |
| `/project-health` | ANY | TRW health audit: tool usage, ceremony compliance, hook rates, issues |
| `/review-pr` | REVIEW | Structured code review with rubric scoring |
| `/test-strategy` | IMPLEMENT | Audit test coverage gaps, suggest improvements |
| `/framework-check` | ANY | Check ceremony compliance and run health |

---

## 10. Hooks

Claude Code hooks automate TRW ceremony enforcement. They fire on session events without consuming tokens.

### Installed hooks

| Hook | Event | Purpose |
|------|-------|---------|
| `session-start.sh` | `SessionStart` | Displays ceremony protocol, recovers state after compaction |
| `pre-compact.sh` | `PreCompact` | Saves run path, phase, and last checkpoint before context compression |
| `post-tool-event.sh` | `PostToolUse` | Logs `file_modified` events after Write/Edit tool use |
| `stop-ceremony.sh` | `Stop` | Blocks session exit until `trw_deliver()` is called (max 2 blocks) |
| `session-end.sh` | `SessionEnd` | Final cleanup |
| `subagent-start.sh` | `SubagentStart` | Injects ceremony protocol + phase-specific self-review guidance |
| `validate-prd-write.sh` | `PostToolUse` | Validates PRD writes against quality gates |
| `task-completed-ceremony.sh` | `TaskCompleted` | Soft gate: logs completion, warns about pending ceremony |
| `lib-trw.sh` | (shared library) | Utility functions used by all hooks |

### How compaction recovery works

1. `pre-compact.sh` saves `{run_path, phase, events_logged, last_checkpoint}` to `.trw/context/pre_compact_state.json`
2. Context gets compressed
3. `session-start.sh` detects `source: "compact"`, reads the saved state, and displays:
   ```
   CONTEXT COMPACTED — your progress is safe.
   RECOVERED: Run at docs/my-task/runs/20260221T.../
   RECOVERED: Phase: implement | Events: 47
   LAST CHECKPOINT: "auth middleware complete"
   CONTINUE: Call trw_session_start(query='your task domain') to reload learnings and active run state.
   After session_start, call trw_status() if you need the current run snapshot.
   ```

---

## 11. The Self-Learning System

Every session produces knowledge that makes the next session better.

```
RECALL → WORK → LEARN → REFLECT → SYNC → (next session starts smarter)
```

### Learning entries

Each learning is a YAML file in `.trw/learnings/entries/`:

```yaml
id: L-abc12345
summary: "Pydantic v2 use_enum_values=True required for YAML round-trip"
detail: "Without this setting, enum fields serialize as enum members..."
tags: ["pydantic", "gotcha", "serialization"]
impact: 0.85
status: active       # active | resolved | obsolete
source_type: agent   # agent | human
```

### Impact scoring

Learnings are ranked by a composite utility score:
- **Q-learning**: Tracks whether recalling a learning correlated with successful outcomes
- **Ebbinghaus decay**: Knowledge that isn't accessed fades in relevance over time
- **Manual impact**: Author-assigned importance (0.0-1.0)

Learnings with impact >= 0.7 are promoted to `CLAUDE.md` by `trw_claude_md_sync`.

### What to record

| Trigger | Example | Impact |
|---------|---------|--------|
| API gotcha | "structlog reserves 'event' as a keyword" | 0.7-0.9 |
| Workaround after >2 retries | "Must cast dict values to str for mypy --strict" | 0.8 |
| Pattern that works well | "3-wave audit pattern with 14 shards" | 0.8-0.9 |
| Environment issue | "WSL2 ENOENT on rapid file creation — retry works" | 0.6-0.7 |
| Architecture decision | "Layered imports: tools → state → models only" | 0.9 |

---

## 12. Requirements Engineering (AARE-F)

TRW includes AARE-F (AI-Augmented Requirements Engineering Framework) for formal requirements management.

### PRD lifecycle

```
DRAFT → REVIEW → APPROVED → IN_PROGRESS → DONE
                                ↓
                            DEPRECATED
```

### Creating and validating PRDs

```python
# Create a PRD from a description
trw_prd_create(input_text="Add JWT auth with refresh tokens", category="CORE", priority="P1")

# Validate against quality gates
trw_prd_validate(prd_path="docs/requirements-aare-f/prds/PRD-CORE-032.md")
```

Or use skills:
- `/prd-new "Add rate limiting to the API"` — creates and validates
- `/prd-groom` — fills in sections to reach sprint-ready quality (>= 0.85)
- `/prd-review` — read-only assessment with READY/NEEDS WORK/BLOCK verdict

---

## 13. Troubleshooting

### "MCP tools not available"

1. Check `.mcp.json` exists in your project root
2. Verify the command is on PATH: `which trw-mcp`
3. Restart the MCP server: type `/mcp` in Claude Code
4. Check debug logs: `.trw/logs/trw-mcp-*.jsonl`

### "trw_build_check fails with path not found"

Your `.trw/config.yaml` is missing the project-specific paths. Set `source_package_path`, `source_package_name`, and `tests_relative_path`.

### "trw_session_start returns no learnings"

Normal for a new project. Learnings accumulate over sessions. After a few sessions with `trw_learn` calls, recall will return relevant entries.

### "CeremonyMiddleware warnings on every tool response"

Call `trw_session_start()`. The middleware prepends warnings until this tool is called — this is intentional enforcement.

### "Session interrupted, work lost"

Work is rarely lost with TRW. Start a new session — `trw_session_start()` detects the active run, `trw_status()` shows your last checkpoint. Continue from there.

### "Hooks not firing"

Check `.claude/settings.json` has the hook registrations. Verify hook scripts are executable: `chmod +x .claude/hooks/*.sh`.

---

## 14. Next Steps

- [TRW Comprehensive Guide](TRW-COMPREHENSIVE-GUIDE.md) -- deep-dive into architecture, orchestration patterns, and design decisions
- [Platform Documentation](https://trwframework.com/docs) -- web-based docs with interactive examples
- [Platform Quickstart](https://trwframework.com/docs/quickstart) -- browser-friendly version of this guide
