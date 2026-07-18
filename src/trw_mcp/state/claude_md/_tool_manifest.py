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

# PRD-CORE-218: the authoritative surface is the CORE-218 manifest. We read its
# pure single-source-of-truth membership module (``surface_packs``) directly
# rather than ``server._surface_manifest_registry.eligible_tool_names()``, because
# importing the registry triggers ``server/__init__`` (eager tool registration) —
# an unwanted import-time side effect for this state-layer module. The eligible
# public surface computed here is byte-identical to ``eligible_tool_names()``
# (both = PACK_TOOLS minus OPERATOR_ONLY_TOOLS).
from trw_mcp.models.surface_packs import KERNEL_TOOLS, OPERATOR_ONLY_TOOLS, PACK_TOOLS

_logger = structlog.get_logger(__name__)

#: The full eligible (public) tool surface — what ``all`` mode exposes. Identical
#: to ``server._surface_manifest_registry.eligible_tool_names()``.
_ELIGIBLE_TOOLS: frozenset[str] = frozenset(
    tool for tools in PACK_TOOLS.values() for tool in tools if tool not in OPERATOR_ONLY_TOOLS
)

# ---------------------------------------------------------------------------
# FR01: Canonical tool description mapping (single source of truth)
# ---------------------------------------------------------------------------


class ToolEntry(NamedTuple):
    """A tool name paired with its human-readable description."""

    name: str
    description: str


class PrescriptiveLanguageClassification(NamedTuple):
    """Classification for instruction-surface wording changes."""

    category: str
    rewrite_allowed: bool
    rationale: str


_SAFETY_TERMS: Final[frozenset[str]] = frozenset(
    {
        "must not",
        "never",
        "secret",
        "credential",
        "security",
        "destructive",
        "approval",
        "human review",
    }
)
_PROCESS_TERMS: Final[frozenset[str]] = frozenset(
    {
        "must",
        "shall",
        "required",
        "checkpoint",
        "deliver",
        "build_check",
        "validate",
        "test",
    }
)


def classify_prescriptive_language(text: str) -> PrescriptiveLanguageClassification:
    """Classify instruction language before tone rewrites.

    Safety-critical language is not rewriteable by a tone-only pass; process-
    critical language may be clarified but not weakened; advisory language can
    be softened.
    """
    lowered = text.lower()
    if any(term in lowered for term in _SAFETY_TERMS):
        return PrescriptiveLanguageClassification(
            category="safety-critical",
            rewrite_allowed=False,
            rationale="preserves enforceable safety or approval boundary language",
        )
    if any(term in lowered for term in _PROCESS_TERMS):
        return PrescriptiveLanguageClassification(
            category="process-critical",
            rewrite_allowed=True,
            rationale="may clarify wording but must preserve the required action",
        )
    return PrescriptiveLanguageClassification(
        category="advisory",
        rewrite_allowed=True,
        rationale="tone rewrite may soften non-normative guidance",
    )


