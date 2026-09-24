"""The one decision on whether learnings may reach the agent by a given path.

``learning_recall_enabled`` is the master switch (PRD-CORE-125-FR03): off, no
path delivers a learning, so an eval arm labelled no-recall means exactly that.
``session_start_recall_enabled`` is a finer switch beneath it
(``auto_recall_enabled`` now gates only the prompt hook, which reads the master too). ``"passive"`` is every path the agent did not ask for: edit hints,
ceremony nudges, the AGENTS.md learnings section, REVIEW.md and the
``trw://learnings/summary`` resource; only the master switch governs those. The master's reader was lost in merge 70bb84843 (2026-04-11) and
the flag stayed settable, which is how five months of no-recall arms ran with
recall on.
"""

from __future__ import annotations

from typing import Literal

from trw_mcp.models.config import TRWConfig

RecallPath = Literal["tool", "session_start", "passive"]


def learnings_injection_allowed(config: TRWConfig, path: RecallPath) -> bool:
    """Whether ``path`` may deliver learnings under ``config``."""
    if not config.learning_recall_enabled:
        return False
    if path == "session_start":
        return config.session_start_recall_enabled is not False
    return True


def passive_learnings_allowed() -> bool:
    """:func:`learnings_injection_allowed` for a ``"passive"`` path that holds no config."""
    from trw_mcp.models.config import get_config

    return learnings_injection_allowed(get_config(), "passive")
