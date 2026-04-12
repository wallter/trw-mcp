"""Instruction-tool manifest: mapping, conditional rendering, and validation.

PRD-CORE-135: Ensures instruction files only describe tools that are actually
exposed. Provides:

- TOOL_DESCRIPTIONS: canonical short description for every trw_* tool
- resolve_exposed_tools: resolve the effective tool set from config
- validate_instruction_manifest: find tool mentions not in the exposed set
- check_instruction_tool_parity: delivery gate R-08 (soft warning)
"""

from __future__ import annotations

import re
from pathlib import Path

import structlog

from trw_mcp.models.config._defaults import TOOL_PRESETS

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FR01: Canonical tool description mapping (single source of truth)
# ---------------------------------------------------------------------------

TOOL_DESCRIPTIONS: dict[str, str] = {
    # Core
    "trw_session_start": "Load prior learnings and recover any active run",
    "trw_checkpoint": "Save milestone progress so you can resume after interruptions",
    "trw_learn": "Record durable technical discoveries (no status reports)",
    "trw_deliver": "Persist everything when done (learnings, checkpoint, instruction sync)",
    # Memory
    "trw_recall": "Retrieve relevant learnings for a specific topic",
    "trw_learn_update": "Update an existing learning entry with new detail or status",
    "trw_knowledge_sync": "Synchronize knowledge store with the latest session data",
    # Quality
    "trw_preflight_log": "Log pre-implementation checklist completion for audit trail",
    "trw_build_check": "Run lint, type-check, and tests to verify your work",
    "trw_review": "Run code review analysis on changed files",
    "trw_prd_create": "Create a new PRD from a template",
    "trw_prd_validate": "Validate PRD structure and completeness",
    # Observability
    "trw_status": "Show current run status and session overview",
    "trw_run_report": "Generate a detailed report for a completed run",
    "trw_usage_report": "Report tool usage statistics across sessions",
    "trw_analytics_report": "Generate analytics dashboard data",
    "trw_quality_dashboard": "Show quality metrics and trend data",
    "trw_ceremony_status": "Show ceremony compliance status for the current session",
    # Admin
    "trw_ceremony_approve": "Approve a ceremony escalation proposal",
    "trw_ceremony_revert": "Revert a ceremony configuration change",
    "trw_trust_level": "View or update the project trust tier",
    "trw_progressive_expand": "Progressively expand tool exposure for a project",
    "trw_pre_compact_checkpoint": "Save checkpoint before context compaction",
    "trw_init": "Initialize TRW in a project directory",
    "trw_claude_md_sync": "Synchronize CLAUDE.md with current TRW configuration",
}

# Compile-time assertion: every tool in the "all" preset has a description
_ALL_TOOLS = set(TOOL_PRESETS["all"])
_DESCRIBED_TOOLS = set(TOOL_DESCRIPTIONS)
assert _ALL_TOOLS == _DESCRIBED_TOOLS, (
    f"TOOL_DESCRIPTIONS / TOOL_PRESETS mismatch: "
    f"missing={_ALL_TOOLS - _DESCRIBED_TOOLS}, "
    f"extra={_DESCRIBED_TOOLS - _ALL_TOOLS}"
)


# ---------------------------------------------------------------------------
# FR01: Resolve effective exposed tools from config
# ---------------------------------------------------------------------------


def resolve_exposed_tools(
    mode: str = "all",
    custom_list: tuple[str, ...] | list[str] = (),
) -> set[str]:
    """Resolve the set of exposed tool names from mode + custom list.

    Args:
        mode: Tool exposure mode (all, core, minimal, standard, custom).
        custom_list: Explicit tool list when mode is "custom".

    Returns:
        Set of tool names that are currently exposed.
    """
    if mode == "custom":
        return set(custom_list)
    preset = TOOL_PRESETS.get(mode)
    if preset is None:
        _logger.warning("unknown_tool_exposure_mode", mode=mode)
        return set(TOOL_PRESETS["all"])
    return set(preset)


# ---------------------------------------------------------------------------
# FR01: Render tool list filtered by exposure
# ---------------------------------------------------------------------------


def render_tool_list(
    exposed_tools: set[str] | None = None,
    *,
    prefix: str = "- ",
    include_backticks: bool = True,
) -> str:
    """Render a markdown list of tool descriptions, filtered by exposure.

    Args:
        exposed_tools: Set of exposed tool names. None means all tools.
        prefix: Line prefix (default: "- " for markdown lists).
        include_backticks: Wrap tool names in backticks.

    Returns:
        Rendered markdown string with one tool per line.
    """
    lines: list[str] = []
    for tool_name, description in TOOL_DESCRIPTIONS.items():
        if exposed_tools is not None and tool_name not in exposed_tools:
            continue
        if include_backticks:
            lines.append(f"{prefix}`{tool_name}()` \u2014 {description}")
        else:
            lines.append(f"{prefix}{tool_name}() \u2014 {description}")
    return "\n".join(lines) + "\n" if lines else ""


# ---------------------------------------------------------------------------
# FR02: Instruction-manifest validator
# ---------------------------------------------------------------------------

# Matches trw_* tool names in running text. Does NOT match inside backtick
# code blocks that are just describing the tool (we want to catch prose
# mentions that promise tool availability).
_TOOL_MENTION_RE = re.compile(r"\btrw_\w+\b")


def validate_instruction_manifest(
    instruction_text: str,
    exposed_tools: set[str],
) -> list[str]:
    """Find trw_* tool mentions in instruction text that are not exposed.

    Args:
        instruction_text: Raw instruction file content.
        exposed_tools: Set of tool names that should be available.

    Returns:
        Sorted list of tool names mentioned but NOT in exposed_tools.
    """
    mentioned = set(_TOOL_MENTION_RE.findall(instruction_text))
    # Only flag names that are actually known tools (ignore trw_dir, etc.)
    known_tools = set(TOOL_DESCRIPTIONS)
    unexpected = (mentioned & known_tools) - exposed_tools
    return sorted(unexpected)


# ---------------------------------------------------------------------------
# FR03: Delivery gate R-08 — instruction-tool parity check
# ---------------------------------------------------------------------------


def check_instruction_tool_parity(
    project_root: Path,
    exposed_tools: set[str],
) -> str | None:
    """Check AGENTS.md for tool mentions not in the exposed set.

    This is delivery gate R-08 (soft warning, not a hard blocker).

    Args:
        project_root: Root directory of the project.
        exposed_tools: Set of currently exposed tool names.

    Returns:
        Warning string if mismatches found, None if clean.
    """
    agents_md = project_root / "AGENTS.md"
    if not agents_md.exists():
        return None

    try:
        content = agents_md.read_text(encoding="utf-8")
    except OSError:
        _logger.warning("instruction_parity_read_error", path=str(agents_md))
        return None

    mismatches = validate_instruction_manifest(content, exposed_tools)
    if not mismatches:
        return None

    warning = (
        f"AGENTS.md mentions {len(mismatches)} unexposed tool(s): "
        f"{', '.join(mismatches)}. "
        "Consider updating instructions or tool exposure config."
    )
    _logger.info(
        "instruction_tool_parity_mismatch",
        mismatches=mismatches,
        count=len(mismatches),
    )
    return warning
