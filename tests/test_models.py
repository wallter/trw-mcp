"""Tests for Pydantic models — run, config, learning, requirements."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import PhaseTimeCaps, TRWConfig, _reset_config, get_config
from trw_mcp.models.learning import (
    Analytics,
    LearningEntry,
    Pattern,
    Reflection,
)
from trw_mcp.models.requirements import (
    PRDFrontmatter,
    PRDStatus,
    Priority,
    TraceabilityResult,
    ValidationFailure,
    ValidationResult,
)
from trw_mcp.models.run import (
    Confidence,
    Event,
    OutputContract,
    Phase,
    RunState,
    RunStatus,
    ShardCard,
    WaveEntry,
    WaveManifest,
    WaveStatus,
)


class TestTRWConfig:
    """Tests for TRWConfig defaults and environment override."""

    def test_defaults(self) -> None:
        config = TRWConfig()
        assert config.parallelism_max == 10
        assert config.min_shards_target == 3
        assert config.consensus_quorum == 0.67
        assert config.checkpoint_secs == 600
        assert config.timebox_hours == 8
        assert config.framework_version.startswith("v") and "_TRW" in config.framework_version
        assert config.telemetry is False

    def test_learning_defaults(self) -> None:
        config = TRWConfig()
        assert config.learning_max_entries == 500
        assert config.learning_promotion_impact == 0.7
        assert config.learning_prune_age_days == 30
        assert config.learning_repeated_op_threshold == 3
        assert config.claude_md_max_lines == 500
        assert config.sub_claude_md_max_lines == 50

    def test_aaref_quality_gates(self) -> None:
        config = TRWConfig()
        assert config.ambiguity_rate_max == 0.05
        assert config.completeness_min == 0.85
        assert config.traceability_coverage_min == 0.90

    def test_path_defaults(self) -> None:
        config = TRWConfig()
        assert config.trw_dir == ".trw"
        assert config.learnings_dir == "learnings"
        assert config.scripts_dir == "scripts"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_PARALLELISM_MAX", "20")
        config = TRWConfig()
        assert config.parallelism_max == 20

    def test_removed_fields_not_in_config(self) -> None:
        """PRD-FIX-016-FR02: Verify dead fields are removed."""
        config = TRWConfig()
        for removed in ("correlation_min", "learning_prune_threshold",
                        "validation_smell_false_positive_max", "llm_max_tokens"):
            assert not hasattr(config, removed), f"{removed} should be removed"

    def test_orc_defaults_still_exist(self) -> None:
        """PRD-FIX-016-FR03: ORC prompt-level fields still present with correct defaults."""
        config = TRWConfig()
        # Fields unique to ORC (min_shards_target, consensus_quorum,
        # checkpoint_secs already verified in test_defaults)
        assert config.min_shards_floor == 2
        assert config.max_child_depth == 2
        assert config.max_research_waves == 3

    def test_extra_env_vars_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PRD-FIX-016-FR05: Removed env vars are silently dropped."""
        monkeypatch.setenv("TRW_CORRELATION_MIN", "0.99")
        monkeypatch.setenv("TRW_LEARNING_PRUNE_THRESHOLD", "0.5")
        config = TRWConfig()
        assert not hasattr(config, "correlation_min")
        assert not hasattr(config, "learning_prune_threshold")

    def test_config_yaml_with_removed_keys_loads(self, tmp_path: Path) -> None:
        """PRD-FIX-016-FR05: YAML with removed keys loads without error."""
        # TRWConfig loads from env vars, not directly from YAML — extra="ignore"
        # ensures unknown keys don't break loading.
        (tmp_path / "config.yaml").write_text(
            "correlation_min: 0.99\n"
            "learning_prune_threshold: 0.5\n"
            "llm_max_tokens: 1000\n"
            "parallelism_max: 15\n"
        )
        config = TRWConfig()
        assert not hasattr(config, "correlation_min")


