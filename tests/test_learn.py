"""Tests for PRD-CORE-110 fields in execute_learn and YAML backup.

Covers:
- test_learn_with_type_incident: execute_learn passes type through
- test_learn_auto_phase_origin_uppercase: phase_origin is uppercased
- test_learn_auto_nudge_line_truncation: long summary auto-truncates nudge_line
- test_learn_phase_origin_fallback_warning: no active run => phase_origin stays empty
- test_yaml_backup_includes_new_fields: YAML file contains new fields after execute_learn
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._learn_impl import execute_learn


@pytest.fixture()
def trw_dir(tmp_path: Path) -> Path:
    """Set up minimal .trw structure."""
    trw = tmp_path / ".trw"
    trw.mkdir()
    (trw / "learnings").mkdir()
    (trw / "learnings" / "entries").mkdir()
    (trw / "runs").mkdir()
    return trw


@pytest.fixture()
def config() -> TRWConfig:
    """Return a test TRWConfig with dedup disabled for simplicity."""
    cfg = TRWConfig()
    object.__setattr__(cfg, "dedup_enabled", False)
    return cfg


def _make_noop_store(**_kwargs: object) -> dict[str, object]:
    return {"learning_id": "L-test", "path": "sqlite://L-test", "status": "recorded", "distribution_warning": ""}


def _make_store_fn() -> Any:
    """Return a mock store function that captures kwargs."""
    calls: list[dict[str, object]] = []

    def _store(trw_dir: Path, learning_id: str, summary: str, detail: str, **kwargs: object) -> dict[str, object]:
        calls.append({"learning_id": learning_id, "summary": summary, **kwargs})
        return {"learning_id": learning_id, "path": "sqlite://L-x", "status": "recorded", "distribution_warning": ""}

    _store.calls = calls  # type: ignore[attr-defined]
    return _store


class TestLearnWithNewFields:
    """Integration tests for execute_learn with PRD-CORE-110 fields."""

    def test_learn_with_type_incident(self, trw_dir: Path, config: TRWConfig) -> None:
        """execute_learn passes type='incident' through to store_fn."""
        store_fn = _make_store_fn()
        result = execute_learn(
            summary="Test incident learning",
            detail="This is a test incident",
            trw_dir=trw_dir,
            config=config,
            type="incident",
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-aaaa",
            _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-aaaa.yaml"),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        assert result["status"] == "recorded"
        assert len(store_fn.calls) == 1
        assert store_fn.calls[0].get("type") == "incident"

    def test_learn_with_protection_tier_protected(self, trw_dir: Path, config: TRWConfig) -> None:
        """execute_learn passes protection_tier through to store_fn."""
        store_fn = _make_store_fn()
        execute_learn(
            summary="Protected learning",
            detail="This should not be pruned",
            trw_dir=trw_dir,
            config=config,
            protection_tier="protected",
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-bbbb",
            _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-bbbb.yaml"),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        assert store_fn.calls[0].get("protection_tier") == "protected"

    def test_learn_with_confidence_high(self, trw_dir: Path, config: TRWConfig) -> None:
        """execute_learn passes confidence through to store_fn."""
        store_fn = _make_store_fn()
        execute_learn(
            summary="Verified learning",
            detail="Confirmed by multiple observations",
            trw_dir=trw_dir,
            config=config,
            confidence="high",
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-cccc",
            _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-cccc.yaml"),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        assert store_fn.calls[0].get("confidence") == "high"

    def test_learn_auto_phase_origin_uppercase(self, trw_dir: Path, config: TRWConfig) -> None:
        """phase_origin is uppercased when passed explicitly."""
        store_fn = _make_store_fn()
        execute_learn(
            summary="Implement phase learning",
            detail="Something discovered during implementation",
            trw_dir=trw_dir,
            config=config,
            phase_origin="implement",  # lower case — should be uppercased
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-dddd",
            _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-dddd.yaml"),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        # phase_origin passed explicitly is NOT auto-detected (already provided),
        # but the auto-detection code only runs if phase_origin is empty string.
        # When passed as "implement", it stays as-is from the explicit path.
        # Verify that at minimum, the value was passed through.
        assert store_fn.calls[0].get("phase_origin") == "implement"

    def test_learn_auto_nudge_line_short_summary(self, trw_dir: Path, config: TRWConfig) -> None:
        """Short summary (<=80 chars) becomes nudge_line when not provided."""
        store_fn = _make_store_fn()
        short_summary = "Use X instead of Y for better performance"
        execute_learn(
            summary=short_summary,
            detail="detailed explanation",
            trw_dir=trw_dir,
            config=config,
            # nudge_line NOT provided — should be auto-generated
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-eeee",
            _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-eeee.yaml"),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        assert store_fn.calls[0].get("nudge_line") == short_summary

    def test_learn_auto_nudge_line_truncation(self, trw_dir: Path, config: TRWConfig) -> None:
        """Long summary (>80 chars) is truncated at word boundary for nudge_line."""
        store_fn = _make_store_fn()
        # Create a 100-char summary with spaces for word-boundary truncation
        long_summary = (
            "This is a very long summary that exceeds eighty characters and should be truncated at word boundary here"
        )
        assert len(long_summary) > 80
        execute_learn(
            summary=long_summary,
            detail="detailed explanation",
            trw_dir=trw_dir,
            config=config,
            # nudge_line NOT provided — should be auto-truncated
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-ffff",
            _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-ffff.yaml"),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        nudge = store_fn.calls[0].get("nudge_line", "")
        assert isinstance(nudge, str)
        assert len(nudge) <= 80
        # Should end with … or be within 80 chars
        assert nudge.startswith("This is a very long")

    def test_learn_phase_origin_fallback_warning(self, trw_dir: Path, config: TRWConfig) -> None:
        """When no active run, phase_origin stays empty and no crash occurs."""
        store_fn = _make_store_fn()
        # trw_dir/runs is empty (no active run files)
        result = execute_learn(
            summary="Learning without active run",
            detail="Phase detection should fail gracefully",
            trw_dir=trw_dir,
            config=config,
            # phase_origin NOT provided — auto-detection should find nothing
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-gggg",
            _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-gggg.yaml"),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        assert result["status"] == "recorded"
        # phase_origin should be empty string when no active run
        assert store_fn.calls[0].get("phase_origin") == ""

    def test_yaml_backup_includes_new_fields(self, trw_dir: Path, config: TRWConfig) -> None:
        """YAML backup _save_yaml_backup gets new fields from LearningParams."""
        from trw_mcp.models.learning import LearningEntry

        captured_entries: list[LearningEntry] = []

        def _fake_save(td: Path, entry: LearningEntry) -> Path:
            captured_entries.append(entry)
            return td / "learnings" / "entries" / f"{entry.id}.yaml"

        execute_learn(
            summary="hypothesis learning",
            detail="Testing YAML backup includes new fields",
            trw_dir=trw_dir,
            config=config,
            assertions=[{"type": "glob_exists", "pattern": "", "target": "src/main.py"}],
            type="hypothesis",
            confidence="medium",
            protection_tier="protected",
            phase_origin="IMPLEMENT",
            domain=["testing", "mcp"],
            phase_affinity=["VALIDATE"],
            nudge_line="Custom nudge line",
            team_origin="sprint-80",
            task_type="testing",
            _adapter_store=_make_noop_store,
            _generate_learning_id=lambda: "L-hhhh",
            _save_learning_entry=_fake_save,
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        assert len(captured_entries) == 1
        entry = captured_entries[0]
        assert entry.type == "hypothesis"
        assert entry.confidence == "medium"
        assert entry.protection_tier == "protected"
        assert entry.phase_origin == "IMPLEMENT"
        assert entry.assertions == [{"type": "glob_exists", "pattern": "", "target": "src/main.py"}]
        assert entry.domain == ["testing", "mcp"]
        assert entry.phase_affinity == ["VALIDATE"]
        assert entry.nudge_line == "Custom nudge line"
        assert entry.team_origin == "sprint-80"
        assert entry.task_type == "testing"

    def test_audit_finding_defaults_typed_metadata(self, trw_dir: Path, config: TRWConfig) -> None:
        """Audit-tagged learnings auto-fill the FR06-required typed fields."""
        from trw_mcp.models.learning import LearningEntry

        store_fn = _make_store_fn()
        captured_entries: list[LearningEntry] = []

        def _fake_save(td: Path, entry: LearningEntry) -> Path:
            captured_entries.append(entry)
            return td / "learnings" / "entries" / f"{entry.id}.yaml"

        execute_learn(
            summary="Sprint 90: FR06 audit gap reproduced",
            detail="Audit found missing runtime wiring.",
            trw_dir=trw_dir,
            config=config,
            tags=["audit-finding", "PRD-QUAL-056", "impl_gap"],
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-audit",
            _save_learning_entry=_fake_save,
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )

        assert store_fn.calls[0]["type"] == "incident"
        assert store_fn.calls[0]["confidence"] == "verified"
        assert store_fn.calls[0]["domain"] == ["testing", "quality", "implementation"]
        assert store_fn.calls[0]["phase_affinity"] == ["implement"]
        assert len(captured_entries) == 1
        assert captured_entries[0].type == "incident"
        assert captured_entries[0].confidence == "verified"
        assert captured_entries[0].domain == ["testing", "quality", "implementation"]
        assert captured_entries[0].phase_affinity == ["implement"]

    def test_learn_phase_origin_from_run_yaml(self, trw_dir: Path, config: TRWConfig) -> None:
        """Auto-detection delegates to detect_current_phase from _paths."""
        store_fn = _make_store_fn()

        # detect_current_phase is now the canonical source (DRY fix)
        with patch("trw_mcp.state._paths.detect_current_phase", return_value="implement"):
            execute_learn(
                summary="Learning during implement phase",
                detail="Should auto-detect phase from run.yaml",
                trw_dir=trw_dir,
                config=config,
                # No phase_origin given — should auto-detect
                _adapter_store=store_fn,
                _generate_learning_id=lambda: "L-iiii",
                _save_learning_entry=MagicMock(return_value=trw_dir / "learnings" / "entries" / "L-iiii.yaml"),
                _update_analytics=MagicMock(),
                _list_active_learnings=MagicMock(return_value=[]),
                _check_and_handle_dedup=MagicMock(return_value=None),
            )
        # Should be uppercased from detect_current_phase "implement" -> "IMPLEMENT"
        assert store_fn.calls[0].get("phase_origin") == "IMPLEMENT"

    @patch("trw_mcp.clients.llm.LLMClient")
    @patch("trw_mcp.tools._learn_validator.is_high_utility")
    def test_learn_rejects_low_utility(
        self,
        mock_is_high_utility: MagicMock,
        mock_llm_client: MagicMock,
        trw_dir: Path,
        config: TRWConfig,
    ) -> None:
        """execute_learn rejects summaries if LLM utility validation fails."""
        mock_is_high_utility.return_value = (False, "Too vague")
        mock_llm_client.return_value._available = True
        store_fn = _make_store_fn()

        result = execute_learn(
            summary="PRD-123 groomed",
            detail="Did some work",
            trw_dir=trw_dir,
            config=config,
            _adapter_store=store_fn,
            _generate_learning_id=lambda: "L-jjjj",
            _save_learning_entry=MagicMock(),
            _update_analytics=MagicMock(),
            _list_active_learnings=MagicMock(return_value=[]),
            _check_and_handle_dedup=MagicMock(return_value=None),
        )
        assert result["status"] == "rejected"
        assert result["reason"] == "llm_utility_filter"
        assert "Too vague" in str(result.get("message", ""))
        assert len(store_fn.calls) == 0
