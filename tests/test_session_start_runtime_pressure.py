"""PRD-FIX-080 + PRD-CORE-257 regression tests for MCP timeout hardening."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from trw_mcp.models.config import TRWConfig
from trw_mcp.state.memory_pressure import WriterCensus, live_memory_writer_pids, take_writer_census
from trw_mcp.tools._ceremony_helpers import run_auto_maintenance
from trw_mcp.tools._session_recall_helpers import perform_session_recalls
from trw_mcp.tools._session_recall_pressure import SurfaceTrackingResult


def _minimal_trw_dir(tmp_path: Path) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory" / "memory.db.writers").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "learnings" / "receipts").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


def _write_lock(trw_dir: Path, name: str, pid: int, *, epoch: float | None = None) -> None:
    """Write a writer lock in the producer's two-line shape (pid, registration epoch).

    Mirrors ``trw-memory/src/trw_memory/storage/_writer_registry.py`` so the
    PRD-CORE-257-FR10 identity check sees a realistic record.
    """
    registered = time.time() if epoch is None else epoch
    (trw_dir / "memory" / "memory.db.writers" / name).write_text(f"{pid}\n{registered:.6f}\n", encoding="utf-8")


def _fake_peer_registry(trw_dir: Path, monkeypatch: pytest.MonkeyPatch, *, peers: int) -> list[int]:
    """Register ``peers`` synthetic live peer writers plus this process.

    Eight live peer processes cannot be conjured in a unit test, so liveness is
    stubbed while everything else (lock parsing, identity, peer arithmetic) runs
    for real.
    """
    import trw_mcp.state.memory_pressure as mp

    pids = [900_000 + index for index in range(peers)]
    _write_lock(trw_dir, "self.lock", os.getpid())
    for pid in pids:
        _write_lock(trw_dir, f"peer-{pid}.lock", pid)
    monkeypatch.setattr(mp, "_pid_is_alive", lambda pid: True)
    return pids


def _run_async(coro: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _get_tools_sync(server: Any) -> dict[str, Any]:
    tools = _run_async(server.list_tools())
    return {tool.name: tool for tool in tools}


def test_live_memory_writer_pids_ignores_stale_locks(tmp_path: Path) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    live_pid = os.getpid()
    _write_lock(trw_dir, "live.lock", live_pid)
    _write_lock(trw_dir, "stale.lock", 999_999_999)
    (trw_dir / "memory" / "memory.db.writers" / "bad.lock").write_text("not-a-pid\n", encoding="utf-8")

    assert live_memory_writer_pids(trw_dir) == [live_pid]
    # Self-only registration is the steady state for a stdio per-instance
    # server — it must never trigger deferral, which would otherwise turn into a
    # permanent skip and let WAL grow unbounded.
    census = take_writer_census(trw_dir, threshold=2)
    assert census.under_pressure is False
    assert census.writer_pids == (live_pid,)
    assert census.peer_writer_count == 0


def _write_pins(trw_dir: Path, entries: dict[str, dict[str, Any]]) -> None:
    """Write ``.trw/runtime/pins.json`` with the given pin entries."""
    import json

    runtime_dir = trw_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "pins.json").write_text(json.dumps(entries), encoding="utf-8")


def _iso_ago(hours: float) -> str:
    """Return an ISO8601 ``Z``-suffixed timestamp *hours* in the past."""
    from datetime import datetime, timedelta, timezone

    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def test_live_memory_writer_pids_excludes_stale_heartbeat_pid(tmp_path: Path) -> None:
    """F5 root-cause A: a live-but-abandoned writer (stale heartbeat) is dropped.

    A PID whose freshest pins.json heartbeat is older than ``pin_ttl_hours``
    must NOT count as an active writer, even though its process is still alive.
    Fresh-heartbeat PIDs and PIDs with no pin entry stay counted (fail-open).
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    live_pid = os.getpid()
    parent_pid = os.getppid()

    _write_lock(trw_dir, "stale.lock", live_pid)
    _write_lock(trw_dir, "fresh.lock", parent_pid)

    # live_pid has only a stale heartbeat (5h old, well past the 1h TTL);
    # parent_pid has a fresh heartbeat (well within TTL).
    _write_pins(
        trw_dir,
        {
            "session-stale": {
                "run_path": str(trw_dir),
                "pid": live_pid,
                "last_heartbeat_ts": _iso_ago(5.0),
            },
            "session-fresh": {
                "run_path": str(trw_dir),
                "pid": parent_pid,
                "last_heartbeat_ts": _iso_ago(0.1),
            },
        },
    )

    pids = live_memory_writer_pids(trw_dir, pin_ttl_hours=1)
    assert live_pid not in pids  # stale heartbeat → excluded
    assert parent_pid in pids  # fresh heartbeat → counted


