"""Bootstrap configuration and instruction templates."""

from __future__ import annotations


def _default_config(
    *,
    source_package: str = "",
    test_path: str = "",
    runs_root: str = ".trw/runs",
    target_platforms: list[str] | None = None,
) -> str:
    """Generate default ``.trw/config.yaml``.

    Args:
        source_package: If set, adds ``source_package_name`` field.
        test_path: If set, adds ``tests_relative_path`` field.
        runs_root: Base directory for run artifacts (relative to project root).
        target_platforms: Platforms to sync instruction files for.
            e.g. ``["claude-code", "opencode"]``. Defaults to ``["claude-code"]``.
    """
    from trw_mcp.models.config import get_config

    config = get_config()
    platforms = target_platforms or ["claude-code"]
    lines = [
        "# TRW Framework Configuration",
        "# See trw://config resource for all available fields.",
        "task_root: docs",
        "",
        "# Where run artifacts (events, checkpoints, reports) are stored.",
        "# Each trw_init creates: {runs_root}/{task_name}/{run_id}/",
        f"runs_root: {runs_root}",
        "",
        "# Verbose logging. THE toggle for log level, for every client alike —",
        "# no client's generated MCP config passes --debug. Set to true for",
        "# pre-release / local framework development: DEBUG-level events plus a",
        "# .trw/logs/trw-mcp-<date>.jsonl file sink. Env overrides, highest first:",
        "# TRW_LOG_LEVEL, then TRW_DEBUG.",
        "debug: false",
        "claude_md_max_lines: 500",
        f"framework_version: {config.framework_version}",
    ]
    if source_package:
        lines.append(f"source_package_name: {source_package}")
    if test_path:
        lines.append(f"tests_relative_path: {test_path}")

    # Target platforms -- controls which instruction files are written
    # (client instruction file, AGENTS.md, .cursorrules, etc.) during deliver/sync.
    # Supported: claude-code, opencode, cursor, codex, copilot, antigravity-cli
    lines.append("")
    lines.append("# Target platforms for instruction file sync")
    lines.append("target_platforms:")
    lines.extend(f'  - "{p}"' for p in platforms)

    lines.extend(
        [
            "",
            "# Platform telemetry — set platform_api_key to enable",
            "# platform_urls:",
            '#   - "https://api.trwframework.com"',
            "# platform_api_key: ''",
            "# platform_telemetry_enabled: true",
        ]
    )
    return "\n".join(lines) + "\n"


def _minimal_review_md() -> str:
    """Generate initial ``REVIEW.md`` for Anthropic's agentic reviewer.

    Returns the same template used by ``generate_review_md()`` in
    ``state/claude_md/_sync.py`` but with no learnings injected (fresh install).
    """
    from trw_mcp.state.claude_md._review_md import _REVIEW_TEMPLATE

    return _REVIEW_TEMPLATE.replace(
        "{learning_entries}",
        "<!-- No qualifying learnings (impact >= 0.7) found -->",
    )


def _minimal_claude_md() -> str:
    """Generate a minimal Claude-compatible instruction file with TRW protocol.

    The deliver-gate statement is pulled from the shared renderer rather than
    written out here. This template is a hand-maintained copy of the protocol,
    and the copy had drifted: it omitted the gate entirely, so a freshly
    installed claude-code project had no deliver-gate statement in its
    instruction surface — inline before externalization, and in the
    ``.trw/INSTRUCTIONS.md`` sidecar after it, since the sidecar is built from
    this block. `trw-mcp doctor` reported the FAIL correctly; nothing had
    reconciled the two.
    """
    from trw_mcp.state.claude_md.sections._tool_lifecycle import render_deliver_gate_statement

    return (
        _MINIMAL_CLAUDE_MD_TEMPLATE.replace(
            _GATE_ANCHOR,
            f"{render_deliver_gate_statement().strip()}\n\n{_GATE_ANCHOR}",
            1,
        )
        if _GATE_ANCHOR in _MINIMAL_CLAUDE_MD_TEMPLATE
        else _MINIMAL_CLAUDE_MD_TEMPLATE
    )


