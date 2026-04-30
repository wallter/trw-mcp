"""Cost computation tests for the usage report tool."""

from __future__ import annotations

import pytest

from trw_mcp.tools.usage import _compute_cost


class TestComputeCost:
    """Tests for _compute_cost helper function."""

    def test_usage_report_cost_haiku(self) -> None:
        """Haiku pricing: $0.80 input, $4.00 output per Mtok."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 1_000_000, 1_000_000)
        assert cost == pytest.approx(4.80, rel=1e-6)

    def test_usage_report_cost_haiku_small(self) -> None:
        """Haiku pricing for small token counts."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 1000, 500)
        assert cost == pytest.approx(0.0028, rel=1e-5)

    def test_usage_report_cost_sonnet(self) -> None:
        """Sonnet pricing: $3.00 input, $15.00 output per Mtok."""
        cost = _compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.00, rel=1e-6)

    def test_usage_report_cost_sonnet_small(self) -> None:
        """Sonnet pricing for small token counts."""
        cost = _compute_cost("claude-sonnet-4-6", 1000, 500)
        assert cost == pytest.approx(0.0105, rel=1e-5)

    def test_usage_report_cost_unknown_model(self) -> None:
        """Unknown model uses default sonnet rate ($3.00/$15.00)."""
        cost_unknown = _compute_cost("gpt-4-turbo", 1_000_000, 1_000_000)
        cost_sonnet = _compute_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost_unknown == cost_sonnet

    def test_cost_zero_tokens(self) -> None:
        """Zero tokens yields zero cost."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 0, 0)
        assert cost == 0.0

    def test_cost_rounded_to_six_places(self) -> None:
        """Cost is rounded to 6 decimal places."""
        cost = _compute_cost("claude-haiku-4-5-20251001", 1, 1)
        assert cost == pytest.approx(5e-6, rel=1e-4)


class TestOpus47Pricing:
    """PRD-QUAL-072 FR02 — 4.7 present, 4.6 retained, pricing mirrors 4.6."""

    def test_opus_47_pricing_present(self) -> None:
        """FR02: claude-opus-4-7 key exists in the cost table."""
        from trw_mcp.tools.usage import _COST_RATES

        assert "claude-opus-4-7" in _COST_RATES
        rates = _COST_RATES["claude-opus-4-7"]
        assert rates["input"] == 15.00
        assert rates["output"] == 75.00

    def test_opus_46_pricing_retained(self) -> None:
        """FR02: backward compat — legacy 4.6 key is not removed."""
        from trw_mcp.tools.usage import _COST_RATES

        assert "claude-opus-4-6" in _COST_RATES
        assert _COST_RATES["claude-opus-4-6"]["input"] == 15.00
        assert _COST_RATES["claude-opus-4-6"]["output"] == 75.00

    def test_opus_47_cost_compute_roundtrips(self) -> None:
        """FR02: _compute_cost resolves 4.7 without falling back to default."""
        assert _compute_cost("claude-opus-4-7", 1_000_000, 1_000_000) == 90.0
