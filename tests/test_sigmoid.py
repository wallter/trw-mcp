"""Tests for sigmoid_normalize — PRD-CORE-104-FR05.

Covers: sigmoid mapping of composite outcome scores to [0, 1].
"""

from __future__ import annotations

from trw_mcp.scoring._correlation import sigmoid_normalize


class TestSigmoidNormalize:
    """Tests for sigmoid_normalize()."""

    def test_zero_maps_to_05(self) -> None:
        """sigmoid(0.0) is exactly 0.5."""
        assert abs(sigmoid_normalize(0.0) - 0.5) < 0.001

    def test_negative_4_maps_below_002(self) -> None:
        """sigmoid(-4.0) < 0.02."""
        assert sigmoid_normalize(-4.0) < 0.02

    def test_positive_4_maps_above_098(self) -> None:
        """sigmoid(4.0) > 0.98."""
        assert sigmoid_normalize(4.0) > 0.98

    def test_always_in_0_1_range(self) -> None:
        """For scores in [-10, 10], result is always in (0, 1)."""
        for score in range(-10, 11):
            result = sigmoid_normalize(float(score))
            assert 0 < result < 1, f"sigmoid({score}) = {result} out of (0,1)"

    def test_monotonically_increasing(self) -> None:
        """Sigmoid is monotonically increasing."""
        scores = [-5.0, -2.0, -1.0, 0.0, 1.0, 2.0, 5.0]
        results = [sigmoid_normalize(s) for s in scores]
        for i in range(len(results) - 1):
            assert results[i] < results[i + 1], (
                f"Not monotonic: sigmoid({scores[i]})={results[i]} >= "
                f"sigmoid({scores[i+1]})={results[i+1]}"
            )

    def test_steepness_parameter(self) -> None:
        """Higher steepness produces more extreme values."""
        # At x=1, higher steepness should map closer to 1
        low_steep = sigmoid_normalize(1.0, steepness=0.5)
        high_steep = sigmoid_normalize(1.0, steepness=3.0)
        assert high_steep > low_steep

    def test_symmetry(self) -> None:
        """sigmoid(x) + sigmoid(-x) = 1.0 (symmetry around 0.5)."""
        for x in [0.5, 1.0, 2.0, 3.0, 5.0]:
            pos = sigmoid_normalize(x)
            neg = sigmoid_normalize(-x)
            assert abs(pos + neg - 1.0) < 0.0001, (
                f"Symmetry violated: sigmoid({x})={pos} + sigmoid({-x})={neg} != 1.0"
            )
