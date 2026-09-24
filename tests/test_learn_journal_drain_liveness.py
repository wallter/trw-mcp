"""Tests for the trw_learn write-ahead journal drain (PRD-INFRA-171-FR06).

PRD-CORE-280 FR01 removed the writer-pressure census and its deferral
machinery: the drain now runs unconditionally on every session_start rather
than throttling under writer contention. What remains here:

- every sweep asks for the full drain limit (``TestFullDrainLimit``);
- an **operator-invocable drain** flushes pending on demand
  (``TestOperatorDrain``);
- the wall-clock budget tests, which are independent of writer pressure.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest
import structlog

from tests._memory_fixtures import MemoryDaemon, attach_checkout
from trw_mcp.models.config import TRWConfig
from trw_mcp.state import learn_journal
from trw_mcp.tools._ceremony_helpers import run_auto_maintenance


def _trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    return trw_dir


def _journal(trw_dir: Path, index: int) -> str:
    """Journal one replayable pending record and return its learning id."""
    learning_id = f"L-live{index:03d}"
    learn_journal.journal_pending(
        trw_dir,
        learning_id,
        {
            "summary": f"drain liveness probe record {index:03d} with a summary long enough to pass gates",
            "detail": f"detailed body for drain-liveness probe record {index:03d} under sustained writer pressure",
            "impact": 0.5,
        },
    )
    return learning_id


def _attach(trw_dir: Path, memory_daemon: MemoryDaemon, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin *trw_dir* to the session daemon so a real replay (the ``learn-drain``
    subcommand, ...) never opens an in-process ``memory.db`` (PRD-CORE-280 slice e1)."""
    monkeypatch.setenv("TRW_USER_DIR", str(memory_daemon.user_dir))
    attach_checkout(trw_dir, memory_daemon)


class TestOperatorDrain:
    """FR06 (e): an operator can flush pending without waiting for a quiet session_start."""

    def test_learn_drain_subcommand_flushes_pending(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        memory_daemon: MemoryDaemon,
    ) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        trw_dir = _trw_dir(tmp_path)
        _attach(trw_dir, memory_daemon, monkeypatch)
        config = TRWConfig(embeddings_enabled=False)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda *_a, **_k: trw_dir)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
        for i in range(2):
            _journal(trw_dir, i)

        assert "learn-drain" in SUBCOMMAND_HANDLERS
        SUBCOMMAND_HANDLERS["learn-drain"](argparse.Namespace(limit=None, as_json=False))

        assert learn_journal.pending_count(trw_dir) == 0
        out = capsys.readouterr().out
        assert "learn-drain" in out and "2" in out

    def test_learn_drain_exits_nonzero_when_a_record_is_retained(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        from trw_mcp.server._subcommands import SUBCOMMAND_HANDLERS

        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda *_a, **_k: trw_dir)
        monkeypatch.setattr("trw_mcp.models.config.get_config", lambda: config)
        monkeypatch.setattr(
            "trw_mcp.tools._learn_journal_wiring.replay_journaled_learn",
            lambda *_a, **_k: "error",
        )
        _journal(trw_dir, 0)

        with pytest.raises(SystemExit) as excinfo:
            SUBCOMMAND_HANDLERS["learn-drain"](argparse.Namespace(limit=None, as_json=True))

        assert excinfo.value.code == 1
        assert learn_journal.pending_count(trw_dir) == 1  # retained, never dropped
        assert '"retained": 1' in capsys.readouterr().out

    def test_learn_drain_parses_from_the_cli(self) -> None:
        from trw_mcp.server._cli_argparse import _build_arg_parser

        ns = _build_arg_parser().parse_args(["learn-drain", "--limit", "7", "--json"])
        assert ns.command == "learn-drain"
        assert ns.limit == 7
        assert ns.as_json is True


class TestFullDrainLimit:
    """NFR02 plus the un-pressured regression guard."""

    def test_the_sweep_uses_the_full_drain_limit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory_daemon: MemoryDaemon
    ) -> None:
        """Every sweep asks for the whole drain limit; no census clamps it (PRD-CORE-280 FR01).

        The wall-clock budget is pinned wide so the assertion is about the count
        limit, not about how fast this machine replays three records.
        """
        trw_dir = _trw_dir(tmp_path)
        _attach(trw_dir, memory_daemon, monkeypatch)
        config = TRWConfig(embeddings_enabled=False, dedup_enabled=False, learn_journal_drain_budget_ms=120_000)
        seen: list[int] = []
        real_drain = learn_journal.drain_pending

        def _spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(int(kwargs["limit"]))
            return real_drain(*args, **kwargs)

        monkeypatch.setattr(learn_journal, "drain_pending", _spy)
        for i in range(3):
            _journal(trw_dir, i)

        run_auto_maintenance(trw_dir, config)

        assert seen == [config.learn_journal_drain_limit]
        assert learn_journal.pending_count(trw_dir) == 0


