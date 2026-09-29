"""Bootstrap configuration and instruction templates."""

from __future__ import annotations


def _default_config(
    *,
    runs_root: str = ".trw/runs",
    target_platforms: list[str] | None = None,
) -> str:
    """Generate default ``.trw/config.yaml``.

    Args:
        runs_root: Base directory for run artifacts (relative to project root).
        target_platforms: Platforms to sync instruction files for.
            e.g. ``["claude-code", "opencode"]``. Defaults to ``["claude-code"]``.
    """
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
        # No framework_version line: the installed package supplies it, and a pin written here would freeze
        # the project at this canon through every later upgrade (M1 dry run, 2026-09-26).
    ]

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
