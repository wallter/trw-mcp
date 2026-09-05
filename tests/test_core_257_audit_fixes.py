"""PRD-CORE-257 external-audit fixes — attribution tests for the P1/P2 rows fixed here.

A codex cross-vendor audit
(``.trw/runs/release-train-2026-09-followup/20260904T214337Z-7578af1c/scratch/sub-1/audits/codex-core257-postimpl-findings.md``)
found 12 rows against the landed CORE-257 implementation, several backed by
execution probes. This file reproduces those probes against the FIXED code
and pins the corrected behavior red-first: reverting any one fix below turns
its assertion red.

Does not duplicate ``tests/test_core_257_pressure_coverage.py`` (a prior
tester-owned gap-coverage file for the same PRD) or
``tests/test_session_start_runtime_pressure.py`` (the PRD's own acceptance
suite, updated in place where the fixes here changed its encoded contract).

Rows covered here:

* Row 1 (P1): the single-winner claim is a REAL O_CREAT|O_EXCL lease file,
  proven with a real multi-process race and a real dead-process reclaim —
  not a monkeypatched liveness probe.
* Row 2 (P1): a real unwritable ledger directory runs instead of deferring
  forever; a real corrupt ledger still elects exactly one winner across real
  processes.
* Row 3 (P1): unparseable and implausibly-future ledger timestamps degrade
  the entry instead of reporting a healthy zero-age streak.
* Row 4 (P1): a real unreadable writer-lock directory (chmod, not a
  monkeypatched ``_iter_lock_paths``) reports ``census_state="unreadable"``;
  an individual unreadable lock degrades ``identity_state``.
* Row 5 (P1): ``trw_status["writer_pressure"]`` survives a real exception
  inside the block builder, and its four state fields are the real Literal
  vocabularies, not plain ``str``.
* Row 7 (P2): an expired ``nudges`` streak is not marked complete until
  ``nudge_content`` actually lands on the response.
* Peer-review finding folded into row 7/8's scope: a ``nudges`` streak opened
  under pressure closes once a later call finds pressure has cleared, even
  when that later call emits no nudge.
* Row 9 (P2): ``stale_runs`` never opens a ledger streak while
  ``run_auto_close_enabled`` is False.
"""

from __future__ import annotations

import concurrent.futures
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.geteuid() == 0 if hasattr(os, "geteuid") else False,
    reason="permission-based probes are meaningless when running as root",
)


def _minimal_trw_dir(base: Path) -> Path:
    trw_dir = base / ".trw"
    (trw_dir / "memory" / "memory.db.writers").mkdir(parents=True)
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    (trw_dir / "learnings" / "receipts").mkdir(parents=True)
    (trw_dir / "context").mkdir(parents=True)
    return trw_dir


