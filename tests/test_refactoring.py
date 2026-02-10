"""Tests for PRD-CORE-016: Proactive Refactoring Workflow.

Covers: DebtEntry model, DebtRegistry, RefactorClassification,
refactoring budget, debt gate, MCP tools.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.debt import (
    CLASSIFICATION_ACTIONS,
    DebtCategory,
    DebtEntry,
    DebtPriority,
    DebtRegistry,
    DebtStatus,
    RefactorClassification,
    compute_refactoring_budget,
)
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


# ---------------------------------------------------------------------------
# RefactorClassification (REQ-001)
# ---------------------------------------------------------------------------


class TestRefactorClassification:
    """PRD-CORE-016-REQ-001: 2x2 classification system."""

    def test_blocking_architectural(self) -> None:
        """Cannot complete + changes interface = blocking-architectural."""
        result = RefactorClassification.classify(
            blocks_output_contract=False,
            changes_interface=True,
        )
        assert result == RefactorClassification.BLOCKING_ARCHITECTURAL

    def test_blocking_local(self) -> None:
        """Cannot complete + no interface change = blocking-local."""
        result = RefactorClassification.classify(
            blocks_output_contract=False,
            changes_interface=False,
        )
        assert result == RefactorClassification.BLOCKING_LOCAL

    def test_deferrable_architectural(self) -> None:
        """Can complete + changes interface = deferrable-architectural."""
        result = RefactorClassification.classify(
            blocks_output_contract=True,
            changes_interface=True,
        )
        assert result == RefactorClassification.DEFERRABLE_ARCHITECTURAL

    def test_deferrable_local(self) -> None:
        """Can complete + no interface change = deferrable-local."""
        result = RefactorClassification.classify(
            blocks_output_contract=True,
            changes_interface=False,
        )
        assert result == RefactorClassification.DEFERRABLE_LOCAL

    def test_all_classifications_have_actions(self) -> None:
        """Every classification has a prescribed action."""
        for cls in RefactorClassification:
            assert cls.value in CLASSIFICATION_ACTIONS


# ---------------------------------------------------------------------------
# DebtEntry Model (REQ-005)
# ---------------------------------------------------------------------------


class TestDebtEntry:
    """PRD-CORE-016-REQ-005: DebtEntry model."""

    def test_create_minimal(self) -> None:
        """DebtEntry with required fields only."""
        entry = DebtEntry(id="DEBT-001", title="Test debt")
        assert entry.id == "DEBT-001"
        assert entry.priority == "medium"
        assert entry.status == "discovered"
        assert entry.decay_score == 0.5

    def test_decay_score_computation(self) -> None:
        """Decay score increases with time and assessment count."""
        entry = DebtEntry(id="DEBT-001", title="Test", assessment_count=2)
        score = entry.compute_decay_score(days_since_discovery=10)
        # 0.3 + (10 * 0.01) + (2 * 0.05) = 0.3 + 0.1 + 0.1 = 0.5
        assert score == 0.5

    def test_decay_score_capped_at_1(self) -> None:
        """Decay score cannot exceed 1.0."""
        entry = DebtEntry(id="DEBT-001", title="Test", assessment_count=20)
        score = entry.compute_decay_score(days_since_discovery=100)
        assert score == 1.0

    def test_auto_promotion_to_critical(self) -> None:
        """Entry with decay_score >= 0.9 should auto-promote."""
        entry = DebtEntry(
            id="DEBT-001", title="Test",
            decay_score=0.95, priority="high",
        )
        assert entry.should_auto_promote() is True

    def test_no_auto_promotion_below_threshold(self) -> None:
        """Entry with decay_score < 0.9 should not auto-promote."""
        entry = DebtEntry(
            id="DEBT-001", title="Test",
            decay_score=0.85, priority="high",
        )
        assert entry.should_auto_promote() is False

    def test_no_auto_promotion_if_already_critical(self) -> None:
        """Entry already critical should not auto-promote again."""
        entry = DebtEntry(
            id="DEBT-001", title="Test",
            decay_score=0.95, priority="critical",
        )
        assert entry.should_auto_promote() is False


# ---------------------------------------------------------------------------
# DebtRegistry (REQ-005)
# ---------------------------------------------------------------------------


class TestDebtRegistry:
    """PRD-CORE-016-REQ-005: DebtRegistry."""

    def test_empty_registry(self) -> None:
        """Empty registry has no entries."""
        registry = DebtRegistry()
        assert registry.entries == []
        assert registry.version == "1.0"

    def test_next_id_empty(self) -> None:
        """First ID is DEBT-001."""
        registry = DebtRegistry()
        assert registry.next_id() == "DEBT-001"

    def test_next_id_sequential(self) -> None:
        """Next ID increments from highest existing."""
        registry = DebtRegistry(entries=[
            DebtEntry(id="DEBT-001", title="First"),
            DebtEntry(id="DEBT-003", title="Third"),
        ])
        assert registry.next_id() == "DEBT-004"

    def test_roundtrip_yaml(self, tmp_path: Path) -> None:
        """Registry loads and saves without data loss."""
        writer = FileStateWriter()
        reader = FileStateReader()

        registry = DebtRegistry(entries=[
            DebtEntry(
                id="DEBT-001",
                title="Test debt",
                description="Some issue",
                classification="deferrable-local",
                priority="medium",
                category="code_quality",
                decay_score=0.6,
            ),
        ])

        from trw_mcp.state.persistence import model_to_dict
        path = tmp_path / "debt-registry.yaml"
        writer.write_yaml(path, model_to_dict(registry))

        loaded_data = reader.read_yaml(path)
        loaded = DebtRegistry(**loaded_data)
        assert len(loaded.entries) == 1
        assert loaded.entries[0].id == "DEBT-001"
        assert loaded.entries[0].decay_score == 0.6

    def test_get_actionable(self) -> None:
        """Actionable items are above threshold and not resolved."""
        registry = DebtRegistry(entries=[
            DebtEntry(id="DEBT-001", title="High", decay_score=0.8, status="assessed"),
            DebtEntry(id="DEBT-002", title="Low", decay_score=0.3, status="assessed"),
            DebtEntry(id="DEBT-003", title="Resolved", decay_score=0.9, status="resolved"),
            DebtEntry(id="DEBT-004", title="Critical", decay_score=0.95, status="discovered"),
        ])
        actionable = registry.get_actionable(decay_threshold=0.7)
        assert len(actionable) == 2
        # Sorted by decay_score descending
        assert actionable[0].id == "DEBT-004"
        assert actionable[1].id == "DEBT-001"


# ---------------------------------------------------------------------------
# Refactoring Budget (REQ-004)
# ---------------------------------------------------------------------------


class TestRefactoringBudget:
    """PRD-CORE-016-REQ-004: Refactoring budget calculation."""

    def test_no_debt(self) -> None:
        """No debt = 0 refactoring shards."""
        result = compute_refactoring_budget(5, False, False)
        assert result["refactor_shards"] == 0
        assert result["feature_shards"] == 5

    def test_high_debt(self) -> None:
        """High debt = at least 15% allocation."""
        result = compute_refactoring_budget(5, False, True)
        assert result["refactor_shards"] >= 1
        assert result["refactor_shards"] + result["feature_shards"] == 5

    def test_critical_debt(self) -> None:
        """Critical debt = up to 20% allocation."""
        result = compute_refactoring_budget(5, True, False)
        assert result["refactor_shards"] >= 1
        assert result["refactor_shards"] + result["feature_shards"] == 5

    def test_single_shard_wave(self) -> None:
        """Single-shard wave with debt allocates 1 refactor shard."""
        result = compute_refactoring_budget(1, True, True)
        assert result["refactor_shards"] == 1
        assert result["feature_shards"] == 0


# ---------------------------------------------------------------------------
# MCP Tools Integration
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def set_project_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set TRW_PROJECT_ROOT to temp directory for all tests."""
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    # Create .trw dir
    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir(parents=True, exist_ok=True)

    import trw_mcp.tools.refactoring as refactor_mod
    monkeypatch.setattr(refactor_mod, "_config", refactor_mod.TRWConfig())
    return tmp_path