class TestGetConfig:
    """Tests for get_config() singleton factory."""

    def test_returns_singleton(self) -> None:
        _reset_config()
        c1 = get_config()
        c2 = get_config()
        assert c1 is c2

    def test_reset_clears(self) -> None:
        _reset_config()
        first = get_config()
        _reset_config()
        assert get_config() is not first

    def test_reset_with_custom(self) -> None:
        custom = TRWConfig(debug=True)
        _reset_config(custom)
        assert get_config() is custom
        assert get_config().debug is True
        _reset_config()

    def test_reset_none_clears_singleton(self) -> None:
        _reset_config(TRWConfig())
        assert get_config() is not None
        _reset_config(None)
        # Next call creates a fresh instance
        c = get_config()
        assert isinstance(c, TRWConfig)
        _reset_config()


class TestPhaseTimeCaps:
    """Tests for PhaseTimeCaps."""

    def test_defaults(self) -> None:
        caps = PhaseTimeCaps()
        assert caps.research == 0.25
        assert caps.plan == 0.15
        assert caps.implement == 0.35
        assert caps.validate_phase == 0.10
        assert caps.review == 0.10
        assert caps.deliver == 0.05

    def test_get_cap_valid(self) -> None:
        caps = PhaseTimeCaps()
        assert caps.get_cap("research") == 0.25
        assert caps.get_cap("implement") == 0.35
        assert caps.get_cap("deliver") == 0.05

    def test_get_cap_invalid(self) -> None:
        caps = PhaseTimeCaps()
        with pytest.raises(ValueError, match="Unknown phase"):
            caps.get_cap("invalid")


class TestConfidenceFromScore:
    """Tests for Confidence.from_score() boundary values."""

    def test_high_at_boundary(self) -> None:
        assert Confidence.from_score(0.85) == Confidence.HIGH

    def test_medium_at_boundary(self) -> None:
        assert Confidence.from_score(0.70) == Confidence.MEDIUM

    def test_low_below_medium(self) -> None:
        assert Confidence.from_score(0.69) == Confidence.LOW


class TestRunState:
    """Tests for RunState model."""

    def test_create_minimal(self) -> None:
        state = RunState(run_id="test-123", task="test-task")
        assert state.run_id == "test-123"
        assert state.task == "test-task"
        assert state.framework == "v18.0_TRW"
        assert state.status == RunStatus.ACTIVE
        assert state.phase == Phase.RESEARCH
        assert state.confidence == Confidence.MEDIUM

    def test_create_full(self) -> None:
        state = RunState(
            run_id="20260206T120000Z-abcd1234",
            task="my-task",
            framework="v18.0_TRW",
            status=RunStatus.COMPLETE,
            phase=Phase.DELIVER,
            confidence=Confidence.HIGH,
            objective="Build something",
            variables={"TASK": "my-task"},
        )
        assert state.status == "complete"  # use_enum_values converts to string
        assert state.phase == "deliver"
        assert state.variables["TASK"] == "my-task"


class TestShardCard:
    """Tests for ShardCard model."""

    def test_create_minimal(self) -> None:
        card = ShardCard(id="shard-001", title="Test shard", wave=1)
        assert card.id == "shard-001"
        assert card.wave == 1
        assert card.confidence == Confidence.MEDIUM
        assert card.self_decompose is True

    def test_with_output_contract(self) -> None:
        card = ShardCard(
            id="shard-002",
            title="Contracted shard",
            wave=1,
            output_contract=OutputContract(
                file="result.yaml",
                schema_keys=["summary", "findings"],
                required=True,
            ),
        )
        assert card.output_contract is not None
        assert card.output_contract.file == "result.yaml"

    def test_wave_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            ShardCard(id="shard-bad", title="Bad", wave=0)