def test_live_memory_writer_pids_counts_pid_without_pin_entry(tmp_path: Path) -> None:
    """F5 root-cause A fail-open: a writer PID with no pin entry stays counted.

    Non-ceremony writers (no pins.json record) must never be dropped by the
    heartbeat-age filter — that would under-report pressure and let a real
    concurrent writer go undetected.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    live_pid = os.getpid()
    _write_lock(trw_dir, "nopin.lock", live_pid)
    # pins.json names a DIFFERENT pid only.
    _write_pins(
        trw_dir,
        {
            "session-other": {
                "run_path": str(trw_dir),
                "pid": 999_999_998,
                "last_heartbeat_ts": _iso_ago(0.1),
            }
        },
    )

    pids = live_memory_writer_pids(trw_dir, pin_ttl_hours=1)
    assert pids == [live_pid]


def test_live_memory_writer_pids_uses_freshest_heartbeat_for_pid(tmp_path: Path) -> None:
    """A pid with multiple pins is judged by its FRESHEST heartbeat."""
    trw_dir = _minimal_trw_dir(tmp_path)
    live_pid = os.getpid()
    _write_lock(trw_dir, "multi.lock", live_pid)
    _write_pins(
        trw_dir,
        {
            "session-old": {
                "run_path": str(trw_dir),
                "pid": live_pid,
                "last_heartbeat_ts": _iso_ago(9.0),
            },
            "session-new": {
                "run_path": str(trw_dir),
                "pid": live_pid,
                "last_heartbeat_ts": _iso_ago(0.2),
            },
        },
    )

    pids = live_memory_writer_pids(trw_dir, pin_ttl_hours=1)
    assert pids == [live_pid]  # freshest heartbeat is fresh → counted


def test_live_memory_writer_pids_none_ttl_skips_heartbeat_filter(tmp_path: Path) -> None:
    """When pin_ttl_hours is None the heartbeat filter is disabled (back-compat)."""
    trw_dir = _minimal_trw_dir(tmp_path)
    live_pid = os.getpid()
    _write_lock(trw_dir, "stale.lock", live_pid)
    _write_pins(
        trw_dir,
        {
            "session-stale": {
                "run_path": str(trw_dir),
                "pid": live_pid,
                "last_heartbeat_ts": _iso_ago(99.0),
            }
        },
    )

    assert live_memory_writer_pids(trw_dir) == [live_pid]
    assert live_memory_writer_pids(trw_dir, pin_ttl_hours=None) == [live_pid]


def test_should_defer_optional_work_drops_stale_writer_via_ttl(tmp_path: Path) -> None:
    """A peer writer with a stale heartbeat no longer triggers deferral."""
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())
    _write_lock(trw_dir, "peer.lock", os.getppid())
    # Self fresh, peer stale → peer should be excluded, leaving self-only (no defer).
    _write_pins(
        trw_dir,
        {
            "self": {
                "run_path": str(trw_dir),
                "pid": os.getpid(),
                "last_heartbeat_ts": _iso_ago(0.1),
            },
            "peer": {
                "run_path": str(trw_dir),
                "pid": os.getppid(),
                "last_heartbeat_ts": _iso_ago(5.0),
            },
        },
    )

    census = take_writer_census(trw_dir, threshold=2, pin_ttl_hours=1)
    assert os.getppid() not in census.writer_pids
    assert census.peer_writer_count == 0
    assert census.under_pressure is False


def test_peer_writer_threshold_replaces_writer_present(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-CORE-257-FR01: the threshold DECIDES deferral, measured in peer writers.

    Replaces ``test_should_defer_session_start_optional_work_triggers_on_peer_pid``
    (one peer at threshold 2 asserted a reason in the two-value set) and
    ``test_perform_session_recalls_defers_optional_side_effects_on_writer_presence``
    / ``test_run_auto_maintenance_defers_optional_checks_on_writer_presence`` /
    ``test_append_ceremony_status_defers_nudges_on_writer_presence``, all three of
    which asserted the deleted ``writer_present`` reason. Before this change the
    predicate deferred as soon as ONE peer writer existed and the threshold only
    relabelled the reason, so raising the knob changed a string, not a decision.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())

    # Self-only registration never defers, at any threshold.
    for threshold in (2, 8, 64):
        self_only = take_writer_census(trw_dir, threshold=threshold)
        assert self_only.under_pressure is False
        assert self_only.peer_writer_count == 0
        assert self_only.census_state == "measured"

    # 1..7 peers at threshold 8 do NOT defer — the behaviour this FR restores.
    for peers in range(1, 8):
        sub = _minimal_trw_dir(tmp_path / f"below-{peers}")
        _fake_peer_registry(sub, monkeypatch, peers=peers)
        census = take_writer_census(sub, threshold=8)
        assert census.peer_writer_count == peers
        assert census.under_pressure is False, f"{peers} peers must not defer at threshold 8"

    # 8 peers at threshold 8 does — the boundary is inclusive.
    at_bound = _minimal_trw_dir(tmp_path / "at-bound")
    fake_pids = _fake_peer_registry(at_bound, monkeypatch, peers=8)
    census = take_writer_census(at_bound, threshold=8)
    assert census.peer_writer_count == 8
    assert census.under_pressure is True
    assert census.writer_count == 9
    assert census.threshold == 8
    assert set(fake_pids) <= set(census.writer_pids)
    # ...and the same registry one notch below the bar does not.
    assert take_writer_census(at_bound, threshold=9).under_pressure is False

    # The Field floor (ge=2) is the only clamp; no internal clamp remains.
    with pytest.raises(ValidationError):
        TRWConfig(session_start_writer_pressure_threshold=1)


def test_writer_present_reason_is_deleted_from_the_package() -> None:
    """FR01/FR04: the two-tier reason vocabulary is deleted, not deprecated."""
    src = Path(__file__).resolve().parents[1] / "src" / "trw_mcp"
    offenders = sorted(
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if "writer_present" in path.read_text(encoding="utf-8")
        or "retain_legacy_reason" in path.read_text(encoding="utf-8")
    )
    assert offenders == []


def test_census_unreadable_registry_is_not_a_healthy_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR05: an absence of measurement is not a measurement of absence."""
    import trw_mcp.state.memory_pressure as mp

    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "self.lock", os.getpid())

    def _explode(_writers_dir: Path) -> list[Path]:
        raise OSError("registry unreadable")

    monkeypatch.setattr(mp, "_iter_lock_paths", _explode)
    census = take_writer_census(trw_dir, threshold=8)

    assert census.census_state == "unreadable"
    assert census.under_pressure is False
    assert census.writer_count == 0
    assert census.peer_writer_count == 0


def test_writer_identity_and_heartbeat_state(tmp_path: Path) -> None:
    """PRD-CORE-257-FR10: PID-reuse ghosts are excluded, unmeasured says so."""
    from trw_mcp.state._pin_store import pin_store_path
    from trw_mcp.state._writer_census_identity import read_writer_lock, scoped_pin_store_path

    trw_dir = _minimal_trw_dir(tmp_path)
    writers = trw_dir / "memory" / "memory.db.writers"

    # BOTH lines of the lock are parsed — the producer writes pid + epoch.
    (writers / "shape.lock").write_text("4242\n1788557175.537830\n", encoding="utf-8")
    record = read_writer_lock(writers / "shape.lock")
    assert record is not None
    assert record.pid == 4242
    assert record.registered_epoch == pytest.approx(1788557175.537830)
    (writers / "shape.lock").unlink()

    # A lock registered long before the process now holding that pid started is
    # a PID-reuse ghost and is excluded from the census.
    _write_lock(trw_dir, "self.lock", os.getpid())
    _write_lock(trw_dir, "ghost.lock", os.getppid(), epoch=1000.0)
    census = take_writer_census(trw_dir, threshold=8)
    assert os.getppid() not in census.writer_pids
    assert census.writer_pids == (os.getpid(),)
    # This reader NEVER unlinks a lock — pruning belongs to trw-memory.
    assert (writers / "ghost.lock").exists()

    # A heartbeat dated a year in the FUTURE is implausible clock skew: the pid
    # is not marked fresh and the state says the heartbeat was not measured.
    (writers / "ghost.lock").unlink()
    _write_lock(trw_dir, "peer.lock", os.getppid())
    _write_pins(
        trw_dir,
        {
            "future": {
                "run_path": str(trw_dir),
                "pid": os.getppid(),
                "last_heartbeat_ts": _iso_ago(-24 * 365),
            }
        },
    )
    skewed = take_writer_census(trw_dir, threshold=8, pin_ttl_hours=1)
    assert skewed.heartbeat_state != "measured"
    assert os.getppid() in skewed.writer_pids  # fail-open: counted, not fresh

    # OD-2 contract: the scoped pin read resolves to the same path the global
    # pin store uses, so the two definitions cannot drift apart.
    assert scoped_pin_store_path(trw_dir) == trw_dir / "runtime" / "pins.json"
    assert scoped_pin_store_path(pin_store_path().parent.parent) == pin_store_path()

    # Where the OS cannot expose a birth time the pid stays counted and the
    # census reports identity as unverified rather than implying it was checked.
    (writers / "noepoch.lock").write_text("1\n", encoding="utf-8")
    unverified = take_writer_census(trw_dir, threshold=8)
    assert 1 in unverified.writer_pids
    assert unverified.identity_state == "unverified"


