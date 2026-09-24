"""PRD-FIX-088 FR01 / R10 / PRD-CORE-293: ``trw_build_check`` latency guard.

R10 superseded background scheduling: builds no longer initiate temporal
Q attribution. PRD-CORE-293 then deleted the outcome-correlation dispatcher
entirely (no bg worker, no event-triggered correlation function), so the
remaining regression guard is that no fabricated timing for the retired
step reappears.
"""

from __future__ import annotations

from typing import Any


def test_retired_q_learning_dispatch_has_no_fabricated_timing(build_check_invoke: Any) -> None:
    """R10/PRD-CORE-293: a removed attribution step must not appear as scheduled or timed."""
    result = build_check_invoke()
    assert "q_learning_dispatch" not in result["step_durations_ms"]
    assert "q_learning_deferred" not in result
