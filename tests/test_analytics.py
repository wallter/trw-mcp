"""Tests for PRD-CORE-110 base-62 ID generation in analytics/core.py.

Covers:
- test_new_id_format: generate_learning_id returns base-62 format
- test_hex_fallback_on_runtime_error: fallback to hex when RuntimeError
"""

from __future__ import annotations

import re
from unittest.mock import patch


class TestGenerateLearningId:
    """Tests for the updated generate_learning_id() with base-62 encoding."""

    def test_new_id_format(self) -> None:
        """generate_learning_id returns format L-{4 base62 chars}."""
        from trw_mcp.state.analytics.core import generate_learning_id

        lid = generate_learning_id()
        assert re.match(r"^L-[a-zA-Z0-9]{4}$", lid), f"ID {lid!r} does not match base-62 format"

    def test_id_starts_with_prefix(self) -> None:
        """ID always starts with 'L-'."""
        from trw_mcp.state.analytics.core import generate_learning_id

        lid = generate_learning_id()
        assert lid.startswith("L-")

    def test_id_length(self) -> None:
        """ID has exactly 6 chars (L- + 4 base62 chars)."""
        from trw_mcp.state.analytics.core import generate_learning_id

        lid = generate_learning_id()
        assert len(lid) == 6

    def test_ids_are_unique(self) -> None:
        """Multiple calls produce different IDs (probabilistic)."""
        from trw_mcp.state.analytics.core import generate_learning_id

        ids = {generate_learning_id() for _ in range(20)}
        # With 14.7M combinations, collisions in 20 draws are astronomically unlikely
        assert len(ids) == 20

    def test_hex_fallback_on_runtime_error(self) -> None:
        """When generate_compact_id raises RuntimeError, falls back to hex format."""
        from trw_mcp.state.analytics.core import generate_learning_id

        with patch("trw_memory.utils.generate_compact_id", side_effect=RuntimeError("exhausted")):
            lid = generate_learning_id()
        # Fallback produces hex format: L-{8 hex chars}
        assert re.match(r"^L-[a-f0-9]{8}$", lid), f"Fallback ID {lid!r} does not match hex format"

    def test_hex_fallback_starts_with_prefix(self) -> None:
        """Fallback ID still starts with 'L-'."""
        from trw_mcp.state.analytics.core import generate_learning_id

        with patch("trw_memory.utils.generate_compact_id", side_effect=RuntimeError("exhausted")):
            lid = generate_learning_id()
        assert lid.startswith("L-")
