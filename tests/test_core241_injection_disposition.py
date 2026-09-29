"""PRD-CORE-241 kept `agents_md_learning_injection`; PRD-CORE-341 removed it.

PRD-CORE-241 argued the AGENTS.md learnings section was the only static learning
channel for light clients that read AGENTS.md without reaching
`trw_session_start`. The operator overruled that on 2026-09-28: learnings are
fetched on demand through the instance lifecycle (session start, `trw_recall`,
the prompt hook, edit hints), never written into an instruction file
(`test_agents_md_link.py`). What remains pinned here is FR07: the retired
learning-injection nudge messenger stays unreachable through config.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig


class TestRetiredMessengerStaysRetired:
    """FR07 — the removed nudge messenger must not be reachable through config."""

    def test_config_rejects_retired_learning_injection_messenger(self) -> None:
        with pytest.raises(Exception) as excinfo:
            TRWConfig(nudge_messenger="learning_injection")  # type: ignore[arg-type]
        # Pydantic echoes the rejected input, so the term appears in the message
        # regardless. What matters is that it is absent from the permitted set.
        message = str(excinfo.value)
        allowed = message.split("Input should be")[-1].split("[type=")[0]
        assert "learning_injection" not in allowed, (
            f"learning_injection is still an accepted nudge_messenger value: {allowed}"
        )

    @pytest.mark.parametrize("messenger", ["standard", "contextual"])
    def test_live_messengers_still_validate(self, messenger: str) -> None:
        config = TRWConfig(nudge_messenger=messenger)  # type: ignore[arg-type]
        assert config.nudge_messenger == messenger