def test_perform_session_recalls_compacts_response_under_writer_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    # The threshold is pinned explicitly (it is no longer 2, and a fixture that
    # relies on the default would silently stop exercising pressure when the
    # documented starting point is retuned from the FR07 census log).
    _fake_peer_registry(trw_dir, monkeypatch, peers=3)
    config = TRWConfig.model_validate(
        {
            "recall_max_results": 25,
            "session_start_defer_under_writer_pressure": True,
            "session_start_writer_pressure_threshold": 3,
        }
    )
    entries = [
        {
            "id": f"L-{idx}",
            "summary": f"Learning {idx}",
            "impact": 0.9,
            "status": "active",
            "tags": ["tag", "mcp", "timeout"],
            "detail": "verbose detail that should not be returned under writer pressure",
        }
        for idx in range(20)
    ]

    def _recall(*args: object, max_results: int | None = None, **kwargs: object) -> list[dict[str, object]]:
        return entries[: max_results or len(entries)]

    with (
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_recall) as recall,
        patch("trw_mcp.models.config.get_config", return_value=config),
        patch("trw_mcp.tools._session_recall_helpers.log_ranked_selections"),
        patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
    ):
        learnings, _auto, extra = perform_session_recalls(trw_dir, "mcp timeout", config, MagicMock())

    assert recall.call_count == 2
    assert {call.kwargs["max_results"] for call in recall.call_args_list} == {8}
    assert len(learnings) == 8
    assert extra["response_compacted"] is True
    assert all(set(entry) <= {"id", "summary", "impact", "status"} for entry in learnings)


def test_perform_session_recalls_compacts_when_the_census_reports_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One census decides both compaction and the side-effect deferral.

    Replaces ``..._compacts_if_pressure_appears_after_recall``, which patched
    the deleted ``should_defer_memory_side_effects`` with a two-element
    ``side_effect`` list precisely because the old code took two differently
    calibrated censuses per recall.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    _fake_peer_registry(trw_dir, monkeypatch, peers=3)
    config = TRWConfig.model_validate(
        {
            "recall_max_results": 25,
            "session_start_defer_under_writer_pressure": True,
            "session_start_writer_pressure_threshold": 3,
        }
    )
    entries = [
        {
            "id": f"L-{idx}",
            "summary": f"Learning {idx}",
            "impact": 0.9,
            "status": "active",
            "tags": ["tag"],
        }
        for idx in range(20)
    ]

    def _recall(*args: object, max_results: int | None = None, **kwargs: object) -> list[dict[str, object]]:
        return entries[: max_results or len(entries)]

    with (
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_recall),
        patch("trw_mcp.models.config.get_config", return_value=config),
        patch(
            "trw_mcp.tools._session_recall_helpers.record_session_start_surfaces",
            return_value=SurfaceTrackingResult(ids=[], recorded=True, deferred_effects=()),
        ),
        patch("trw_mcp.tools._session_recall_helpers.log_ranked_selections"),
        patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt"),
    ):
        learnings, _auto, extra = perform_session_recalls(trw_dir, "mcp timeout", config, MagicMock())

    assert len(learnings) == 8
    assert extra["response_compacted"] is True
    assert all("tags" not in entry for entry in learnings)


def test_run_auto_maintenance_defers_backfill_but_still_checkpoints_under_writer_pressure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD-CORE-248 FR04: writer pressure defers the backfill and STILL checkpoints.

    This test previously asserted ``wal.assert_not_called()`` and a
    ``wal_checkpoint_deferred`` advisory. That encoded the defect: two live
    writers is the ordinary steady state, so the checkpoint was cancelled
    permanently and the WAL grew to 25.0 MiB against a 10 MB threshold. Pressure
    now picks the checkpoint MODE (PASSIVE, which never resets the WAL) inside
    ``maybe_checkpoint_wal`` instead of cancelling the work.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    _fake_peer_registry(trw_dir, monkeypatch, peers=3)
    config = TRWConfig(session_start_defer_under_writer_pressure=True, session_start_writer_pressure_threshold=3)

    backfill = MagicMock(return_value={"embedded": 1, "skipped": 0, "failed": 0})
    wal = MagicMock(return_value={"checkpointed": True})

    stale_close = MagicMock(return_value={"count": 1, "runs_closed": ["stale-run"], "errors": []})

    with (
        patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
        patch("trw_mcp.state.analytics._stale_runs.auto_close_stale_runs", stale_close),
        patch(
            "trw_mcp.state.memory_adapter.check_embeddings_status",
            return_value={"enabled": True, "available": True, "advisory": ""},
        ),
        patch("trw_mcp.state.memory_adapter.backfill_embeddings", backfill),
        patch("trw_mcp.state.memory_adapter.maybe_checkpoint_wal", wal),
    ):
        result = run_auto_maintenance(trw_dir, config)

    backfill.assert_not_called()
    wal.assert_called_once_with(trw_dir)
    stale_close.assert_not_called()
    assert result["stale_runs_deferred"]["reason"] == "writer_pressure"
    # PRD-CORE-257-FR08: with coverage healthy there is no post-recovery backfill
    # to skip, so the only block present is the unrelated hot-path one. Pressure
    # no longer suppresses the whole embeddings step.
    # PRD-CORE-263 DEF-11: "not performed", not "deferred" — nothing schedules
    # a later bulk backfill for a healthy corpus.
    assert result["embeddings_backfill_not_performed"]["reason"] == "session_start_hot_path"
    assert result["step_outcomes"]["stale_runs"] == "deferred"
    assert result["step_outcomes"]["auto_upgrade_check"] == "deferred"
    assert "wal_checkpoint_deferred" not in result


def test_candidate_run_hints_list_live_pinned_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state import _pin_store as pin_store_mod
    from trw_mcp.state._pin_store import upsert_pin_entry
    from trw_mcp.tools.ceremony import _candidate_run_hints

    _reset_config()
    pin_store_mod.invalidate_pin_store_cache()
    config = get_config()
    trw_dir = tmp_path / config.trw_dir
    trw_dir.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / config.runs_root / "task" / "20260502T000000Z-runtime-hardening"
    (run_dir / "meta").mkdir(parents=True)
    (run_dir / "meta" / "run.yaml").write_text("status: active\nphase: implement\n", encoding="utf-8")

    upsert_pin_entry("other-session", run_dir)

    candidates = _candidate_run_hints(limit=1)

    assert candidates
    assert candidates[0]["run_path"] == str(run_dir)
    # adopt_command was dropped from candidate entries (2026-07-12): it only
    # repeated run_path; the hint text states the trw_adopt_run convention.
    assert "adopt_command" not in candidates[0]


def test_append_ceremony_status_defers_nudges_under_writer_pressure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trw_dir = _minimal_trw_dir(tmp_path)
    _fake_peer_registry(trw_dir, monkeypatch, peers=3)
    (trw_dir / "config.yaml").write_text(
        "session_start_defer_under_writer_pressure: true\n"
        "session_start_writer_pressure_threshold: 3\n"
        "nudge_enabled: true\n",
        encoding="utf-8",
    )

    from trw_mcp.tools._ceremony_status import append_ceremony_status

    with (
        patch(
            "trw_mcp.state._ceremony_progress_state.increment_tool_call_counter",
            side_effect=AssertionError("counter write must be deferred under writer pressure"),
        ),
        patch(
            "trw_mcp.state._paths.resolve_run_path",
            side_effect=AssertionError("active run resolution must be deferred under writer pressure"),
        ),
    ):
        response = append_ceremony_status({}, trw_dir=trw_dir)

    assert "ceremony_status" in response
    nudge_deferred = response["nudge_deferred"]
    assert isinstance(nudge_deferred, dict)
    assert nudge_deferred["reason"] == "writer_pressure"
    assert "nudge_content" not in response