# The gate statement is inserted immediately above this heading.
_GATE_ANCHOR = "### Framework Reference"

_MINIMAL_CLAUDE_MD_TEMPLATE = """\
# Project Instructions

This file provides guidance to AI coding clients when working with code in this repository.

## What This Is

{Describe your project here}

## Build & Test Commands

```bash
# Add your project's build and test commands here
```

## Project Conventions

{Add project-specific conventions here}

<!-- TRW AUTO-GENERATED — do not edit between markers -->
<!-- trw:start -->

TRW tools help you build effectively and preserve your work across sessions:
- **Start**: call `trw_session_start()` to load prior learnings and recover any active run
- **Start**: read `.trw/frameworks/FRAMEWORK-CORE.md` — it defines the methodology your tools implement
- **Finish**: call `trw_deliver()` to persist your learnings for future sessions

### Framework Reference

**Read `.trw/frameworks/FRAMEWORK-CORE.md` at session start** — it defines the methodology your tools implement.

The framework covers: 6-phase execution model with exit criteria per phase, formation selection for parallel work, quality gates with rubric scoring, phase reversion rules, adaptive planning, anti-skip safeguards, and portable coordination protocol. Re-read after context compaction and at phase transitions. Without it, tools work but methodology is missing — you'll pass tool checks while skipping the process that prevents rework.

## TRW Behavioral Protocol (Auto-Generated)

- `trw_session_start()` loads your prior learnings and recovers any active run — call it first so you have full context before writing code
- `trw_status()` shows your current phase, completed work, and next steps — call it when resuming so you pick up where you left off instead of redoing work
- `trw_init(task_name)` creates your run directory and event log — call it for new tasks so checkpoints and progress tracking work
- `trw_checkpoint(message)` saves your implementation progress — call it after each milestone so you can resume here if context compacts, instead of re-implementing from scratch
- `trw_learn(summary, detail)` records discoveries for all future sessions — call it when you hit errors or find gotchas so no agent repeats your mistakes
- `trw_instructions_sync()` refreshes the client instruction file (CLAUDE.md / AGENTS.md / etc.) — call it at delivery so the next session starts with the latest protocol
- For quick tasks without a run: `trw_recall()` gives you relevant prior learnings at the start, `trw_learn()` saves new ones for next time

## TRW Ceremony Tools (Auto-Generated)

### Execution Phases

```
RESEARCH → PLAN → IMPLEMENT → VALIDATE → REVIEW → DELIVER
```

### Tool Lifecycle

| Phase | Tool | When to Use |
|-------|------|-------------|
| Start | `trw_session_start` | At session start — loads learnings + run state |
| Start | `trw_recall` | Quick tasks — retrieves relevant prior learnings |
| Start | `trw_status` | When resuming — shows phase, progress, next steps |
| RESEARCH | `trw_init` | New tasks — creates run directory for tracking |
| Any | `trw_learn` | On errors/discoveries — saves for future sessions |
| Any | `trw_checkpoint` | After milestones — preserves progress across compactions |
| VALIDATE | `trw_build_check` | Before delivery — records project-native validation results |
| DELIVER | `trw_instructions_sync` | At delivery — refreshes the client instruction file |
| DELIVER | `trw_deliver` | At task completion — persists everything in one call |

### Example Flows

**Quick Task** (no run needed):
```
trw_session_start -> work -> trw_learn (if discovery) -> trw_deliver()
```

**Full Run**:
```
trw_session_start -> trw_init(task_name, prd_scope)
  -> work + trw_checkpoint (periodic) + trw_learn (discoveries)
  -> trw_build_check(tests_passed=<bool>, test_count=<n>, failure_count=<n>, static_checks_clean=<bool|null>, scope='<exact command>')
  -> trw_deliver()
```

<!-- trw:end -->
"""
