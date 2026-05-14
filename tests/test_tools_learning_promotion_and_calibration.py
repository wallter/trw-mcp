"""Tests for promotion legacy behavior, distribution warnings, and calibration wiring."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
        backend.update(learning_id, q_value=0.9, q_observations=5)

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
        backend.update(learning_id, q_value=0.2, q_observations=5)

        sync_result = tools["trw_claude_md_sync"].fn(scope="root")
        # Should use q_value (0.2) — not promoted
        assert sync_result["learnings_promoted"] == 0

class TestTrwLearnDistributionWarning:
    """Tests for PRD-CORE-034 impact score distribution advisory in trw_learn."""

    def _write_entry(self, entries_dir: Path, fname: str, impact: float, status: str = "active") -> None:
        entries_dir.mkdir(parents=True, exist_ok=True)
        (entries_dir / fname).write_text(f"id: {fname}\nimpact: {impact}\nstatus: {status}\n")

    def test_learn_distribution_warning_critical_tier(self, tmp_path: Path) -> None:
        """Warning fires when critical tier exceeds 5% cap."""
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
        assert "critical" in result["distribution_warning"]
        assert "cap" in result["distribution_warning"]

    def test_learn_distribution_warning_high_tier(self, tmp_path: Path) -> None:
        """Warning fires when high tier exceeds 20% cap."""
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
        assert "high" in result["distribution_warning"]
        assert "cap" in result["distribution_warning"]

    def test_learn_no_warning_when_disabled(self, tmp_path: Path) -> None:
        """No warning when impact_forced_distribution_enabled=False."""
        disabled_cfg = _CFG.model_copy(update={"impact_forced_distribution_enabled": False})
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
            assert result["distribution_warning"] == ""

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
        assert result["distribution_warning"] == ""

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
        assert result["distribution_warning"] == ""

class TestBayesianCalibrationWiring:
    """Verify compute_calibration_accuracy + bayesian_calibrate wiring in trw_learn."""

    def test_impact_is_calibrated_on_save(self, tmp_path: Path) -> None:
        """trw_learn stores a Bayesian-calibrated impact, not the raw value."""
        tools = _get_tools()
        raw_impact = 0.9
        result = tools["trw_learn"].fn(
            summary="High impact learning",
            detail="Very important discovery",
            impact=raw_impact,
        )
        assert result["status"] == "recorded"

        # With no recall history (default weight 1.0), calibrated should differ from raw.
        # bayesian_calibrate(0.9, org_mean=0.5, user_weight=1.0, org_weight=0.5)
        # = (0.9*1 + 0.5*0.5) / (1+0.5) = 1.15/1.5 ≈ 0.7667
        reader = FileStateReader()
        entries_dir = _entries_dir(tmp_path)
        for entry_file in entries_dir.glob("*.yaml"):
            data = reader.read_yaml(entry_file)
            if data.get("id") == result["learning_id"]:
                stored_impact = float(str(data["impact"]))
                # Stored impact should be pulled toward org_mean (0.5), not exactly 0.9
                assert stored_impact < raw_impact
                # But should still be > org_mean (user weight dominates)
                assert stored_impact > 0.5
                break

    def test_calibration_failure_falls_back_to_raw_impact(
        self,
        tmp_path: Path,
        reader: FileStateReader,
    ) -> None:
        """If Bayesian calibration raises, raw impact is used (fail-open)."""
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