def test_build_check_always_defers_q_learning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PRD-FIX-088 FR01: Q-learning is ALWAYS deferred (not only under writer pressure).

    Pre-FIX-088 this test asserted ``reason == 'writer_present'``; that
    deferral path was conditional on detected peer writers and inline
    otherwise. The 91-second hang on 2026-05-04 (call dc084e2b)
    proved the inline path was unsafe at any corpus size, so FIX-088
    made deferral unconditional. The reason now is the literal
    ``"deferred_always"``.
    """
    from fastmcp import FastMCP

    import trw_mcp.tools.build as build_mod
    import trw_mcp.tools.build._registration as reg_mod

    trw_dir = _minimal_trw_dir(tmp_path)
    _write_lock(trw_dir, "peer.lock", os.getppid())
    config = TRWConfig.model_validate(
        {
            "trw_dir": str(trw_dir),
        }
    )

    monkeypatch.setattr(reg_mod, "get_config", lambda: config)
    monkeypatch.setattr(reg_mod, "resolve_trw_dir", lambda: trw_dir)
    monkeypatch.setattr(reg_mod, "find_active_run", lambda **kwargs: None)
    # The bg worker catches exceptions and logs; we don't need to
    # inject one to prove inline-Q-learning would have failed. We just
    # stub the work so the worker exits cleanly.
    monkeypatch.setattr(
        "trw_mcp.scoring.process_outcome_for_event",
        lambda event_type, event_data=None: [],
    )

    server = FastMCP("test")
    build_mod.register_build_tools(server)
    tool_fn = _get_tools_sync(server)["trw_build_check"].fn

    result = tool_fn(tests_passed=True, test_count=1, static_checks_clean=True, scope="focused")

    assert result["tests_passed"] is True
    q_learning_deferred = result["q_learning_deferred"]
    assert isinstance(q_learning_deferred, dict)
    assert q_learning_deferred["reason"] == "deferred_always"
    assert q_learning_deferred["thread_state"] in {"launched", "queued"}


# ---------------------------------------------------------------------------
# PRD-CORE-257-FR03 / NFR02-NFR04: the bounded per-step deferral ledger.
# ---------------------------------------------------------------------------


def _ledger_file(trw_dir: Path) -> Path:
    return trw_dir / "runtime" / "deferral_ledger.json"


def _write_ledger_entry(trw_dir: Path, step: str, **fields: object) -> None:
    import json

    path = _ledger_file(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    entry = {
        "last_completed_ts": None,
        "deferred_since_ts": None,
        "running_since_ts": None,
        "deferred_count": 0,
    }
    entry.update(fields)
    existing[step] = entry
    path.write_text(json.dumps(existing), encoding="utf-8")


def test_deferral_ledger_expiry_runs_step_anyway(tmp_path: Path) -> None:
    """FR03: a streak at or past the bound forces the step to run despite pressure."""
    from trw_mcp.state.deferral_ledger import (
        COVERED_STEPS,
        read_ledger,
        record_completion,
        step_deferral_decision,
    )

    trw_dir = _minimal_trw_dir(tmp_path)
    assert "stale_runs" in COVERED_STEPS

    # Cold start: no file at all is NOT "infinitely old" — it opens a streak.
    first = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert first.defer is True
    assert first.expired is False
    assert first.age_hours == 0.0
    assert first.ledger_state == "ok"

    # A clean file with no entry for THIS step is also a cold start.
    other = step_deferral_decision(trw_dir, "nudges", under_pressure=True, max_deferral_hours=6)
    assert (other.defer, other.expired, other.ledger_state) == (True, False, "ok")

    # 5 hours of streak against a 6-hour bound still defers, and states its age.
    _write_ledger_entry(trw_dir, "stale_runs", deferred_since_ts=_iso_ago(5.0), deferred_count=4)
    held = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert held.defer is True
    assert held.expired is False
    assert 4.9 < held.age_hours < 5.1
    assert held.deferred_count == 5

    # Expiry is INCLUSIVE at the bound.
    _write_ledger_entry(trw_dir, "stale_runs", deferred_since_ts=_iso_ago(6.0), deferred_count=9)
    at_bound = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert at_bound.defer is False
    assert at_bound.expired is True

    # A win holds its single-winner claim until completion (or staleness+death
    # recovery, tested separately) — the process that won at_bound completes
    # the step in production, which is what releases it here too.
    record_completion(trw_dir, "stale_runs")

    # ...and 7 hours certainly expires; completion resets the whole streak.
    _write_ledger_entry(trw_dir, "stale_runs", deferred_since_ts=_iso_ago(7.0), deferred_count=11)
    expired = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert expired.expired is True
    record_completion(trw_dir, "stale_runs")
    entries, state = read_ledger(trw_dir)
    assert state == "ok"
    assert entries["stale_runs"].last_completed_ts is not None
    assert entries["stale_runs"].deferred_since_ts is None
    assert entries["stale_runs"].running_since_ts is None
    assert entries["stale_runs"].deferred_count == 0


def test_expired_step_has_a_single_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR03/RISK-002: 6-8 servers reading one expired entry must not all run it.

    PRD-CORE-257 audit row 1: arbitration is a per-step ``O_CREAT|O_EXCL``
    lease file (``trw_mcp.state._deferral_claims``), not a read-modify-write
    of the ledger's ``running_since_ts`` — so a "dead winner" scenario here
    manufactures a stale CLAIM FILE, not a stale ledger field, and stale/dead
    liveness is proven by monkeypatching the owner-alive probe rather than by
    presuming a specific pid is dead (matching this suite's existing
    ``_pid_is_alive`` monkeypatch convention). A real multi-process race and a
    real dead-pid reclaim are covered end-to-end in
    ``test_core_257_audit_fixes.py``.
    """
    import trw_mcp.state._deferral_claims as dc
    from trw_mcp.state.deferral_ledger import claim_forced_run, step_deferral_decision

    trw_dir = _minimal_trw_dir(tmp_path)
    _write_ledger_entry(trw_dir, "embeddings_backfill", deferred_since_ts=_iso_ago(9.0), deferred_count=20)

    winner = step_deferral_decision(trw_dir, "embeddings_backfill", under_pressure=True, max_deferral_hours=6)
    assert winner.expired is True

    # Every other process reading the same expired entry defers instead: the
    # winner's claim file is held (fresh, owner alive) and cannot be won again.
    for _ in range(5):
        loser = step_deferral_decision(trw_dir, "embeddings_backfill", under_pressure=True, max_deferral_hours=6)
        assert loser.expired is False
        assert loser.defer is True

    # A claim whose owner is BOTH past the bound AND dead is reclaimed: a dead
    # winner cannot wedge the step forever.
    claim_path = trw_dir / "runtime" / "deferral-claims" / "embeddings_backfill.lock"
    claim_path.write_text(f"999999\n{_iso_ago(9.0)}\n", encoding="utf-8")
    monkeypatch.setattr(dc, "_claim_owner_alive", lambda _pid: False)
    assert claim_forced_run(trw_dir, "embeddings_backfill", max_deferral_hours=6) is True

    # A stale claim is NOT reclaimed by age alone: liveness must also fail.
    from trw_mcp.state.deferral_ledger import record_completion

    record_completion(trw_dir, "embeddings_backfill")
    monkeypatch.setattr(dc, "_claim_owner_alive", lambda _pid: True)
    claim_path.write_text(f"999999\n{_iso_ago(9.0)}\n", encoding="utf-8")
    assert claim_forced_run(trw_dir, "embeddings_backfill", max_deferral_hours=6) is False, (
        "a stale claim whose owner is still ALIVE must not be reclaimed"
    )


