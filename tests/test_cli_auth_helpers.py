"""Tests for CLI auth helper behavior."""

from __future__ import annotations

from trw_mcp.cli.auth import _format_countdown


class TestFormatCountdown:
    def test_minutes_and_seconds(self) -> None:
        assert _format_countdown(863) == "14:23"

    def test_zero(self) -> None:
        assert _format_countdown(0) == "0:00"

    def test_negative_clamps(self) -> None:
        assert _format_countdown(-5) == "0:00"

    def test_exact_minute(self) -> None:
        assert _format_countdown(60) == "1:00"

    def test_seconds_only(self) -> None:
        assert _format_countdown(45) == "0:45"
