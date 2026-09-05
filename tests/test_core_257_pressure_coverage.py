"""PRD-CORE-257 tester-owned gap coverage.

The implementer's own suite (``test_session_start_runtime_pressure.py`` +
``test_tools_orchestration_core.py`` + ``test_embedder_warmup.py``) already
exercises the FR01-FR12/NFR01-NFR05 acceptance criteria directly. This file
adds only the boundary and integration cases that survive a requirement-blind
reading of that coverage:

* a PID-reuse ghost is not just excluded from the census, it is logged at
  WARNING with the pid and epoch that proved the exclusion (FR10);
* the heartbeat filter reads the pin store SCOPED to the ``trw_dir`` it was
  given, proven by planting conflicting data at the path a global resolver
  would have used instead (FR10);
* a ledger that cannot be READ degrades ``trw_status``'s ``writer_pressure``
  block end-to-end, not just the ``read_ledger`` return value (FR03/FR05/NFR02);
* a ledger that cannot be WRITTEN (read succeeds, write fails) produces a
  ``defer=True`` decision whose ``ledger_state`` survives the compact-fold
  pipeline into the session-start ``deferred_ledger_state`` summary key
  (FR03/FR04/FR11/NFR02) — the corrupt-READ case can never reach the fold
  because a degraded read forces every step to execute (nothing to fold);
* every real emission of ``under_pressure`` is never separated from
  ``census_state`` in the same block, across both a healthy and an unreadable
  census (FR05's "an absence of measurement is not a measurement of absence").

All cases run the real production path: no mock of ``take_writer_census``,
``step_deferral_decision``, ``writer_pressure_details``,
``trim_session_start_payload``, or the ``trw_status``/``trw_init`` tool
functions. Only I/O boundaries (a monkeypatched ``_write_ledger`` return, a
fabricated lock/pin file) are stubbed.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
import structlog

from tests._tools_orchestration_support import orch_tools, set_project_root  # noqa: F401
from trw_mcp.state.deferral_ledger import step_deferral_decision
from trw_mcp.state.memory_pressure import WriterCensus, take_writer_census, writer_pressure_details
from trw_mcp.tools._session_start_trim import trim_session_start_payload

if TYPE_CHECKING:
    from trw_mcp.models.typed_dicts import SessionStartResultDict


def _minimal_trw_dir(base: Path) -> Path:
    trw_dir = base / ".trw"
    (trw_dir / "memory" / "memory.db.writers").mkdir(parents=True)
    (trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    return trw_dir


def _write_lock(trw_dir: Path, name: str, pid: int, *, epoch: float | None = None) -> None:
    registered = time.time() if epoch is None else epoch
    (trw_dir / "memory" / "memory.db.writers" / name).write_text(f"{pid}\n{registered:.6f}\n", encoding="utf-8")


def _write_pins(trw_dir: Path, entries: dict[str, dict[str, Any]]) -> None:
    runtime_dir = trw_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "pins.json").write_text(json.dumps(entries), encoding="utf-8")


def _iso_ago(hours: float) -> str:
    from datetime import datetime, timedelta, timezone

    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# ---------------------------------------------------------------------------
# FR10: PID-reuse ghost exclusion is logged at WARNING, not swallowed silently.
# ---------------------------------------------------------------------------


def test_pid_reuse_ghost_excluded_and_logged_at_warning(tmp_path: Path) -> None:
    """A ghost is excluded from the census AND the exclusion is observable.

    The PRD frames pruning as declined-by-design and dead locks as DEBUG-only,
    but a PID-reuse ghost is explicitly called out as "a correctness exclusion,
    not routine noise" and must log at WARNING with the pid and the
    registration epoch that proved the mismatch. A test that only checks the
    pid is missing from ``writer_pids`` cannot tell an operator apart from a
    silent registry-rot fix.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    self_pid = os.getpid()
    ghost_pid = os.getppid()

    _write_lock(trw_dir, "self.lock", self_pid)
    # An epoch far in the past cannot be this (still-alive) process's own
    # registration: whichever process holds ghost_pid today started long after
    # epoch 1000.0, so the lock is evidence of a killed-and-recycled writer.
    _write_lock(trw_dir, "ghost.lock", ghost_pid, epoch=1000.0)

    with structlog.testing.capture_logs() as logs:
        census = take_writer_census(trw_dir, threshold=8)

    assert ghost_pid not in census.writer_pids
    assert census.writer_pids == (self_pid,)

    ghost_events = [e for e in logs if e["event"] == "writer_registry_pid_reuse_ghost"]
    assert len(ghost_events) == 1, "the ghost exclusion must be logged exactly once"
    assert ghost_events[0]["log_level"] == "warning"
    assert ghost_events[0]["pid"] == ghost_pid
    assert ghost_events[0]["registered_epoch"] == pytest.approx(1000.0)

    # The lock file itself is left alone: this reader never prunes.
    assert (trw_dir / "memory" / "memory.db.writers" / "ghost.lock").exists()


