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
import structlog

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

    def test_schema_validation_error_dead_letters_on_the_first_attempt(self, tmp_path: Path) -> None:
        """A permanently-invalid pending record (trw-memory's own validation
        exception) must dead-letter on attempt 1, not replay to exhaustion.

        Regression: ``DETERMINISTIC_EXCEPTIONS`` only listed the stdlib
        ``ValueError``/``TypeError`` pair, so ``trw_memory.exceptions.
        SchemaValidationError`` (e.g. ``confidence="verified"`` without
        evidence) fell to the TRANSIENT branch and replayed with a full ERROR
        traceback up to ``max_attempts`` times before dead-lettering.
        """
        from trw_memory.exceptions import SchemaValidationError

        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-badschema")

        def _replay(_lid: str, _payload: dict[str, object]) -> str:
            raise SchemaValidationError("confidence='verified' requires evidence", reason="unsubstantiated_verified")

        result = learn_journal.drain_pending(trw_dir, _replay, limit=10, max_attempts=5)

        assert result.get("dead_lettered") == 1
        assert learn_journal.pending_count(trw_dir) == 0
        (record_path,) = _dead_letter_files(trw_dir)
        dead_letter = json.loads(record_path.read_text(encoding="utf-8"))["dead_letter"]
        assert dead_letter["reason"] == "deterministic_error:SchemaValidationError"
        assert dead_letter["attempts"] == 1

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


# ---------------------------------------------------------------------------
# PRD-FIX-130-FR02 / NFR02 / NFR03 / NFR04: background continuation accounting
# ---------------------------------------------------------------------------


def _quiet_census(trw_dir: Path):  # type: ignore[no-untyped-def]
    """A writer census with no peers — the unpressured branch."""
    from trw_mcp.state.memory_pressure import take_writer_census

    return take_writer_census(trw_dir, threshold=2)


def _drain_step(trw_dir: Path, config: TRWConfig) -> dict[str, object]:
    """Run the session_start drain sub-step and return its maintenance payload."""
    from trw_mcp.tools._ceremony_maintenance_steps import _run_learn_journal_drain

    maintenance: dict[str, object] = {}
    _run_learn_journal_drain(
        trw_dir,
        config,
        maintenance,  # type: ignore[arg-type]
        census=_quiet_census(trw_dir),
        defer_memory_heavy=False,
    )
    return maintenance


def _join_drain_thread(timeout: float = 60.0) -> None:
    """Join the FR02 continuation with an EXPLICIT finite timeout.

    A join that times out is a failure, never a pass: a background thread that
    never finishes is exactly the "consumer that exists and never runs" defect
    PRD-INFRA-171 was written for.
    """
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    thread = steps._DRAIN_THREAD
    if thread is None:
        return
    thread.join(timeout)
    assert not thread.is_alive(), f"background drain still alive after {timeout}s"


@pytest.fixture(autouse=True)
def _reset_drain_thread():  # type: ignore[no-untyped-def]
    """Module-level thread handles leak between tests unless reset explicitly."""
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    steps._DRAIN_THREAD = None
    yield
    thread = steps._DRAIN_THREAD
    if thread is not None:
        thread.join(30.0)
    steps._DRAIN_THREAD = None