def _iso_ago(hours: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(hours=hours)
    return ts.isoformat()


def _write_ledger_entry(trw_dir: Path, step: str, **fields: object) -> None:
    import json

    path = trw_dir / "runtime" / "deferral_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, object] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # justified: test helper, best-effort seed
            existing = {}
    entry: dict[str, object] = {
        "last_completed_ts": None,
        "deferred_since_ts": None,
        "running_since_ts": None,
        "deferred_count": 0,
    }
    entry.update(fields)
    existing[step] = entry
    path.write_text(json.dumps(existing), encoding="utf-8")


# ---------------------------------------------------------------------------
# Row 1: a REAL O_CREAT|O_EXCL claim file, proven across real processes.
# ---------------------------------------------------------------------------


def _claim_worker(args: tuple[str, str, int]) -> bool:
    trw_dir_str, step, max_deferral_hours = args
    from trw_mcp.state.deferral_ledger import claim_forced_run

    return claim_forced_run(Path(trw_dir_str), step, max_deferral_hours=max_deferral_hours)


def test_real_multiprocess_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    """8 REAL processes racing one expired step: exactly one must win.

    The audit's execution probe against the old ledger-map compare-and-swap
    returned ``{'A': True, 'B': True}`` for two concurrent claimants. This
    reproduces the race with real OS processes (not threads sharing one
    interpreter) against the fixed lease-file arbitration.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_ledger_entry(trw_dir, "embeddings_backfill", deferred_since_ts=_iso_ago(9.0), deferred_count=5)

    with concurrent.futures.ProcessPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_claim_worker, [(str(trw_dir), "embeddings_backfill", 6)] * 8))

    assert results.count(True) == 1, f"expected exactly one real-process winner, got {results}"


def test_dead_owner_claim_is_reclaimed_by_a_new_real_process(tmp_path: Path) -> None:
    """A claim naming a genuinely dead real pid is reclaimed; a live one is not.

    No liveness monkeypatch: a real subprocess wins the claim and exits, then
    this test confirms its pid is actually dead via ``os.kill(pid, 0)`` before
    proving reclaim — the same signal-0 probe the production code uses.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    _write_ledger_entry(trw_dir, "embeddings_backfill", deferred_since_ts=_iso_ago(9.0), deferred_count=5)

    with concurrent.futures.ProcessPoolExecutor(max_workers=1) as pool:
        won = pool.submit(_claim_worker, (str(trw_dir), "embeddings_backfill", 6)).result()
    assert won is True

    claim_path = trw_dir / "runtime" / "deferral-claims" / "embeddings_backfill.lock"
    owner_pid = int(claim_path.read_text(encoding="utf-8").splitlines()[0])

    # The pool process has exited by the time .result() returned; confirm it.
    for _ in range(50):
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:  # pragma: no cover - defensive; process should have exited well before this
        pytest.fail(f"pool worker pid {owner_pid} did not exit in time for this probe")

    # Backdate the claim past the bound: age + death together permit reclaim.
    claim_path.write_text(f"{owner_pid}\n{_iso_ago(9.0)}\n", encoding="utf-8")

    from trw_mcp.state.deferral_ledger import claim_forced_run

    assert claim_forced_run(trw_dir, "embeddings_backfill", max_deferral_hours=6) is True, (
        "a claim whose owner is confirmed dead and past the bound must be reclaimed"
    )

    # A FRESH claim from the CURRENT (live) process must NOT be reclaimed by
    # a second contender: age alone is not staleness.
    assert claim_forced_run(trw_dir, "embeddings_backfill", max_deferral_hours=6) is False


# ---------------------------------------------------------------------------
# Row 2: a real unwritable ledger directory, and a real corrupt ledger with
# single-winner arbitration across real processes.
# ---------------------------------------------------------------------------


def test_real_unwritable_ledger_dir_runs_instead_of_deferring_forever(tmp_path: Path) -> None:
    """A genuinely unwritable runtime directory must RUN the step, not defer.

    Real ``chmod``, not a monkeypatched ``_write_ledger``: the old contract
    let a missing-but-unwritable ledger defer forever because
    ``deferred_since_ts`` could never be durably recorded.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    runtime_dir = trw_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(runtime_dir, 0o500)  # read + execute, no write: cannot create files inside
    try:
        from trw_mcp.state.deferral_ledger import step_deferral_decision

        decision = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
        assert decision.defer is False, "an unwritable ledger dir must not defer forever (audit row 2)"
        assert decision.expired is True
        assert decision.ledger_state == "degraded"
    finally:
        os.chmod(runtime_dir, 0o700)


def _real_decision_worker(args: tuple[str, str]) -> bool:
    trw_dir_str, step = args
    from pathlib import Path as _Path

    from trw_mcp.state.deferral_ledger import step_deferral_decision

    decision = step_deferral_decision(_Path(trw_dir_str), step, under_pressure=True, max_deferral_hours=6)
    return decision.defer


def test_real_corrupt_ledger_elects_a_single_winner_across_real_processes(tmp_path: Path) -> None:
    """A genuinely corrupt (but writable-directory) ledger must not herd.

    Real corrupt file content across real processes: the old
    ``claim_forced_run`` returned ``True`` unconditionally whenever the read
    was degraded, so every one of N processes ran the same expensive step at
    once. Exactly one of these real processes must run it.
    """
    trw_dir = _minimal_trw_dir(tmp_path)
    (trw_dir / "runtime").mkdir(parents=True, exist_ok=True)
    (trw_dir / "runtime" / "deferral_ledger.json").write_text("{not valid json at all", encoding="utf-8")

    with concurrent.futures.ProcessPoolExecutor(max_workers=6) as pool:
        defers = list(pool.map(_real_decision_worker, [(str(trw_dir), "auto_upgrade_check")] * 6))

    assert defers.count(False) == 1, f"exactly one real process must run against a corrupt ledger, got {defers}"
    assert defers.count(True) == 5


# ---------------------------------------------------------------------------
# Row 3: invalid and implausibly-future ledger timestamps degrade the entry.
# ---------------------------------------------------------------------------


def test_unparseable_timestamp_degrades_instead_of_reporting_zero_age(tmp_path: Path) -> None:
    from trw_mcp.state.deferral_ledger import step_deferral_decision

    trw_dir = _minimal_trw_dir(tmp_path)
    _write_ledger_entry(trw_dir, "stale_runs", deferred_since_ts="not-a-timestamp", deferred_count=3)

    decision = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert decision.ledger_state == "degraded", "an unparseable timestamp must degrade the whole entry"
    # Degraded means single-winner arbitration decides run-vs-defer; either
    # is a valid outcome of that arbitration, but "healthy zero-age forever"
    # (the old defect) is not — assert the ledger_state is what changed.


def test_implausibly_future_timestamp_degrades_instead_of_passing_as_healthy(tmp_path: Path) -> None:
    from trw_mcp.state.deferral_ledger import step_deferral_decision

    trw_dir = _minimal_trw_dir(tmp_path)
    year_2999 = "2999-01-01T00:00:00+00:00"
    _write_ledger_entry(trw_dir, "stale_runs", deferred_since_ts=year_2999, deferred_count=3)

    decision = step_deferral_decision(trw_dir, "stale_runs", under_pressure=True, max_deferral_hours=6)
    assert decision.ledger_state == "degraded", "a year-2999 deferred_since_ts must not pass as a healthy streak"


def test_future_timestamp_within_clock_skew_is_still_trusted(tmp_path: Path) -> None:
    """A few seconds of future skew (real clock jitter) must NOT degrade the entry."""
    from trw_mcp.state.deferral_ledger import read_ledger

    trw_dir = _minimal_trw_dir(tmp_path)
    near_now = (datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()
    _write_ledger_entry(trw_dir, "stale_runs", deferred_since_ts=near_now, deferred_count=1)

    _entries, state = read_ledger(trw_dir)
    assert state == "ok", "a couple seconds of clock skew must not be treated as a corrupted timestamp"


# ---------------------------------------------------------------------------
# Row 4: a real unreadable writer-lock directory, and a real unreadable lock.
# ---------------------------------------------------------------------------


def test_real_unreadable_writer_registry_reports_census_unreadable(tmp_path: Path) -> None:
    """Real ``chmod`` on the writers dir — not a monkeypatched ``_iter_lock_paths``.

    On Python 3.12, ``Path.glob`` silently swallowed the ``scandir``
    ``PermissionError`` here and returned an empty result, so a genuinely
    unreadable registry reported a healthy zero-writer census.
    """
    from trw_mcp.state.memory_pressure import take_writer_census

    trw_dir = _minimal_trw_dir(tmp_path)
    writers_dir = trw_dir / "memory" / "memory.db.writers"
    (writers_dir / "peer.lock").write_text(f"{os.getpid() + 1}\n{time.time():.6f}\n", encoding="utf-8")
    os.chmod(writers_dir, 0o000)
    try:
        census = take_writer_census(trw_dir, threshold=2)
        assert census.census_state == "unreadable", (
            "a directory this process cannot even list must not read as measured"
        )
        assert census.writer_count == 0
    finally:
        os.chmod(writers_dir, 0o700)


def test_real_unreadable_lock_degrades_identity_state_not_verified(tmp_path: Path) -> None:
    """One unreadable lock among readable ones must degrade identity_state.

    The directory itself stays listable (so ``census_state`` is still
    ``measured``); only the individual lock FILE is unreadable, which used
    to be swallowed as "malformed" and left ``identity_state`` at
    ``verified``.
    """
    from trw_mcp.state.memory_pressure import take_writer_census

    trw_dir = _minimal_trw_dir(tmp_path)
    writers_dir = trw_dir / "memory" / "memory.db.writers"
    self_pid = os.getpid()
    (writers_dir / "self.lock").write_text(f"{self_pid}\n{time.time():.6f}\n", encoding="utf-8")
    unreadable = writers_dir / "unreadable.lock"
    unreadable.write_text(f"{self_pid + 1}\n{time.time():.6f}\n", encoding="utf-8")
    os.chmod(unreadable, 0o000)
    try:
        census = take_writer_census(trw_dir, threshold=2)
        assert census.census_state == "measured", "the directory itself is still listable"
        assert census.identity_state == "unverified", (
            "an unreadable individual lock is a measurement failure, not evidence of absence"
        )
    finally:
        os.chmod(unreadable, 0o700)


# ---------------------------------------------------------------------------
# Row 5: writer_pressure survives a real exception, and is really typed.
# ---------------------------------------------------------------------------


def test_writer_pressure_key_present_even_when_the_block_builder_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Required key must survive an exception from inside the real builder."""
    import trw_mcp.tools._orchestration_status_assembly as assembly

    def _boom() -> object:
        raise RuntimeError("simulated total failure of the pressure block")

    monkeypatch.setattr(assembly, "_writer_pressure_block", _boom)

    result = assembly.assemble_status_result(
        {"run_id": "r", "task": "t", "phase": "implement", "status": "active", "confidence": "high", "framework": "v1"},
        [],
        {},
        Path("/tmp/does-not-matter"),
        __import__("trw_mcp.state.persistence", fromlist=["FileStateReader"]).FileStateReader(),
        Path("/tmp/does-not-matter/meta.yaml"),
    )

    assert "writer_pressure" in result, "writer_pressure must never be omitted, even on total failure"
    pressure = result["writer_pressure"]
    assert pressure["census_state"] == "unreadable"
    assert pressure["ledger_state"] == "degraded"
    assert pressure["under_pressure"] is False


def test_writer_pressure_state_fields_are_the_real_closed_vocabularies() -> None:
    """The four typed_dicts aliases must equal their state/ originals, not drift as plain str."""
    from typing import get_args

    from trw_mcp.models.typed_dicts._orchestration import (
        CensusState as TypedDictCensusState,
    )
    from trw_mcp.models.typed_dicts._orchestration import (
        HeartbeatState as TypedDictHeartbeatState,
    )
    from trw_mcp.models.typed_dicts._orchestration import (
        IdentityState as TypedDictIdentityState,
    )
    from trw_mcp.models.typed_dicts._orchestration import (
        LedgerState as TypedDictLedgerState,
    )
    from trw_mcp.state._writer_census_identity import HeartbeatState, IdentityState
    from trw_mcp.state.deferral_ledger import LedgerState
    from trw_mcp.state.memory_pressure import CensusState

    assert get_args(TypedDictCensusState) == get_args(CensusState)
    assert get_args(TypedDictLedgerState) == get_args(LedgerState)
    assert get_args(TypedDictHeartbeatState) == get_args(HeartbeatState)
    assert get_args(TypedDictIdentityState) == get_args(IdentityState)


# ---------------------------------------------------------------------------
# Row 9: stale_runs never opens a streak while auto-close is disabled.
# ---------------------------------------------------------------------------


def test_stale_runs_disabled_never_opens_a_deferral_streak(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Under pressure with auto-close OFF, no ``stale_runs`` ledger entry appears.

    Reverting the row-9 fix (consulting the ledger unconditionally before the
    ``run_auto_close_enabled`` check) turns this red: a streak would open and
    age even though nothing about auto-close is happening.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state.deferral_ledger import read_ledger
    from trw_mcp.tools._ceremony_helpers import run_auto_maintenance

    trw_dir = _minimal_trw_dir(tmp_path)
    _register_peers(trw_dir, monkeypatch, count=2)

    config = TRWConfig(  # type: ignore[call-arg]
        session_start_defer_under_writer_pressure=True,
        session_start_writer_pressure_threshold=2,
        run_auto_close_enabled=False,
    )
    run_auto_maintenance(trw_dir, config)

    entries, _state = read_ledger(trw_dir)
    assert "stale_runs" not in entries, "a disabled auto-close must never open a stale_runs streak"


# ---------------------------------------------------------------------------
# Row 7 + peer-review finding: bounded EVALUATION must guarantee bounded
# EMISSION, and pressure clearing must close a streak it opened.
# ---------------------------------------------------------------------------


def _nudge_config_yaml(threshold: int = 2) -> str:
    return (
        "session_start_defer_under_writer_pressure: true\n"
        f"session_start_writer_pressure_threshold: {threshold}\n"
        "nudge_enabled: true\n"
    )


def _register_peers(trw_dir: Path, monkeypatch: pytest.MonkeyPatch, *, count: int) -> None:
    """Register *count* live peer writers (threshold's Pydantic floor is 2, so
    "under pressure" tests need at least 2 peers)."""
    import trw_mcp.state.memory_pressure as mp

    (trw_dir / "memory" / "memory.db.writers" / "self.lock").write_text(
        f"{os.getpid()}\n{time.time():.6f}\n", encoding="utf-8"
    )
    for offset in range(1, count + 1):
        (trw_dir / "memory" / "memory.db.writers" / f"peer-{offset}.lock").write_text(
            f"{os.getpid() + offset}\n{time.time():.6f}\n", encoding="utf-8"
        )
    monkeypatch.setattr(mp, "_pid_is_alive", lambda _pid: True)


def test_expired_nudge_streak_not_completed_when_no_content_is_emitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bounded EVALUATION alone must not close the streak: emission must land too.

    Forces pool selection to fail (returns ``None``) on an EXPIRED streak, so
    no ``nudge_content`` is ever set. Reverting the row-7 fix (completing
    immediately on ``decision.expired`` inside ``_apply_nudge_pressure``)
    turns this red: the streak would close with no nudge ever having reached
    a caller.
    """
    import trw_mcp.tools._ceremony_status as cs

    trw_dir = _minimal_trw_dir(tmp_path)
    _register_peers(trw_dir, monkeypatch, count=2)
    (trw_dir / "config.yaml").write_text(_nudge_config_yaml(threshold=2), encoding="utf-8")
    _write_ledger_entry(trw_dir, "nudges", deferred_since_ts=_iso_ago(9.0), deferred_count=12)

    monkeypatch.setattr(cs, "select_pool", lambda *_a, **_k: None)

    response = cs.append_ceremony_status({}, trw_dir=trw_dir)

    assert "nudge_content" not in response, "the pool was forced to select nothing"

    from trw_mcp.state.deferral_ledger import read_ledger

    entries, _state = read_ledger(trw_dir)
    entry = entries.get("nudges")
    assert entry is not None, "an un-emitted expired streak must not vanish from the ledger"
    assert entry.deferred_since_ts is not None, (
        "the streak must stay OPEN when no nudge was actually emitted (row 7) — "
        "completing it here would let a bounded EVALUATION substitute for a bounded EMISSION"
    )


def test_expired_nudge_streak_completed_once_content_actually_lands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mirror case: when a nudge IS emitted, the streak DOES close."""
    import trw_mcp.tools._ceremony_status as cs

    trw_dir = _minimal_trw_dir(tmp_path)
    _register_peers(trw_dir, monkeypatch, count=2)
    (trw_dir / "config.yaml").write_text(_nudge_config_yaml(threshold=2), encoding="utf-8")
    _write_ledger_entry(trw_dir, "nudges", deferred_since_ts=_iso_ago(9.0), deferred_count=12)

    monkeypatch.setattr(cs, "select_pool", lambda *_a, **_k: "workflow")
    monkeypatch.setattr(cs, "resolve_pool_content", lambda *_a, **_k: "do the next thing")
    monkeypatch.setattr("trw_mcp.tools._ceremony_nudge_emission.account_nudge_emission", lambda *_a, **_k: None)

    response = cs.append_ceremony_status({}, trw_dir=trw_dir)

    assert response.get("nudge_content") == "do the next thing"

    from trw_mcp.state.deferral_ledger import read_ledger

    entries, _state = read_ledger(trw_dir)
    entry = entries.get("nudges")
    assert entry is not None
    assert entry.deferred_since_ts is None, "an ACTUALLY emitted nudge must close the streak"
    assert entry.deferred_count == 0


def test_nudge_streak_closes_once_pressure_clears_even_with_no_emission(tmp_path: Path) -> None:
    """Peer-review finding: pressure clearing must close a streak it opened,
    independent of whether THIS call happens to emit a nudge.

    Before this fix, the healthy (``not under_pressure``) branch returned
    immediately without ever calling ``step_deferral_decision``/
    ``record_completion``, so a streak opened while pressure was present
    stayed open forever once pressure cleared.
    """
    import trw_mcp.tools._ceremony_status as cs

    trw_dir = _minimal_trw_dir(tmp_path)
    # No peer registered: pressure is NOT present on this call.
    (trw_dir / "memory" / "memory.db.writers" / "self.lock").write_text(
        f"{os.getpid()}\n{time.time():.6f}\n", encoding="utf-8"
    )
    (trw_dir / "config.yaml").write_text(_nudge_config_yaml(threshold=2), encoding="utf-8")

    # Simulate a streak opened during an EARLIER pressured call.
    _write_ledger_entry(trw_dir, "nudges", deferred_since_ts=_iso_ago(9.0), deferred_count=12)

    cs.append_ceremony_status({}, trw_dir=trw_dir)

    from trw_mcp.state.deferral_ledger import read_ledger

    entries, _state = read_ledger(trw_dir)
    entry = entries.get("nudges")
    assert entry is not None
    assert entry.deferred_since_ts is None, (
        "a streak opened under pressure must close once a later call finds pressure has cleared"
    )
    assert entry.last_completed_ts is not None


def test_healthy_machine_pays_no_ledger_write_when_no_streak_is_open(tmp_path: Path) -> None:
    """The common case (no open streak, no pressure) must not write the ledger."""
    import trw_mcp.tools._ceremony_status as cs

    trw_dir = _minimal_trw_dir(tmp_path)
    (trw_dir / "memory" / "memory.db.writers" / "self.lock").write_text(
        f"{os.getpid()}\n{time.time():.6f}\n", encoding="utf-8"
    )
    (trw_dir / "config.yaml").write_text(_nudge_config_yaml(threshold=2), encoding="utf-8")

    cs.append_ceremony_status({}, trw_dir=trw_dir)

    assert not (trw_dir / "runtime" / "deferral_ledger.json").exists(), (
        "a healthy machine with no open streak must not pay a ledger write"
    )
