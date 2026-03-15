"""Tests for Bayesian impact score calibration (PRD-CORE-034)."""

from __future__ import annotations

import pytest

from trw_mcp.scoring import bayesian_calibrate, compute_calibration_accuracy

# ---------------------------------------------------------------------------
# bayesian_calibrate
# ---------------------------------------------------------------------------


def test_bayesian_calibrate_equal_weights() -> None:
    """Equal weights: result is midpoint between user and org."""
    result = bayesian_calibrate(user_impact=0.8, org_mean=0.5, user_weight=1.0, org_weight=1.0)
    assert result == pytest.approx(0.65, abs=1e-6)


def test_bayesian_calibrate_high_user_weight() -> None:
    """High user_weight pulls result closer to user_impact."""
    result = bayesian_calibrate(user_impact=0.8, org_mean=0.5, user_weight=2.0, org_weight=1.0)
    # (0.8*2 + 0.5*1) / (2+1) = 2.1/3 = 0.7
    assert result == pytest.approx(0.7, abs=1e-6)
    # Must be closer to 0.8 than 0.5
    assert result > 0.65


def test_bayesian_calibrate_high_org_weight() -> None:
    """High org_weight pulls result closer to org_mean."""
    result = bayesian_calibrate(user_impact=0.8, org_mean=0.5, user_weight=1.0, org_weight=2.0)
    # (0.8*1 + 0.5*2) / (1+2) = 1.8/3 = 0.6
    assert result == pytest.approx(0.6, abs=1e-6)
    # Must be closer to 0.5 than 0.8
    assert result < 0.65


def test_bayesian_calibrate_zero_weights() -> None:
    """When both weights are zero, returns user_impact unchanged."""
    result = bayesian_calibrate(user_impact=0.7, org_mean=0.3, user_weight=0.0, org_weight=0.0)
    assert result == pytest.approx(0.7, abs=1e-6)


def test_bayesian_calibrate_bounds_clamp_high() -> None:
    """Result never exceeds 1.0 even with extreme inputs."""
    result = bayesian_calibrate(user_impact=1.0, org_mean=1.0, user_weight=10.0, org_weight=0.0)
    assert 0.0 <= result <= 1.0


def test_bayesian_calibrate_bounds_clamp_low() -> None:
    """Result never goes below 0.0."""
    result = bayesian_calibrate(user_impact=0.0, org_mean=0.0, user_weight=1.0, org_weight=1.0)
    assert result == pytest.approx(0.0, abs=1e-6)


def test_bayesian_calibrate_org_weight_cap() -> None:
    """org_weight > 2.0 is capped at 2.0."""
    capped = bayesian_calibrate(user_impact=0.8, org_mean=0.5, user_weight=1.0, org_weight=5.0)
    at_cap = bayesian_calibrate(user_impact=0.8, org_mean=0.5, user_weight=1.0, org_weight=2.0)
    assert capped == pytest.approx(at_cap, abs=1e-6)


def test_bayesian_calibrate_default_weights() -> None:
    """Default weights (user=1.0, org=0.5) give correct result."""
    result = bayesian_calibrate(user_impact=0.8)
    # (0.8*1 + 0.5*0.5) / (1+0.5) = (0.8 + 0.25) / 1.5 = 1.05/1.5 = 0.7
    assert result == pytest.approx(0.7, abs=1e-6)


@pytest.mark.parametrize(
    "user_impact,org_mean,user_weight,org_weight,expected",
    [
        (0.0, 0.5, 1.0, 1.0, 0.25),
        (1.0, 0.5, 1.0, 1.0, 0.75),
        (0.5, 0.5, 1.0, 1.0, 0.5),
        (0.9, 0.4, 1.0, 0.5, pytest.approx((0.9 + 0.2) / 1.5, abs=1e-6)),
        (0.3, 0.7, 2.0, 1.0, pytest.approx((0.6 + 0.7) / 3.0, abs=1e-6)),
    ],
)
def test_bayesian_calibrate_parametrized(
    user_impact: float,
    org_mean: float,
    user_weight: float,
    org_weight: float,
    expected: float,
) -> None:
    result = bayesian_calibrate(
        user_impact=user_impact,
        org_mean=org_mean,
        user_weight=user_weight,
        org_weight=org_weight,
    )
    assert result == expected


# ---------------------------------------------------------------------------
# compute_calibration_accuracy
# ---------------------------------------------------------------------------


def test_calibration_accuracy_no_data() -> None:
    """No recall data → default weight 1.0."""
    stats: dict[str, object] = {"total_recalls": 0, "positive_outcomes": 0}
    assert compute_calibration_accuracy(stats) == pytest.approx(1.0)


def test_calibration_accuracy_empty_dict() -> None:
    """Missing keys → treat as zero → default weight 1.0."""
    assert compute_calibration_accuracy({}) == pytest.approx(1.0)


def test_calibration_accuracy_high_positive() -> None:
    """80% positive outcomes → weight 2.0."""
    stats: dict[str, object] = {"total_recalls": 10, "positive_outcomes": 8}
    assert compute_calibration_accuracy(stats) == pytest.approx(2.0)


def test_calibration_accuracy_at_75_pct() -> None:
    """Exactly 75% positive → weight 2.0 (boundary)."""
    stats: dict[str, object] = {"total_recalls": 4, "positive_outcomes": 3}
    assert compute_calibration_accuracy(stats) == pytest.approx(2.0)


def test_calibration_accuracy_medium_positive() -> None:
    """60% positive outcomes → weight 1.5."""
    stats: dict[str, object] = {"total_recalls": 10, "positive_outcomes": 6}
    assert compute_calibration_accuracy(stats) == pytest.approx(1.5)


def test_calibration_accuracy_at_50_pct() -> None:
    """Exactly 50% positive → weight 1.5 (boundary)."""
    stats: dict[str, object] = {"total_recalls": 4, "positive_outcomes": 2}
    assert compute_calibration_accuracy(stats) == pytest.approx(1.5)


def test_calibration_accuracy_low_positive() -> None:
    """30% positive outcomes → weight 1.0."""
    stats: dict[str, object] = {"total_recalls": 10, "positive_outcomes": 3}
    assert compute_calibration_accuracy(stats) == pytest.approx(1.0)


def test_calibration_accuracy_at_25_pct() -> None:
    """Exactly 25% positive → weight 1.0 (boundary)."""
    stats: dict[str, object] = {"total_recalls": 4, "positive_outcomes": 1}
    assert compute_calibration_accuracy(stats) == pytest.approx(1.0)


def test_calibration_accuracy_very_low() -> None:
    """10% positive outcomes → weight 0.5."""
    stats: dict[str, object] = {"total_recalls": 10, "positive_outcomes": 1}
    assert compute_calibration_accuracy(stats) == pytest.approx(0.5)


def test_calibration_accuracy_zero_positive() -> None:
    """0% positive outcomes → weight 0.5."""
    stats: dict[str, object] = {"total_recalls": 5, "positive_outcomes": 0}
    assert compute_calibration_accuracy(stats) == pytest.approx(0.5)