class TestBackgroundContinuation:
    """FR02: what the clock refused still lands in THIS process, honestly counted.

    NON-VACUITY: every assertion here is on ``pending_count`` and on the payload
    counts through the public maintenance sub-step. Remove the scheduler and
    ``deferred_to_background`` is 0 while records stay on disk; remove the FR01
    budget and ``replayed_inline`` is the whole backlog.
    """

    def test_budget_remainder_drains_on_a_single_flight_background_thread(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_drain_budget_ms=0)
        for i in range(6):
            _journal(trw_dir, f"L-bg{i:03d}", detail=f"{_DETAIL} number {i}")
        assert learn_journal.pending_count(trw_dir) == 6

        maintenance = _drain_step(trw_dir, config)

        payload = maintenance["pending_learns_replayed"]
        assert isinstance(payload, dict)
        assert payload["replayed_inline"] == 0, payload
        assert payload["deferred_to_background"] == 6, payload
        _join_drain_thread()
        assert learn_journal.pending_count(trw_dir) == 0

    def test_a_second_overlapping_schedule_starts_no_second_thread(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        with structlog.testing.capture_logs() as logs:
            first = steps._schedule_background_drain(trw_dir, config, 1, False)
            second = steps._schedule_background_drain(trw_dir, config, 1, False)
        assert first is True
        # Either the first thread already finished (then the second legitimately
        # starts) or it was still running and the guard refused. The property is
        # that a refusal is LOUD, never silent.
        if second is False:
            assert any(e.get("event") == "learn_journal_drain_already_running" for e in logs), logs
        _join_drain_thread()

    def test_completion_event_carries_counts_and_a_duration(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_drain_budget_ms=0)
        for i in range(3):
            _journal(trw_dir, f"L-ev{i:03d}", detail=f"{_DETAIL} event {i}")

        with structlog.testing.capture_logs() as logs:
            _drain_step(trw_dir, config)
            _join_drain_thread()

        scheduled = [e for e in logs if e.get("event") == "learn_journal_background_drain_scheduled"]
        completed = [e for e in logs if e.get("event") == "learn_journal_background_drain_completed"]
        assert scheduled, logs
        assert completed, logs
        event = completed[0]
        for field in ("replayed", "recovered", "dead_lettered", "retained", "duration_ms"):
            assert field in event, (field, event)
        # NFR03: counts and durations only — never learning content.
        assert not any(_SUMMARY in str(v) or _DETAIL in str(v) for v in event.values()), event

    def test_drain_completed_event_carries_the_budget_fields(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_drain_budget_ms=0)
        _journal(trw_dir, "L-budget1")

        with structlog.testing.capture_logs() as logs:
            _drain_step(trw_dir, config)
            _join_drain_thread()

        done = [e for e in logs if e.get("event") == "learn_journal_drain_completed"]
        assert done, logs
        for field in ("replayed_inline", "deferred_to_background", "budget_ms", "budget_exhausted"):
            assert field in done[0], (field, done[0])
        assert not any(_SUMMARY in str(v) or _DETAIL in str(v) for v in done[0].values()), done[0]

    def test_payload_never_reports_a_deferred_record_as_replayed(self, tmp_path: Path) -> None:
        """replayed_inline + deferred_to_background == the records the sweep could attempt."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_drain_budget_ms=0, learn_journal_drain_limit=4)
        for i in range(9):
            _journal(trw_dir, f"L-cnt{i:03d}", detail=f"{_DETAIL} count {i}")

        maintenance = _drain_step(trw_dir, config)

        payload = maintenance["pending_learns_replayed"]
        assert isinstance(payload, dict)
        inline = int(str(payload["replayed_inline"]))
        background = int(str(payload["deferred_to_background"]))
        assert inline == 0
        assert inline + background == 4, payload  # the per-sweep count limit
        _join_drain_thread()
        # The 5 the count limit (not the clock) held back are still pending.
        assert learn_journal.pending_count(trw_dir) == 5

    def test_background_drain_failure_is_fail_open_and_clears_its_handle(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NFR02: the thread is a daemon, it catches everything, and it lets go of its handle."""
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-boom001")

        def _boom(*_a: object, **_kw: object) -> None:
            raise RuntimeError("simulated background replay failure")

        monkeypatch.setattr(learn_journal, "drain_pending", _boom)
        with structlog.testing.capture_logs() as logs:
            assert steps._schedule_background_drain(trw_dir, config, 1, False) is True
            thread = steps._DRAIN_THREAD
            assert thread is not None
            assert thread.daemon is True
            thread.join(30.0)
            assert not thread.is_alive()

        assert steps._DRAIN_THREAD is None, "the handle must be cleared in a finally"
        assert any(e.get("event") == "learn_journal_background_drain_failed" for e in logs), logs
        assert learn_journal.pending_count(trw_dir) == 1  # retained, never lost

    def test_termination_mid_replay_leaves_records_on_disk_and_uncounted(self, tmp_path: Path) -> None:
        """NFR02: daemon=True is safe because consume happens only after a terminal outcome.

        Simulates interpreter shutdown killing the thread between records: the
        replay of record 2 raises ``SystemExit`` (a BaseException the sweep does
        NOT catch, exactly like a real kill). Records 2 and 3 must still be on
        disk, record 1's attempt accounting must have been persisted, and nothing
        unconsumed may be reported as replayed.
        """
        trw_dir = _trw_dir(tmp_path)
        for index in range(3):
            _journal(trw_dir, f"L-kill{index:03d}")
        seen: list[str] = []

        def _replay(learning_id: str, _payload: dict[str, object]) -> str:
            seen.append(learning_id)
            if len(seen) == 1:
                return "error"  # transient: retained, attempt persisted
            raise SystemExit("simulated interpreter shutdown mid-replay")

        with pytest.raises(SystemExit):
            learn_journal.drain_pending(trw_dir, _replay, limit=50, max_attempts=5)

        # Nothing was consumed, so every accepted learning is still recoverable.
        assert learn_journal.pending_count(trw_dir) == 3
        first = json.loads((trw_dir / "learnings" / "pending" / f"{seen[0]}.json").read_text(encoding="utf-8"))
        assert first["attempts"] == 1, first
        killed = json.loads((trw_dir / "learnings" / "pending" / f"{seen[1]}.json").read_text(encoding="utf-8"))
        assert "attempts" not in killed or killed["attempts"] == 0, killed

    def test_a_concurrent_process_replaying_the_same_records_adds_no_duplicate(self, tmp_path: Path) -> None:
        """Cross-process safety without a lock file: consume + file-existence classification.

        Two server processes can each schedule a continuation over the same
        pending directory. This simulates the second one arriving after the first
        already consumed the files: its own sweep simply never yields them, so it
        books no recovery and writes no second row.
        """
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=False)
        for i in range(3):
            learn_journal.journal_pending(
                trw_dir,
                f"L-xproc{i:03d}",
                {
                    "summary": f"cross-process replay probe number {i} with a summary past the noise gate",
                    "detail": f"{_DETAIL} xproc {i}",
                    "impact": 0.5,
                },
            )

        first = learn_journal.drain_pending(
            trw_dir, _real_replay(trw_dir, config), limit=50, learnings_dir=config.learnings_dir
        )
        assert int(first["recovered"]) == 3, first
        assert learn_journal.pending_count(trw_dir) == 0

        # The "second process" runs the identical sweep against the same directory.
        second = learn_journal.drain_pending(
            trw_dir, _real_replay(trw_dir, config), limit=50, learnings_dir=config.learnings_dir
        )
        assert second == {}, second  # nothing pending -> nothing attempted, nothing recovered

        from trw_mcp.state.memory_adapter import list_active_learnings

        summaries = [str(e.get("summary", "")) for e in list_active_learnings(trw_dir)]
        assert len(summaries) == len(set(summaries)), summaries

    def test_pending_schema_and_dead_letter_behaviour_unchanged(self, tmp_path: Path) -> None:
        """NFR04: no migration, same dead-letter reason, and no key was removed."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-compat01")
        record = json.loads((trw_dir / "learnings" / "pending" / "L-compat01.json").read_text(encoding="utf-8"))
        assert record["version"] == learn_journal.JOURNAL_RECORD_VERSION

        result = learn_journal.drain_pending(
            trw_dir, _real_replay(trw_dir, config), limit=50, learnings_dir=config.learnings_dir
        )
        assert set(result) >= {"pending", "replayed", "recovered"}

        # A deterministic refusal still moves aside with the same accounting.
        _journal(trw_dir, "L-compat02", detail=_OVERLONG_DETAIL)
        refused = learn_journal.drain_pending(
            trw_dir, _real_replay(trw_dir, config), limit=50, learnings_dir=config.learnings_dir
        )
        assert int(refused.get("dead_lettered", 0)) == 1, refused
        assert _dead_letter_files(trw_dir)


# ---------------------------------------------------------------------------
# Double-audit round (FIX130-01/02/07/08/09/10/11): the concurrency and
# truthfulness properties the first implementation asserted but did not hold.
# ---------------------------------------------------------------------------


def _sweep_ctx(replay, *, flush=None, degraded=False, index_failed=False):  # type: ignore[no-untyped-def]
    """A hand-built sweep context, so a test can control what a worker does."""
    from trw_mcp.tools._learn_journal_wiring import SweepContext

    return SweepContext(
        replay=replay,
        flush=flush if flush is not None else (lambda: True),
        degraded=lambda: degraded,
        index_failed=lambda: index_failed,
    )


class TestRecordClaims:
    """FIX130-01: two live drains must never replay the same record.

    NON-VACUITY: delete the ``acquire_claim`` call in ``drain_pending`` and
    ``test_two_racing_drains_never_replay_one_record_twice`` fails on the
    duplicate-id assertion — both threads materialise the same pending list and
    both enter the replay for every record.
    """

    def test_two_racing_drains_never_replay_one_record_twice(self, tmp_path: Path) -> None:
        import threading
        import time as _time

        trw_dir = _trw_dir(tmp_path)
        for i in range(4):
            _journal(trw_dir, f"L-race{i:03d}")

        lock = threading.Lock()
        seen: list[str] = []
        start = threading.Barrier(2, timeout=30.0)
        results: dict[str, dict[str, object]] = {}

        def _replay(learning_id: str, _payload: dict[str, object]) -> str:
            with lock:
                seen.append(learning_id)
            # Hold the record long enough that an unclaimed peer, which already
            # has this id in its own snapshot, would enter the replay too.
            _time.sleep(0.05)
            learn_journal.consume_pending(trw_dir, learning_id)
            return "recorded"

        def _drive(tag: str) -> None:
            start.wait()
            results[tag] = dict(learn_journal.drain_pending(trw_dir, _replay, limit=50))

        threads = [threading.Thread(target=_drive, args=(tag,), name=tag) for tag in ("a", "b")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60.0)
            assert not thread.is_alive()

        assert sorted(seen) == sorted(set(seen)), f"a record was replayed twice across two drains: {seen}"
        assert sorted(set(seen)) == [f"L-race{i:03d}" for i in range(4)], seen
        assert learn_journal.pending_count(trw_dir) == 0
        total_attempted = sum(int(r.get("replayed", 0)) for r in results.values())
        assert total_attempted == 4, results
        # The loser reports the truth rather than silently doing nothing: every
        # record is attempted by exactly one drain and seen by the other as
        # contended, so the two counts partition the snapshot on both sides.
        assert sum(int(r.get("contended", 0)) for r in results.values()) >= 1, results
        for result in results.values():
            assert int(result.get("replayed", 0)) + int(result.get("contended", 0)) + int(
                result.get("deferred", 0)
            ) == int(result.get("pending", 0)), result

    def test_a_live_owners_claim_defers_the_record_instead_of_racing_it(self, tmp_path: Path) -> None:
        from trw_mcp.state._learn_journal_claims import claim_path_for
        from trw_mcp.state._writer_census_identity import process_birth_epoch

        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-held001")
        record = trw_dir / "learnings" / "pending" / "L-held001.json"
        claim = claim_path_for(record)
        claim.write_text(json.dumps({"pid": os.getpid(), "epoch": process_birth_epoch(os.getpid())}))

        seen: list[str] = []
        result = learn_journal.drain_pending(trw_dir, lambda lid, _p: seen.append(lid) or "recorded", limit=50)

        assert seen == [], "a record another live drain holds must not be replayed"
        assert int(result.get("contended", 0)) == 1, result
        assert int(result.get("replayed", 0)) == 0, result
        assert "deferred" not in result, result  # contended is NOT deferred
        assert claim.is_file(), "a live owner's claim must not be stolen"
        assert learn_journal.pending_count(trw_dir) == 1

    def test_a_dead_owners_claim_is_reclaimed(self, tmp_path: Path) -> None:
        from trw_mcp.state._learn_journal_claims import claim_path_for

        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-stale01")
        record = trw_dir / "learnings" / "pending" / "L-stale01.json"
        claim = claim_path_for(record)
        # A PID that cannot be alive: the crashed owner of a previous sweep.
        claim.write_text(json.dumps({"pid": 2**22 + 7, "epoch": 1.0}))

        seen: list[str] = []

        def _replay(learning_id: str, _payload: dict[str, object]) -> str:
            seen.append(learning_id)
            learn_journal.consume_pending(trw_dir, learning_id)
            return "recorded"

        result = learn_journal.drain_pending(trw_dir, _replay, limit=50)

        assert seen == ["L-stale01"], "a crashed owner must not strand its record forever"
        assert int(result.get("recovered", 0)) == 1, result
        assert not claim.exists(), "the reclaimed claim must be released"

    def test_a_claim_is_not_mistaken_for_a_pending_record(self, tmp_path: Path) -> None:
        """The claim sidecar must stay invisible to every pending-directory scan."""
        from trw_mcp.state._learn_journal_claims import acquire_claim, release_claim

        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, "L-invis01")
        record = trw_dir / "learnings" / "pending" / "L-invis01.json"
        held = acquire_claim(record)
        assert held is not None
        try:
            assert learn_journal.pending_count(trw_dir) == 1
            assert [lid for lid, _p in learn_journal.iter_pending(trw_dir)] == ["L-invis01"]
        finally:
            release_claim(held)


class TestMigrationClaim:
    """FIX130-07: 'exactly once' must hold ACROSS processes, not per process.

    NON-VACUITY: remove the claim from ``run_batch_dedup_migration`` and
    ``test_two_racing_migrations_run_the_scan_once`` records two batch_dedup
    calls — both callers see the same absent marker.
    """

    def test_two_racing_migrations_run_the_scan_once(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import threading
        import time as _time

        from trw_mcp.state import dedup as dedup_mod
        from trw_mcp.tools import _learn_journal_background as background

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        calls: list[str] = []
        lock = threading.Lock()

        def _fake_batch(td: Path, _reader: object, writer: object, *, config: object = None) -> dict[str, object]:
            # Scoped to THIS test's store: a background drain left over from a
            # neighbouring test schedules its own migration against a different
            # trw_dir, and counting it here would be a spurious second "run".
            if td == trw_dir:
                with lock:
                    calls.append("ran")
            _time.sleep(0.1)
            (td / "learnings" / "dedup_migration.yaml").write_text("completed: true\n")
            return {"status": "completed", "entries_scanned": 0}

        monkeypatch.setattr(dedup_mod, "batch_dedup", _fake_batch)
        start = threading.Barrier(2, timeout=30.0)
        outcomes: list[dict[str, object]] = []

        def _drive() -> None:
            start.wait()
            outcomes.append(background.run_batch_dedup_migration(trw_dir, config))

        threads = [threading.Thread(target=_drive) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60.0)
            assert not thread.is_alive()

        assert calls == ["ran"], f"the quadratic migration ran {len(calls)} times concurrently"
        assert len(outcomes) == 2, outcomes
        statuses = sorted(str(o.get("status", "")) for o in outcomes)
        # The loser either lost the claim (contended) or won it after the winner
        # wrote the marker and re-checked the need away (skipped). Both are
        # truthful; what must never happen is two scans, or a second
        # "completed" for work this caller did not do.
        assert statuses[0] == "completed", outcomes
        assert statuses[1] in {"contended", "skipped"}, outcomes

    def test_a_skipped_migration_leaves_no_claim_behind(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state import dedup as dedup_mod
        from trw_mcp.state._learn_journal_claims import claim_path_for
        from trw_mcp.tools import _learn_journal_background as background

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        monkeypatch.setattr(
            dedup_mod, "batch_dedup", lambda *_a, **_kw: {"status": "skipped", "reason": "embeddings unavailable"}
        )

        outcome = background.run_batch_dedup_migration(trw_dir, config)

        marker = trw_dir / "learnings" / "dedup_migration.yaml"
        assert outcome["status"] == "skipped", outcome
        assert not marker.exists(), "a skipped migration must NOT be marked complete"
        assert not claim_path_for(marker).exists(), "a skipped migration must not leave a stale claim"
        # And the next attempt is therefore free to run.
        assert background.run_batch_dedup_migration(trw_dir, config)["status"] == "skipped"


class TestOverlappingSweeps:
    """FIX130-02: a remainder nobody is coming for is never reported as handled.

    NON-VACUITY: restore ``to_background = 0`` on a refused schedule and
    ``test_a_refused_schedule_reports_the_remainder_to_the_next_sweep`` fails —
    the payload claims zero deferred work while records sit on disk.
    """

    def test_a_second_overlapping_schedule_starts_no_second_thread(self, tmp_path: Path) -> None:
        import threading

        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-hold001")
        gate = threading.Event()
        entered = threading.Event()

        def _blocking(_lid: str, _payload: dict[str, object]) -> str:
            entered.set()
            gate.wait(30.0)
            return "error"

        try:
            with structlog.testing.capture_logs() as logs:
                first = steps._schedule_background_drain(trw_dir, config, 1, False, sweep=_sweep_ctx(_blocking))
                assert first is True
                assert entered.wait(30.0), "the first worker never started"
                held = steps._DRAIN_THREAD
                assert held is not None and held.is_alive()
                second = steps._schedule_background_drain(trw_dir, config, 1, False, sweep=_sweep_ctx(_blocking))
            # The point of the test: a SECOND thread must not exist while the
            # first is provably still inside its replay.
            assert second is False, "an overlapping schedule started a second worker"
            assert steps._DRAIN_THREAD is held, "the single-flight handle was overwritten"
            assert any(e.get("event") == "learn_journal_drain_already_running" for e in logs), logs
        finally:
            gate.set()
            _join_drain_thread()

    def test_a_refused_schedule_reports_the_remainder_to_the_next_sweep(self, tmp_path: Path) -> None:
        import threading

        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_drain_budget_ms=0)
        for i in range(3):
            _journal(trw_dir, f"L-over{i:03d}")
        gate = threading.Event()
        entered = threading.Event()

        def _blocking(_lid: str, _payload: dict[str, object]) -> str:
            entered.set()
            gate.wait(30.0)
            return "error"

        try:
            assert steps._schedule_background_drain(trw_dir, config, 1, False, sweep=_sweep_ctx(_blocking)) is True
            assert entered.wait(30.0)
            maintenance = _drain_step(trw_dir, config)
        finally:
            gate.set()
            _join_drain_thread()

        payload = maintenance["pending_learns_replayed"]
        assert isinstance(payload, dict)
        assert payload["replayed_inline"] == 0, payload
        assert payload["deferred_to_background"] == 0, payload
        assert int(str(payload["deferred_to_next_sweep"])) > 0, payload

    def test_the_worker_relists_and_takes_records_added_after_its_snapshot(self, tmp_path: Path) -> None:
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _journal(trw_dir, "L-relist001")
        seen: list[str] = []

        def _replay(learning_id: str, _payload: dict[str, object]) -> str:
            seen.append(learning_id)
            learn_journal.consume_pending(trw_dir, learning_id)
            if learning_id == "L-relist001":
                # Arrives AFTER the worker's first snapshot — the overlapping
                # sweep's remainder, in the shape the running flight sees it.
                _journal(trw_dir, "L-relist002")
            return "recorded"

        assert steps._schedule_background_drain(trw_dir, config, 5, False, sweep=_sweep_ctx(_replay)) is True
        _join_drain_thread()

        assert seen == ["L-relist001", "L-relist002"], seen
        assert learn_journal.pending_count(trw_dir) == 0


class TestBackgroundTruthfulness:
    """FIX130-08/09/10: the completion event and the payload state what happened."""

    def test_a_skipped_migration_is_not_reported_as_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.state import dedup as dedup_mod
        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        monkeypatch.setattr(
            dedup_mod, "batch_dedup", lambda *_a, **_kw: {"status": "skipped", "reason": "embeddings unavailable"}
        )

        with structlog.testing.capture_logs() as logs:
            assert steps._schedule_background_drain(trw_dir, config, 0, True) is True
            _join_drain_thread()

        done = [e for e in logs if e.get("event") == "learn_journal_background_drain_completed"]
        assert done, logs
        assert done[0]["migration_requested"] is True, done[0]
        assert done[0]["migration_outcome"] == "skipped", done[0]
        assert done[0]["migration_run"] is False, "a skipped migration was reported as run"

    def test_missing_marker_is_not_reported_as_pending_capture_work(self, tmp_path: Path) -> None:
        import threading

        from trw_mcp.tools import _ceremony_maintenance_steps as steps

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=True, learn_journal_drain_budget_ms=0)
        _journal(trw_dir, "L-mig900")
        gate = threading.Event()
        entered = threading.Event()

        def _blocking(_lid: str, _payload: dict[str, object]) -> str:
            entered.set()
            gate.wait(30.0)
            return "error"

        try:
            assert steps._schedule_background_drain(trw_dir, config, 1, False, sweep=_sweep_ctx(_blocking)) is True
            assert entered.wait(30.0)
            maintenance = _drain_step(trw_dir, config)
        finally:
            gate.set()
            _join_drain_thread()

        payload = maintenance["pending_learns_replayed"]
        assert isinstance(payload, dict)
        assert "migration_pending" not in payload, payload
        assert "migration_scheduled" not in payload, payload

    def test_a_failed_index_flush_retains_its_rows_and_says_so(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """FIX130-10: clearing the sink first silently discarded the whole batch."""
        from trw_mcp.state.analytics import entries as entries_mod
        from trw_mcp.tools import _learn_journal_wiring as wiring

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=False)
        for i in range(3):
            _journal(trw_dir, f"L-idx{i:03d}", detail=f"{_DETAIL} index {i}")

        written: list[list[object]] = []

        def _boom(_td: Path, rows: list[object]) -> None:
            raise OSError("simulated index write failure")

        monkeypatch.setattr(entries_mod, "update_learning_index_batch", _boom)
        sweep = wiring.make_sweep_replay(trw_dir, config)
        learn_journal.drain_pending(trw_dir, sweep.replay, limit=50, learnings_dir=config.learnings_dir)
        assert sweep.flush() is False
        assert sweep.index_failed() is True

        # The rows survived the failure: a later flush still projects them.
        monkeypatch.setattr(entries_mod, "update_learning_index_batch", lambda _td, rows: written.append(list(rows)))
        assert sweep.flush() is True
        assert written and len(written[0]) == 3, written

    def test_retired_quota_active_listing_is_not_called_or_marked_degraded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """CD: retired quota listing cannot fail or degrade replay."""
        from trw_mcp.state import memory_adapter

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=False)
        _journal(trw_dir, "L-deg001")
        attempts: list[int] = []

        def _boom(*_a: object, **_kw: object) -> list[dict[str, object]]:
            attempts.append(1)
            raise RuntimeError("simulated active-set listing failure")

        monkeypatch.setattr(memory_adapter, "list_active_learnings", _boom)
        with structlog.testing.capture_logs() as logs:
            maintenance = _drain_step(trw_dir, config)
            _join_drain_thread()

        assert attempts == [], "capture no longer needs the quota active set"
        warned = [e for e in logs if e.get("event") == "drain_shared_active_set_unavailable"]
        assert not warned, logs
        payload = maintenance["pending_learns_replayed"]
        assert isinstance(payload, dict)
        assert not payload.get("active_set_degraded", False), payload


class TestOperatorDrainIsBatchedToo:
    """Operator drain retains batched index writes without quota corpus loading.

    Returning to per-record index writes fails the one-write assertion;
    restoring retired quota work fails the zero-listing assertion.
    """

    def test_a_five_record_operator_drain_writes_the_index_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from trw_mcp.state import memory_adapter
        from trw_mcp.state.analytics import entries as entries_mod

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=False)
        for i in range(5):
            _journal(trw_dir, f"L-cli{i:03d}", detail=f"{_DETAIL} cli {i}")

        index_writes: list[Path] = []
        active_listings: list[int] = []
        real_write = entries_mod.FileStateWriter.write_yaml
        real_list = memory_adapter.list_active_learnings

        def _counting_write(self: object, path: Path, data: object) -> None:
            if path.name == "index.yaml":
                index_writes.append(path)
            real_write(self, path, data)  # type: ignore[arg-type]

        def _counting_list(*args: object, **kwargs: object) -> object:
            active_listings.append(1)
            return real_list(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(entries_mod.FileStateWriter, "write_yaml", _counting_write)
        monkeypatch.setattr(memory_adapter, "list_active_learnings", _counting_list)

        code = TestCliExitContract._invoke(trw_dir, config, monkeypatch)

        payload = json.loads(capsys.readouterr().out)
        assert code == 0, payload
        assert payload["pending_after"] == 0, payload
        assert payload["recovered"] == 5, payload
        assert len(index_writes) == 1, f"the operator drain did {len(index_writes)} index writes for 5 records"
        assert len(active_listings) == 0, f"the operator drain did {len(active_listings)} active listings"
        # NFR: the operator drain stays UNBOUNDED in time (FR01) — all five ran.
        assert payload["replayed"] == 5, payload
