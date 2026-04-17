"""Tests for LearningEntry coerce validators rejecting invalid enum values.

PRD-CORE-110 fix: _coerce_type, _coerce_confidence, and _coerce_protection_tier
must raise ValueError on invalid non-empty strings instead of silently coercing.
Empty strings still default to backward-compatible values.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trw_mcp.models.learning import LearningEntry


class TestCoerceTypeRejects:
    """_coerce_type raises on invalid non-empty string."""

    def test_invalid_type_raises(self) -> None:
        """Invalid type string raises ValidationError."""
        with pytest.raises(ValidationError, match="type must be one of"):
            LearningEntry(id="L-x", summary="s", detail="d", type="bogus")

    def test_empty_type_defaults_to_pattern(self) -> None:
        """Empty string defaults to 'pattern' for backward compat."""
        entry = LearningEntry(id="L-x", summary="s", detail="d", type="")
        assert entry.type == "pattern"

    def test_valid_types_accepted(self) -> None:
        """All valid type strings are accepted."""
        for t in ("incident", "pattern", "convention", "hypothesis", "workaround"):
            entry = LearningEntry(id="L-x", summary="s", detail="d", type=t)
            assert entry.type == t

    def test_non_string_raises(self) -> None:
        """Non-string type raises ValueError."""
        with pytest.raises(ValidationError, match="type must be a string"):
            LearningEntry(id="L-x", summary="s", detail="d", type=42)  # type: ignore[arg-type]


class TestCoerceConfidenceRejects:
    """_coerce_confidence raises on invalid non-empty string."""

    def test_invalid_confidence_raises(self) -> None:
        """Invalid confidence string raises ValidationError."""
        with pytest.raises(ValidationError, match="confidence must be one of"):
            LearningEntry(id="L-x", summary="s", detail="d", confidence="excellent")

    def test_empty_confidence_defaults_to_unverified(self) -> None:
        """Empty string defaults to 'unverified' for backward compat."""
        entry = LearningEntry(id="L-x", summary="s", detail="d", confidence="")
        assert entry.confidence == "unverified"

    def test_valid_confidences_accepted(self) -> None:
        """All valid confidence strings are accepted."""
        for c in ("hypothesis", "unverified", "low", "medium", "high", "verified"):
            entry = LearningEntry(id="L-x", summary="s", detail="d", confidence=c)
            assert entry.confidence == c

    def test_non_string_raises(self) -> None:
        """Non-string confidence raises ValueError."""
        with pytest.raises(ValidationError, match="confidence must be a string"):
            LearningEntry(id="L-x", summary="s", detail="d", confidence=99)  # type: ignore[arg-type]


class TestCoerceProtectionTierRejects:
    """_coerce_protection_tier raises on invalid non-empty string."""

    def test_invalid_tier_raises(self) -> None:
        """Invalid protection_tier string raises ValidationError."""
        with pytest.raises(ValidationError, match="protection_tier must be one of"):
            LearningEntry(id="L-x", summary="s", detail="d", protection_tier="top-secret")

    def test_empty_tier_defaults_to_normal(self) -> None:
        """Empty string defaults to 'normal' for backward compat."""
        entry = LearningEntry(id="L-x", summary="s", detail="d", protection_tier="")
        assert entry.protection_tier == "normal"

    def test_valid_tiers_accepted(self) -> None:
        """All valid protection_tier strings are accepted."""
        for p in ("critical", "high", "normal", "low", "protected", "permanent"):
            entry = LearningEntry(id="L-x", summary="s", detail="d", protection_tier=p)
            assert entry.protection_tier == p

    def test_non_string_raises(self) -> None:
        """Non-string protection_tier raises ValueError."""
        with pytest.raises(ValidationError, match="protection_tier must be a string"):
            LearningEntry(id="L-x", summary="s", detail="d", protection_tier=3.14)  # type: ignore[arg-type]
