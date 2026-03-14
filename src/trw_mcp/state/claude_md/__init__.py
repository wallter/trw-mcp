"""CLAUDE.md rendering and sync — template loading, section generation, marker-based merge.

This package decomposes the monolithic claude_md module into focused submodules:
- ``_templates``: Data-driven section builders (learnings, patterns, adherence) and constants
- ``_static_sections``: Static content renderers (protocol, ceremony, delegation, watchlist)
- ``_promotion``: Learning promotion logic and data collection
- ``_parser``: Marker parsing, template loading, merge logic
- ``_sync``: Sync orchestration (the main entry point)

All public symbols are re-exported here for backward compatibility.
"""

from pathlib import Path  # noqa: F401 — re-exported for test backward compat

from trw_mcp.state._paths import (
    resolve_project_root,
    resolve_trw_dir,
)
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    _truncate_with_markers,
    load_claude_md_template,
    merge_trw_section,
    render_template,
)
from trw_mcp.state.claude_md._promotion import (
    collect_context_data,
    collect_patterns,
    collect_promotable_learnings,
)
from trw_mcp.state.claude_md._sync import execute_claude_md_sync
from trw_mcp.state.claude_md._static_sections import (
    render_agent_teams_protocol,
    render_behavioral_protocol,
    render_ceremony_flows,
    render_ceremony_quick_ref,
    render_ceremony_table,
    render_closing_reminder,
    render_delegation_protocol,
    render_imperative_opener,
    render_phase_descriptions,
    render_rationalization_watchlist,
)
from trw_mcp.state.claude_md._templates import (
    BEHAVIORAL_PROTOCOL_CAP,
    CEREMONY_TOOLS,
    CLAUDEMD_LEARNING_CAP,
    CLAUDEMD_PATTERN_CAP,
    PHASE_DESCRIPTIONS,
    CeremonyTool,
    _ADHERENCE_MAX_ENTRIES,
    _ADHERENCE_TAGS,
    _render_context_section,
    render_adherence,
    render_architecture,
    render_categorized_learnings,
    render_conventions,
    render_patterns,
)

__all__ = [
    # Constants
    "BEHAVIORAL_PROTOCOL_CAP",
    "CEREMONY_TOOLS",
    "CLAUDEMD_LEARNING_CAP",
    "CLAUDEMD_PATTERN_CAP",
    "CeremonyTool",
    "PHASE_DESCRIPTIONS",
    "TRW_AUTO_COMMENT",
    "TRW_MARKER_END",
    "TRW_MARKER_START",
    # Private but imported by tests
    "_ADHERENCE_MAX_ENTRIES",
    "_ADHERENCE_TAGS",
    "_render_context_section",
    "_truncate_with_markers",
    # Template rendering
    "render_adherence",
    "render_agent_teams_protocol",
    "render_architecture",
    "render_behavioral_protocol",
    "render_categorized_learnings",
    "render_ceremony_flows",
    "render_ceremony_quick_ref",
    "render_ceremony_table",
    "render_closing_reminder",
    "render_conventions",
    "render_delegation_protocol",
    "render_imperative_opener",
    "render_patterns",
    "render_phase_descriptions",
    "render_rationalization_watchlist",
    "render_template",
    # Template loading / parsing / merge
    "load_claude_md_template",
    "merge_trw_section",
    # Promotion / data collection
    "collect_context_data",
    "collect_patterns",
    "collect_promotable_learnings",
    # Sync orchestration
    "execute_claude_md_sync",
    # Path resolvers (re-exported for backward-compat patching in tests)
    "resolve_project_root",
    "resolve_trw_dir",
]
