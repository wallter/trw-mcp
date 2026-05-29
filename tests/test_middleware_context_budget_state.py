"""State, config, and hashing tests for context budget middleware."""

from __future__ import annotations

import pytest
from mcp.types import TextContent

from trw_mcp.middleware._compression import hash_content
from trw_mcp.middleware.context_budget import get_turn_count, get_verbosity_tier, reset_state


class TestTurnTracking:
    """Tests for per-session turn count management."""

    def test_reset_state(self) -> None:
        """reset_state clears all counts."""
        from trw_mcp.middleware.context_budget import _turn_counts

        _turn_counts["sess-1"] = 5
        reset_state()
        assert get_turn_count("sess-1") == 0


class TestVerbosityTiers:
    """Tests for get_verbosity_tier with default and custom thresholds."""

    def test_full_tier_default(self) -> None:
        """Turns 1-10 return 'full' with defaults."""
        for turn in range(1, 11):
            assert get_verbosity_tier(turn) == "full"

    def test_compact_tier(self) -> None:
        """Turns 11-30 return 'compact' with defaults."""
        for turn in (11, 20, 30):
            assert get_verbosity_tier(turn) == "compact"

    def test_minimal_tier(self) -> None:
        """Turns 31+ return 'minimal' with defaults."""
        for turn in (31, 50, 100):
            assert get_verbosity_tier(turn) == "minimal"

    def test_custom_thresholds(self) -> None:
        """Custom compact_after and minimal_after values respected."""
        assert get_verbosity_tier(3, compact_after=2, minimal_after=5) == "compact"
        assert get_verbosity_tier(6, compact_after=2, minimal_after=5) == "minimal"
        assert get_verbosity_tier(1, compact_after=2, minimal_after=5) == "full"


class TestConfig:
    """Tests for TRWConfig observation masking defaults."""

    def test_config_defaults(self) -> None:
        """TRWConfig has correct default values for observation masking."""
        from trw_mcp.models.config import TRWConfig

        config = TRWConfig()
        assert config.observation_masking is True
        assert config.compact_after_turns == 10
        assert config.minimal_after_turns == 30

    def test_config_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Environment variables override defaults."""
        from trw_mcp.models.config import TRWConfig

        monkeypatch.setenv("TRW_OBSERVATION_MASKING", "false")
        monkeypatch.setenv("TRW_COMPACT_AFTER_TURNS", "5")
        monkeypatch.setenv("TRW_MINIMAL_AFTER_TURNS", "15")
        config = TRWConfig()
        assert config.observation_masking is False
        assert config.compact_after_turns == 5
        assert config.minimal_after_turns == 15


class TestHashContent:
    """Tests for the content hashing utility."""

    def test_same_content_same_hash(self) -> None:
        """Identical TextContent produces identical hashes."""
        c1 = [TextContent(type="text", text="hello")]
        c2 = [TextContent(type="text", text="hello")]
        assert hash_content(c1) == hash_content(c2)

    def test_different_content_different_hash(self) -> None:
        """Different text produces different hashes."""
        c1 = [TextContent(type="text", text="hello")]
        c2 = [TextContent(type="text", text="world")]
        assert hash_content(c1) != hash_content(c2)
