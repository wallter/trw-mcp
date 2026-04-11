"""Legacy nudge-state compatibility layer.

Live tool wiring uses ``trw_mcp.state.ceremony_progress``. Legacy/offline nudge
code continues to import this module, which re-exports the shared persistence
surface from ``_ceremony_progress_state``.
"""

from __future__ import annotations

from trw_mcp.state._ceremony_progress_state import _NUDGE_HISTORY_CAP as _NUDGE_HISTORY_CAP
from trw_mcp.state._ceremony_progress_state import _STEPS as _STEPS
from trw_mcp.state._ceremony_progress_state import CeremonyState as CeremonyState
from trw_mcp.state._ceremony_progress_state import NudgeContext as NudgeContext
from trw_mcp.state._ceremony_progress_state import NudgeHistoryEntry as NudgeHistoryEntry
from trw_mcp.state._ceremony_progress_state import ToolName as ToolName
from trw_mcp.state._ceremony_progress_state import _from_dict as _from_dict
from trw_mcp.state._ceremony_progress_state import _parse_nudge_history as _parse_nudge_history
from trw_mcp.state._ceremony_progress_state import _state_path as _state_path
from trw_mcp.state._ceremony_progress_state import _step_complete as _step_complete
from trw_mcp.state._ceremony_progress_state import clear_nudge_history as clear_nudge_history
from trw_mcp.state._ceremony_progress_state import increment_files_modified as increment_files_modified
from trw_mcp.state._ceremony_progress_state import increment_learnings as increment_learnings
from trw_mcp.state._ceremony_progress_state import increment_nudge_count as increment_nudge_count
from trw_mcp.state._ceremony_progress_state import increment_tool_call_counter as increment_tool_call_counter
from trw_mcp.state._ceremony_progress_state import is_nudge_eligible as is_nudge_eligible
from trw_mcp.state._ceremony_progress_state import mark_build_check as mark_build_check
from trw_mcp.state._ceremony_progress_state import mark_checkpoint as mark_checkpoint
from trw_mcp.state._ceremony_progress_state import mark_deliver as mark_deliver
from trw_mcp.state._ceremony_progress_state import mark_review as mark_review
from trw_mcp.state._ceremony_progress_state import mark_session_started as mark_session_started
from trw_mcp.state._ceremony_progress_state import read_ceremony_state as read_ceremony_state
from trw_mcp.state._ceremony_progress_state import record_nudge_shown as record_nudge_shown
from trw_mcp.state._ceremony_progress_state import record_pool_ignore as record_pool_ignore
from trw_mcp.state._ceremony_progress_state import record_pool_nudge as record_pool_nudge
from trw_mcp.state._ceremony_progress_state import reset_ceremony_state as reset_ceremony_state
from trw_mcp.state._ceremony_progress_state import reset_nudge_count as reset_nudge_count
from trw_mcp.state._ceremony_progress_state import set_ceremony_phase as set_ceremony_phase
from trw_mcp.state._ceremony_progress_state import write_ceremony_state as write_ceremony_state
