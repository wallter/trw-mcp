"""Drain-accounting tests for the trw_learn write-ahead journal.

Defect (adversarial audit I-2, 2026-07-25). ``execute_learn`` runs the write-time
accept gates BEFORE any ``consume_journal`` and returns their rejection payload
immediately. That payload's status is ``"rejected"`` — not ``"error"`` — and the
drain driver booked every non-``"error"`` status as ``recovered``. On the REPLAY
path the pending file already exists, so a gate-rejected record was:

* never consumed, and re-attempted on every sweep FOREVER;
* counted as a recovery in ``learn_journal_drain_completed`` telemetry while
  ``pending_count`` never moved;
* reported by ``trw-mcp learn-drain`` with exit code **0** — whose own docstring
  promised "every attempted record reached a terminal outcome" — while
  ``pending_after == pending_before``.

The trigger is real, not hypothetical: the gates can be STRICTER at replay time
than at journal time (``llm_utility_filter_enabled`` flipped on, or tightened
length caps / injection patterns / noise heuristics shipped by an upgrade). The
journal exists precisely to survive restarts and upgrades.

These tests pin the PROPERTY, not one scenario:

- ``TestNoFalseRecovery`` — over a MATRIX of non-consuming replay outcomes
  (every deterministic status, every transient status, raised exceptions), a
  replay that consumed nothing is never counted as ``recovered``.
- ``TestNoRecordIsRetriedForever`` — a deterministic refusal moves aside on the
  first attempt; a transient failure moves aside once its budget is spent.
- ``TestGatesAreNotWeakened`` — the accept gates still refuse the same content,
  on both the write path and the replay path.
- ``TestDeadLetterIsNotLoss`` — the moved record keeps its payload, records the
  reason, and re-arms if an operator moves it back.
- ``TestCliExitContract`` — ``learn-drain`` never exits 0 while pending failed
  to fall.

NON-VACUITY (observed against HEAD, i.e. before the fix):
``TestNoFalseRecovery``, ``TestGatesAreNotWeakened::
test_replay_path_rejection_is_not_weakened_into_a_store`` and
``TestCliExitContract`` are written against the PRE-CHANGE public API only
(``drain_pending``/``pending_count``/``SUBCOMMAND_HANDLERS``) and fail on
BEHAVIOR there — e.g. ``AssertionError: replay consumed nothing but was booked
recovered: {'pending': 1, 'replayed': 1, 'recovered': 1}`` and
``learn-drain exited 0 while pending stayed at 1``. The remaining classes
exercise the new dead-letter/retry-budget API and are expected to fail on a
missing symbol there.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.state import learn_journal

_SUMMARY = "drain accounting probe summary that is long enough to clear the noise filter"
_DETAIL = "detailed body for the drain-accounting regression probe"

# Content the write-time policy refuses deterministically (detail cap is 4000).
_OVERLONG_DETAIL = "x" * 8000


def _trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    return trw_dir


def _dead_letter_files(trw_dir: Path) -> list[Path]:
    """Dead-letter records via the literal on-disk layout (no new symbols)."""
    directory = trw_dir / "learnings" / "dead_letter"
    return sorted(directory.glob("*.json")) if directory.is_dir() else []


def _journal(trw_dir: Path, learning_id: str, *, detail: str = _DETAIL) -> None:
    learn_journal.journal_pending(
        trw_dir,
        learning_id,
        {"summary": _SUMMARY, "detail": detail, "impact": 0.5},
    )


def _real_replay(trw_dir: Path, config: TRWConfig):  # type: ignore[no-untyped-def]
    from trw_mcp.tools._learn_journal_wiring import replay_journaled_learn

    return lambda lid, payload: replay_journaled_learn(trw_dir, config, lid, payload)


class TestNoFalseRecovery:
    """The accounting property: nothing consumed => nothing recovered."""

    # Every terminal shape a replay can produce WITHOUT consuming its record.
    # Statuses come from execute_learn's early returns (accept gates ->
    # "rejected", store failure -> "error") plus a success string, which the
    # journal-disabled path can produce without a consume.
    NON_CONSUMING = [
        ("rejected", None),
        ("invalid", None),
        ("error", None),
        ("recorded", None),
        ("", ValueError("'gotcha' is not a valid MemoryType")),
        ("", TypeError("payload shape changed")),
        ("", RuntimeError("backend connection dropped")),
    ]

    @pytest.mark.parametrize(("status", "error"), NON_CONSUMING)
    def test_a_replay_that_consumes_nothing_is_never_recovered(
        self, tmp_path: Path, status: str, error: BaseException | None
    ) -> None:
        """Across every non-consuming outcome, ``recovered`` stays zero."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-none")

        def _replay(_lid: str, _payload: dict[str, object]) -> str:
            if error is not None:
                raise error
            return status

        result = learn_journal.drain_pending(trw_dir, _replay, limit=10)

        assert result.get("recovered", 0) == 0, f"replay consumed nothing but was booked recovered: {result}"
        assert result["replayed"] == 1
        # Every attempted record is accounted for by exactly one disposition.
        dispositions = result.get("recovered", 0) + result.get("dead_lettered", 0) + result.get("retained", 0)
        assert dispositions == result["replayed"]

    def test_recovered_is_booked_on_the_file_not_the_status_string(self, tmp_path: Path) -> None:
        """Ground truth is the pending file: a consuming replay IS a recovery.

        The mirror of the defect — classification must follow the disk, so a
        replay whose record is gone counts even when its status string is not a
        recognised success token.
        """
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-gone")

        def _replay(learning_id: str, _payload: dict[str, object]) -> str:
            learn_journal.consume_pending(trw_dir, learning_id)
            return "some-future-status"

        result = learn_journal.drain_pending(trw_dir, _replay, limit=10)

        assert result["recovered"] == 1
        assert learn_journal.pending_count(trw_dir) == 0

    def test_session_start_sweep_reports_no_false_recovery(self, tmp_path: Path) -> None:
        """The real maintenance consumer, end to end, with real gates.

        A journaled record whose detail exceeds the content-policy cap (the
        upgrade hazard: caps tightened after the record was written) must not be
        reported as replayed-and-recovered by ``run_auto_maintenance``.
        """
        from trw_mcp.tools._ceremony_helpers import run_auto_maintenance

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-gated", detail=_OVERLONG_DETAIL)

        maintenance = run_auto_maintenance(trw_dir, config)

        replayed = maintenance.get("pending_learns_replayed", {})
        assert isinstance(replayed, dict)
        assert replayed.get("recovered", 0) == 0, replayed
        assert learn_journal.pending_count(trw_dir) == 0  # moved aside, not left pending