def test_ledger_degraded_runs_steps_and_is_visible(tmp_path: Path) -> None:
    """NFR02: a lost ledger runs every covered step; it never restarts every bound.

    Each winning decision is followed by ``record_completion`` — the same
    thing a real caller does via ``_record_step_outcome`` for an
    ``expired_ran`` outcome — which releases that step's single-winner claim
    (PRD-CORE-257 audit row 1) so the NEXT corrupt-content iteration measures
    a fresh contest rather than a claim still held from the last one.
    """
    from trw_mcp.state.deferral_ledger import COVERED_STEPS, read_ledger, record_completion, step_deferral_decision

    trw_dir = _minimal_trw_dir(tmp_path)
    path = _ledger_file(trw_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    for corrupt in ('{"stale_runs": {"deferred_since_ts"', "not json at all", '["a", "b"]', '{"stale_runs": 7}'):
        path.write_text(corrupt, encoding="utf-8")
        _entries, state = read_ledger(trw_dir)
        assert state == "degraded", corrupt
        for step in sorted(COVERED_STEPS):
            decision = step_deferral_decision(trw_dir, step, under_pressure=True, max_deferral_hours=6)
            assert decision.defer is False, (corrupt, step)
            assert decision.expired is True
            assert decision.ledger_state == "degraded"
            record_completion(trw_dir, step)
        # ``record_completion`` writes a valid ledger; degrade it again for
        # the next corrupt-content variant under test.
        path.write_text(corrupt, encoding="utf-8")

    # Persistently degraded across a span longer than the bound: still runs,
    # on every session start, not just the first (the claim must not wedge a
    # LIVE process out of its own step across repeated evaluations).
    path.write_text("torn", encoding="utf-8")
    for _ in range(4):
        decision = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=1)
        assert decision.defer is False
        assert decision.ledger_state == "degraded"
        record_completion(trw_dir, "stale_runs")


