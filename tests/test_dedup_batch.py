"""Tests for batch dedup migration behavior."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

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
    """FR05: a journal replay must never pay the O(N^2) migration inline.

    Measured 2026-09-04: the migration firing inside the FIRST replay turned a
    33,552 ms drain into 321,050 ms. FR01's "overruns by at most one record's
    replay" is vacuous if one replay can take 307 s, so this is a correctness
    dependency of the budget.

    NON-VACUITY: drop ``and not _from_journal`` from the migration branch in
    ``tools/_learn_impl.py`` and ``test_journal_replay_never_runs_batch_dedup_inline``
    fails on ``calls == []``.
    """

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
        real_batch = dedup_mod.batch_dedup

        def _spy(*args: object, **kwargs: object) -> object:
            calls.append("called")
            return real_batch(*args, **kwargs)  # type: ignore[arg-type]

        with patch.object(dedup_mod, "batch_dedup", _spy):
            self._seed_pending(trw_dir, "L-mig001")
            replay_journaled_learn(trw_dir, config, "L-mig001", {"summary": "x", "detail": "y"})
            assert calls == [], "batch dedup ran INSIDE a journal replay"

            # The interactive path is deliberately unchanged.
            from trw_mcp.tools._learn_impl import execute_learn

            execute_learn(
                summary="interactive learn probe with a summary long enough to clear the noise filter",
                detail="detail body for the interactive migration probe",
                trw_dir=trw_dir,
                config=config,
            )
            assert calls, "the interactive path must still run the migration inline"

    def test_marker_is_not_written_when_the_replay_skipped_the_migration(self, tmp_path: Path) -> None:
        """Skipping is not completing: the migration stays owed and the next sweep retries."""
        from trw_mcp.tools._learn_journal_wiring import replay_journaled_learn

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        config = self._config()
        self._seed_pending(trw_dir, "L-mig002")

        replay_journaled_learn(trw_dir, config, "L-mig002", {"summary": "x", "detail": "y"})

        assert is_migration_needed(trw_dir) is True

    def test_drain_reschedules_the_migration_onto_the_background_thread(self, tmp_path: Path) -> None:
        """The migration is not lost by being skipped — the drain hands it to FR02.

        Asserted on the SIDE EFFECT, not on the thread handle: the continuation
        clears its own handle in a ``finally``, so a fast migration can be done
        before the caller looks and ``_DRAIN_THREAD is not None`` would flake.
        """
        import time as _time

        from trw_mcp.state.memory_pressure import take_writer_census
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = tmp_path / ".trw"
        (trw_dir / "learnings" / "entries").mkdir(parents=True)
        config = self._config()
        self._seed_pending(trw_dir, "L-mig003")
        assert is_migration_needed(trw_dir) is True
        ran: list[str] = []
        steps._DRAIN_THREAD = None

        from trw_mcp.tools import _learn_journal_background as background

        def _spy(*_a: object, **_kw: object) -> dict[str, object]:
            ran.append("migrated")
            return {"status": "completed"}

        with patch.object(background, "run_batch_dedup_migration", _spy):
            maintenance: dict[str, object] = {}
            steps._run_learn_journal_drain(
                trw_dir,
                config,
                maintenance,  # type: ignore[arg-type]
                census=take_writer_census(trw_dir, threshold=2),
                defer_memory_heavy=False,
            )
            deadline = _time.monotonic() + 60.0
            while not ran and _time.monotonic() < deadline:
                _time.sleep(0.02)
            thread = steps._DRAIN_THREAD
            if thread is not None:
                thread.join(60.0)
                assert not thread.is_alive()
        steps._DRAIN_THREAD = None

        assert ran == ["migrated"], "the owed migration was never rescheduled onto the continuation"

    def test_skipped_migration_is_logged_and_leaves_the_marker_absent(
        self, tmp_path: Path, reader: FileStateReader, writer: FileStateWriter, captured_structlog: list[dict]
    ) -> None:
        """An unbounded scan still queued to fire must be visible, not silent."""
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
