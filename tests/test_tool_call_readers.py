"""Run-log readers count tool calls under the new name and under the name historical logs carry (PRD-FIX-150).

Only ``tool_call`` is written now. Run logs recorded before hold ``tool_invocation`` rows, are never
rewritten, and past runs are still re-scored from them, so both must earn the same credit.
"""

from __future__ import annotations

import pytest

_CEREMONY = (
    "trw_session_start",
    "trw_init",
    "trw_checkpoint",
    "trw_learn",
    "trw_build_check",
    "trw_review",
    "trw_deliver",
)


def _rows(event: str) -> list[dict[str, object]]:
    return [{"event": event, "tool_name": name, "success": True} for name in _CEREMONY]


@pytest.mark.parametrize("event", ["tool_call", "tool_invocation"])
def test_ceremony_score_credits_tool_calls_under_either_name(event: str) -> None:
    from trw_mcp.state.analytics.report import compute_ceremony_score

    scored = compute_ceremony_score(_rows(event))

    assert scored == compute_ceremony_score(_rows("tool_call"))
    assert scored["session_start"] and scored["deliver"] and scored["checkpoint_count"] >= 1


@pytest.mark.parametrize("event", ["tool_call", "tool_invocation"])
def test_tier_score_credits_tool_calls_under_either_name(event: str) -> None:
    from trw_mcp.scoring._tier_score import compute_tier_ceremony_score

    assert compute_tier_ceremony_score(_rows(event), "STANDARD") == compute_tier_ceremony_score(
        _rows("tool_call"), "STANDARD"
    )


def test_an_unrelated_event_earns_no_tool_credit() -> None:
    from trw_mcp.state.analytics.report import compute_ceremony_score

    assert compute_ceremony_score(_rows("tool_call"))["score"] > compute_ceremony_score(_rows("other_event"))["score"]
