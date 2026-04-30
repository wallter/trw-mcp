"""Shared helpers for split CORE-116 recall scoring tests."""

from __future__ import annotations


def _make_entry(**overrides: object) -> dict[str, object]:
    """Create a synthetic learning entry with PRD-CORE-116 fields."""
    base: dict[str, object] = {
        "id": "L-test",
        "summary": "test summary payments",
        "detail": "test detail",
        "tags": ["test"],
        "impact": 0.7,
        "status": "active",
        "created": "2026-04-01",
        "recurrence": 1,
        "q_value": 0.7,
        "q_observations": 5,
        "access_count": 3,
        "source_type": "agent",
        "domain": [],
        "phase_affinity": [],
        "team_origin": "",
        "outcome_correlation": 0.0,
        "anchor_validity": 1.0,
        "type": "pattern",
        "confidence": "unverified",
    }
    base.update(overrides)
    return base


def _score_of(ranked: list[dict[str, object]], index: int = 0) -> float:
    """Extract combined_score from ranked result at given index."""
    return float(str(ranked[index]["combined_score"]))