@pytest.fixture
def tools(tmp_path: Path) -> dict[str, object]:
    """Create refactoring tools on a fresh FastMCP server."""
    from fastmcp import FastMCP
    from trw_mcp.tools.refactoring import register_refactoring_tools

    srv = FastMCP("test-refactoring")
    register_refactoring_tools(srv)
    return {t.name: t for t in srv._tool_manager._tools.values()}


class TestTrwRefactorClassifyTool:
    """MCP tool: trw_refactor_classify."""

    def test_classify_blocking_architectural(self, tools: dict[str, object]) -> None:
        result = tools["trw_refactor_classify"].fn(
            description="Cross-module interface change needed",
            blocks_output_contract=False,
            changes_interface=True,
        )
        assert result["classification"] == "blocking-architectural"
        assert "prerequisite PRD" in result["action"].lower() or "PRD" in result["action"]

    def test_classify_deferrable_local(self, tools: dict[str, object]) -> None:
        result = tools["trw_refactor_classify"].fn(
            description="Dead code cleanup",
            blocks_output_contract=True,
            changes_interface=False,
        )
        assert result["classification"] == "deferrable-local"


class TestTrwDebtRegisterTool:
    """MCP tool: trw_debt_register."""

    def test_create_entry(self, tools: dict[str, object], tmp_path: Path) -> None:
        """Creates a new debt entry."""
        result = tools["trw_debt_register"].fn(
            title="Duplicated validation logic",
            description="Logic duplicated across 4 tool functions",
            classification="deferrable-architectural",
            priority="high",
            category="code_duplication",
            affected_files=["src/tools/orchestration.py", "src/tools/learning.py"],
        )
        assert result["status"] == "created"
        assert result["debt_id"] == "DEBT-001"
        assert result["classification"] == "deferrable-architectural"

    def test_resolve_entry(self, tools: dict[str, object], tmp_path: Path) -> None:
        """Resolves an existing debt entry."""
        # Create first
        tools["trw_debt_register"].fn(
            title="Test debt",
            priority="medium",
        )

        # Resolve
        result = tools["trw_debt_register"].fn(
            title="",  # Not used for resolve
            resolve_id="DEBT-001",
            resolve_prd="PRD-FIX-099",
        )
        assert result["status"] == "resolved"
        assert result["debt_id"] == "DEBT-001"

    def test_resolve_nonexistent(self, tools: dict[str, object]) -> None:
        """Resolving non-existent entry returns not_found."""
        result = tools["trw_debt_register"].fn(
            title="",
            resolve_id="DEBT-999",
        )
        assert result["status"] == "not_found"

    def test_sequential_ids(self, tools: dict[str, object]) -> None:
        """Multiple entries get sequential IDs."""
        r1 = tools["trw_debt_register"].fn(title="First")
        r2 = tools["trw_debt_register"].fn(title="Second")
        assert r1["debt_id"] == "DEBT-001"
        assert r2["debt_id"] == "DEBT-002"


