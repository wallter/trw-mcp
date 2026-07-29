"""Drain-liveness tests for the trw_learn write-ahead journal (PRD-INFRA-171-FR06).

Measured defect (2026-07-25, across all 122 ``trw-mcp`` log files in ``.trw/logs``):
``learn_journal_pending_written`` fired **42** times, ``learn_journal_drain`` — the
INFO success event — fired **ZERO** times, ever. 42 accepted learnings went onto
disk and not one logged sweep took any off.

The cause is arithmetic, not a bug in the drain. ``session_start_writer_pressure_threshold``
defaults to 2 and ``should_defer_session_start_optional_work`` defers as soon as a
single PEER writer exists, so in this repo's 5-to-10-instance fleet the gate never
disengages and ``_run_learn_journal_drain`` took its early return on every sweep.
"Durable" degraded into "indefinitely queued": a record in ``.trw/learnings/pending``
is safe but not recallable, so no future session inherits it.

The deferral's INTENT is sound — recovery must not fight a live writer for the
memory backend — so these tests do NOT assert that the gate is gone. They assert
the bounded escape hatch that makes progress inevitable:

- a small **minimum-progress batch** drains on every sweep even under sustained
  pressure (``TestMinimumProgressBatch``);
- a record at or past the **age bound** drains regardless of pressure, inclusive
  at the boundary (``TestAgeEscapeHatch``);
- every sweep that replays anything emits a **maintenance-layer success event**
  with replayed/retained counts, and the deferral event is RETAINED alongside it
  (``TestSweepObservability``);
- an **operator-invocable drain** flushes pending on demand (``TestOperatorDrain``);
- the liveness path is fail-open and the un-pressured path is unchanged
  (``TestFailOpenAndUnpressuredParity``).

NON-VACUITY: ``TestMinimumProgressBatch`` and ``TestAgeEscapeHatch`` are written
against the PRE-CHANGE public API only (``run_auto_maintenance`` +
``learn_journal.pending_count``). Against HEAD~ they fail on BEHAVIOR — the drain
early-returns under pressure and ``pending_count`` never moves — not on a missing
symbol.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import pytest
import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.state import learn_journal
from trw_mcp.tools._ceremony_helpers import run_auto_maintenance

_PEER_PIDS = [4242, 4243, 4244]


def _trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    return trw_dir


def _pin_writer_pressure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold peer writers above the pressure threshold for the whole test.

    Patches the SOURCE module because ``run_auto_maintenance`` imports the
    predicate inside the function body.
    """

    def _always_pressured(_trw_dir: Path, **_kw: object) -> tuple[bool, list[int], str]:
        return True, list(_PEER_PIDS), "writer_pressure"

    monkeypatch.setattr(
        "trw_mcp.state.memory_pressure.should_defer_session_start_optional_work",
        _always_pressured,
    )


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


def _age(trw_dir: Path, learning_id: str, seconds: float) -> None:
    """Backdate a pending record's mtime by *seconds* (no sleeping)."""
    path = learn_journal.pending_dir(trw_dir) / f"{learning_id}.json"
    stamp = os.stat(path).st_mtime - seconds
    os.utime(path, (stamp, stamp))


def _stored_summaries(trw_dir: Path) -> set[str]:
    from trw_mcp.state.memory_adapter import list_active_learnings

    return {str(e.get("summary", "")) for e in list_active_learnings(trw_dir)}


