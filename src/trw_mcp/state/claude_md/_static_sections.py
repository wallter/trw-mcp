"""PRD-CORE-149-FR01: facade for ``claude_md.sections`` sub-package.

This module preserves the legacy public API. All implementations have
been extracted into focused modules under
``trw_mcp.state.claude_md.sections/``. New code should import from the
sub-package directly; existing callers continue to work via this facade.
"""

from __future__ import annotations

# PRD-CORE-149-FR01: the facade re-exports every symbol legacy tests patch
# (``get_config``, ``resolve_project_root``, ``FileStateReader``,
# ``MemoryConfig``, ``time``) so ``monkeypatch.setattr(_static_sections,
# "get_config", ...)`` continues to work. Section modules look these
# symbols up on the facade at call time (see ``sections._lookups``).
import time as time
from pathlib import Path as Path

import yaml as yaml
from trw_memory.graph import list_org_shared_entries as list_org_shared_entries
from trw_memory.models.config import MemoryConfig as MemoryConfig

from trw_mcp.models.config import get_config as get_config
from trw_mcp.state._paths import resolve_project_root as resolve_project_root
from trw_mcp.state.claude_md._renderer import SESSION_BOUNDARY_TEXT as _SESSION_BOUNDARY_TEXT
from trw_mcp.state.claude_md.sections import (
    _analytics_cache as _analytics_cache,
)
from trw_mcp.state.persistence import FileStateReader as FileStateReader
from trw_mcp.state.claude_md.sections import (
    _format_learning_session_claim as _format_learning_session_claim,
)
from trw_mcp.state.claude_md.sections import (
    _load_analytics_counts as _load_analytics_counts,
)
from trw_mcp.state.claude_md.sections import (
    _load_prompting_guide as _load_prompting_guide,
)
from trw_mcp.state.claude_md.sections import (
    generate_behavioral_protocol_md as generate_behavioral_protocol_md,
)
from trw_mcp.state.claude_md.sections import (
    render_agent_teams_protocol as render_agent_teams_protocol,
)
from trw_mcp.state.claude_md.sections import (
    render_agents_trw_section as render_agents_trw_section,
)
from trw_mcp.state.claude_md.sections import (
    render_behavioral_protocol as render_behavioral_protocol,
)
from trw_mcp.state.claude_md.sections import (
    render_ceremony_flows as render_ceremony_flows,
)
from trw_mcp.state.claude_md.sections import (
    render_ceremony_quick_ref as render_ceremony_quick_ref,
)
from trw_mcp.state.claude_md.sections import (
    render_ceremony_table as render_ceremony_table,
)
from trw_mcp.state.claude_md.sections import (
    render_closing_reminder as render_closing_reminder,
)
from trw_mcp.state.claude_md.sections import (
    render_codex_instructions as render_codex_instructions,
)
from trw_mcp.state.claude_md.sections import (
    render_codex_trw_section as render_codex_trw_section,
)
from trw_mcp.state.claude_md.sections import (
    render_delegation_protocol as render_delegation_protocol,
)
from trw_mcp.state.claude_md.sections import (
    render_framework_reference as render_framework_reference,
)
from trw_mcp.state.claude_md.sections import (
    render_imperative_opener as render_imperative_opener,
)
from trw_mcp.state.claude_md.sections import (
    render_memory_harmonization as render_memory_harmonization,
)
from trw_mcp.state.claude_md.sections import (
    render_minimal_protocol as render_minimal_protocol,
)
from trw_mcp.state.claude_md.sections import (
    render_opencode_instructions as render_opencode_instructions,
)
from trw_mcp.state.claude_md.sections import (
    render_phase_descriptions as render_phase_descriptions,
)
from trw_mcp.state.claude_md.sections import (
    render_rationalization_watchlist as render_rationalization_watchlist,
)
from trw_mcp.state.claude_md.sections import (
    render_shared_learnings as render_shared_learnings,
)

__all__ = [
    "_SESSION_BOUNDARY_TEXT",
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