class _FakeClock:
    """A monotonic clock the test drives, so no assertion depends on real time."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        return self.now

    def advance_on_replay(self, learning_id: str, payload: dict[str, object]) -> str:
        """A replay that consumes nothing and burns *step* seconds of fake time."""
        self.now += self.step
        return "error"


class TestWallClockBudget:
    """PRD-FIX-130-FR01: the sweep is bounded by a clock, not only by a count.

    NON-VACUITY: every test here drives ``drain_pending`` with a fake monotonic
    clock and asserts on the ATTEMPTED count. Delete the budget break condition
    in ``state/learn_journal.drain_pending`` and all four fail on behaviour —
    a count-only sweep attempts every record regardless of the clock.
    """

    def test_wall_clock_budget_stops_the_sweep_before_the_next_replay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Record 1 burns the whole budget, so record 2 is never started."""
        trw_dir = _trw_dir(tmp_path)
        for i in range(5):
            _journal(trw_dir, i)
        clock = _FakeClock(step=10.0)
        monkeypatch.setattr(learn_journal.time, "monotonic", clock)

        result = learn_journal.drain_pending(
            trw_dir,
            clock.advance_on_replay,
            limit=50,
            budget_seconds=3.0,
        )

        assert result["replayed"] == 1, result
        assert result["deferred"] == 4, result
        assert result.get("budget_exhausted") is True, result
        # The bound is the property, not the number: attempted is a function of
        # the clock and the budget, never of the backlog size.
        assert learn_journal.pending_count(trw_dir) == 5

    def test_attempted_count_does_not_grow_with_the_backlog(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The property a count limit cannot deliver: K-independence."""
        attempted: list[int] = []
        for k in (5, 20, 50):
            trw_dir = _trw_dir(tmp_path / f"k{k}")
            for i in range(k):
                _journal(trw_dir, i)
            clock = _FakeClock(step=2.0)
            monkeypatch.setattr(learn_journal.time, "monotonic", clock)
            result = learn_journal.drain_pending(
                trw_dir,
                clock.advance_on_replay,
                limit=50,
                budget_seconds=3.0,
            )
            attempted.append(int(result["replayed"]))
        assert attempted == [2, 2, 2], attempted

    def test_zero_budget_defers_every_record(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``learn_journal_drain_budget_ms: 0`` is the config-only background-only mode."""
        trw_dir = _trw_dir(tmp_path)
        for i in range(4):
            _journal(trw_dir, i)
        clock = _FakeClock(step=1.0)
        monkeypatch.setattr(learn_journal.time, "monotonic", clock)

        result = learn_journal.drain_pending(
            trw_dir,
            clock.advance_on_replay,
            limit=50,
            budget_seconds=0.0,
        )

        assert result["replayed"] == 0, result
        assert result["deferred"] == 4, result
        assert result.get("budget_exhausted") is True, result

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0, float("-inf")])
    def test_invalid_budget_refuses_instead_of_running_unbounded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: float
    ) -> None:
        """An unusable budget is 0, never 'unbounded' — silently restoring the defect is the failure."""
        trw_dir = _trw_dir(tmp_path)
        for i in range(3):
            _journal(trw_dir, i)
        clock = _FakeClock(step=1.0)
        monkeypatch.setattr(learn_journal.time, "monotonic", clock)

        with structlog.testing.capture_logs() as logs:
            result = learn_journal.drain_pending(
                trw_dir,
                clock.advance_on_replay,
                limit=50,
                budget_seconds=bad,
            )

        assert result["replayed"] == 0, result
        assert any(e.get("event") == "learn_journal_drain_budget_invalid" for e in logs), logs

    def test_none_budget_stays_unbounded_for_the_operator_cli(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An operator-invoked drain is not a hot path and keeps its old semantics."""
        trw_dir = _trw_dir(tmp_path)
        for i in range(6):
            _journal(trw_dir, i)
        clock = _FakeClock(step=1000.0)
        monkeypatch.setattr(learn_journal.time, "monotonic", clock)

        result = learn_journal.drain_pending(trw_dir, clock.advance_on_replay, limit=50)

        assert result["replayed"] == 6, result
        assert "budget_exhausted" not in result, result

    def test_zero_pending_reads_no_clock_and_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """NFR01: the budget is resolved only AFTER the empty-pending early return."""
        trw_dir = _trw_dir(tmp_path)
        reads: list[int] = []

        def _counting_monotonic() -> float:
            reads.append(1)
            return 0.0

        monkeypatch.setattr(learn_journal.time, "monotonic", _counting_monotonic)

        result = learn_journal.drain_pending(
            trw_dir,
            lambda _lid, _p: "recorded",
            limit=50,
            budget_seconds=3.0,
        )

        assert result == {}
        assert reads == [], "the zero-pending path must not even read the clock"
