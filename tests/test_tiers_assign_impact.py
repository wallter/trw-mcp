"""Tests for impact tier assignment behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestAssignImpactTiers:
    """PRD-FIX-052-FR01/FR02: assign_impact_tiers() labels entries by impact score."""

    def _setup_entries_dir(self, tmp_path: Path) -> tuple[Path, Path, FileStateWriter]:
        """Create .trw/learnings/entries/ and return (trw_dir, entries_dir, writer)."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        writer = FileStateWriter()
        return trw_dir, entries_dir, writer

    def _write_entry(
        self,
        entries_dir: Path,
        writer: FileStateWriter,
        entry_id: str,
        impact: float = 0.5,
        status: str = "active",
    ) -> Path:
        """Write a minimal entry YAML for tier assignment tests."""
        path = entries_dir / f"{entry_id}.yaml"
        writer.write_yaml(
            path,
            {
                "id": entry_id,
                "summary": f"summary for {entry_id}",
                "detail": f"detail for {entry_id}",
                "tags": ["test"],
                "impact": impact,
                "status": status,
            },
        )
        return path

    def test_assign_impact_tiers_critical(self, tmp_path: Path) -> None:
        """Entry with impact=0.95 gets tier='critical' (>= 0.9 boundary)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-crit", impact=0.95)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=[{"id": "e-crit", "impact": 0.95, "status": "active"}]):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["critical"] == 1

    def test_assign_impact_tiers_high(self, tmp_path: Path) -> None:
        """Entry with impact=0.75 gets tier='high' (>= 0.7, < 0.9)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-high", impact=0.75)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=[{"id": "e-high", "impact": 0.75, "status": "active"}]):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["high"] == 1

    def test_assign_impact_tiers_medium(self, tmp_path: Path) -> None:
        """Entry with impact=0.5 gets tier='medium' (>= 0.4, < 0.7)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-med", impact=0.5)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=[{"id": "e-med", "impact": 0.5, "status": "active"}]):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["medium"] == 1

    def test_assign_impact_tiers_low(self, tmp_path: Path) -> None:
        """Entry with impact=0.2 gets tier='low' (< 0.4)."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-low", impact=0.2)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=[{"id": "e-low", "impact": 0.2, "status": "active"}]):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["low"] == 1

    def test_assign_impact_tiers_boundary_at_0_9(self, tmp_path: Path) -> None:
        """Entry with impact exactly 0.9 gets tier='critical'."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-boundary-crit", impact=0.9)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[{"id": "e-boundary-crit", "impact": 0.9, "status": "active"}],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["critical"] == 1

    def test_assign_impact_tiers_boundary_at_0_7(self, tmp_path: Path) -> None:
        """Entry with impact exactly 0.7 gets tier='high'."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-boundary-high", impact=0.7)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[{"id": "e-boundary-high", "impact": 0.7, "status": "active"}],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert dist["high"] == 1

    def test_assign_impact_tiers_idempotent(self, tmp_path: Path) -> None:
        """Running assign_impact_tiers twice produces the same distribution."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-idem", impact=0.8)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        fake_entries = [{"id": "e-idem", "impact": 0.8, "status": "active"}]
        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=fake_entries):
            dist1 = mgr.assign_impact_tiers(trw_dir)
        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=fake_entries):
            dist2 = mgr.assign_impact_tiers(trw_dir)

        assert dist1 == dist2

    def test_assign_impact_tiers_writes_yaml(self, tmp_path: Path) -> None:
        """After assignment, the YAML file contains the correct impact_tier field."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        reader = FileStateReader()
        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        self._write_entry(entries_dir, writer, "e-yaml", impact=0.92)
        mgr = TierManager(trw_dir=trw_dir, reader=reader, writer=writer)

        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=[{"id": "e-yaml", "impact": 0.92, "status": "active"}]):
            mgr.assign_impact_tiers(trw_dir)

        data = reader.read_yaml(entries_dir / "e-yaml.yaml")
        assert data.get("impact_tier") == "critical"

    def test_assign_impact_tiers_distribution_sums_to_entry_count(self, tmp_path: Path) -> None:
        """Distribution counts sum to total number of active entries processed."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, entries_dir, writer = self._setup_entries_dir(tmp_path)
        for entry_id, impact in [("e1", 0.95), ("e2", 0.75), ("e3", 0.5), ("e4", 0.2)]:
            self._write_entry(entries_dir, writer, entry_id, impact=impact)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        fake_entries = [
            {"id": "e1", "impact": 0.95, "status": "active"},
            {"id": "e2", "impact": 0.75, "status": "active"},
            {"id": "e3", "impact": 0.5, "status": "active"},
            {"id": "e4", "impact": 0.2, "status": "active"},
        ]
        with patch("trw_mcp.state.tiers.list_active_learnings", return_value=fake_entries):
            dist = mgr.assign_impact_tiers(trw_dir)

        total = sum(dist.values())
        assert total == 4
        assert dist["critical"] == 1
        assert dist["high"] == 1
        assert dist["medium"] == 1
        assert dist["low"] == 1

    def test_assign_impact_tiers_skips_missing_yaml(self, tmp_path: Path) -> None:
        """Entry in SQLite but with no YAML file on disk is skipped gracefully."""
        from unittest.mock import patch

        from trw_mcp.state.tiers import TierManager

        trw_dir, _, writer = self._setup_entries_dir(tmp_path)
        mgr = TierManager(trw_dir=trw_dir, reader=FileStateReader(), writer=writer)

        with patch(
            "trw_mcp.state.tiers.list_active_learnings",
            return_value=[{"id": "missing-yaml-entry", "impact": 0.8, "status": "active"}],
        ):
            dist = mgr.assign_impact_tiers(trw_dir)

        assert sum(dist.values()) == 0

    def test_impact_tier_field_default_is_question_mark(self) -> None:
        """LearningEntry.impact_tier defaults to '?' when not set (FR02)."""
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(id="test", summary="x", detail="y")
        assert entry.impact_tier == "?"

    def test_impact_tier_invalid_value_raises(self) -> None:
        """LearningEntry with invalid impact_tier raises ValidationError (Literal type)."""
        from pydantic import ValidationError

        from trw_mcp.models.learning import LearningEntry

        with pytest.raises(ValidationError):
            LearningEntry(id="t", summary="x", detail="y", impact_tier="invalid")  # type: ignore[arg-type]