def test_ledger_fails_open_on_corrupt_and_unwritable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR02: no ledger read or write raises; a write failure never blocks the step."""
    import trw_mcp.state.deferral_ledger as dl

    trw_dir = _minimal_trw_dir(tmp_path)

    def _unwritable(*_args: object, **_kwargs: object) -> bool:
        raise OSError("read-only runtime directory")

    monkeypatch.setattr(dl, "_write_ledger", _unwritable)
    decision = dl.step_deferral_decision(trw_dir, "side_effects", under_pressure=True, max_deferral_hours=6)
    assert decision.ledger_state == "degraded"
    assert dl.record_completion(trw_dir, "side_effects") == "degraded"


def test_ledger_contains_no_pids_or_user_content(tmp_path: Path) -> None:
    """NFR03: the file carries only step names, counts and UTC timestamps."""
    import json

    from trw_mcp.state.deferral_ledger import COVERED_STEPS, record_completion, step_deferral_decision

    trw_dir = _minimal_trw_dir(tmp_path)
    for step in sorted(COVERED_STEPS):
        step_deferral_decision(trw_dir, step, under_pressure=True, max_deferral_hours=6)
    record_completion(trw_dir, "nudges")

    raw = _ledger_file(trw_dir).read_text(encoding="utf-8")
    assert str(os.getpid()) not in raw
    assert "L-" not in raw
    parsed = json.loads(raw)
    assert set(parsed) <= COVERED_STEPS
    for entry in parsed.values():
        assert set(entry) == {
            "last_completed_ts",
            "deferred_since_ts",
            "running_since_ts",
            "deferred_count",
        }
        assert isinstance(entry["deferred_count"], int)
        assert entry["deferred_count"] >= 0
        for key in ("last_completed_ts", "deferred_since_ts", "running_since_ts"):
            value = entry[key]
            assert value is None or (value.endswith("+00:00") and "T" in value)

    # An out-of-range bound is rejected by Pydantic, not by the ledger.
    with pytest.raises(ValidationError):
        TRWConfig(session_start_max_deferral_hours=0)
    with pytest.raises(ValidationError):
        TRWConfig(session_start_max_deferral_hours=169)


def test_ledger_is_idempotent_and_concurrent_safe(tmp_path: Path) -> None:
    """NFR04: repeated evaluation keeps one entry per step; writers never tear it."""
    import json
    import threading

    from trw_mcp.state.deferral_ledger import COVERED_STEPS, read_ledger, step_deferral_decision

    trw_dir = _minimal_trw_dir(tmp_path)
    for _ in range(2):
        for step in sorted(COVERED_STEPS):
            step_deferral_decision(trw_dir, step, under_pressure=True, max_deferral_hours=6)

    entries, state = read_ledger(trw_dir)
    assert state == "ok"
    assert set(entries) == COVERED_STEPS

    def _hammer() -> None:
        for _ in range(15):
            step_deferral_decision(trw_dir, "pending_learns", under_pressure=True, max_deferral_hours=6)

    threads = [threading.Thread(target=_hammer) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    parsed = json.loads(_ledger_file(trw_dir).read_text(encoding="utf-8"))
    assert set(parsed) == COVERED_STEPS
    assert read_ledger(trw_dir)[1] == "ok"


def test_per_step_outcome_vocabulary(tmp_path: Path) -> None:
    """FR12: every recorded outcome comes from the closed four-value vocabulary."""
    from trw_mcp.state.deferral_ledger import STEP_OUTCOMES, step_deferral_decision, step_outcome

    trw_dir = _minimal_trw_dir(tmp_path)
    assert STEP_OUTCOMES == ("executed", "deferred", "expired_ran", "failed")

    ran = step_deferral_decision(trw_dir, "stale_runs", under_pressure=False, max_deferral_hours=6)
    assert step_outcome(ran) == "executed"
    assert step_outcome(ran, failed=True) == "failed"

    deferred = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert step_outcome(deferred) == "deferred"

    _write_ledger_entry(trw_dir, "stale_runs", deferred_since_ts=_iso_ago(8.0), deferred_count=3)
    expired = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert step_outcome(expired) == "expired_ran"
    assert step_outcome(expired) in STEP_OUTCOMES


def test_deferral_advisory_states_age_and_still_folds(tmp_path: Path) -> None:
    """FR04/FR11: one builder, every block states its streak age, the fold survives."""
    from trw_mcp.state.deferral_ledger import step_deferral_decision
    from trw_mcp.state.memory_pressure import writer_pressure_details
    from trw_mcp.tools._session_start_trim import _DEFERRED_SHAPE_KEYS, trim_session_start_payload

    trw_dir = _minimal_trw_dir(tmp_path)
    census = WriterCensus(
        writer_pids=(1, 2, 3, 4),
        writer_count=4,
        peer_writer_count=3,
        threshold=3,
        under_pressure=True,
        census_state="measured",
        identity_state="verified",
        heartbeat_state="measured",
    )

    first = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    block = writer_pressure_details(census, first)
    assert block["reason"] == "writer_pressure"
    assert block["deferral_age_hours"] == 0.0
    assert block["deferred_count"] == 1
    assert block["peer_writer_count"] == 3
    assert block["census_state"] == "measured"
    assert block["ledger_state"] == "ok"
    assert "defer_reason" not in block

    _write_ledger_entry(trw_dir, "side_effects", deferred_since_ts=_iso_ago(5.0), deferred_count=4)
    aged = step_deferral_decision(trw_dir, "side_effects", under_pressure=True, max_deferral_hours=6)
    aged_block = writer_pressure_details(census, aged)
    assert 4.9 <= float(str(aged_block["deferral_age_hours"])) <= 5.1
    assert aged_block["deferred_count"] == 5

    # Contract: the compact fold's admission set must be a SUPERSET of every key
    # the one builder emits, or the block silently stops folding (RISK-004).
    assert set(block) <= _DEFERRED_SHAPE_KEYS

    payload = {
        "learnings": [],
        "run": {"active_run": None},
        "errors": [],
        "success": True,
        "stale_runs_deferred": dict(block),
        "side_effects_deferred": dict(aged_block),
    }
    folded = trim_session_start_payload(payload, verbose=False)
    assert "stale_runs_deferred" not in folded
    assert "side_effects_deferred" not in folded
    assert folded["deferred"]["writer_pressure"] == ["side_effects", "stale_runs"]
    assert folded["deferred_threshold"] == 3
    assert 4.9 <= float(str(folded["deferred_max_age_hours"])) <= 5.1
    assert folded["deferred_census_state"] == "measured"
    assert folded["deferred_ledger_state"] == "ok"


def test_pressure_skips_only_nudge_emission(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR06: pressure suppresses nudge TEXT, not the ceremony machinery.

    Replaces ``test_append_ceremony_status_defers_nudges_under_writer_pressure``,
    which asserted the counter was NOT called. The deferral branch used to
    ``return response`` before ``increment_tool_call_counter`` and
    ``attach_reversion_prompt``, so under the steady-state pressure measured on
    both reporting platforms the nudge cooldown counter never advanced and the
    phase-reversion prompt never reached a response — two ceremony mechanisms
    disabled by a check meant only to suppress nudge text. Reverting the
    restructure turns the counter and reversion assertions red.
    """
    from trw_mcp.tools._ceremony_status import append_ceremony_status

    trw_dir = _minimal_trw_dir(tmp_path)
    _fake_peer_registry(trw_dir, monkeypatch, peers=3)
    (trw_dir / "config.yaml").write_text(
        "session_start_defer_under_writer_pressure: true\n"
        "session_start_writer_pressure_threshold: 3\n"
        "nudge_enabled: true\n",
        encoding="utf-8",
    )

    counter_calls: list[Path] = []
    reversion_calls: list[object] = []
    monkeypatch.setattr(
        "trw_mcp.state._ceremony_progress_state.increment_tool_call_counter",
        lambda d: counter_calls.append(d),
    )
    monkeypatch.setattr(
        "trw_mcp.tools._ceremony_nudge_emission.attach_reversion_prompt",
        lambda response, **kwargs: reversion_calls.append(response),
    )

    response = append_ceremony_status({}, trw_dir=trw_dir)

    assert "ceremony_status" in response
    assert len(counter_calls) == 1, "the nudge cooldown counter must advance under pressure"
    assert len(reversion_calls) == 1, "the phase-reversion prompt must still reach the response"
    assert "nudge_content" not in response
    nudge_deferred = response["nudge_deferred"]
    assert isinstance(nudge_deferred, dict)
    assert nudge_deferred["reason"] == "writer_pressure"
    assert nudge_deferred["deferral_age_hours"] == 0.0
    assert nudge_deferred["deferred_count"] == 1

    # ...and once the bound has passed, the nudge is emitted despite pressure.
    _write_ledger_entry(trw_dir, "nudges", deferred_since_ts=_iso_ago(9.0), deferred_count=40)
    forced = append_ceremony_status({}, trw_dir=trw_dir)
    assert "nudge_deferred" not in forced
    assert len(counter_calls) == 2


