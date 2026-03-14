"""Dashboard trend TypedDicts (state/dashboard.py)."""

from __future__ import annotations

from typing import TypedDict


class CeremonyTrendResult(TypedDict):
    """Return shape of ``compute_ceremony_trend()``."""

    avg: float | None
    min: float | None
    max: float | None
    slope: float | None
    session_count: int
    pass_rate: float | None


class CoverageTrendResult(TypedDict):
    """Return shape of ``compute_coverage_trend()``."""

    avg: float | None
    min: float | None
    max: float | None
    below_threshold_count: int
    session_count: int


# "pass" is a Python keyword so this TypedDict uses the functional form.
ReviewTrendResult = TypedDict(
    "ReviewTrendResult",
    {
        "block": int,
        "warn": int,
        "pass": int,
        "total": int,
    },
)
ReviewTrendResult.__doc__ = "Return shape of ``compute_review_trend()``."


class DegradationAlertResult(TypedDict):
    """Return shape of each alert dict produced by ``detect_degradation()``."""

    type: str
    consecutive_sessions: int
    threshold: int
    first_occurrence: str
    severity: str
    start_index: int
    end_index: int
    scores: list[float]
    length: int
