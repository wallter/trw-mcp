"""Durability tests for the trw_learn write-ahead journal.

Root cause (2026-07-24, specimen #6 of the SURFACE-CENSUS §6 defect class —
durable persistence gated behind slow, interruptible work): ``execute_learn``
performs its first durable write (``store_learning``) only AFTER a slow
pre-store pipeline (active-set load + semantic dedup + embedding cold-start).
On the first learn of a session that pipeline can exceed the MCP client's 120s
tool timeout; the client backgrounds the call, and if the session then EXITS
mid-pipeline the accepted learning is NEVER persisted — a silent loss.

The fix journals an ACCEPTED learning to ``.trw/learnings/pending/<id>.json``
(fsync'd) BEFORE the slow work, consumes it on any terminal-handled outcome,
and replays it on the next ``session_start`` maintenance sweep.

These tests PROVE the behavior, not its existence:
- A dedup that is interrupted (raises) mid-pipeline → the learning still
  persists, recallably, via the real session_start drain (``TestDurability``).
- Byte-identical re-learns still collapse and the journal is consumed
  (``test_byte_identical_relearn_collapses_and_consumes_journal``).
- A record whose store was never consumed replays EXACTLY ONCE, no duplicate
  (``test_replay_after_store_does_not_duplicate``).
- A store error RETAINS the record; the next real drain recovers it
  (``test_store_error_retains_journal_then_recovers``).
- The ``learn_journal_enabled`` kill switch disables journaling
  (``test_disabled_journal_writes_nothing``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state import learn_journal
from trw_mcp.tools._ceremony_helpers import run_auto_maintenance
from trw_mcp.tools._learn_impl import execute_learn

_S = "journal durability probe summary that is unique enough to key on"
_D = "detailed body for the write-ahead-journal durability regression probe"


def _trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    return trw_dir


def _active_with_summary(trw_dir: Path, summary: str) -> int:
    """Count ACTIVE stored learnings whose summary matches (real backend query)."""
    from trw_mcp.state.memory_adapter import list_active_learnings

    return sum(1 for e in list_active_learnings(trw_dir) if str(e.get("summary", "")) == summary)


def _boom(*_a: object, **_k: object) -> object:
    raise RuntimeError("simulated session exit mid-dedup")


class TestDurability:
    """The non-negotiable invariant: an accepted learning is never silently lost."""

    def test_interrupted_dedup_is_recovered_by_session_start_drain(self, tmp_path: Path) -> None:
        """A learning whose call dies mid-dedup still lands, via the real drain.

        Phase 1 injects a dedup that raises (the process is killed before the
        store is reached). Nothing is stored, but the journal holds the record.
        Phase 2 runs the REAL ``run_auto_maintenance`` (the session_start
        consumer) — it must replay the record so the learning becomes recallable
        exactly once, and consume the pending file.
        """
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)

        with pytest.raises(RuntimeError):
            execute_learn(summary=_S, detail=_D, trw_dir=trw_dir, config=config, _check_and_handle_dedup=_boom)

        # Journaled before the slow work; store never reached.
        assert learn_journal.pending_count(trw_dir) == 1
        assert _active_with_summary(trw_dir, _S) == 0

        maintenance = run_auto_maintenance(trw_dir, config)

        assert "pending_learns_replayed" in maintenance
        assert learn_journal.pending_count(trw_dir) == 0
        assert _active_with_summary(trw_dir, _S) == 1

    def test_byte_identical_relearn_collapses_and_consumes_journal(self, tmp_path: Path) -> None:
        """Exact-content dedup still collapses byte-identical re-learns.

        The journal must not resurrect the "one summary 92x" pathology: a second
        identical learn is deduped (not a second row), and both calls leave the
        pending directory empty.
        """
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)

        r1 = execute_learn(summary=_S, detail=_D, trw_dir=trw_dir, config=config)
        assert r1.get("status") == "recorded"
        assert learn_journal.pending_count(trw_dir) == 0  # consumed on store success

        r2 = execute_learn(summary=_S, detail=_D, trw_dir=trw_dir, config=config)
        assert r2.get("status") in {"merged", "skipped"}  # exact-content dedup
        assert learn_journal.pending_count(trw_dir) == 0  # consumed on the dedup path

        assert _active_with_summary(trw_dir, _S) == 1  # exactly one row

    def test_replay_after_store_does_not_duplicate(self, tmp_path: Path) -> None:
        """A record whose store completed but consume did not replays exactly once.

        Simulates a kill in the tiny window AFTER the DB write but BEFORE the
        journal was consumed: re-journal the same content, then drain. Exact
        dedup collapses the replay against the existing row — one row, no dup,
        journal consumed.
        """
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)

        first = execute_learn(summary=_S, detail=_D, trw_dir=trw_dir, config=config)
        assert first.get("status") == "recorded"
        assert learn_journal.pending_count(trw_dir) == 0

        # A stranded pending record for the already-stored content.
        learn_journal.journal_pending(trw_dir, "L-stranded", {"summary": _S, "detail": _D, "impact": 0.5})
        assert learn_journal.pending_count(trw_dir) == 1

        run_auto_maintenance(trw_dir, config)

        assert learn_journal.pending_count(trw_dir) == 0
        assert _active_with_summary(trw_dir, _S) == 1

    def test_store_error_retains_journal_then_recovers(self, tmp_path: Path) -> None:
        """A store ERROR retains the pending record; the next real drain recovers it.

        Phase 1 injects a store that returns ``status="error"`` (mirrors a
        backend-unavailable failure). The learning must NOT be lost — the record
        stays for replay. Phase 2's real drain stores it.
        """
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)

        def _failing_store(_trw_dir: Path, *, learning_id: str, **_kw: object) -> dict[str, object]:
            return {
                "learning_id": learning_id,
                "path": f"sqlite://{learning_id}",
                "status": "error",
                "error": "simulated backend unavailable",
                "distribution_warning": "",
            }

        result = execute_learn(
            summary=_S,
            detail=_D,
            trw_dir=trw_dir,
            config=config,
            _check_and_handle_dedup=lambda *_a, **_k: None,
            _adapter_store=_failing_store,
        )
        assert result.get("status") == "error"
        assert learn_journal.pending_count(trw_dir) == 1  # RETAINED, not lost
        assert _active_with_summary(trw_dir, _S) == 0

        run_auto_maintenance(trw_dir, config)

        assert learn_journal.pending_count(trw_dir) == 0
        assert _active_with_summary(trw_dir, _S) == 1

    def test_disabled_journal_writes_nothing(self, tmp_path: Path) -> None:
        """The kill switch disables journaling entirely (legacy loss-prone path)."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_enabled=False)

        with pytest.raises(RuntimeError):
            execute_learn(summary=_S, detail=_D, trw_dir=trw_dir, config=config, _check_and_handle_dedup=_boom)

        assert learn_journal.pending_count(trw_dir) == 0