class TestNoRecordIsRetriedForever:
    """Consumption policy: deterministic => aside now, transient => aside eventually."""

    def test_gate_rejected_replay_is_dead_lettered_on_the_first_attempt(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-gated", detail=_OVERLONG_DETAIL)

        result = learn_journal.drain_pending(
            trw_dir,
            _real_replay(trw_dir, config),
            limit=10,
            max_attempts=config.learn_journal_max_replay_attempts,
        )

        assert result.get("dead_lettered") == 1
        assert result.get("recovered", 0) == 0
        assert learn_journal.pending_count(trw_dir) == 0
        assert [p.name for p in _dead_letter_files(trw_dir)] == ["L-gated.json"]

    def test_a_dead_lettered_record_is_never_replayed_again(self, tmp_path: Path) -> None:
        """The point of the whole fix: the second sweep has nothing to do."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-gated", detail=_OVERLONG_DETAIL)
        replay = _real_replay(trw_dir, config)
        calls: list[str] = []

        def _counting(lid: str, payload: dict[str, object]) -> str:
            calls.append(lid)
            return replay(lid, payload)

        for _ in range(3):
            learn_journal.drain_pending(trw_dir, _counting, limit=10, max_attempts=5)

        assert calls == ["L-gated"], f"record was re-attempted {len(calls)} times"

    def test_deterministic_exception_dead_letters_instead_of_poisoning_the_journal(self, tmp_path: Path) -> None:
        """An invalid enum / schema violation fails identically forever."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-badenum")

        def _replay(_lid: str, _payload: dict[str, object]) -> str:
            raise ValueError("'gotcha' is not a valid MemoryType")

        result = learn_journal.drain_pending(trw_dir, _replay, limit=10, max_attempts=5)

        assert result.get("dead_lettered") == 1
        assert learn_journal.pending_count(trw_dir) == 0

    def test_transient_failure_is_retried_within_the_budget(self, tmp_path: Path) -> None:
        """A store error is the case the journal exists for — keep retrying it."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-transient")

        result = learn_journal.drain_pending(trw_dir, lambda *_a: "error", limit=10, max_attempts=5)

        assert result.get("retained") == 1
        assert result.get("dead_lettered", 0) == 0
        assert learn_journal.pending_count(trw_dir) == 1  # still queued, never lost

    def test_transient_failure_dead_letters_once_the_budget_is_exhausted(self, tmp_path: Path) -> None:
        """The budget is persisted, so it survives across sweeps (and restarts)."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-transient")
        outcomes = []

        for _ in range(3):
            outcomes.append(learn_journal.drain_pending(trw_dir, lambda *_a: "error", limit=10, max_attempts=3))

        assert [bool(o.get("retained")) for o in outcomes] == [True, True, False]
        assert outcomes[-1].get("dead_lettered") == 1
        assert learn_journal.pending_count(trw_dir) == 0
        assert [p.name for p in _dead_letter_files(trw_dir)] == ["L-transient.json"]

    def test_zero_budget_retains_transient_failures_indefinitely(self, tmp_path: Path) -> None:
        """Config-only rollback: 0 disables the budget for TRANSIENT failures."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-transient")

        for _ in range(6):
            learn_journal.drain_pending(trw_dir, lambda *_a: "error", limit=10, max_attempts=0)

        assert learn_journal.pending_count(trw_dir) == 1
        assert not _dead_letter_files(trw_dir)

    def test_zero_budget_still_dead_letters_a_deterministic_refusal(self, tmp_path: Path) -> None:
        """The rollback knob cannot restore the infinite-retry defect."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-gated")

        result = learn_journal.drain_pending(trw_dir, lambda *_a: "rejected", limit=10, max_attempts=0)

        assert result.get("dead_lettered") == 1
        assert learn_journal.pending_count(trw_dir) == 0

    def test_retry_bookkeeping_does_not_reset_the_age_escape_hatch(self, tmp_path: Path) -> None:
        """Attempt counts are rewritten with the ORIGINAL mtime preserved.

        The age hatch (PRD-INFRA-171-FR06) and the FIFO replay order are both
        keyed on mtime; bumping it on every failed sweep would make an aged
        record permanently young and defeat the eventual-drain guarantee.
        """
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-aged")
        path = learn_journal.pending_dir(trw_dir) / "L-aged.json"
        backdated = os.stat(path).st_mtime - 10_000.0
        os.utime(path, (backdated, backdated))

        learn_journal.drain_pending(trw_dir, lambda *_a: "error", limit=10, max_attempts=5)

        assert os.stat(path).st_mtime == pytest.approx(backdated, abs=1.0)
        assert learn_journal.aged_pending_count(trw_dir, max_age_seconds=3600.0) == 1