def test_verified_lock_emits_no_ghost_warning(tmp_path: Path) -> None:
    """Negative case: a freshly-registered lock for a live pid logs nothing."""
    trw_dir = _minimal_trw_dir(tmp_path)
    self_pid = os.getpid()
    _write_lock(trw_dir, "self.lock", self_pid)

    with structlog.testing.capture_logs() as logs:
        census = take_writer_census(trw_dir, threshold=8)

    assert census.writer_pids == (self_pid,)
    assert not [e for e in logs if e["event"] == "writer_registry_pid_reuse_ghost"]


# ---------------------------------------------------------------------------
# FR10: the heartbeat filter is scoped to the trw_dir it is given, not the
# global resolver a differently-configured project would use.
# ---------------------------------------------------------------------------


def test_heartbeat_filter_reads_the_given_trw_dir_not_a_global_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR10 regression: two projects' pin stores must never cross-contaminate.

    Before FR10, ``_stale_heartbeat_pids`` took a ``trw_dir`` argument and then
    called the process-global ``load_pin_store()``, which resolves its own
    path — so under a non-default project it filtered against the WRONG
    project's pins. This plants a "global" pins.json (at the path
    ``resolve_trw_dir()`` currently resolves to) with data that would produce
    the OPPOSITE verdict from the explicit project's own pins.json, then
    proves the explicit project's own file — not the global one — decided the
    outcome, in both directions.
    """
    from trw_mcp.models.config import _reset_config, get_config
    from trw_mcp.state import _pin_store as pin_store_mod

    global_root = tmp_path / "global-project"
    global_root.mkdir()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(global_root))
    _reset_config()
    pin_store_mod.invalidate_pin_store_cache()
    global_trw_dir = global_root / str(get_config().trw_dir)
    global_trw_dir.mkdir(parents=True, exist_ok=True)

    peer_pid = os.getppid()

    # Direction 1: global pins say STALE (would exclude); explicit pins say
    # FRESH. If the filter reads the explicit store, the peer stays counted.
    explicit_a = _minimal_trw_dir(tmp_path / "project-a")
    _write_lock(explicit_a, "self.lock", os.getpid())
    _write_lock(explicit_a, "peer.lock", peer_pid)
    _write_pins(
        global_trw_dir,
        {"global-session": {"run_path": str(global_trw_dir), "pid": peer_pid, "last_heartbeat_ts": _iso_ago(9.0)}},
    )
    _write_pins(
        explicit_a,
        {"local-session": {"run_path": str(explicit_a), "pid": peer_pid, "last_heartbeat_ts": _iso_ago(0.1)}},
    )
    census_a = take_writer_census(explicit_a, threshold=8, pin_ttl_hours=1)
    assert peer_pid in census_a.writer_pids, "the explicit project's FRESH heartbeat must win, not the global STALE one"
    # "unavailable" would mean the store could not be read at all; self has no
    # pin entry in either fixture, so the honest state is "partial" (some
    # counted pids, here just the peer, had a usable heartbeat) rather than
    # the fully-covered "measured" — either proves the file was actually read.
    assert census_a.heartbeat_state in {"measured", "partial"}

    # Direction 2: global pins say FRESH (would keep); explicit pins say STALE.
    # If the filter reads the explicit store, the peer is dropped.
    explicit_b = _minimal_trw_dir(tmp_path / "project-b")
    _write_lock(explicit_b, "self.lock", os.getpid())
    _write_lock(explicit_b, "peer.lock", peer_pid)
    _write_pins(
        global_trw_dir,
        {"global-session": {"run_path": str(global_trw_dir), "pid": peer_pid, "last_heartbeat_ts": _iso_ago(0.1)}},
    )
    _write_pins(
        explicit_b,
        {"local-session": {"run_path": str(explicit_b), "pid": peer_pid, "last_heartbeat_ts": _iso_ago(9.0)}},
    )
    census_b = take_writer_census(explicit_b, threshold=8, pin_ttl_hours=1)
    assert peer_pid not in census_b.writer_pids, (
        "the explicit project's STALE heartbeat must win, not the global FRESH one"
    )
    assert census_b.heartbeat_state in {"measured", "partial"}


# ---------------------------------------------------------------------------
# FR03/FR05/NFR02: a ledger that cannot be READ degrades trw_status end-to-end.
# ---------------------------------------------------------------------------


def test_ledger_read_degraded_surfaces_on_trw_status_and_logs_warning(
    orch_tools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """NFR02/FR05: a torn ledger file is visible on ``trw_status``, not inferred.

    The existing suite proves ``read_ledger`` itself reports ``degraded`` for a
    torn file. This proves that state actually reaches the live
    ``trw_status`` tool's ``writer_pressure.ledger_state`` field — the surface
    an operator reads — and that the degradation is logged at WARNING rather
    than silently downgraded to a healthy-looking ``ok``.
    """
    from trw_mcp.state._paths import resolve_trw_dir

    trw_dir = resolve_trw_dir()
    (trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    (trw_dir / "runtime" / "deferral_ledger.json").write_text("{not valid json", encoding="utf-8")

    init_result = orch_tools["trw_init"].fn(task_name="ledger-degraded-status-task")

    with structlog.testing.capture_logs() as logs:
        status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

    pressure = status["writer_pressure"]
    assert pressure["ledger_state"] == "degraded"
    # The status block never conflates census health with ledger health.
    assert pressure["census_state"] == "measured"

    degraded_events = [e for e in logs if e["event"] == "deferral_ledger_degraded"]
    assert degraded_events, "a torn ledger must log deferral_ledger_degraded, not disappear"
    assert degraded_events[0]["log_level"] == "warning"


def test_ledger_missing_file_reports_ok_on_trw_status(orch_tools: dict[str, Any]) -> None:
    """Negative/boundary case: cold start (no ledger file yet) reports ok, not degraded."""
    init_result = orch_tools["trw_init"].fn(task_name="ledger-cold-start-status-task")
    status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

    pressure = status["writer_pressure"]
    assert pressure["ledger_state"] == "ok"
    assert pressure["deferred_steps"] == {}


# ---------------------------------------------------------------------------
# FR03/FR04/FR11/NFR02: a ledger that cannot be WRITTEN still defers honestly,
# and the degraded state survives the compact-fold pipeline that feeds the
# session-start "deferred" summary.
# ---------------------------------------------------------------------------


def test_ledger_write_failure_runs_instead_of_deferring_forever(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRD-CORE-257 audit row 2: a missing-but-unwritable ledger can never
    durably record ``deferred_since_ts``, so age_hours would stay 0.0 forever
    and the bound could never fire — the old contract ("a write failure never
    blocks the deferral already taken") is exactly that unbounded-deferral
    defect. The corrected contract: a write failure on a fresh streak makes
    the step RUN (through the same single-winner claim as ordinary expiry),
    with ``ledger_state`` reporting ``degraded`` so the caller can tell this
    apart from a genuine bounded expiry.
    """
    import trw_mcp.state.deferral_ledger as dl

    trw_dir = _minimal_trw_dir(tmp_path)
    monkeypatch.setattr(dl, "_write_ledger", lambda *_a, **_k: False)

    decision = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert decision.defer is False, "a write failure must not defer forever (audit row 2)"
    assert decision.expired is True
    assert decision.ledger_state == "degraded"


