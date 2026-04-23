"""PRD-CORE-149-FR01: section renderers extracted from ``_static_sections.py``.

Public API is preserved via re-exports; every caller that historically imported
from ``trw_mcp.state.claude_md._static_sections`` continues to work unchanged.
"""

from __future__ import annotations

from trw_mcp.state.claude_md.sections._behavioral_protocol import (
    generate_behavioral_protocol_md as generate_behavioral_protocol_md,
)
from trw_mcp.state.claude_md.sections._behavioral_protocol import (
    render_behavioral_protocol as render_behavioral_protocol,
)
from trw_mcp.state.claude_md.sections._behavioral_protocol import (
    render_imperative_opener as render_imperative_opener,
)
from trw_mcp.state.claude_md.sections._behavioral_protocol import (
    render_minimal_protocol as render_minimal_protocol,
)
from trw_mcp.state.claude_md.sections._ceremony_table import (
    render_ceremony_flows as render_ceremony_flows,
)
from trw_mcp.state.claude_md.sections._ceremony_table import (
    render_ceremony_quick_ref as render_ceremony_quick_ref,
)
from trw_mcp.state.claude_md.sections._ceremony_table import (
    render_ceremony_table as render_ceremony_table,
)
from trw_mcp.state.claude_md.sections._ceremony_table import (
    render_phase_descriptions as render_phase_descriptions,
)
from trw_mcp.state.claude_md.sections._delegation import (
    render_agent_teams_protocol as render_agent_teams_protocol,
)
from trw_mcp.state.claude_md.sections._delegation import (
    render_agents_trw_section as render_agents_trw_section,
)
from trw_mcp.state.claude_md.sections._delegation import (
    render_codex_trw_section as render_codex_trw_section,
)
from trw_mcp.state.claude_md.sections._delegation import (
    render_delegation_protocol as render_delegation_protocol,
)
from trw_mcp.state.claude_md.sections._delegation import (
    render_rationalization_watchlist as render_rationalization_watchlist,
)
from trw_mcp.state.claude_md.sections._memory_routing import (
    _analytics_cache as _analytics_cache,
)
from trw_mcp.state.claude_md.sections._memory_routing import (
    _format_learning_session_claim as _format_learning_session_claim,
)
from trw_mcp.state.claude_md.sections._memory_routing import (
    _load_analytics_counts as _load_analytics_counts,
)
from trw_mcp.state.claude_md.sections._memory_routing import (
    render_memory_harmonization as render_memory_harmonization,
)
from trw_mcp.state.claude_md.sections._memory_routing import (
    render_shared_learnings as render_shared_learnings,
)
from trw_mcp.state.claude_md.sections._tool_lifecycle import (
    _load_prompting_guide as _load_prompting_guide,
)
from trw_mcp.state.claude_md.sections._tool_lifecycle import (
    render_closing_reminder as render_closing_reminder,
)
from trw_mcp.state.claude_md.sections._tool_lifecycle import (
    render_codex_instructions as render_codex_instructions,
)
from trw_mcp.state.claude_md.sections._tool_lifecycle import (
    render_framework_reference as render_framework_reference,
)
from trw_mcp.state.claude_md.sections._tool_lifecycle import (
    render_opencode_instructions as render_opencode_instructions,
)

__all__ = [
    "_analytics_cache",
    "_format_learning_session_claim",
    "_load_analytics_counts",
    "_load_prompting_guide",
    "generate_behavioral_protocol_md",
    "render_agent_teams_protocol",
    "render_agents_trw_section",
    "render_behavioral_protocol",
    "render_ceremony_flows",
    "render_ceremony_quick_ref",
    "render_ceremony_table",
    "render_closing_reminder",
    "render_codex_instructions",
    "render_codex_trw_section",
    "render_delegation_protocol",
    "render_framework_reference",
    "render_imperative_opener",
    "render_memory_harmonization",
    "render_minimal_protocol",
    "render_opencode_instructions",
    "render_phase_descriptions",
    "render_rationalization_watchlist",
    "render_shared_learnings",
]
