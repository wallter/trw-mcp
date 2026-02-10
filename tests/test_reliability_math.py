"""Tests for reliability math -- P_sys, supermajority, effective-n, BFT.

PRD-QUAL-005-FR08/FR09/FR12: Formal consensus math for adaptive gates.
"""

from __future__ import annotations

import pytest

from trw_mcp.gate.reliability import (
    compute_bft_tolerance,
    compute_effective_n,
    compute_system_error,
    compute_system_error_supermajority,
)


class TestComputeSystemError:
    """Binomial CDF P_sys computation."""

    def test_three_judges_ten_percent(self) -> None:
        assert compute_system_error(3, 0.10, 0.67) == pytest.approx(0.001, abs=0.001)

    def test_five_judges_ten_percent(self) -> None:
        assert compute_system_error(5, 0.10, 0.67) == pytest.approx(0.00046, abs=0.001)

    def test_more_judges_reduces_error(self) -> None:
        """Adding judges monotonically reduces system error."""
        p3 = compute_system_error(3, 0.10)
        p5 = compute_system_error(5, 0.10)
        p7 = compute_system_error(7, 0.10)
        assert p5 < p3
        assert p7 < p5

    def test_single_judge_equals_individual_error(self) -> None:
        assert compute_system_error(1, 0.20, 0.67) == pytest.approx(0.20, abs=0.01)

    def test_invalid_n(self) -> None:
        with pytest.raises(ValueError, match="n must be >= 1"):
            compute_system_error(0, 0.10)

    def test_invalid_p(self) -> None:
        with pytest.raises(ValueError, match="p must be"):
            compute_system_error(3, 0.0)


class TestComputeSystemErrorSupermajority:

    def test_higher_quorum_lower_error(self) -> None:
        """75% quorum produces lower error than 67% for n=7."""
        p_67 = compute_system_error(7, 0.10, 0.67)
        p_75 = compute_system_error_supermajority(7, 0.10, 0.75)
        assert p_75 < p_67

    def test_delegates_to_compute_system_error(self) -> None:
        direct = compute_system_error(5, 0.15, 0.75)
        via_super = compute_system_error_supermajority(5, 0.15, 0.75)
        assert via_super == pytest.approx(direct)


class TestComputeEffectiveN:
    """Effective-n accounting for judge correlation."""

    def test_independent(self) -> None:
        assert compute_effective_n(5, 0.0) == pytest.approx(5.0)

    def test_fully_correlated(self) -> None:
        assert compute_effective_n(5, 1.0) == pytest.approx(1.0)

    def test_partial_correlation(self) -> None:
        n_eff = compute_effective_n(5, 0.3)
        assert 1.0 < n_eff < 5.0

    def test_single_judge(self) -> None:
        assert compute_effective_n(1, 0.5) == pytest.approx(1.0)

    def test_invalid_correlation(self) -> None:
        with pytest.raises(ValueError, match="correlation must be"):
            compute_effective_n(3, 1.5)


class TestComputeBFTTolerance:
    """Byzantine fault tolerance bounds."""

    @pytest.mark.parametrize(
        ("n", "expected"),
        [(3, 0), (5, 1), (7, 2)],
        ids=["n=3", "n=5", "n=7"],
    )
    def test_tolerance_at_67_quorum(self, n: int, expected: int) -> None:
        assert compute_bft_tolerance(n, 0.67) == expected

    def test_invalid_n(self) -> None:
        with pytest.raises(ValueError, match="n must be >= 1"):
            compute_bft_tolerance(0)