class TestTrwDebtGateTool:
    """MCP tool: trw_debt_gate."""

    def test_empty_registry(self, tools: dict[str, object]) -> None:
        """No debt = clean gate."""
        result = tools["trw_debt_gate"].fn(phase="plan")
        assert result["total_active_debt"] == 0
        assert result["debt_assessment"]["critical"] == 0
        assert "gate_warning" not in result

    def test_with_critical_debt(self, tools: dict[str, object]) -> None:
        """Critical debt triggers gate warning."""
        tools["trw_debt_register"].fn(
            title="Critical issue",
            priority="critical",
        )
        result = tools["trw_debt_gate"].fn(phase="plan")
        assert result["total_active_debt"] == 1
        assert result["debt_assessment"]["critical"] == 1
        assert "gate_warning" in result

    def test_budget_recommendation(self, tools: dict[str, object]) -> None:
        """Budget recommendation reflects debt status."""
        tools["trw_debt_register"].fn(title="High debt", priority="high")
        result = tools["trw_debt_gate"].fn(phase="plan")
        budget = result["budget_recommendation"]
        assert budget["refactor_shards"] >= 1

    def test_validate_phase(self, tools: dict[str, object]) -> None:
        """Validate phase works without gate_warning."""
        result = tools["trw_debt_gate"].fn(phase="validate")
        assert result["phase"] == "validate"
        assert "gate_warning" not in result


# ---------------------------------------------------------------------------
# Configurable Parameters (Zero Magic verification)
# ---------------------------------------------------------------------------


class TestConfigurableDecay:
    """Verify decay formula uses configurable parameters, not magic numbers."""

    def test_custom_base_score(self) -> None:
        """Custom base_score changes computation result."""
        entry = DebtEntry(id="DEBT-001", title="Test", assessment_count=0)
        default_score = entry.compute_decay_score(0)
        custom_score = entry.compute_decay_score(0, base_score=0.5)
        assert default_score == 0.3
        assert custom_score == 0.5

    def test_custom_daily_rate(self) -> None:
        """Custom daily_rate changes time contribution."""
        entry = DebtEntry(id="DEBT-001", title="Test", assessment_count=0)
        default_score = entry.compute_decay_score(10)
        fast_score = entry.compute_decay_score(10, daily_rate=0.05)
        assert default_score == 0.4  # 0.3 + 10*0.01
        assert fast_score == 0.8  # 0.3 + 10*0.05

    def test_custom_assessment_rate(self) -> None:
        """Custom assessment_rate changes assessment contribution."""
        entry = DebtEntry(id="DEBT-001", title="Test", assessment_count=5)
        default_score = entry.compute_decay_score(0)
        fast_score = entry.compute_decay_score(0, assessment_rate=0.10)
        assert default_score == 0.55  # 0.3 + 5*0.05
        assert fast_score == 0.8  # 0.3 + 5*0.10