class TestMinimumProgressBatch:
    """Under sustained pressure the sweep still makes bounded progress."""

    def test_pending_drains_under_sustained_writer_pressure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The headline regression: peers pinned above the threshold, records still land.

        Pre-change this asserts the measured 42-in/0-drained defect: the drain
        early-returns, ``pending_count`` stays at 3, and nothing is recallable.
        """
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _pin_writer_pressure(monkeypatch)
        for i in range(3):
            _journal(trw_dir, i)
        assert learn_journal.pending_count(trw_dir) == 3

        run_auto_maintenance(trw_dir, config)

        assert learn_journal.pending_count(trw_dir) < 3
        summaries = _stored_summaries(trw_dir)
        assert any("drain liveness probe record 000" in s for s in summaries)

    def test_pending_does_not_grow_monotonically_across_a_busy_window(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A simulated busy window: writes throughout, pressure throughout.

        The observed pre-change trend was monotonically upward (3, 4, 6, 7, 9).
        With the minimum-progress batch the window must end at or below where it
        started.
        """
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _pin_writer_pressure(monkeypatch)
        for i in range(4):
            _journal(trw_dir, i)
        start = learn_journal.pending_count(trw_dir)
        trace = [start]

        for round_index in range(4):
            _journal(trw_dir, 100 + round_index)  # a live writer keeps journaling
            run_auto_maintenance(trw_dir, config)
            trace.append(learn_journal.pending_count(trw_dir))

        assert trace[-1] <= start, f"pending grew across the busy window: {trace}"
        assert trace[-1] == 0, f"window did not fully drain: {trace}"

    def test_under_pressure_batch_is_strictly_less_than_drain_limit(self, tmp_path: Path) -> None:
        """The deferral's intent survives: the min-progress batch is a SMALLER sweep, not a full one."""
        trw_dir = _trw_dir(tmp_path)
        for i in range(10):
            _journal(trw_dir, i)

        budget = learn_journal.pressure_drain_budget(
            trw_dir,
            drain_limit=50,
            min_batch=2,
            max_age_seconds=0.0,
        )
        assert 0 < budget < 50

    def test_zero_min_batch_and_no_age_bound_restores_defer_always(self, tmp_path: Path) -> None:
        """Config-only rollback to the pre-FR06 behaviour (Rollout Plan P0 rollback)."""
        trw_dir = _trw_dir(tmp_path)
        _journal(trw_dir, 0)
        assert (
            learn_journal.pressure_drain_budget(
                trw_dir,
                drain_limit=50,
                min_batch=0,
                max_age_seconds=0.0,
            )
            == 0
        )

    def test_min_batch_is_clamped_below_a_small_drain_limit(self, tmp_path: Path) -> None:
        """A min_batch >= drain_limit is clamped, never allowed to become a full sweep."""
        trw_dir = _trw_dir(tmp_path)
        for i in range(5):
            _journal(trw_dir, i)
        assert (
            learn_journal.pressure_drain_budget(
                trw_dir,
                drain_limit=3,
                min_batch=99,
                max_age_seconds=0.0,
            )
            == 2
        )


