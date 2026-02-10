"""Tests for framework models (PRD-CORE-017 Step 2.1).

Validates FrameworkVersion parsing/rendering/compatibility, PhaseOverlay,
OverlayRegistry, VocabularyRegistry, and backward-compatible field additions
to RunState, LearningEntry, and TRWConfig.
"""

from __future__ import annotations

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.framework import (
    FrameworkVersion,
    OverlayPhase,
    OverlayRegistry,
    PhaseOverlay,
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


class TestPhaseOverlay:
    """PhaseOverlay model defaults and explicit construction."""

    def test_defaults(self) -> None:
        overlay = PhaseOverlay(phase=OverlayPhase.RESEARCH)
        assert overlay.phase == "research"
        assert overlay.version == "v18.1"
        assert overlay.filename == ""
        assert overlay.content_hash == ""
        assert overlay.line_count == 0
        assert overlay.token_estimate == 0
        assert overlay.sections == []

    def test_explicit_values(self) -> None:
        overlay = PhaseOverlay(
            phase=OverlayPhase.IMPLEMENT,
            version="v18.1.1",
            filename="trw-implement.md",
            content_hash="abc123",
            line_count=180,
            token_estimate=4500,
            sections=["WAVE ORCHESTRATION", "OUTPUT CONTRACTS"],
        )
        assert overlay.phase == "implement"
        assert overlay.line_count == 180
        assert len(overlay.sections) == 2


class TestOverlayRegistry:
    """OverlayRegistry lookup and construction."""

    def test_empty_registry(self) -> None:
        reg = OverlayRegistry()
        assert reg.core_version == "v18.1"
        assert reg.overlays == []
        assert reg.get_overlay("research") is None

    def test_get_overlay_found(self) -> None:
        overlay = PhaseOverlay(phase=OverlayPhase.PLAN, filename="trw-plan.md")
        reg = OverlayRegistry(overlays=[overlay])
        result = reg.get_overlay("plan")
        assert result is not None
        assert result.filename == "trw-plan.md"

    def test_get_overlay_not_found(self) -> None:
        overlay = PhaseOverlay(phase=OverlayPhase.RESEARCH)
        reg = OverlayRegistry(overlays=[overlay])
        assert reg.get_overlay("deliver") is None


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

    def test_runstate_overlay_fields_default_none(self) -> None:
        state = RunState(run_id="test-001", task="test")
        assert state.overlay_version is None
        assert state.assembled_framework_hash is None

    def test_runstate_overlay_fields_accept_values(self) -> None:
        state = RunState(
            run_id="test-002",
            task="test",
            overlay_version="v18.1",
            assembled_framework_hash="sha256:abc",
        )
        assert state.overlay_version == "v18.1"
        assert state.assembled_framework_hash == "sha256:abc"

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

    def test_config_overlay_defaults(self) -> None:
        cfg = TRWConfig()
        assert cfg.overlays_dir == "overlays"
        assert cfg.vocabulary_file == "vocabulary.yaml"
        assert cfg.phase_bonus_matching == 0.15
        assert cfg.phase_bonus_global == 0.0
        assert cfg.phase_bonus_nonmatching == -0.05
        assert cfg.strict_input_criteria is False
        assert cfg.drift_check_on_init is False

    def test_config_framework_version(self) -> None:
        cfg = TRWConfig()
        assert cfg.framework_version == "v18.1_TRW"