def test_dedup_never_deferred_and_tracking_return_is_honest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR09: the cheap dedup write always runs; the tracking return stops lying.

    Two confirmed defects rode in one ``side_effects_deferred`` block. (a) The
    injected-ids dedup write — a bounded text file under the context directory
    that opens no SQLite connection — was skipped whenever that block was
    present, so learnings this session already surfaced became eligible for
    re-injection by the hook next time: correctness paid to save nothing.
    (b) ``record_session_start_surfaces`` returned the same ids whether it wrote
    or skipped, and the caller then wrote a recall receipt for ids that were
    never recorded.
    """
    from trw_mcp.tools._ceremony_session_start_steps import _write_session_start_ids
    from trw_mcp.tools._session_recall_pressure import record_session_start_surfaces

    trw_dir = _minimal_trw_dir(tmp_path)

    # (a) The dedup write is not gated on the deferral advisory at all.
    _write_session_start_ids(trw_dir, [{"id": "L-aaa"}, {"id": "L-bbb"}])
    injected = trw_dir / "context" / "injected_learning_ids.txt"
    assert injected.read_text(encoding="utf-8").split() == ["L-aaa", "L-bbb"]
    source = (
        Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools" / "_ceremony_session_start_steps.py"
    ).read_text(encoding="utf-8")
    assert 'side_effects_deferred" not in extra' not in source

    # (b) A deferral returns recorded=False and names each lost effect.
    with (
        patch("trw_mcp.state.memory_adapter.increment_session_counts") as counts,
        patch("trw_mcp.state.memory_adapter.update_access_tracking") as access,
        patch("trw_mcp.tools._session_recall_pressure._log_session_start_surfaces") as surfaces,
    ):
        deferred = record_session_start_surfaces(trw_dir, ["L-one", "L-one", "L-two"], defer=True)

    assert deferred.ids == ["L-one", "L-two"]
    assert deferred.recorded is False
    assert set(deferred.deferred_effects) == {
        "propensity_log",
        "session_counts",
        "access_tracking",
        "surface_events",
        "recall_receipt",
    }
    counts.assert_not_called()
    access.assert_not_called()
    surfaces.assert_not_called()

    with (
        patch("trw_mcp.state.memory_adapter.increment_session_counts") as counts,
        patch("trw_mcp.state.memory_adapter.update_access_tracking") as access,
        patch("trw_mcp.tools._session_recall_pressure._log_session_start_surfaces") as surfaces,
    ):
        recorded = record_session_start_surfaces(trw_dir, ["L-one"], defer=False)

    assert recorded.recorded is True
    assert recorded.deferred_effects == ()
    counts.assert_called_once()

    # A pressured session_start still writes the dedup file, still enumerates the
    # deferred effects by name, and writes NO recall receipt.
    _fake_peer_registry(trw_dir, monkeypatch, peers=3)
    config = TRWConfig.model_validate(
        {
            "recall_max_results": 25,
            "session_start_defer_under_writer_pressure": True,
            "session_start_writer_pressure_threshold": 3,
        }
    )
    entries = [{"id": f"L-{i}", "summary": f"s{i}", "impact": 0.9, "status": "active"} for i in range(4)]

    def _recall(*args: object, max_results: int | None = None, **kwargs: object) -> list[dict[str, object]]:
        return entries[: max_results or len(entries)]

    with (
        patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_recall),
        patch("trw_mcp.models.config.get_config", return_value=config),
        patch(
            "trw_mcp.tools._session_recall_helpers.log_recall_receipt",
            side_effect=AssertionError("no receipt may be written for ids that were not recorded"),
        ),
    ):
        _learnings, _auto, extra = perform_session_recalls(trw_dir, "mcp timeout", config, MagicMock())

    advisory = extra["side_effects_deferred"]
    assert isinstance(advisory, dict)
    assert set(str(advisory["detail"]).split(", ")) >= {"session_counts", "recall_receipt"}
    assert advisory["reason"] == "writer_pressure"
    # Review P1 (2026-09-05): the REAL call-site block must fold in compact mode.
    # An extra key on this dict silently kept it out of the fold and shipped it
    # verbatim on every pressured session start while the synthetic fold test
    # stayed green.
    from typing import cast

    from trw_mcp.tools._session_start_trim import _DEFERRED_SHAPE_KEYS, trim_session_start_payload

    assert set(advisory) <= _DEFERRED_SHAPE_KEYS, sorted(set(advisory) - _DEFERRED_SHAPE_KEYS)
    folded = trim_session_start_payload(
        cast("Any", {"success": True, "side_effects_deferred": dict(advisory)}), verbose=False
    )
    assert "side_effects_deferred" not in folded
    assert "side_effects" in cast("dict[str, list[str]]", folded["deferred"])["writer_pressure"]


def test_session_start_logs_writer_census_once_at_info(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR07: exactly ONE INFO census event per session_start, healthy or not.

    The census used to reach the log only as a per-site WARNING on the deferral
    branch, so a healthy-but-loaded machine logged nothing at all and a pressured
    one logged the same numbers up to six times — neither is a series an operator
    can retune a threshold from.
    """
    import structlog

    trw_dir = _minimal_trw_dir(tmp_path)
    config = TRWConfig(session_start_defer_under_writer_pressure=True, session_start_writer_pressure_threshold=3)

    def _census_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [e for e in events if e["event"] == "writer_census"]

    with (
        patch("trw_mcp.state.auto_upgrade.check_for_update", return_value={"available": False}),
        patch("trw_mcp.state.analytics._stale_runs.auto_close_stale_runs", return_value={"count": 0}),
        patch(
            "trw_mcp.state.memory_adapter.check_embeddings_status",
            return_value={"enabled": True, "available": True, "advisory": ""},
        ),
        patch("trw_mcp.state.memory_adapter.maybe_checkpoint_wal", return_value={"checkpointed": False}),
    ):
        # Healthy machine: still exactly one line, so the retune data exists.
        _write_lock(trw_dir, "self.lock", os.getpid())
        with structlog.testing.capture_logs() as healthy_logs:
            run_auto_maintenance(trw_dir, config)
        healthy = _census_events(healthy_logs)
        assert len(healthy) == 1
        assert healthy[0]["log_level"] == "info"
        assert healthy[0]["under_pressure"] is False
        assert healthy[0]["census_state"] == "measured"
        assert set(healthy[0]) >= {
            "writer_count",
            "peer_writer_count",
            "threshold",
            "under_pressure",
            "census_state",
        }

        # Pressured machine: one line, not one per deferring step.
        _fake_peer_registry(trw_dir, monkeypatch, peers=3)
        with structlog.testing.capture_logs() as pressured_logs:
            result = run_auto_maintenance(trw_dir, config)
        pressured = _census_events(pressured_logs)
        assert len(pressured) == 1
        assert pressured[0]["under_pressure"] is True

        # FR12: the aggregate says the pass was EVALUATED, and the outcomes say
        # what actually happened. "complete" is never claimed for an all-deferred
        # pass, and every outcome is in the closed vocabulary.
        from trw_mcp.state.deferral_ledger import STEP_OUTCOMES

        assert not [e for e in pressured_logs if e["event"] == "auto_maintenance_complete"]
        aggregate = [e for e in pressured_logs if e["event"] == "auto_maintenance_evaluated"]
        assert len(aggregate) == 1
        outcomes = dict(result.get("step_outcomes", {}))
        assert outcomes
        assert set(outcomes.values()) <= set(STEP_OUTCOMES)
        assert set(outcomes.values()) == {"deferred"}


