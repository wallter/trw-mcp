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
from typing import Final, NamedTuple

import structlog

from trw_mcp.models.config._defaults import TOOL_PRESETS

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# FR01: Canonical tool description mapping (single source of truth)
# ---------------------------------------------------------------------------


class ToolEntry(NamedTuple):
    """A tool name paired with its human-readable description."""

    name: str
    description: str


TOOL_DESCRIPTIONS: Final[dict[str, str]] = {
    # Core
    "trw_session_start": "Load prior learnings and recover any active run",
    "trw_checkpoint": "Save milestone progress so you can resume after interruptions",
    "trw_learn": "Record durable technical discoveries (no status reports)",
    "trw_deliver": "Persist everything when done (learnings, checkpoint, instruction sync)",
    # Memory
    "trw_recall": "Retrieve relevant learnings for a specific topic",
    "trw_learn_update": "Update an existing learning entry with new detail or status",
    # Quality
    "trw_build_check": "Run lint, type-check, and tests to verify your work",
    "trw_review": "Run code review analysis on changed files",
    "trw_prd_create": "Create a new PRD from a template",
    "trw_prd_validate": "Validate PRD structure and completeness",
    # Observability
    "trw_status": "Show current run status and session overview",
    "trw_query_events": "Merged cross-emitter view of HPO telemetry events for a session (FR-7)",
    "trw_surface_diff": "Structured diff between two surface snapshots (FR-8)",
    "trw_surface_classify": "Classify a repository path as SAFE-001 control or advisory surface",
    "trw_mcp_security_status": "Report MCP-security observability counters and shadow-clock state (FR-5)",
    # Admin
    "trw_pre_compact_checkpoint": "Save checkpoint before context compaction",
    "trw_init": "Initialize TRW in a project directory",
    "trw_instructions_sync": "Synchronize the client instruction file (CLAUDE.md/AGENTS.md/etc.) with current TRW configuration",
    "trw_claude_md_sync": "Deprecated alias for trw_instructions_sync — use the canonical name",
    "trw_knowledge_sync": "Generate knowledge topic docs from clustered learnings",
    "trw_heartbeat": "Refresh run liveness and report whether a checkpoint is due",
    "trw_adopt_run": "Attach this session to an existing run for an explicit handoff or resume",
    "trw_meta_tune_rollback": "Restore a promoted SAFE-001 advisory edit from its recorded pre-edit snapshot",
}

# Validate at import time: every tool in the "all" preset has a description
_ALL_TOOLS = set(TOOL_PRESETS["all"])
_DESCRIBED_TOOLS = set(TOOL_DESCRIPTIONS)
if _ALL_TOOLS != _DESCRIBED_TOOLS:
    _missing = _ALL_TOOLS - _DESCRIBED_TOOLS
    _extra = _DESCRIBED_TOOLS - _ALL_TOOLS
    raise RuntimeError(f"TOOL_DESCRIPTIONS / TOOL_PRESETS mismatch: missing={_missing}, extra={_extra}")


# ---------------------------------------------------------------------------
# FR01: Resolve effective exposed tools from config
# ---------------------------------------------------------------------------


def resolve_exposed_tools(
    mode: str = "all",
    custom_list: tuple[str, ...] | list[str] = (),
) -> frozenset[str]:
    """Resolve the set of exposed tool names from mode + custom list.

    Args:
        mode: Tool exposure mode (all, core, minimal, standard, custom).
        custom_list: Explicit tool list when mode is "custom".

    Returns:
        Immutable set of tool names that are currently exposed.
    """
    if mode == "custom":
        result = frozenset(custom_list)
        _logger.debug("resolved_exposed_tools", mode=mode, count=len(result))
        return result
    preset = TOOL_PRESETS.get(mode)
    if preset is None:
        _logger.warning("unknown_tool_exposure_mode", mode=mode, fallback="all")
        return frozenset(TOOL_PRESETS["all"])
    result = frozenset(preset)
    _logger.debug("resolved_exposed_tools", mode=mode, count=len(result))
    return result


# ---------------------------------------------------------------------------
# FR01: Render tool list filtered by exposure
# ---------------------------------------------------------------------------


def render_tool_list(
    exposed_tools: frozenset[str] | set[str] | None = None,
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
    entries = [
        ToolEntry(name=name, description=desc)
        for name, desc in TOOL_DESCRIPTIONS.items()
        if exposed_tools is None or name in exposed_tools
    ]
    lines: list[str] = []
    for entry in entries:
        if include_backticks:
            lines.append(f"{prefix}`{entry.name}()` \u2014 {entry.description}")
        else:
            lines.append(f"{prefix}{entry.name}() \u2014 {entry.description}")
    return "\n".join(lines) + "\n" if lines else ""


# ---------------------------------------------------------------------------
# FR02: Instruction-manifest validator
# ---------------------------------------------------------------------------

# Matches trw_* tool names in running text. Does NOT match inside backtick
# code blocks that are just describing the tool (we want to catch prose
# mentions that promise tool availability).
_TOOL_MENTION_RE: Final[re.Pattern[str]] = re.compile(r"\btrw_\w+\b")

# Known non-tool trw_* identifiers that should never be flagged.
_KNOWN_NON_TOOLS: Final[frozenset[str]] = frozenset(TOOL_DESCRIPTIONS.keys())


def validate_instruction_manifest(
    instruction_text: str,
    exposed_tools: frozenset[str] | set[str],
) -> list[str]:
    """Find trw_* tool mentions in instruction text that are not exposed.

    Args:
        instruction_text: Raw instruction file content.
        exposed_tools: Set of tool names that should be available.

    Returns:
        Sorted list of tool names mentioned but NOT in exposed_tools.
    """
    mentioned = set(_TOOL_MENTION_RE.findall(instruction_text))
    # Only flag names that are actually known tools (ignore trw_dir, trw_config, etc.)
    unexpected = (mentioned & _KNOWN_NON_TOOLS) - set(exposed_tools)
    return sorted(unexpected)


# ---------------------------------------------------------------------------
# FR03: Delivery gate R-08 — instruction-tool parity check
# ---------------------------------------------------------------------------


def check_instruction_tool_parity(
    project_root: Path,
    exposed_tools: frozenset[str] | set[str],
) -> str | None:
    """Check AGENTS.md for tool mentions not in the exposed set.

    This is delivery gate R-08 (soft warning, not a hard blocker).
    Fail-open: returns None on any read error so delivery is not blocked.

    Args:
        project_root: Root directory of the project.
        exposed_tools: Set of currently exposed tool names.

    Returns:
        Warning string if mismatches found, None if clean.
    """
    agents_md = project_root / "AGENTS.md"
    if not agents_md.exists():
        _logger.debug("instruction_parity_skip", reason="no_agents_md", path=str(agents_md))
        return None

    try:
        content = agents_md.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        _logger.warning("instruction_parity_read_error", path=str(agents_md))
        return None

    mismatches = validate_instruction_manifest(content, exposed_tools)
    if not mismatches:
        _logger.info(
            "instruction_tool_parity_clean",
            exposed_count=len(exposed_tools),
            path=str(agents_md),
        )
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
        exposed_count=len(exposed_tools),
    )
    return warning
