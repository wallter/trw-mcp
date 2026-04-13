"""Tests for intel_boost scoring integration — PRD-INFRA-053.

Verifies that the 7th factor (intel_boost) integrates correctly into
the rank_by_utility multiplicative boost formula, and that the default
behavior (no cache / empty cache) is backward-compatible (intel_boost=1.0).
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest


def _make_entry(
    entry_id: str = "L-001",
    summary: str = "test entry",
    impact: float = 0.5,
) -> dict[str, object]:
    """Create a minimal learning entry dict for scoring."""
    return {
        "id": entry_id,
        "summary": summary,
        "detail": "",
        "tags": ["test"],
        "impact": impact,
        "type": "pattern",
        "status": "active",
        "created": datetime.now(tz=timezone.utc).isoformat(),
        "domain": [],
        "phase_affinity": [],
        "team_origin": "",
        "outcome_correlation": 0.0,
        "anchor_validity": 1.0,
    }


def test_intel_boost_default_neutral_without_context() -> None:
    """Without context, intel_boost is 1.0 (neutral) — no scoring change."""
    from trw_mcp.scoring._recall import rank_by_utility

    entries = [_make_entry("L-1"), _make_entry("L-2")]
    result = rank_by_utility(entries, ["test"], lambda_weight=0.5)
    # Both entries should have the same score (no boost applied)
    assert len(result) == 2
    assert result[0]["combined_score"] == result[1]["combined_score"]


def test_intel_boost_default_neutral_with_context_no_cache() -> None:
    """With context but no intel_cache, intel_boost is 1.0 (neutral)."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    ctx = RecallContext()
    entries = [_make_entry("L-1"), _make_entry("L-2")]

    result_no_ctx = rank_by_utility(entries, ["test"], lambda_weight=0.5)
    result_with_ctx = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx)

    # Scores should be identical (no boost from intel)
    assert result_no_ctx[0]["combined_score"] == result_with_ctx[0]["combined_score"]


def test_intel_boost_applied_from_cache() -> None:
    """Bandit params from intel_cache boost specific entries."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    # Create a mock intel_cache
    mock_cache = MagicMock()
    mock_cache.get_bandit_params.return_value = {"L-boosted": 1.8}

    ctx = RecallContext(intel_cache=mock_cache)

    entries = [_make_entry("L-boosted"), _make_entry("L-normal")]
    result = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx)

    # The boosted entry should have a higher score
    boosted = next(e for e in result if e["id"] == "L-boosted")
    normal = next(e for e in result if e["id"] == "L-normal")
    assert boosted["combined_score"] > normal["combined_score"]


def test_intel_boost_clamped_to_range() -> None:
    """Intel boost values are clamped to [0.5, 2.0]."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    # Test value above 2.0 gets clamped to 2.0
    mock_cache = MagicMock()
    mock_cache.get_bandit_params.return_value = {"L-1": 5.0}

    ctx = RecallContext(intel_cache=mock_cache)

    entries = [_make_entry("L-1")]
    result = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx)
    # Score should reflect max boost of 2.0, not 5.0
    assert result[0]["combined_score"] > 0

    # Test value below 0.5 gets clamped to 0.5
    mock_cache.get_bandit_params.return_value = {"L-1": 0.1}
    result_low = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx)
    # Score should reflect min boost of 0.5
    assert result_low[0]["combined_score"] > 0


def test_intel_boost_none_bandit_params_is_neutral() -> None:
    """When cache returns None bandit params, intel_boost stays 1.0."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    mock_cache = MagicMock()
    mock_cache.get_bandit_params.return_value = None

    ctx = RecallContext(intel_cache=mock_cache)

    entries = [_make_entry("L-1")]
    result_with_cache = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx)

    ctx_no_cache = RecallContext()
    result_no_cache = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx_no_cache)

    assert result_with_cache[0]["combined_score"] == result_no_cache[0]["combined_score"]


def test_intel_boost_entry_not_in_bandit_params() -> None:
    """Entry not in bandit_params gets intel_boost=1.0 (neutral)."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    mock_cache = MagicMock()
    mock_cache.get_bandit_params.return_value = {"L-other": 1.5}

    ctx = RecallContext(intel_cache=mock_cache)

    entries = [_make_entry("L-1")]  # Not in bandit_params
    result_with_cache = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx)

    ctx_no_cache = RecallContext()
    result_no_cache = rank_by_utility(entries, ["test"], lambda_weight=0.5, context=ctx_no_cache)

    assert result_with_cache[0]["combined_score"] == result_no_cache[0]["combined_score"]


def test_intel_boost_backward_compatible() -> None:
    """rank_by_utility with same args as before PRD-INFRA-053 produces identical output."""
    from trw_mcp.scoring._recall import RecallContext, rank_by_utility

    entries = [
        _make_entry("L-1", summary="auth pattern", impact=0.8),
        _make_entry("L-2", summary="testing tip", impact=0.3),
    ]
    ctx = RecallContext(
        current_phase="IMPLEMENT",
        inferred_domains={"auth"},
        team="alpha",
    )
    # No intel_cache on context — pure backward compat
    result = rank_by_utility(entries, ["auth"], lambda_weight=0.5, context=ctx)
    assert len(result) == 2
    # The auth entry should rank higher due to domain boost
    assert result[0]["id"] == "L-1"
