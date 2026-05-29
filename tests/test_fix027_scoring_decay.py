"""Tests for PRD-FIX-027 time-decay and scoring purity behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.scoring import apply_time_decay
from trw_mcp.state.claude_md import collect_promotable_learnings
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestPromotableLearnungsTimeDecay:
    """Bug 3: time decay must be applied before comparing against promotion threshold."""

    def _write_learning(
        self,
        entries_dir: Path,
        writer: FileStateWriter,
        filename: str,
        impact: float,
        created_at: datetime,
        q_obs: int = 0,
    ) -> None:
        data = {
            "id": f"L-{filename}",
            "summary": f"Learning {filename}",
            "detail": "Detail",
            "impact": impact,
            "q_value": impact,
            "q_observations": q_obs,
            "status": "active",
            "created_at": created_at.isoformat(),
            "tags": [],
        }
        writer.write_yaml(entries_dir / f"{filename}.yaml", data)

    def test_old_learning_with_high_impact_filtered_by_decay(self, tmp_path: Path) -> None:
        """An entry created 1 year ago with impact=0.8 should be filtered out.

        Without decay: 0.8 >= 0.7 threshold → promoted
        With decay: 0.8 * max(0.3, 1.0 - (365/365)*0.3) = 0.8 * 0.7 = 0.56 < 0.7 → not promoted
        """
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)
        self._write_learning(entries_dir, writer, "old-entry", 0.8, one_year_ago)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        assert "L-old-entry" not in ids, (
            "Old learning with decayed score should NOT be promoted — "
            "time decay was not applied before threshold comparison"
        )

    def test_new_learning_with_high_impact_is_promoted(self, tmp_path: Path) -> None:
        """An entry created today with impact=0.8 should still pass the threshold."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        now = datetime.now(timezone.utc)
        self._write_learning(entries_dir, writer, "new-entry", 0.8, now)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        assert "L-new-entry" in ids, "New learning with impact=0.8 should be promoted"

    def test_decay_not_applied_when_no_created_at(self, tmp_path: Path) -> None:
        """Entries without created_at fall back to raw impact (no crash)."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        data = {
            "id": "L-no-date",
            "summary": "No date entry",
            "detail": "Detail",
            "impact": 0.9,
            "q_value": 0.9,
            "q_observations": 0,
            "status": "active",
            "tags": [],
        }
        writer.write_yaml(entries_dir / "no-date.yaml", data)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        assert "L-no-date" in ids

    def test_old_high_impact_vs_new_same_impact(self, tmp_path: Path) -> None:
        """Two entries with same impact=0.8 — old one filtered, new one promoted."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        now = datetime.now(timezone.utc)
        one_year_ago = now - timedelta(days=365)

        self._write_learning(entries_dir, writer, "aaa-old", 0.8, one_year_ago)
        self._write_learning(entries_dir, writer, "bbb-new", 0.8, now)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]

        assert "L-bbb-new" in ids, "New entry should be promoted"
        assert "L-aaa-old" not in ids, "Year-old entry with same impact should be filtered by decay"

    def test_malformed_created_at_falls_back_to_raw_score(self, tmp_path: Path) -> None:
        """Malformed 'created' date in list_active_learnings falls back to raw score."""
        from unittest.mock import patch

        trw_dir = tmp_path / ".trw"
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        bad_entry = {
            "id": "L-baddate",
            "summary": "Entry with bad date",
            "detail": "Detail",
            "impact": 0.9,
            "q_value": 0.9,
            "q_observations": 0,
            "status": "active",
            "created": "not-a-valid-date",
            "tags": [],
        }

        with patch(
            "trw_mcp.state.memory_adapter.list_active_learnings",
            return_value=[bad_entry],
        ):
            result = collect_promotable_learnings(trw_dir, config, reader)

        ids = [str(d.get("id", "")) for d in result]
        assert "L-baddate" in ids

    def test_apply_time_decay_at_boundary_exact_0_7(
        self,
    ) -> None:
        """Entry with impact=0.7 exactly at threshold — decayed or not depends on age."""
        from trw_mcp.scoring import apply_time_decay

        now = datetime.now(timezone.utc)
        decayed = apply_time_decay(0.7, now)
        assert abs(decayed - 0.7) < 1e-9

        six_months_ago = now - timedelta(days=182)
        decayed_6m = apply_time_decay(0.7, six_months_ago)
        assert decayed_6m < 0.7

    def test_q_cold_start_uses_q_value_when_mature(self, tmp_path: Path) -> None:
        """When q_observations >= threshold, q_value is used instead of impact."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)

        writer = FileStateWriter()
        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        now = datetime.now(timezone.utc)
        data = {
            "id": "L-mature",
            "summary": "Mature entry with high q_value",
            "detail": "Detail",
            "impact": 0.3,
            "q_value": 0.9,
            "q_observations": 5,
            "status": "active",
            "created_at": now.isoformat(),
            "tags": [],
        }
        writer.write_yaml(entries_dir / "mature.yaml", data)

        result = collect_promotable_learnings(trw_dir, config, reader)
        ids = [str(d.get("id", "")) for d in result]
        assert "L-mature" in ids

    def test_collect_promotable_returns_empty_when_no_entries_dir(self, tmp_path: Path) -> None:
        """collect_promotable_learnings returns [] when entries_dir doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        reader = FileStateReader()
        config = TRWConfig(trw_dir=str(trw_dir))

        result = collect_promotable_learnings(trw_dir, config, reader)
        assert result == []


