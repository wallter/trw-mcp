"""F10: Verify ceremony weight constants match expected production values.

This test catches the stale-mock class of bugs by asserting the authoritative
values in a test that lives next to the source, not in the consumer.

Placed in trw-mcp/tests/ (not trw-eval/tests/) because trw-mcp IS installed
here — trw-eval imports via stubs. (Final Audit Fix A)
"""

from __future__ import annotations

from trw_mcp.api.scoring import CEREMONY_WEIGHTS


def test_ceremony_weights_match_expected() -> None:
    """Production CEREMONY_WEIGHTS must match documented values from FRAMEWORK.md."""
    expected = {
        "session_start": 25,
        "deliver": 25,
        "checkpoint": 15,
        "learn": 10,
        "build_check": 10,
        "review": 15,
    }
    assert CEREMONY_WEIGHTS.as_dict() == expected


def test_ceremony_weights_sum_to_100() -> None:
    """CEREMONY_WEIGHTS fields must sum to exactly 100."""
    w = CEREMONY_WEIGHTS.as_dict()
    assert sum(w.values()) == 100


def test_ceremony_weights_has_all_six_keys() -> None:
    """CEREMONY_WEIGHTS must have exactly the 6 expected ceremony step keys."""
    keys = set(CEREMONY_WEIGHTS.as_dict().keys())
    expected_keys = {"session_start", "deliver", "checkpoint", "learn", "build_check", "review"}
    assert keys == expected_keys
