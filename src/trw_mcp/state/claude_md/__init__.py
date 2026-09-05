"""CLAUDE.md rendering and sync — template loading, section generation, marker-based merge.

This package decomposes the monolithic claude_md module into focused submodules:
- ``_templates``: Data-driven section builders (learnings, patterns, adherence) and constants
- ``_static_sections``: Static content renderers (protocol, ceremony, delegation, watchlist)
- ``_promotion``: Learning promotion logic and data collection
- ``_parser``: Marker parsing, template loading, merge logic
- ``_sync``: Sync orchestration (the main entry point)

All public symbols are re-exported here for backward compatibility.
"""

from pathlib import Path

from trw_mcp.state._paths import (
    resolve_project_root,
    resolve_trw_dir,
)
from trw_mcp.state.claude_md._instruction_carrier import (
    CarrierMode,
    CarrierOutcome,
    InstructionFileClass,
    InstructionFileClassification,
    apply_carrier,
    classify_instruction_file,
    heal_pointer,
    pointer_skip_guard,
    resolve_carrier_mode,
)
from trw_mcp.state.claude_md._parser import (
    TRW_AUTO_COMMENT,
    TRW_MARKER_END,
    TRW_MARKER_START,
    load_claude_md_template,
    merge_trw_section,
    render_merged_content,
    render_template,
)
from trw_mcp.state.claude_md._promotion import (
    collect_context_data,
    collect_patterns,
    collect_promotable_learnings,
)
from trw_mcp.state.claude_md._static_sections import (
    render_agents_trw_section,
    render_behavioral_protocol,
    render_ceremony_flows,
    render_ceremony_quick_ref,
    render_ceremony_table,
    render_closing_reminder,
    render_delegation_protocol,
    render_imperative_opener,
    render_memory_harmonization,
    render_phase_descriptions,
    render_rationalization_watchlist,
    render_shared_learnings,
)
from trw_mcp.state.claude_md._sync import execute_claude_md_sync
from trw_mcp.state.claude_md._templates import (
    BEHAVIORAL_PROTOCOL_CAP,
    CEREMONY_TOOLS,
    CLAUDEMD_LEARNING_CAP,
    CLAUDEMD_PATTERN_CAP,
    PHASE_DESCRIPTIONS,
    CeremonyTool,
)
from trw_mcp.state.claude_md._write_guard import (
    InstructionWriteVerdict,
    guarded_instruction_write,
    instruction_write_trigger,
    non_generated_bytes,
)

__all__ = [
    "BEHAVIORAL_PROTOCOL_CAP",
    "CEREMONY_TOOLS",
    "CLAUDEMD_LEARNING_CAP",
    "CLAUDEMD_PATTERN_CAP",
    "PHASE_DESCRIPTIONS",
    "TRW_AUTO_COMMENT",
    "TRW_MARKER_END",
    "TRW_MARKER_START",
    "CarrierMode",
    "CarrierOutcome",
    "CeremonyTool",
    "InstructionFileClass",
    "InstructionFileClassification",
    "InstructionWriteVerdict",
    "apply_carrier",
    "classify_instruction_file",
    "collect_context_data",
    "collect_patterns",
    "collect_promotable_learnings",
    "execute_claude_md_sync",
    "guarded_instruction_write",
    "heal_pointer",
    "instruction_write_trigger",
    "load_claude_md_template",
    "merge_trw_section",
    "non_generated_bytes",
    "pointer_skip_guard",
    "render_agents_trw_section",
    "render_behavioral_protocol",
    "render_ceremony_flows",
    "render_ceremony_quick_ref",
    "render_ceremony_table",
    "render_closing_reminder",
    "render_delegation_protocol",
    "render_imperative_opener",
    "render_memory_harmonization",
    "render_merged_content",
    "render_phase_descriptions",
    "render_rationalization_watchlist",
    "render_shared_learnings",
    "render_template",
    "resolve_carrier_mode",
    "resolve_project_root",
    "resolve_trw_dir",
]