def test_ledger_write_failure_loser_still_folds_the_degraded_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write-failure RUN path above still arbitrates a single winner: a
    process that loses that claim (another process is already running the
    forced step) defers this round, and ITS ``ledger_state: "degraded"`` is
    what must survive the compact-fold pipeline into the session-start
    ``deferred_ledger_state`` summary key (FR04/FR11/NFR02) — the write-failure
    RUN case has no ``*_deferred`` block to fold (nothing was skipped).
    """
    import trw_mcp.state.deferral_ledger as dl

    trw_dir = _minimal_trw_dir(tmp_path)
    monkeypatch.setattr(dl, "_write_ledger", lambda *_a, **_k: False)
    monkeypatch.setattr(dl, "claim_forced_run", lambda *_a, **_k: False)

    decision = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert decision.defer is True
    assert decision.expired is False
    assert decision.ledger_state == "degraded"

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
    block = writer_pressure_details(census, decision)
    assert block["ledger_state"] == "degraded"

    payload: dict[str, object] = {
        "learnings": [],
        "run": {"active_run": None},
        "errors": [],
        "success": True,
        "stale_runs_deferred": dict(block),
    }
    folded = trim_session_start_payload(cast("SessionStartResultDict", payload), verbose=False)

    assert "stale_runs_deferred" not in folded, "the fold must consume the per-step block"
    assert folded["deferred"]["writer_pressure"] == ["stale_runs"]
    assert folded["deferred_ledger_state"] == "degraded", (
        "a ledger write failure must be visible on the compact session-start summary, "
        "not laundered into a healthy-looking fold"
    )


# ---------------------------------------------------------------------------
# FR05: "an absence of measurement is not a measurement of absence" — every
# real emission pairs under_pressure with census_state; never one without
# the other.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("peers", [0, 8])
def test_writer_pressure_block_never_separates_under_pressure_from_census_state(
    orch_tools: dict[str, Any], monkeypatch: pytest.MonkeyPatch, peers: int
) -> None:
    """A consumer reading ``under_pressure`` without ``census_state`` reads an
    unsafe default; this asserts the shape never lets that split happen on the
    live ``trw_status`` block, for both a healthy and an unreadable census."""
    from trw_mcp.state._paths import resolve_trw_dir

    trw_dir = resolve_trw_dir()
    (trw_dir / "memory" / "memory.db.writers").mkdir(parents=True, exist_ok=True)
    _write_lock(trw_dir, "self.lock", os.getpid())
    for index in range(peers):
        _write_lock(trw_dir, f"peer-{index}.lock", 900_000 + index)
    if peers:
        import trw_mcp.state.memory_pressure as mp

        monkeypatch.setattr(mp, "_pid_is_alive", lambda pid: True)

    init_result = orch_tools["trw_init"].fn(task_name=f"pressure-shape-{peers}")
    status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

    pressure = status["writer_pressure"]
    assert "under_pressure" in pressure
    assert "census_state" in pressure
    assert isinstance(pressure["under_pressure"], bool)
    assert pressure["census_state"] in {"measured", "unreadable"}


def test_writer_pressure_block_unreadable_registry_pairs_false_with_unreadable(
    orch_tools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unreadable case specifically: under_pressure is False, never inferred True."""
    import trw_mcp.state.memory_pressure as mp

    def _explode(_writers_dir: Path) -> list[Path]:
        raise OSError("registry unreadable")

    monkeypatch.setattr(mp, "_iter_lock_paths", _explode)

    init_result = orch_tools["trw_init"].fn(task_name="pressure-shape-unreadable")
    status = orch_tools["trw_status"].fn(run_path=init_result["run_path"])

    pressure = status["writer_pressure"]
    assert pressure["census_state"] == "unreadable"
    assert pressure["under_pressure"] is False
    assert pressure["writer_count"] == 0
    assert pressure["peer_writer_count"] == 0