class TestGatesAreNotWeakened:
    """The fix changes what happens AFTER a refusal, never the refusal itself."""

    def test_write_path_rejection_is_unchanged_and_journals_nothing(self, tmp_path: Path) -> None:
        from trw_mcp.tools._learn_impl import execute_learn

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)

        result = execute_learn(summary=_SUMMARY, detail=_OVERLONG_DETAIL, trw_dir=trw_dir, config=config)

        assert result.get("status") == "rejected"
        assert learn_journal.pending_count(trw_dir) == 0  # never durably recorded
        assert not _dead_letter_files(trw_dir)

    def test_replay_path_rejection_is_not_weakened_into_a_store(self, tmp_path: Path) -> None:
        """The gated content must still NOT become a recallable learning."""
        from trw_mcp.state.memory_adapter import list_active_learnings

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-gated", detail=_OVERLONG_DETAIL)

        learn_journal.drain_pending(trw_dir, _real_replay(trw_dir, config), limit=10, max_attempts=5)

        assert not [e for e in list_active_learnings(trw_dir) if str(e.get("summary", "")) == _SUMMARY]


class TestDeadLetterIsNotLoss:
    """Moving aside must preserve the operator's data and the refusal reason."""

    def test_dead_letter_keeps_the_payload_and_records_why(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-gated")

        learn_journal.drain_pending(trw_dir, lambda *_a: "rejected", limit=10, max_attempts=5)

        (record_path,) = _dead_letter_files(trw_dir)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        assert record["learning_id"] == "L-gated"
        assert record["payload"]["summary"] == _SUMMARY
        assert record["dead_letter"]["reason"] == "deterministic_rejection:rejected"
        assert record["dead_letter"]["attempts"] == 1

    def test_an_operator_can_requeue_a_dead_lettered_record(self, tmp_path: Path) -> None:
        """The moved file keeps the full journal shape, so it re-arms on move-back."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-requeue")
        learn_journal.drain_pending(trw_dir, lambda *_a: "rejected", limit=10, max_attempts=5)
        (record_path,) = _dead_letter_files(trw_dir)

        record_path.replace(learn_journal.pending_dir(trw_dir) / record_path.name)

        assert learn_journal.pending_count(trw_dir) == 1
        result = learn_journal.drain_pending(trw_dir, _real_replay(trw_dir, config), limit=10, max_attempts=5)
        assert result["recovered"] == 1

    def test_a_failed_move_is_reported_as_retained_not_dead_lettered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Truthfulness: never book a move that did not happen."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-gated")
        monkeypatch.setattr(
            "trw_mcp.state._learn_journal_disposition.write_record_atomic",
            lambda *_a, **_k: False,
        )

        result = learn_journal.drain_pending(trw_dir, lambda *_a: "rejected", limit=10, max_attempts=5)

        assert result.get("dead_lettered", 0) == 0
        assert result.get("retained") == 1
        assert learn_journal.pending_count(trw_dir) == 1  # still on disk


class TestCliExitContract:
    """``learn-drain`` must never exit 0 while the journal failed to shrink."""

    @staticmethod
    def _invoke(trw_dir: Path, config: TRWConfig, monkeypatch: pytest.MonkeyPatch) -> int:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda *_a, **_k: trw_dir)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
        try:
            SUBCOMMAND_HANDLERS["learn-drain"](argparse.Namespace(limit=None, as_json=True))
        except SystemExit as exit_signal:
            return int(exit_signal.code or 0)
        return 0

    def test_exit_is_nonzero_when_pending_did_not_fall(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Poison records: the replay scan skips them, so pending cannot move."""
        trw_dir = _trw_dir(tmp_path)
        directory = learn_journal.pending_dir(trw_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "future.json").write_text('{"version": 999, "learning_id": "L-f", "payload": {}}')

        code = self._invoke(trw_dir, TRWConfig(embeddings_enabled=False), monkeypatch)

        payload = json.loads(capsys.readouterr().out)
        assert payload["pending_after"] == payload["pending_before"] == 1
        assert code != 0, "learn-drain exited 0 while pending stayed at 1"

    def test_exit_is_two_when_records_were_dead_lettered(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-gated", detail=_OVERLONG_DETAIL)

        code = self._invoke(trw_dir, TRWConfig(embeddings_enabled=False), monkeypatch)

        payload = json.loads(capsys.readouterr().out)
        assert payload["dead_lettered"] == 1
        assert payload["recovered"] == 0
        assert code == 2

    def test_exit_is_zero_only_when_the_journal_actually_drained(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Control: the success path still exits 0 (keeps the above non-vacuous)."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-ok")

        code = self._invoke(trw_dir, TRWConfig(embeddings_enabled=False), monkeypatch)

        payload = json.loads(capsys.readouterr().out)
        assert payload["pending_after"] == 0
        assert payload["recovered"] == 1
        assert code == 0

    def test_a_disabled_journal_does_not_report_a_drain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``consume_journal`` is a no-op when disabled, so no record could leave."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-ok")
        config = TRWConfig(embeddings_enabled=False, learn_journal_enabled=False)

        code = self._invoke(trw_dir, config, monkeypatch)

        payload = json.loads(capsys.readouterr().out)
        assert payload["disabled"] is True
        assert payload["pending_after"] == 1
        assert code != 0