class TestJournalModule:
    """Unit coverage of the durable journal primitives."""

    def test_journal_and_iter_roundtrip(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        payload: dict[str, object] = {"summary": "s", "detail": "d", "impact": 0.7}
        path = learn_journal.journal_pending(trw_dir, "L-abc", payload)
        assert path is not None and path.exists()
        assert learn_journal.pending_count(trw_dir) == 1
        records = list(learn_journal.iter_pending(trw_dir))
        assert records == [("L-abc", payload)]

    def test_consume_removes_record(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        learn_journal.journal_pending(trw_dir, "L-x", {"summary": "s"})
        learn_journal.consume_pending(trw_dir, "L-x")
        assert learn_journal.pending_count(trw_dir) == 0
        # Idempotent — consuming an absent record is a no-op.
        learn_journal.consume_pending(trw_dir, "L-x")

    def test_iter_skips_unknown_version_and_corrupt(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        directory = learn_journal.pending_dir(trw_dir)
        directory.mkdir(parents=True)
        (directory / "future.json").write_text('{"version": 999, "learning_id": "L-f", "payload": {}}')
        (directory / "corrupt.json").write_text("{not json")
        learn_journal.journal_pending(trw_dir, "L-good", {"summary": "ok"})
        # Only the well-formed current-version record is replayable; the poison
        # records are left on disk (not silently discarded).
        assert list(learn_journal.iter_pending(trw_dir)) == [("L-good", {"summary": "ok"})]
        assert learn_journal.pending_count(trw_dir) == 3

    def test_drain_empty_returns_empty(self, tmp_path: Path) -> None:
        assert learn_journal.drain_pending(tmp_path / ".trw", lambda _i, _p: "recorded", limit=10) == {}

    def test_drain_counts_recovered_and_retained(self, tmp_path: Path) -> None:
        """A consuming success counts recovered; a store error counts retained.

        The fake replay CONSUMES on success exactly as ``execute_learn`` does —
        ``recovered`` is booked on the pending file being gone, not on the
        returned string (see ``test_learn_journal_drain_accounting.py``).
        """
        trw_dir = tmp_path / ".trw"
        learn_journal.journal_pending(trw_dir, "L-ok", {"summary": "a"})
        learn_journal.journal_pending(trw_dir, "L-err", {"summary": "b"})

        def _replay(learning_id: str, _payload: dict[str, object]) -> str:
            if learning_id == "L-err":
                return "error"
            learn_journal.consume_pending(trw_dir, learning_id)
            return "recorded"

        result = learn_journal.drain_pending(trw_dir, _replay, limit=10)
        assert result["pending"] == 2
        assert result["replayed"] == 2
        assert result["recovered"] == 1
        assert result["retained"] == 1

    def test_drain_respects_limit_and_defers_remainder(self, tmp_path: Path) -> None:
        trw_dir = tmp_path / ".trw"
        for i in range(3):
            learn_journal.journal_pending(trw_dir, f"L-{i}", {"summary": str(i)})
        calls: list[str] = []

        def _replay(learning_id: str, _payload: dict[str, object]) -> str:
            calls.append(learning_id)
            return "recorded"

        result = learn_journal.drain_pending(trw_dir, _replay, limit=2)
        assert len(calls) == 2  # bounded
        assert result["replayed"] == 2
        assert result["deferred"] == 1