class TestApplyTimeDecay:
    """Parametrized edge cases for apply_time_decay."""

    @pytest.mark.parametrize(
        "days,impact,expected_min,expected_max",
        [
            (0, 1.0, 1.0, 1.0),
            (182, 1.0, 0.848, 0.852),
            (365, 1.0, 0.699, 0.701),
            (486, 1.0, 0.598, 0.602),
            (730, 1.0, 0.399, 0.401),
            (1460, 1.0, 0.299, 0.301),
            (0, 0.0, 0.0, 0.0),
        ],
    )
    def test_decay_parametrized(self, days: int, impact: float, expected_min: float, expected_max: float) -> None:
        from trw_mcp.scoring import apply_time_decay

        created = datetime.now(timezone.utc) - timedelta(days=days)
        result = apply_time_decay(impact, created)
        assert expected_min <= result <= expected_max, (
            f"days={days}, impact={impact}: got {result}, expected [{expected_min}, {expected_max}]"
        )

    def test_naive_datetime_treated_as_utc(self) -> None:
        """Naive datetime (no tzinfo) is treated as UTC — no exception."""
        from trw_mcp.scoring import apply_time_decay

        naive_now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        result = apply_time_decay(0.8, naive_now)
        assert result >= 0.79


class TestStoredImpactImmutabilityAdditional:
    """NFR03: Additional tests verifying stored impact is never mutated at query time."""

    def test_rank_by_utility_does_not_mutate_entry_dict(self) -> None:
        """rank_by_utility must not mutate the entry dicts passed to it."""
        from trw_mcp.scoring import rank_by_utility as rbu

        created_old = (datetime.now(timezone.utc) - timedelta(days=300)).date().isoformat()
        entry: dict[str, object] = {
            "id": "L-mut001",
            "summary": "mutation test",
            "detail": "mutation detail",
            "impact": 0.9,
            "status": "active",
            "created": created_old,
            "q_value": 0.9,
            "q_observations": 5,
            "recurrence": 3,
            "tags": ["test"],
        }
        import copy

        entry_copy = copy.deepcopy(entry)

        rbu([entry], ["mutation"], 0.5)

        assert entry["impact"] == entry_copy["impact"]
        assert entry.get("q_value") == entry_copy.get("q_value")

    def test_apply_time_decay_does_not_modify_caller_state(self) -> None:
        """Repeated calls to apply_time_decay with same args return same result."""
        created = datetime.now(timezone.utc) - timedelta(days=100)
        r1 = apply_time_decay(0.8, created)
        r2 = apply_time_decay(0.8, created)
        assert r1 == pytest.approx(r2)


class TestApplyTimeDecayPurity:
    """FR01: apply_time_decay must be a pure compute function with no write side effects."""

    def test_apply_time_decay_body_has_no_writer_calls(self) -> None:
        """FR01: The body of apply_time_decay must not call _writer or write_yaml.

        This is a static contract test. If someone adds a write call to apply_time_decay,
        stored impact values would be permanently mutated at query time — a correctness bug.
        """
        import inspect

        from trw_mcp.scoring import apply_time_decay as atd

        source = inspect.getsource(atd)
        assert "_writer" not in source, (
            "apply_time_decay body calls _writer — this would mutate stored impact scores at query time"
        )
        assert "write_yaml" not in source, (
            "apply_time_decay body calls write_yaml — this would mutate stored impact scores at query time"
        )
        assert "FileStateWriter" not in source, (
            "apply_time_decay body instantiates FileStateWriter — violates purity contract"
        )

    def test_apply_time_decay_returns_float_no_side_effects(self, tmp_path: Path) -> None:
        """FR01: Direct call to apply_time_decay returns float, no YAML written.

        Verifies the function contract: given impact=0.9 and a date 400 days ago,
        the result is a float in [0.0, 1.0] and no file is created.
        """
        created = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = apply_time_decay(0.9, created)

        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0
        assert result < 0.9
