"""Tests for framework models (PRD-CORE-017).

Validates FrameworkVersion parsing/rendering/compatibility,
VocabularyRegistry, and backward-compatible field additions
to RunState, LearningEntry, and TRWConfig.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.framework import (
    FrameworkVersion,
    VocabularyEntry,
    VocabularyRegistry,
)
from trw_mcp.models.learning import LearningEntry
from trw_mcp.models.run import RunState

# Shared test fixtures for vocabulary tests
_SHARD_ENTRY = VocabularyEntry(term="Shard", definition="Unit of parallel work")
_WAVE_ENTRY = VocabularyEntry(
    term="Wave",
    definition="Sequential group of shards",
    aliases=["wave-group", "shard-batch"],
)


class TestFrameworkVersion:
    """FrameworkVersion parsing, rendering, and compatibility."""

    def test_parse_major_minor(self) -> None:
        v = FrameworkVersion.parse("v18.1")
        assert v.major == 18
        assert v.minor == 1
        assert v.patch == 0
        assert v.suffix == ""

    def test_parse_with_suffix(self) -> None:
        v = FrameworkVersion.parse("v18.1_TRW")
        assert v.major == 18
        assert v.minor == 1
        assert v.patch == 0
        assert v.suffix == "_TRW"

    def test_parse_full_semver(self) -> None:
        v = FrameworkVersion.parse("v18.1.2")
        assert v.major == 18
        assert v.minor == 1
        assert v.patch == 2

    def test_parse_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="Cannot parse version"):
            FrameworkVersion.parse("not-a-version")

    def test_render_without_patch(self) -> None:
        v = FrameworkVersion(major=18, minor=1, suffix="_TRW")
        assert v.render() == "v18.1_TRW"

    def test_render_with_patch(self) -> None:
        v = FrameworkVersion(major=18, minor=1, patch=2, suffix="_TRW")
        assert v.render() == "v18.1.2_TRW"

    def test_roundtrip(self) -> None:
        original = "v18.1_TRW"
        assert FrameworkVersion.parse(original).render() == original

    def test_compatible_same_major(self) -> None:
        v1 = FrameworkVersion.parse("v18.0_TRW")
        v2 = FrameworkVersion.parse("v18.1_TRW")
        assert v1.is_compatible_with(v2)

    def test_incompatible_different_major(self) -> None:
        v1 = FrameworkVersion.parse("v17.0")
        v2 = FrameworkVersion.parse("v18.0")
        assert not v1.is_compatible_with(v2)


class TestVocabularyRegistry:
    """VocabularyRegistry term lookup by name and alias."""

    def test_get_term_by_name(self) -> None:
        reg = VocabularyRegistry(terms=[_SHARD_ENTRY])
        result = reg.get_term("Shard")
        assert result is not None
        assert result.definition == "Unit of parallel work"

    def test_get_term_case_insensitive(self) -> None:
        reg = VocabularyRegistry(terms=[_SHARD_ENTRY])
        assert reg.get_term("shard") is not None
        assert reg.get_term("SHARD") is not None

    def test_get_term_by_alias(self) -> None:
        reg = VocabularyRegistry(terms=[_WAVE_ENTRY])
        assert reg.get_term("wave-group") is not None
        assert reg.get_term("shard-batch") is not None

    def test_get_term_not_found(self) -> None:
        reg = VocabularyRegistry()
        assert reg.get_term("nonexistent") is None


class TestBackwardCompatibility:
    """New optional fields on existing models default correctly."""

    def test_runstate_defaults(self) -> None:
        state = RunState(run_id="test-001", task="test")
        assert state.run_type == "implementation"
        assert state.framework == "v18.0_TRW"

    def test_learning_entry_phase_scope_default_none(self) -> None:
        entry = LearningEntry(id="L-test", summary="test", detail="test detail")
        assert entry.phase_scope is None

    def test_learning_entry_phase_scope_accepts_value(self) -> None:
        entry = LearningEntry(
            id="L-test",
            summary="test",
            detail="test detail",
            phase_scope="implement",
        )
        assert entry.phase_scope == "implement"

    def test_config_phase_bonus_defaults(self) -> None:
        cfg = TRWConfig()
        assert cfg.phase_bonus_matching == 0.15
        assert cfg.phase_bonus_global == 0.0
        assert cfg.phase_bonus_nonmatching == -0.05
        assert cfg.strict_input_criteria is False
