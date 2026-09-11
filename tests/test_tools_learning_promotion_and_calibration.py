"""Tests for promotion legacy behavior, distribution warnings, and calibration wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._tools_learning_shared import _CFG, _entries_dir, _get_tools
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestClaudeMdSyncQValuePromotion:
    """Tests for PRD-CORE-004 Phase 1c — q_value-based promotion in claude_md_sync."""

    def test_mature_entry_uses_q_value(self, tmp_path: Path) -> None:
        """CORE-093: learning promotion removed — q_value no longer drives CLAUDE.md content."""
        from trw_mcp.state.memory_adapter import get_backend

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Mature q promotion test",
            detail="Has high q_value",
            impact=0.3,
        )
        learning_id = result["learning_id"]

        trw_dir = tmp_path / _CFG.trw_dir
        backend = get_backend(trw_dir)
        backend.update(learning_id, q_value=0.9, q_observations=5, namespace="default")

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # CORE-093: learnings_promoted always 0
        assert sync_result["learnings_promoted"] == 0

    def test_immature_entry_uses_impact(self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter) -> None:
        """CORE-093: learning promotion removed — impact no longer drives CLAUDE.md content."""
        tools = _get_tools()
        tools["trw_learn"].fn(
            summary="Immature impact promotion test",
            detail="Uses impact because too few observations",
            impact=0.9,
        )

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # CORE-093: learnings_promoted always 0
        assert sync_result["learnings_promoted"] == 0

    def test_mature_low_q_not_promoted(self, tmp_path: Path) -> None:
        """Mature entry with low q_value is not promoted even if impact is high."""
        from trw_mcp.state.memory_adapter import get_backend

        tools = _get_tools()
        result = tools["trw_learn"].fn(
            summary="Mature low q no promote test",
            detail="High impact but low q_value",
            impact=0.9,  # High impact
        )
        learning_id = result["learning_id"]

        # Update q_value and q_observations in SQLite (where list_active_learnings reads from)
        trw_dir = tmp_path / _CFG.trw_dir
        backend = get_backend(trw_dir)
        backend.update(learning_id, q_value=0.2, q_observations=5, namespace="default")

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # Should use q_value (0.2) — not promoted
        assert sync_result["learnings_promoted"] == 0


class TestTrwLearnDistributionWarning:
    """CD retires automatic quota warnings; explicit scoring tests remain separate."""

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(f"id: {fname}\nimpact: {impact}\nstatus: {status}\n")

    def test_learn_no_quota_warning_critical_tier(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """CD: capture does not enforce historical critical-tier quotas."""
        # Legacy threshold remains parseable but no longer shapes capture.
        cfg = _CFG.model_copy(update={"impact_high_threshold_pct": 100.0, "embeddings_enabled": False})
        monkeypatch.setattr("trw_mcp.tools.learning.get_config", lambda: cfg)
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        # Create 10 active entries all at critical tier -> 100% critical
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Critical learning",
            detail="Very important discovery",
            impact=0.95,
        )
        assert result["status"] == "recorded"
        assert not result.get("distribution_warning")

    def test_learn_no_quota_warning_high_tier(self, tmp_path: Path) -> None:
        """CD: capture does not enforce historical high-tier quotas."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        # Create 10 active entries all at high tier -> 100% high
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.75)

        result = tools["trw_learn"].fn(
            summary="High impact learning",
            detail="Important discovery",
            impact=0.75,
        )
        assert result["status"] == "recorded"
        assert not result.get("distribution_warning")

    def test_learn_no_warning_when_disabled(self, tmp_path: Path) -> None:
        """Legacy disabled configuration remains accepted without quota warnings."""
        disabled_cfg = _CFG.model_copy(
            update={
                "impact_forced_distribution_enabled": False,
                "impact_high_threshold_pct": 100.0,
                "embeddings_enabled": False,
            }
        )
        with patch("trw_mcp.tools.learning.get_config", return_value=disabled_cfg):
            tools = _get_tools()
            entries_dir = _entries_dir(tmp_path)
            for i in range(10):
                self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

            result = tools["trw_learn"].fn(
                summary="Critical learning",
                detail="Very important",
                impact=0.95,
            )
            assert result.get("distribution_warning", "") == ""  # omitted when empty (2026-07-12)

    @pytest.mark.parametrize("forced_distribution", [True, False])
    def test_raw_impact_no_longer_obeys_corpus_quota(self, tmp_path: Path, forced_distribution: bool) -> None:
        """CD now retires the independent quota retained by R10."""
        cfg = _CFG.model_copy(
            update={
                "impact_forced_distribution_enabled": forced_distribution,
                "embeddings_enabled": False,
            }
        )
        assert cfg.impact_high_threshold_pct == 20.0
        with patch("trw_mcp.tools.learning.get_config", return_value=cfg):
            for i in range(10):
                self._write_entry(_entries_dir(tmp_path), f"entry_{i}.yaml", 0.95)
            result = _get_tools()["trw_learn"].fn(
                summary="Independent soft cap remains active after calibration retirement",
                detail="Raw caller impact is preserved independent of historical quota settings.",
                impact=0.95,
            )
        assert result["status"] == "recorded"
        assert not result.get("distribution_warning")
        reader = FileStateReader()
        stored = [reader.read_yaml(path) for path in _entries_dir(tmp_path).glob("*.yaml")]
        matching = [data for data in stored if data.get("id") == result["learning_id"]]
        assert len(matching) == 1
        assert float(str(matching[0]["impact"])) == 0.95

    def test_learn_no_warning_below_threshold(self, tmp_path: Path) -> None:
        """No warning for impact < 0.7 (below distribution check threshold)."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        for i in range(10):
            self._write_entry(entries_dir, f"entry_{i}.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Medium learning",
            detail="Not a high-priority discovery",
            impact=0.5,
        )
        assert result["status"] == "recorded"
        assert result.get("distribution_warning", "") == ""  # omitted when empty (2026-07-12)

    def test_learn_no_warning_when_within_cap(self, tmp_path: Path) -> None:
        """No warning when tier percentage is within cap."""
        tools = _get_tools()
        entries_dir = _entries_dir(tmp_path)
        # 1 critical out of 100 active = 1% -> within 5% cap
        for i in range(99):
            self._write_entry(entries_dir, f"low_{i}.yaml", 0.3)
        self._write_entry(entries_dir, "crit_1.yaml", 0.95)

        result = tools["trw_learn"].fn(
            summary="Another critical learning",
            detail="This one is fine since distribution is within cap",
            impact=0.95,
        )
        assert result["status"] == "recorded"
        assert result.get("distribution_warning", "") == ""  # omitted when empty (2026-07-12)


class TestUnattributedCalibrationRetirement:
    """R10: unowned pooled recall statistics do not calibrate a caller."""

    def test_raw_impact_is_preserved_on_save(self, tmp_path: Path) -> None:
        """The actual registered learn caller preserves raw impact without use evidence."""
        tools = _get_tools()
        raw_impact = 0.9
        result = tools["trw_learn"].fn(
            summary="High impact learning",
            detail="Very important discovery",
            impact=raw_impact,
        )
        assert result["status"] == "recorded"

        reader = FileStateReader()
        stored = [reader.read_yaml(path) for path in _entries_dir(tmp_path).glob("*.yaml")]
        matching = [data for data in stored if data.get("id") == result["learning_id"]]
        assert len(matching) == 1
        assert float(str(matching[0]["impact"])) == raw_impact

    def test_unavailable_pooled_stats_do_not_affect_learning(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """No pooled-statistics acquisition is required to persist a learning."""
        tools = _get_tools()
        raw_impact = 0.8

        with patch(
            "trw_mcp.state.recall_tracking.get_recall_stats",
            side_effect=RuntimeError("tracking boom"),
        ):
            result = tools["trw_learn"].fn(
                summary="Calibration failure test",
                detail="Calibration should fall back gracefully",
                impact=raw_impact,
            )

        assert result["status"] == "recorded"
        # Verify it still saved something
        entries_dir = _entries_dir(tmp_path)
        entry_files = list(entries_dir.glob("*.yaml"))
        assert len(entry_files) >= 1