class TestConfigurableAutoPromote:
    """Verify auto-promote uses configurable threshold."""

    def test_custom_threshold_triggers_promotion(self) -> None:
        """Lower threshold triggers promotion at lower scores."""
        entry = DebtEntry(
            id="DEBT-001", title="Test",
            decay_score=0.7, priority="high",
        )
        assert entry.should_auto_promote() is False
        assert entry.should_auto_promote(threshold=0.6) is True

    def test_custom_threshold_prevents_promotion(self) -> None:
        """Higher threshold prevents promotion at default scores."""
        entry = DebtEntry(
            id="DEBT-001", title="Test",
            decay_score=0.92, priority="high",
        )
        assert entry.should_auto_promote() is True
        assert entry.should_auto_promote(threshold=0.95) is False


class TestConfigurableRegistry:
    """Verify registry uses configurable prefix and threshold."""

    def test_custom_id_prefix(self) -> None:
        """Custom prefix changes generated ID format."""
        registry = DebtRegistry()
        assert registry.next_id(prefix="TD") == "TD-001"

    def test_custom_id_prefix_sequential(self) -> None:
        """Custom prefix with existing entries increments correctly."""
        registry = DebtRegistry(entries=[
            DebtEntry(id="TD-005", title="Existing"),
        ])
        assert registry.next_id(prefix="TD") == "TD-006"

    def test_custom_actionable_threshold(self) -> None:
        """Custom threshold changes which entries are actionable."""
        registry = DebtRegistry(entries=[
            DebtEntry(id="DEBT-001", title="Mid", decay_score=0.5, status="assessed"),
            DebtEntry(id="DEBT-002", title="High", decay_score=0.8, status="assessed"),
        ])
        strict = registry.get_actionable(decay_threshold=0.7)
        lenient = registry.get_actionable(decay_threshold=0.4)
        assert len(strict) == 1
        assert len(lenient) == 2


class TestConfigurableBudget:
    """Verify budget calculation uses configurable ratios."""

    def test_custom_critical_ratio(self) -> None:
        """Custom critical_ratio changes allocation."""
        # Default 20% of 10 shards = 2
        default = compute_refactoring_budget(10, True, False)
        # 50% of 10 shards = 5
        custom = compute_refactoring_budget(10, True, False, critical_ratio=0.50)
        assert default["refactor_shards"] == 2
        assert custom["refactor_shards"] == 5

    def test_custom_high_ratio(self) -> None:
        """Custom high_ratio changes allocation."""
        # Default 15% of 10 shards = 2 (ceil)
        default = compute_refactoring_budget(10, False, True)
        # 30% of 10 shards = 3
        custom = compute_refactoring_budget(10, False, True, high_ratio=0.30)
        assert default["refactor_shards"] == 2
        assert custom["refactor_shards"] == 3


class TestConfigFields:
    """Verify TRWConfig has all debt-related fields."""

    def test_debt_config_defaults(self) -> None:
        """All debt config fields exist with expected defaults."""
        from trw_mcp.models.config import TRWConfig
        config = TRWConfig()
        assert config.debt_registry_filename == "debt-registry.yaml"
        assert config.debt_id_prefix == "DEBT"
        assert config.debt_initial_decay_score == 0.5
        assert config.debt_decay_base_score == 0.3
        assert config.debt_decay_daily_rate == 0.01
        assert config.debt_decay_assessment_rate == 0.05
        assert config.debt_auto_promote_threshold == 0.9
        assert config.debt_actionable_threshold == 0.7
        assert config.debt_budget_critical_ratio == 0.20
        assert config.debt_budget_high_ratio == 0.15
        assert config.debt_default_wave_size == 5

    def test_debt_config_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Debt config fields can be overridden via env vars."""
        monkeypatch.setenv("TRW_DEBT_AUTO_PROMOTE_THRESHOLD", "0.95")
        monkeypatch.setenv("TRW_DEBT_DEFAULT_WAVE_SIZE", "8")
        from trw_mcp.models.config import TRWConfig
        config = TRWConfig()
        assert config.debt_auto_promote_threshold == 0.95
        assert config.debt_default_wave_size == 8

    def test_model_validate_roundtrip(self, tmp_path: Path) -> None:
        """DebtRegistry.model_validate works with read_yaml output."""
        writer = FileStateWriter()
        reader = FileStateReader()

        from trw_mcp.state.persistence import model_to_dict
        registry = DebtRegistry(entries=[
            DebtEntry(id="DEBT-001", title="Test", decay_score=0.6),
        ])
        path = tmp_path / "test-registry.yaml"
        writer.write_yaml(path, model_to_dict(registry))

        data = reader.read_yaml(path)
        loaded = DebtRegistry.model_validate(data)
        assert len(loaded.entries) == 1
        assert loaded.entries[0].id == "DEBT-001"
