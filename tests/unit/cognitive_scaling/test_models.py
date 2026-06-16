"""Unit tests for PRD-SCALE-001 cognitive-scaling models (FR02 taxonomy)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.cognitive_scaling import (
    CEREMONY_TIER_BY_MODE,
    PlanningMode,
    ScoutClassification,
    ScoutSignals,
    SessionProfileOverlay,
)
from trw_mcp.probe.budget import PLANNING_MODE_BUDGETS


def test_planning_mode_closed_taxonomy() -> None:
    """FR02: exactly four modes with stable numeric codes."""
    assert [m.value for m in PlanningMode] == [0, 1, 2, 3]
    assert PlanningMode.DIRECT == 0
    assert PlanningMode.TRIANGULATED_WITH_PROBE == 3


def test_planning_mode_names_match_probe_budget_table() -> None:
    """FR07: PlanningMode names are the canonical probe-budget keys."""
    assert {m.name for m in PlanningMode} == set(PLANNING_MODE_BUDGETS)


def test_ceremony_tier_map_covers_every_mode() -> None:
    """FR03: every mode maps to a ceremony tier; DIRECT downgrades to MINIMAL."""
    assert set(CEREMONY_TIER_BY_MODE) == set(PlanningMode)
    assert CEREMONY_TIER_BY_MODE[PlanningMode.DIRECT] == "MINIMAL"
    assert CEREMONY_TIER_BY_MODE[PlanningMode.TRIANGULATED] == "COMPREHENSIVE"


def test_signals_available_and_hit_counts() -> None:
    """ScoutSignals counts respect the availability gate."""
    s = ScoutSignals(
        blast_radius_hit=True,
        churn_hit=True,
        churn_available=False,  # hit but unavailable -> not counted
        precedent_gap_hit=True,
    )
    assert s.available_count() == 2  # blast + precedent (churn unavailable)
    assert s.hit_count() == 2  # blast + precedent; churn hit ignored (unavailable)


def test_session_profile_overlay_rejects_extra_keys() -> None:
    """Overlay is extra=forbid so drift surfaces as a ValidationError."""
    with pytest.raises(ValidationError):
        SessionProfileOverlay(
            ceremony_tier="MINIMAL",
            planning_mode=0,
            probe_budget=0,
            bogus="x",  # type: ignore[call-arg]
        )


def test_session_profile_overlay_clamps_mode_range() -> None:
    """planning_mode must be within the 0-3 numeric taxonomy."""
    with pytest.raises(ValidationError):
        SessionProfileOverlay(ceremony_tier="MINIMAL", planning_mode=4, probe_budget=0)


def test_scout_classification_defaults_direct() -> None:
    """A default ScoutClassification is a safe DIRECT/MINIMAL."""
    c = ScoutClassification()
    assert c.planning_mode == PlanningMode.DIRECT
    assert c.ceremony_tier == "MINIMAL"
    assert c.source == "scout"
