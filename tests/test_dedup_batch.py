"""Tests for batch dedup migration behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._dedup_test_support import mock_embed, write_entry
from tests._structlog_capture import captured_structlog  # noqa: F401
from trw_mcp.models.config import TRWConfig
from trw_mcp.state.dedup import batch_dedup, is_migration_needed
from trw_mcp.state.persistence import FileStateReader, FileStateWriter


class TestBatchDedup:
    """Tests for FR05 — batch_dedup and is_migration_needed."""

    def test_is_migration_needed_true_when_no_marker(self, tmp_path: Path) -> None:
        """is_migration_needed returns True when marker file doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        # Default config: learnings_dir="learnings", so marker = .trw/learnings/dedup_migration.yaml
        # That file won't exist in a fresh tmp dir
        assert is_migration_needed(trw_dir) is True

    def test_is_migration_needed_false_after_marker_written(self, tmp_path: Path) -> None:
        """is_migration_needed returns False when marker file exists."""
        cfg = TRWConfig(embeddings_enabled=True)
        trw_dir = tmp_path / ".trw"
        learnings_dir = trw_dir / cfg.learnings_dir
        learnings_dir.mkdir(parents=True)
        marker = learnings_dir / "dedup_migration.yaml"
        marker.write_text("completed: true\n", encoding="utf-8")
        assert is_migration_needed(trw_dir) is False

    def test_batch_dedup_skips_when_no_entries_dir(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup returns 'skipped' when entries directory doesn't exist."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()
        config = TRWConfig(embeddings_enabled=True)

        result = batch_dedup(trw_dir, reader, writer, config=config)
        assert result["status"] == "skipped"
        assert "no entries directory" in str(result.get("reason", ""))

    def test_batch_dedup_skips_when_embeddings_unavailable(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup returns 'skipped' when embeddings unavailable."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=False):
            result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "skipped"
        assert "embeddings unavailable" in str(result.get("reason", ""))

    def test_batch_dedup_writes_migration_marker(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup writes dedup_migration.yaml marker after completion."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        marker = trw_dir / "learnings" / "dedup_migration.yaml"
        assert marker.exists()
        marker_data = reader.read_yaml(marker)
        assert marker_data.get("completed") is True
        assert "run_at" in marker_data

    def test_batch_dedup_merges_near_duplicates(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup merges entries above merge threshold."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        # Write two entries
        write_entry(entries_dir, writer, "L-batch01", "batch test alpha", "first detail here alpha")
        write_entry(entries_dir, writer, "L-batch02", "batch test beta", "second detail here beta")

        # Control vectors: L-batch01 and L-batch02 will be at 0.90 similarity
        existing_vec = mock_embed("batch test alpha first detail here alpha")
        import math as _math

        cos_theta = 0.90
        sin_theta = _math.sqrt(1 - cos_theta**2)
        orth = [0.0] * len(existing_vec)
        orth[0] = -existing_vec[1]
        orth[1] = existing_vec[0]
        orth_norm = sum(v * v for v in orth) ** 0.5
        if orth_norm > 0:
            orth = [v / orth_norm for v in orth]
        near_vec = [cos_theta * e + sin_theta * o for e, o in zip(existing_vec, orth)]
        near_norm = sum(v * v for v in near_vec) ** 0.5
        near_vec = [v / near_norm for v in near_vec]

        call_count = [0]

        def controlled_embed(text: str) -> list[float]:
            call_count[0] += 1
            if "L-batch02" in text or "second detail here beta" in text or "batch test beta" in text:
                return near_vec
            return mock_embed(text)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=controlled_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        assert int(str(result.get("entries_scanned", 0))) == 2

    def test_batch_dedup_completes_with_no_active_entries(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup completes cleanly with zero active entries."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True)

        # Write only resolved entries
        path = entries_dir / "L-resolved.yaml"
        writer.write_yaml(
            path,
            {
                "id": "L-resolved",
                "summary": "s",
                "detail": "d",
                "tags": [],
                "evidence": [],
                "impact": 0.5,
                "status": "resolved",
                "recurrence": 1,
                "created": "2026-01-01",
                "updated": "2026-01-01",
                "merged_from": [],
            },
        )

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        assert int(str(result.get("entries_scanned", 0))) == 0
        assert int(str(result.get("entries_merged", 0))) == 0

    def test_batch_dedup_obsoletes_exact_duplicates(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter
    ) -> None:
        """batch_dedup marks exact duplicates (>=skip_threshold) as obsolete."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        config = TRWConfig(embeddings_enabled=True, dedup_skip_threshold=0.95, dedup_merge_threshold=0.85)

        identical_summary = "exact duplicate entry for batch"
        identical_detail = "same detail for exact duplicate"
        write_entry(entries_dir, writer, "L-exact01", identical_summary, identical_detail)
        write_entry(entries_dir, writer, "L-exact02", identical_summary, identical_detail)

        with patch("trw_mcp.state.dedup.embedding_available", return_value=True):
            with patch("trw_mcp.state.dedup.embed", side_effect=mock_embed):
                result = batch_dedup(trw_dir, reader, writer, config=config)

        assert result["status"] == "completed"
        # One of the two entries should be obsoleted
        data2 = reader.read_yaml(entries_dir / "L-exact02.yaml")
        assert str(data2.get("status", "")) == "obsolete"


# ---------------------------------------------------------------------------
# PRD-FIX-130-FR05: the one-time migration leaves the replay hot path
# ---------------------------------------------------------------------------


class TestMigrationIsOffTheReplayPath:
    """Capture and recovery never invoke whole-corpus maintenance implicitly."""

    def _config(self) -> TRWConfig:
        return TRWConfig(embeddings_enabled=False, dedup_enabled=True)

    def _seed_pending(self, trw_dir: Path, learning_id: str) -> None:
        from trw_mcp.state import learn_journal

        learn_journal.journal_pending(
            trw_dir,
            learning_id,
            {
                "summary": f"migration off-path probe {learning_id} with a summary past the noise gate",
                "detail": f"detail body for migration off-path probe {learning_id}",
                "impact": 0.5,
            },
        )

    def test_journal_replay_never_runs_batch_dedup_inline(self, tmp_path: Path) -> None:
        import trw_mcp.state.dedup as dedup_mod
        from trw_mcp.tools._learn_journal_wiring import replay_journaled_learn

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        config = self._config()
        assert is_migration_needed(trw_dir) is True

        calls: list[str] = []

        def _spy(*args: object, **kwargs: object) -> object:
            calls.append("called")
            return {"status": "completed"}  # Sentinel: never load a semantic model.

        with patch.object(dedup_mod, "batch_dedup", _spy):
            self._seed_pending(trw_dir, "L-mig001")
            replay_journaled_learn(trw_dir, config, "L-mig001", {"summary": "x", "detail": "y"})
            assert calls == [], "batch dedup ran INSIDE a journal replay"

            # Ordinary capture has the same no-batch boundary.
            from trw_mcp.tools._learn_impl import execute_learn

            result = execute_learn(
                summary="interactive learn probe with a summary long enough to clear the noise filter",
                detail="detail body for the interactive migration probe",
                trw_dir=trw_dir,
                config=config,
            )
            assert calls == [], "ordinary capture must not run batch migration"
            assert result["status"] == "recorded"
            assert is_migration_needed(trw_dir) is True

    def test_marker_is_not_written_when_the_replay_skipped_the_migration(self, tmp_path: Path) -> None:
        """Capture does not forge completion of explicit maintenance."""
        from trw_mcp.tools._learn_journal_wiring import replay_journaled_learn

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        config = self._config()
        self._seed_pending(trw_dir, "L-mig002")

        replay_journaled_learn(trw_dir, config, "L-mig002", {"summary": "x", "detail": "y"})

        assert is_migration_needed(trw_dir) is True

    def test_drain_does_not_schedule_for_missing_migration_marker(self, tmp_path: Path) -> None:
        from trw_mcp.state import learn_journal
        from trw_mcp.state.memory_pressure import take_writer_census
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        config = self._config()
        self._seed_pending(trw_dir, "L-mig003")
        with patch.object(steps, "_schedule_background_drain", return_value=False) as schedule:
            maintenance = {}
            steps._run_learn_journal_drain(
                trw_dir,
                config,
                maintenance,
                census=take_writer_census(trw_dir, threshold=2),
                defer_memory_heavy=False,
            )
        schedule.assert_not_called()
        assert learn_journal.pending_count(trw_dir) == 0
        assert maintenance["pending_learns_replayed"]["recovered"] == 1
        assert is_migration_needed(trw_dir) is True

    def test_skipped_migration_is_logged_and_leaves_the_marker_absent(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter, captured_structlog: list[dict]
    ) -> None:
        """An explicit skipped scan is visible and does not forge completion."""
        trw_dir = tmp_path / ".trw"
        trw_dir.mkdir()

        result = batch_dedup(trw_dir, reader, writer, config=TRWConfig(embeddings_enabled=False))

        assert result["status"] == "skipped"
        skipped = [e for e in captured_structlog if e.get("event") == "batch_dedup_skipped"]
        assert skipped, captured_structlog
        assert skipped[0].get("reason")
        assert is_migration_needed(trw_dir) is True

    def test_migration_emits_a_start_and_a_timed_completion_event(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter, captured_structlog: list[dict]
    ) -> None:
        """A scan that can cost minutes may never be invisible at the default level."""
        trw_dir = tmp_path / ".trw"
        entries_dir = trw_dir / "learnings" / "entries"
        entries_dir.mkdir(parents=True)
        write_entry(entries_dir, writer, "one", "first migration event probe entry", "detail one")
        write_entry(entries_dir, writer, "two", "second migration event probe entry", "detail two")

        with (
            patch("trw_mcp.state.dedup.embedding_available", return_value=True),
            patch("trw_mcp.state.dedup.embed", side_effect=mock_embed),
        ):
            result = batch_dedup(trw_dir, reader, writer, config=TRWConfig(embeddings_enabled=True))

        assert result["status"] == "completed"
        started = [e for e in captured_structlog if e.get("event") == "batch_dedup_started"]
        completed = [e for e in captured_structlog if e.get("event") == "batch_dedup_complete"]
        assert started and int(str(started[0]["entries"])) == 2, captured_structlog
        assert completed and "duration_ms" in completed[0], captured_structlog
        assert is_migration_needed(trw_dir) is False


@pytest.mark.parametrize("remainder", [0, 2])
def test_maintenance_continuation_requests_replay_only(tmp_path, monkeypatch, remainder):
    from unittest.mock import Mock

    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    trw_dir = tmp_path / ".trw"
    trw_dir.mkdir()
    schedule = Mock(return_value=True)
    monkeypatch.setattr(steps, "_schedule_background_drain", schedule)
    sweep = Mock()
    sweep.index_failed.return_value = False
    sweep.degraded.return_value = False
    maintenance = {}
    steps._finish_drain_sweep(
        trw_dir,
        TRWConfig(embeddings_enabled=False, dedup_enabled=True),
        maintenance,
        {"replayed": 1, "deferred": remainder, "budget_exhausted": bool(remainder)},
        sweep=sweep,
        limit=3,
        budget_ms=1,
        under_pressure=False,
    )
    if remainder:
        assert schedule.call_args.args[2:] == (2, False)
        sweep.flush.assert_not_called()
    else:
        schedule.assert_not_called()
        sweep.flush.assert_called_once()
    assert maintenance["pending_learns_replayed"]["deferred_to_background"] == remainder
