"""Tests for type-aware decay in _entry_utility() (PRD-CORE-102, Task 4)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _entry(
    entry_type: str = "",
    confidence: str = "verified",
    days_old: int = 30,
    impact: float = 0.8,
    expires: str = "",
) -> dict[str, object]:
    created = (datetime.now(timezone.utc).date() - timedelta(days=days_old)).isoformat()
    e: dict[str, object] = {
        "id": "L-test",
        "summary": "test entry",
        "impact": impact,
        "created": created,
        "type": entry_type,
        "confidence": confidence,
    }
    if expires:
        e["expires"] = expires
    return e


def test_incident_unverified_no_decay() -> None:
    """Unverified incident at 90 days still has high utility (no decay)."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    entry = _entry(entry_type="incident", confidence="unverified", days_old=90, impact=0.9)
    utility = _entry_utility(entry, today)
    # With half_life=9999 (no decay), utility should be very high
    assert utility > 0.7, f"Unverified incident should not decay much: {utility}"


def test_incident_verified_90d() -> None:
    """Verified incident at 90 days starts decaying (half_life=90d)."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    # verified incident uses half_life=90
    entry_90d = _entry(entry_type="incident", confidence="verified", days_old=90, impact=0.9)
    entry_1d = _entry(entry_type="incident", confidence="verified", days_old=1, impact=0.9)
    u_90 = _entry_utility(entry_90d, today)
    u_1 = _entry_utility(entry_1d, today)
    # 90 days old should have lower utility than 1 day old
    assert u_90 < u_1, f"90d utility {u_90} should be less than 1d utility {u_1}"


def test_convention_no_decay_365d() -> None:
    """Convention at 200 days still has reasonable utility (half_life=365d)."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    entry = _entry(entry_type="convention", confidence="verified", days_old=200, impact=0.8)
    utility = _entry_utility(entry, today)
    # With half_life=365, 200 days is below half-life, so should retain good utility
    assert utility > 0.5, f"Convention at 200d should retain utility: {utility}"


def test_hypothesis_prune_after_5_sessions() -> None:
    """Hypothesis at 14+ days has very low utility (half_life=7d)."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    entry = _entry(entry_type="hypothesis", confidence="verified", days_old=14, impact=0.6)
    utility = _entry_utility(entry, today)
    # With half_life=7, 14 days = 2x half-life, significant decay expected
    # At 2 half-lives, retention ≈ exp(-ln2 * 14/7) = exp(-2*ln2) ≈ 0.25
    # So utility should be notably lower than base
    entry_fresh = _entry(entry_type="hypothesis", confidence="verified", days_old=0, impact=0.6)
    utility_fresh = _entry_utility(entry_fresh, today)
    assert utility < utility_fresh, f"Stale hypothesis {utility} should be less than fresh {utility_fresh}"


def test_workaround_expired_demotes_transient() -> None:
    """Workaround past expires date → 0.01."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    past_date = (today - timedelta(days=5)).isoformat()
    entry = _entry(entry_type="workaround", confidence="verified", days_old=10, impact=0.7)
    entry["expires"] = past_date

    utility = _entry_utility(entry, today)
    assert utility == 0.01, f"Expired workaround should return 0.01, got {utility}"


def test_untyped_default_14d() -> None:
    """Entry without type field uses default 14-day half-life."""
    from trw_mcp.scoring._decay import _entry_utility
    from trw_mcp.scoring._utils import get_config

    today = datetime.now(timezone.utc).date()
    cfg = get_config()
    assert cfg.learning_decay_half_life_days == 14.0  # Verify default

    entry_typed = _entry(entry_type="", confidence="verified", days_old=14, impact=0.8)
    utility = _entry_utility(entry_typed, today)
    # At 14 days with half_life=14, utility should be decayed
    entry_fresh = _entry(entry_type="", confidence="verified", days_old=0, impact=0.8)
    utility_fresh = _entry_utility(entry_fresh, today)
    assert utility < utility_fresh


def test_malformed_expires_iso_fallback() -> None:
    """Malformed expires string doesn't crash — treated as no expiry."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    entry = _entry(entry_type="workaround", confidence="verified", days_old=1, impact=0.7)
    entry["expires"] = "not-a-date"

    # Should not raise
    utility = _entry_utility(entry, today)
    assert utility > 0.0


def test_not_yet_expired_no_demote() -> None:
    """Entry with future expires date is not demoted."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    future_date = (today + timedelta(days=10)).isoformat()
    entry = _entry(entry_type="workaround", confidence="verified", days_old=1, impact=0.7)
    entry["expires"] = future_date

    utility = _entry_utility(entry, today)
    assert utility > 0.01, f"Not-yet-expired entry should not be demoted: {utility}"


def test_type_half_life_lookup() -> None:
    """_type_half_life returns correct values for known types."""
    from trw_mcp.scoring._decay import _type_half_life
    from trw_mcp.scoring._utils import get_config

    cfg = get_config()
    assert _type_half_life("incident", cfg) == 90.0
    assert _type_half_life("convention", cfg) == 365.0
    assert _type_half_life("pattern", cfg) == 30.0
    assert _type_half_life("hypothesis", cfg) == 7.0
    assert _type_half_life("workaround", cfg) == 14.0
    # Unknown type falls back to config default
    assert _type_half_life("unknown_type", cfg) == cfg.learning_decay_half_life_days


def test_incident_default_confidence_is_unverified() -> None:
    """When confidence field is absent, incident defaults to 'unverified' (no decay)."""
    from trw_mcp.scoring._decay import _entry_utility

    today = datetime.now(timezone.utc).date()
    entry: dict[str, object] = {
        "id": "L-inc",
        "summary": "incident without confidence",
        "impact": 0.8,
        "created": (today - timedelta(days=100)).isoformat(),
        "type": "incident",
        # No 'confidence' field
    }
    utility = _entry_utility(entry, today)
    # Should treat as unverified → no decay
    assert utility > 0.6, f"Incident with missing confidence should not decay: {utility}"