class TestWaveManifest:
    """Tests for WaveManifest model."""

    def test_empty(self) -> None:
        manifest = WaveManifest()
        assert manifest.waves == []

    def test_with_waves(self) -> None:
        manifest = WaveManifest(waves=[
            WaveEntry(wave=1, shards=["shard-001", "shard-002"], status=WaveStatus.COMPLETE),
            WaveEntry(wave=2, shards=["shard-003"], status=WaveStatus.PENDING, depends_on=[1]),
        ])
        assert len(manifest.waves) == 2
        assert manifest.waves[1].depends_on == [1]


class TestEvent:
    """Tests for Event model."""

    def test_create(self) -> None:
        event = Event(
            ts=datetime(2026, 2, 6, 12, 0, 0),
            event="run_init",
            data={"task": "test"},
        )
        assert event.event == "run_init"
        assert event.data["task"] == "test"


class TestLearningEntry:
    """Tests for LearningEntry model."""

    def test_create_minimal(self) -> None:
        entry = LearningEntry(
            id="L-12345678",
            summary="Test learning",
            detail="Some detail",
        )
        assert entry.id == "L-12345678"
        assert entry.impact == 0.5
        assert entry.recurrence == 1
        assert entry.promoted_to_claude_md is False

    def test_impact_bounds(self) -> None:
        with pytest.raises(ValidationError):
            LearningEntry(id="L-bad", summary="Bad", detail="Bad", impact=1.5)

    def test_tags_default(self) -> None:
        entry = LearningEntry(id="L-00", summary="S", detail="D")
        assert entry.tags == []


class TestReflection:
    """Tests for Reflection model."""

    def test_create(self) -> None:
        reflection = Reflection(
            id="R-001",
            run_id="run-001",
            scope="session",
            timestamp=datetime(2026, 2, 6, 12, 0, 0),
            events_analyzed=42,
            what_worked=["phase transitions"],
            what_failed=["shard timeout"],
        )
        assert reflection.events_analyzed == 42
        assert len(reflection.what_worked) == 1


class TestPattern:
    """Tests for Pattern model."""

    def test_create(self) -> None:
        pattern = Pattern(
            name="error-retry",
            domain="error-handling",
            description="Retry with exponential backoff",
            confidence=0.8,
            occurrences=5,
        )
        assert pattern.occurrences == 5
        assert pattern.confidence == 0.8


class TestPRDFrontmatter:
    """Tests for PRDFrontmatter model."""

    def test_create_minimal(self) -> None:
        fm = PRDFrontmatter(id="PRD-CORE-001", title="Test PRD")
        assert fm.id == "PRD-CORE-001"
        assert fm.status == PRDStatus.DRAFT
        assert fm.priority == Priority.P1

    def test_quality_gates_defaults(self) -> None:
        fm = PRDFrontmatter(id="PRD-CORE-001", title="Test")
        assert fm.quality_gates.ambiguity_rate_max == 0.05
        assert fm.quality_gates.completeness_min == 0.85


class TestValidationResult:
    """Tests for ValidationResult model."""

    def test_valid(self) -> None:
        result = ValidationResult(valid=True, completeness_score=1.0)
        assert result.valid is True
        assert result.failures == []

    def test_with_failures(self) -> None:
        result = ValidationResult(
            valid=False,
            failures=[
                ValidationFailure(
                    field="title",
                    rule="required",
                    message="Title is required",
                    severity="error",
                ),
            ],
        )
        assert result.valid is False
        assert len(result.failures) == 1


class TestTraceabilityResult:
    """Tests for TraceabilityResult model."""

    def test_defaults(self) -> None:
        result = TraceabilityResult()
        assert result.total_requirements == 0
        assert result.coverage == 0.0
        assert result.untraced_requirements == []


class TestAnalytics:
    """Tests for Analytics model."""

    def test_defaults(self) -> None:
        analytics = Analytics()
        assert analytics.sessions_tracked == 0
        assert analytics.total_learnings == 0
        # PRD-QUAL-012-FR02/FR03: Revived fields have defaults
        assert analytics.reflections_completed == 0
        assert analytics.success_rate == 0.0
        assert analytics.q_learning_activations == 0