class TestAgeEscapeHatch:
    """A record past the age bound drains regardless of writer pressure."""

    def test_aged_record_drains_under_pressure_with_zero_min_batch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """With the min-progress batch disabled, AGE is the only thing that can drain it."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(
            embeddings_enabled=False,
            learn_journal_drain_min_batch=0,
            learn_journal_pending_max_age_hours=1.0,
        )
        _pin_writer_pressure(monkeypatch)
        aged = _journal(trw_dir, 0)
        _journal(trw_dir, 1)
        _age(trw_dir, aged, seconds=2 * 3600)
        assert learn_journal.pending_count(trw_dir) == 2

        run_auto_maintenance(trw_dir, config)

        assert any("drain liveness probe record 000" in s for s in _stored_summaries(trw_dir))
        assert learn_journal.pending_count(trw_dir) == 1  # the young record is still held

    def test_age_bound_is_inclusive_at_the_boundary(self, tmp_path: Path) -> None:
        """Exactly at the bound drains; a second younger is held."""
        trw_dir = _trw_dir(tmp_path)
        exact = _journal(trw_dir, 0)
        young = _journal(trw_dir, 1)
        _age(trw_dir, exact, seconds=600.0)
        _age(trw_dir, young, seconds=599.0)

        assert learn_journal.aged_pending_count(trw_dir, max_age_seconds=600.0) == 1

    def test_disabled_age_bound_counts_nothing(self, tmp_path: Path) -> None:
        trw_dir = _trw_dir(tmp_path)
        aged = _journal(trw_dir, 0)
        _age(trw_dir, aged, seconds=10_000.0)
        assert learn_journal.aged_pending_count(trw_dir, max_age_seconds=0.0) == 0

    def test_poison_record_never_inflates_the_age_budget(self, tmp_path: Path) -> None:
        """An aged unknown-version record is skipped, so the hatch cannot spin on it."""
        trw_dir = _trw_dir(tmp_path)
        directory = learn_journal.pending_dir(trw_dir)
        directory.mkdir(parents=True, exist_ok=True)
        poison = directory / "future.json"
        poison.write_text('{"version": 999, "learning_id": "L-f", "payload": {}}', encoding="utf-8")
        stamp = os.stat(poison).st_mtime - 10_000.0
        os.utime(poison, (stamp, stamp))

        assert learn_journal.pending_count(trw_dir) == 1
        assert learn_journal.aged_pending_count(trw_dir, max_age_seconds=1.0) == 0
        assert (
            learn_journal.pressure_drain_budget(
                trw_dir,
                drain_limit=50,
                min_batch=0,
                max_age_seconds=1.0,
            )
            == 0
        )


class TestSweepObservability:
    """FR06 (d): drain liveness must be measurable from the ceremony layer."""

    def test_sweep_emits_maintenance_layer_success_event(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _pin_writer_pressure(monkeypatch)
        _journal(trw_dir, 0)

        with structlog.testing.capture_logs() as logs:
            run_auto_maintenance(trw_dir, config)

        events = [e for e in logs if e.get("event") == "learn_journal_drain_completed"]
        assert len(events) == 1, [e.get("event") for e in logs]
        assert events[0]["replayed"] >= 1
        assert events[0]["retained"] == 0
        assert events[0]["under_pressure"] is True

    def test_deferral_event_is_retained_when_records_remain(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The bounded batch replaces defer-EVERYTHING, not the deferral signal itself."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_drain_min_batch=1)
        _pin_writer_pressure(monkeypatch)
        for i in range(3):
            _journal(trw_dir, i)

        with structlog.testing.capture_logs() as logs:
            maintenance = run_auto_maintenance(trw_dir, config)

        assert "pending_learns_replayed" in maintenance
        assert "pending_learns_deferred" in maintenance
        assert any(e.get("event") == "learn_journal_drain_deferred" for e in logs)
        assert any(e.get("event") == "learn_journal_drain_completed" for e in logs)

    def test_no_deferral_advisory_when_the_batch_cleared_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _pin_writer_pressure(monkeypatch)
        _journal(trw_dir, 0)

        maintenance = run_auto_maintenance(trw_dir, config)

        assert "pending_learns_deferred" not in maintenance

    def test_nothing_pending_costs_no_payload_and_no_event(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _pin_writer_pressure(monkeypatch)

        with structlog.testing.capture_logs() as logs:
            maintenance = run_auto_maintenance(trw_dir, config)

        assert "pending_learns_replayed" not in maintenance
        assert "pending_learns_deferred" not in maintenance
        assert not [e for e in logs if e.get("event") == "learn_journal_drain_completed"]


class TestOperatorDrain:
    """FR06 (e): an operator can flush pending without waiting for a quiet session_start."""

    def test_learn_drain_subcommand_flushes_pending(
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


class TestFailOpenAndUnpressuredParity:
    """NFR02 plus the un-pressured regression guard."""

    def test_broken_liveness_path_degrades_to_defer_and_log(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False)
        _pin_writer_pressure(monkeypatch)
        _journal(trw_dir, 0)

        def _boom(*_a: object, **_k: object) -> int:
            raise RuntimeError("simulated liveness-path failure")

        monkeypatch.setattr(learn_journal, "pressure_drain_budget", _boom)

        with structlog.testing.capture_logs() as logs:
            maintenance = run_auto_maintenance(trw_dir, config)  # must not raise

        assert isinstance(maintenance, dict)
        assert any(e.get("event") == "maintenance_learn_journal_drain_failed" for e in logs)
        assert learn_journal.pending_count(trw_dir) == 1  # retained, never dropped

    def test_unpressured_sweep_uses_the_full_drain_limit(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no peers the sweep is byte-identical to today: full limit, no clamp."""
        trw_dir = _trw_dir(tmp_path)
        config = TRWConfig(embeddings_enabled=False, learn_journal_drain_min_batch=1)
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