TOOL_DESCRIPTIONS: Final[dict[str, str]] = {
    # Core
    "trw_session_start": "Load prior learnings and recover any active run",
    "trw_checkpoint": "Save milestone progress so you can resume after interruptions",
    "trw_learn": "Record durable technical discoveries (no status reports)",
    "trw_deliver": "Persist everything when done (learnings, checkpoint, instruction sync)",
    # Memory
    "trw_recall": "Retrieve relevant learnings for a specific topic",
    "trw_learn_update": "Update an existing learning entry with new detail or status",
    "trw_graph_related": "Traverse a bounded typed neighborhood from one learning",
    # Quality
    "trw_build_check": "Record project-native test/build/static-check results after you run them",
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
    # trw_knowledge_sync removed by PRD-FIX-076 (dead MCP surface).
    "trw_heartbeat": "Refresh run liveness and report whether a checkpoint is due",
    "trw_adopt_run": "Attach this session to an existing run for an explicit handoff or resume",
    "trw_meta_tune_rollback": "Restore a promoted SAFE-001 advisory edit from its recorded pre-edit snapshot",
    # Probe (PRD-CORE-144) — gated OFF by default via TRW_PROBE_ENABLED
    "trw_probe": "Run a bounded sandboxed experiment to resolve a disputed plan assumption (gated by TRW_PROBE_ENABLED)",
    "trw_probe_budget_status": "Report live probe budget usage for a session (read-only)",
    # Phase control (PRD-INTENT-002 FR06) — recommended by the phase-mask denial
    "trw_request_tool_access": "Grant one masked tool call when the current phase has hidden it (phase-exposure override)",
    # Profile (PRD-HPO-PROF-001 FR-11)
    "trw_profile_explain": "Explain how the resolved session profile was layered (read-only introspection)",
    # Intelligence pipeline (PRD-FIX-COMPOUNDING-6 FR02)
    "trw_pipeline_health": "Report intelligence-pipeline health for a project (read-only probe)",
    # Cross-client dispatch (Phase 3) — second-opinion audit by another agent CLI
    "trw_dispatch": "Dispatch a prompt to another coding-agent CLI for a second opinion (background job; poll trw_dispatch_status)",
    "trw_dispatch_status": "Poll a background dispatch job and return its status + redacted result when terminal",
    # Crash-safe delivery operations (PRD-CORE-208)
    "trw_delivery_status": "Read a delivery operation's durable status without mutation",
    "trw_delivery_recover": "Perform capability-bound stale/crash recovery for a delivery operation",
    # Code intelligence + risk (read-only / advisory)
    "trw_skill_discovery": "Discover available TRW skills and their metadata (read-only)",
    "trw_code_search": "Search local code by query and look up symbols",
    "trw_code_symbol": "Look up a symbol definition in the local code index",
    "trw_code_index_update": "Refresh the local SHA-256 code index for fast search",
    "trw_before_edit_hint": "Surface risk hints for a file before you edit it",
    "trw_before_edit_hint_batch": "Surface risk hints for a batch of files before editing",
    "trw_codebase_risk_report": "Report aggregate codebase risk for a repository",
    "trw_entity_risk_map": "Map per-entity risk across a repository",
    "trw_ordering_compare": "Compare candidate build/work ordering strategies",
    "trw_cross_repo_ordering": "Compute cross-repository work ordering",
    # Evidence + coordination
    "trw_agent_work_evidence": "Export AgentWorkEvidence v1 for a run (delivered=wired coordination)",
    "trw_validate_agent_work_evidence": "Validate an AgentWorkEvidence v1 record",
    "trw_prd_diff": "Structural diff between two PRD versions (read-only)",
    "trw_submit_feedback": "Submit feedback to the TRW backend portal (thin client)",
}

# Validate at import time: every eligible (public) manifest tool has a
# description, and no description names a non-eligible tool.
_ALL_TOOLS = set(_ELIGIBLE_TOOLS)
_DESCRIBED_TOOLS = set(TOOL_DESCRIPTIONS)
if _ALL_TOOLS != _DESCRIBED_TOOLS:
    _missing = _ALL_TOOLS - _DESCRIBED_TOOLS
    _extra = _DESCRIBED_TOOLS - _ALL_TOOLS
    raise RuntimeError(f"TOOL_DESCRIPTIONS / eligible-manifest mismatch: missing={_missing}, extra={_extra}")


# ---------------------------------------------------------------------------
# FR01: Resolve effective exposed tools from config
# ---------------------------------------------------------------------------


def resolve_exposed_tools(mode: str = "standard") -> frozenset[str]:
    """Resolve the tool surface an instruction file should describe (PRD-CORE-218).

    Instruction files (CLAUDE.md/AGENTS.md/etc.) project the TASK-INDEPENDENT
    baseline of the CORE-218 resolution authority:

      * ``"all"`` — the full eligible public surface (operator-escape mode).
      * anything else (``"standard"``, the default) — the kernel-only baseline,
        i.e. ``resolve_tool_surface(None, "standard").tools``. A concrete run's
        task packs are resolved per-session at the middleware layer
        (SurfaceAuthorityMiddleware); instruction files stay task-independent so
        they never over-promise tools a given session has masked.

    Args:
        mode: ``tool_resolution_mode`` (``"standard"`` | ``"all"``).

    Returns:
        Immutable set of tool names the instruction surface may describe.
    """
    if mode == "all":
        result = frozenset(_ELIGIBLE_TOOLS)
    else:
        result = frozenset(KERNEL_TOOLS)
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
