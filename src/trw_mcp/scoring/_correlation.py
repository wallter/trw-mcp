"""Recall-window correlation.

Internal module -- all public names are re-exported from ``trw_mcp.scoring``.

PRD-CORE-293: the Q-learning writers that used to live here (per-entry q_value /
q_observations updates, outcome_history appends, the contradiction penalty and the
uncalled nudge->action rewards) were deleted with the reward loop; nothing fed them
(q_observations was 0 on every row). What remains is read-only: which learnings a
session recalled (``correlate_recalls``, used by the retraction nudge). The event
reward vocabulary (REWARD_MAP / EVENT_ALIASES) went with it in trw-mcp 6.1.0.
"""

from __future__ import annotations

from trw_mcp.scoring._io_boundary import (
    _find_session_start_ts as _find_session_start_ts,
)
from trw_mcp.scoring._recall_window import (
    _CONSECUTIVE_OLD_EARLY_EXIT as _CONSECUTIVE_OLD_EARLY_EXIT,
)
from trw_mcp.scoring._recall_window import (
    correlate_recalls as correlate_recalls,
)

__all__ = [
    "_find_session_start_ts",
    "correlate_recalls",
]