def test_census_and_ledger_overhead_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR01: census + ledger add <= 25 ms p95, and open no SQLite connection."""
    import sqlite3
    import statistics
    import time as _time

    from trw_mcp.state.deferral_ledger import step_deferral_decision

    trw_dir = _minimal_trw_dir(tmp_path)
    _fake_peer_registry(trw_dir, monkeypatch, peers=15)

    opened: list[str] = []
    real_connect = sqlite3.connect

    def _tracked(*args: Any, **kwargs: Any) -> Any:
        opened.append(str(args[0] if args else ""))
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _tracked)

    samples: list[float] = []
    for _ in range(20):
        start = _time.perf_counter()
        census = take_writer_census(trw_dir, threshold=8, pin_ttl_hours=24)
        step_deferral_decision(trw_dir, "stale_runs", under_pressure=census.under_pressure, max_deferral_hours=6)
        samples.append((_time.perf_counter() - start) * 1000.0)

    p95 = statistics.quantiles(samples, n=20)[-1]
    assert p95 <= 25.0, f"census+ledger p95 {p95:.2f} ms exceeds the 25 ms budget"
    assert opened == [], f"the deferral path must open no SQLite connection, saw {opened}"


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="process birth time is Linux-only")
def test_process_birth_epoch_is_the_task_start_not_the_procfs_inode(tmp_path: Path) -> None:
    """FR10 regression: a long-lived process must not read as a PID-reuse ghost.

    The first implementation used ``os.stat("/proc/<pid>").st_ctime``, which
    reads like a creation time and is not one — the procfs inode timestamp
    tracks when the entry was last instantiated. Measured on this box, a server
    started at epoch 1788557135 reported ``st_ctime`` 1788561547, 73 minutes
    late, and the census classified ALL FIVE live writers in the repo registry
    as ghosts: writer_count 5 -> 0, so pressure could never fire. This test
    reproduces the shape by touching the procfs inode before classifying.
    """
    from trw_mcp.state._writer_census_identity import LockRecord, lock_identity, process_birth_epoch

    pid = os.getppid()
    birth = process_birth_epoch(pid)
    assert birth is not None
    assert birth <= time.time(), "a process cannot have started in the future"

    # Touch the procfs inode, which is what moved st_ctime forward in the wild.
    os.stat(f"/proc/{pid}")

    # A lock registered one second AFTER the process started is a genuine
    # registration, not a ghost — whatever the inode timestamp now says.
    assert lock_identity(LockRecord(pid=pid, registered_epoch=birth + 1.0)) == "verified"
    # ...and a lock registered before that process existed still IS one.
    assert lock_identity(LockRecord(pid=pid, registered_epoch=birth - 3600.0)) == "ghost"


# ---------------------------------------------------------------------------
# PRD-CORE-263-FR05: a deferral names a consumer, or stops calling itself one.
# ---------------------------------------------------------------------------


def _pressured_recall_config(**overrides: object) -> TRWConfig:
    payload: dict[str, object] = {
        "recall_max_results": 25,
        "session_start_defer_under_writer_pressure": True,
        "session_start_writer_pressure_threshold": 3,
        "session_start_max_deferral_hours": 6,
    }
    payload.update(overrides)
    return TRWConfig.model_validate(payload)


def test_deferred_recall_bookkeeping_names_a_consumer_or_is_not_called_deferred(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-263-FR05 — the word "deferred" has to be true.

    Under writer pressure the recall step skips propensity logging, session
    counters, access tracking, surface logging and the recall receipt. Before
    this change it emitted one generic ``side_effects_deferred`` block, nothing
    durable recorded the skip, and a repository-wide search for a consumer of
    that state returned only the producer and the payload type. "Deferred" told
    a reader the work was pending when it was abandoned.

    Three things make it true now, and this test asserts each:

    1. **The unpressured path is unchanged** — all five effects still run, in
       the same order, so FR05 is a change to the pressured branch only.
    2. **A pressured deferral is itemised and journaled** — the advisory names
       each lost effect rather than hiding five losses behind one key, and the
       deferral ledger records the streak.
    3. **The named consumer resolves and runs** — the consumer is
       ``step_deferral_decision`` at the ``perform_session_recalls`` call site,
       and a streak past ``session_start_max_deferral_hours`` makes a LATER
       session start perform the bookkeeping despite continuing pressure, then
       clear the streak.

    Attribution: reverting FR05 removes the ledger record (limb 2), the
    per-effect enumeration (limb 2) and the forced later run (limb 3), and
    writes the recall receipt for ids that were never recorded (limb 1's
    inverse, asserted in the pressured branch).
    """
    from trw_mcp.state.deferral_ledger import COVERED_STEPS, read_ledger
    from trw_mcp.tools._session_recall_pressure import DEFERRABLE_SIDE_EFFECTS

    entries = [{"id": f"L-{i}", "summary": f"s{i}", "impact": 0.9, "status": "active"} for i in range(4)]

    def _recall(*args: object, max_results: int | None = None, **kwargs: object) -> list[dict[str, object]]:
        return entries[: max_results or len(entries)]

    def _run(trw_dir: Path, config: TRWConfig) -> tuple[dict[str, object], dict[str, MagicMock]]:
        calls = {
            "propensity_log": MagicMock(),
            "session_counts": MagicMock(),
            "access_tracking": MagicMock(),
            "surface_events": MagicMock(),
            "recall_receipt": MagicMock(),
        }
        with (
            patch("trw_mcp.state.memory_adapter.recall_learnings", side_effect=_recall),
            patch("trw_mcp.models.config.get_config", return_value=config),
            patch("trw_mcp.tools._session_recall_helpers.log_ranked_selections", calls["propensity_log"]),
            patch("trw_mcp.state.memory_adapter.increment_session_counts", calls["session_counts"]),
            patch("trw_mcp.state.memory_adapter.update_access_tracking", calls["access_tracking"]),
            patch("trw_mcp.tools._session_recall_pressure._log_session_start_surfaces", calls["surface_events"]),
            patch("trw_mcp.tools._session_recall_helpers.log_recall_receipt", calls["recall_receipt"]),
        ):
            _learnings, _auto, extra = perform_session_recalls(trw_dir, "mcp timeout", config, MagicMock())
        return dict(extra), calls

    # -- 1. No pressure: every one of the five effects runs, exactly as today. --
    quiet_dir = _minimal_trw_dir(tmp_path / "quiet")
    quiet_extra, quiet_calls = _run(quiet_dir, _pressured_recall_config())
    assert "side_effects_deferred" not in quiet_extra
    for name, mock in quiet_calls.items():
        assert mock.call_count == 1, f"{name} must still run when there is no pressure"

    # -- 2. Pressure: itemised, journaled, and no receipt for unrecorded ids. --
    loud_dir = _minimal_trw_dir(tmp_path / "loud")
    _fake_peer_registry(loud_dir, monkeypatch, peers=3)
    loud_extra, loud_calls = _run(loud_dir, _pressured_recall_config())

    advisory = loud_extra["side_effects_deferred"]
    assert isinstance(advisory, dict)
    # ``detail`` is the one fold-admitted free-text slot; it must name each
    # skipped operation, not just say "deferred", and may not promise a
    # resumption in words the ledger does not back.
    named = set(str(advisory["detail"]).split(", "))
    assert named == set(DEFERRABLE_SIDE_EFFECTS), "the advisory must name each skipped operation, not just 'deferred'"
    for name, mock in loud_calls.items():
        assert mock.call_count == 0, f"{name} must not run while deferred"

    assert "side_effects" in COVERED_STEPS
    journaled, ledger_state = read_ledger(loud_dir)
    assert ledger_state == "ok"
    assert journaled["side_effects"].deferred_since_ts is not None, "a reported deferral must leave a durable record"
    assert journaled["side_effects"].deferred_count == 1
    assert advisory["deferral_age_hours"] == 0.0
    assert advisory["ledger_state"] == "ok"

    # -- 3. The named consumer is a real call site, and it runs. --
    helpers_src = (
        Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools" / "_session_recall_helpers.py"
    ).read_text(encoding="utf-8")
    assert "step_deferral_decision(" in helpers_src
    assert "record_completion(" in helpers_src

    # A streak older than the configured bound: pressure is unchanged, and the
    # work happens anyway rather than being deferred a second time.
    _write_ledger_entry(loud_dir, "side_effects", deferred_since_ts=_iso_ago(7.0), deferred_count=9)
    resumed_extra, resumed_calls = _run(loud_dir, _pressured_recall_config())
    assert "side_effects_deferred" not in resumed_extra, "an expired streak must run the work, not report it deferred"
    for name, mock in resumed_calls.items():
        assert mock.call_count == 1, f"{name} must run once the deferral bound expires"

    drained, drained_state = read_ledger(loud_dir)
    assert drained_state == "ok"
    assert drained["side_effects"].deferred_since_ts is None, "a completed run must close the streak it resumed"
    assert drained["side_effects"].deferred_count == 0
    assert drained["side_effects"].last_completed_ts is not None
