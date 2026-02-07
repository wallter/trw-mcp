"""Tests for Pydantic models — run, config, learning, requirements."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from trw_mcp.models.config import TRWConfig, PhaseTimeCaps
from trw_mcp.models.run import (
    Confidence,
    Event,
    OutputContract,
    Phase,
    RunState,
    RunStatus,
    ShardCard,
    ShardStatus,
    WaveEntry,
    WaveManifest,
    WaveStatus,
)
from trw_mcp.models.learning import (
    Analytics,
    ContextArchitecture,
    ContextConventions,
    LearningEntry,
    LearningIndex,
    Pattern,
    PatternIndex,
    Reflection,
    Script,
    ScriptIndex,
)
from trw_mcp.models.requirements import (
    EvidenceLevel,
    PRDConfidence,
    PRDDates,
    PRDEvidence,
    PRDFrontmatter,
    PRDQualityGates,
    PRDStatus,
    PRDTraceability,
    Priority,
    Requirement,
    TraceabilityResult,
    ValidationFailure,
    ValidationResult,
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
        assert config.framework_version == "v17.1_TRW"
        assert config.telemetry is False

    def test_learning_defaults(self) -> None:
        config = TRWConfig()
        assert config.learning_max_entries == 500
        assert config.learning_prune_threshold == 0.3
        assert config.learning_promotion_impact == 0.7
        assert config.learning_prune_age_days == 30
        assert config.learning_repeated_op_threshold == 3
        assert config.claude_md_max_lines == 200
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


class TestPhaseTimeCaps:
    """Tests for PhaseTimeCaps."""

    def test_defaults(self) -> None:
        caps = PhaseTimeCaps()
        assert caps.research == 0.25
        assert caps.plan == 0.15
        assert caps.implement == 0.40

    def test_get_cap_valid(self) -> None:
        caps = PhaseTimeCaps()
        assert caps.get_cap("research") == 0.25
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
        assert state.framework == "v17.1_TRW"
        assert state.status == RunStatus.ACTIVE
        assert state.phase == Phase.RESEARCH
        assert state.confidence == Confidence.MEDIUM

    def test_create_full(self) -> None:
        state = RunState(
            run_id="20260206T120000Z-abcd1234",
            task="my-task",
            framework="v17.1_TRW",
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
        contract = OutputContract(
            file="result.yaml",
            schema_keys=["summary", "findings"],
            required=True,
        )
        card = ShardCard(
            id="shard-002",
            title="Contracted shard",
            wave=1,
            output_contract=contract,
        )
        assert card.output_contract is not None
        assert card.output_contract.file == "result.yaml"

    def test_wave_must_be_positive(self) -> None:
        with pytest.raises(Exception):
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
        with pytest.raises(Exception):
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
        assert analytics.top_tools_used == []
