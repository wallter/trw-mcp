"""PRD-CORE-248 FR06 — the ``memory_wal`` doctor row.

Before this there was no WAL or writer visibility in diagnostics at all, so a
starved store (25.0 MiB WAL against a 10 MB threshold, checkpoint permanently
cancelled by writer pressure) was invisible until someone read the code.

The row is exercised through the real doctor catalogue, not by calling the
sibling in isolation, so a row that exists but is never registered fails here.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


def _seed_project(tmp_path: Path, *, wal_bytes: int) -> Path:
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)
    db_path = trw_dir / "memory" / "memory.db"
    db_path.write_bytes(b"")
    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * wal_bytes)
    return tmp_path


def _row(target: Path, name: str = "memory_wal") -> object:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _doctor_core

    results = _doctor_core(target, TRWConfig())
    matches = [r for r in results if r.name == name]
    assert matches, f"doctor produced no {name} row; got {[r.name for r in results]}"
    return matches[0]


def test_memory_wal_row_reports_size_writers_and_checkpoint_age(tmp_path: Path) -> None:
    """The row carries all three values and WARNs under an oversized-and-stale store."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import record_checkpoint_attempt, record_effective_checkpoint

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    db_path = target / ".trw" / "memory" / "memory.db"
    record_checkpoint_attempt(db_path, now=time.time())
    record_effective_checkpoint(db_path, now=time.time() - cfg.wal_checkpoint_max_age_seconds - 60)
    # Two live writers, so the count is non-trivial.
    writers = target / ".trw" / "memory" / "memory.db.writers"
    writers.mkdir(parents=True, exist_ok=True)
    (writers / "self.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
    (writers / "peer.lock").write_text(f"{os.getppid()}\n", encoding="utf-8")

    row = _row(target)

    assert row.status == "WARN"
    assert "15.0 MiB" in row.message
    assert "2 live writer(s)" in row.message
    assert "last checkpoint attempt" in row.message
    assert "last checkpoint that CLEARED THE BACKLOG" in row.message


def test_unknown_checkpoint_age_reads_unknown_and_passes(tmp_path: Path) -> None:
    """US-004 AC2: a store with no persisted timestamp reports unknown, status PASS."""
    from trw_mcp.models.config import TRWConfig

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb - 5) * 1024 * 1024)

    row = _row(target)

    assert row.status == "PASS", "an unknown age on a store that never needed a checkpoint is not a fault"
    assert "last checkpoint that CLEARED THE BACKLOG unknown ago" in row.message


def test_oversized_but_freshly_checkpointed_and_reclaimed_is_pass(tmp_path: Path) -> None:
    """A big WAL that is keeping up AND being reclaimed is a busy store, not a fault.

    Both clocks are required now. A fresh backlog clock alone is what the
    unsafe-engine store also shows -- it clears its whole backlog on every run
    and never reclaims a byte -- so the healthy case has to be distinguished by
    the thing that actually differs: a reset happened.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import record_effective_checkpoint, record_reset_checkpoint

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    db = target / ".trw" / "memory" / "memory.db"
    record_effective_checkpoint(db, now=time.time())
    record_reset_checkpoint(db, now=time.time())

    assert _row(target).status == "PASS"


def test_no_wal_file_reports_zero_and_passes(tmp_path: Path) -> None:
    """Negative case: a store with no WAL reports zero size and PASSes."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "memory").mkdir(parents=True)

    row = _row(tmp_path)

    assert row.status == "PASS"
    assert "WAL 0.0 MiB" in row.message


def test_memory_wal_check_opens_no_sqlite_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """FR06: a diagnostic about contention must not add a writer to the store."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._subcommands_doctor import _check_memory_wal

    target = _seed_project(tmp_path, wal_bytes=1024)
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *a, **k: pytest.fail("memory_wal must not open a SQLite connection"),
    )
    result = _check_memory_wal(target, TRWConfig())
    assert result.name == "memory_wal"


def test_memory_wal_is_registered_in_the_doctor_catalogue() -> None:
    """A row nobody dispatches is not a row (wiring assertion)."""
    from trw_mcp.server._subcommands_doctor import _CHECKS

    assert ("memory_wal", "_check_memory_wal") in _CHECKS


def test_warn_reads_the_effective_clock_not_the_attempt_clock(tmp_path: Path) -> None:
    """Review finding 4: a store that checkpoints hourly and reclaims nothing must WARN.

    This is the unsafe-engine steady state — only PASSIVE may run, PASSIVE never
    truncates — and it is exactly the case a row reading the ATTEMPT clock could
    never report, because that clock is always fresh.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import record_checkpoint_attempt, record_effective_checkpoint

    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    db_path = target / ".trw" / "memory" / "memory.db"
    # Attempted seconds ago; last actually accomplished anything, long ago.
    record_checkpoint_attempt(db_path, now=time.time() - 5)
    record_effective_checkpoint(db_path, now=time.time() - cfg.wal_checkpoint_max_age_seconds - 600)

    row = _row(target)

    assert row.status == "WARN"
    assert "cleared the WAL backlog" in row.message


def _unsafe_engine_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, object]:
    """A store that earns the engine remedy, with the engine forced unsafe.

    The engine has to be FORCED: on an interpreter whose own SQLite already
    carries the fix (this repository's dev box runs 3.53.4) the branch under test
    is unreachable, and a test that silently depends on the box's SQLite is a test
    that reports the box, not the code.
    """
    import time as _time

    from trw_memory.storage import _dbapi

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state._wal_triggers import record_effective_checkpoint

    monkeypatch.setattr(_dbapi, "is_wal_reset_safe", lambda: False)
    monkeypatch.setattr(_dbapi, "sqlite_version", lambda: "3.50.4")
    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    record_effective_checkpoint(target / ".trw" / "memory" / "memory.db", now=_time.time())
    return target, cfg


def test_the_row_always_reports_the_selected_engine(tmp_path: Path) -> None:
    """PRD-INFRA-185 FR04 — an operator can see which driver actually won."""
    from trw_memory.storage._dbapi import backend, sqlite_version

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._doctor_memory_wal import memory_wal_row

    target = _seed_project(tmp_path, wal_bytes=0)
    _status, message = memory_wal_row(target, TRWConfig())
    assert f"engine {backend()} {sqlite_version()}" in message


def test_unsafe_engine_row_names_qualifying_interpreters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-INFRA-185 FR04 — the remedy names something that exists on THIS box.

    sub_oASyvgLj4UwcwK2c followed the old remedy literally on macOS, installed the
    only pysqlite3 wheel there is, and got SQLite 3.51.1: an engine change with no
    effect on the problem. The row now points at interpreters it actually probed.
    """
    from trw_memory.storage._wal_checkpoint import WAL_RESET_UNSAFE_REMEDY

    from trw_mcp.server import _doctor_memory_wal
    from trw_mcp.server._doctor_memory_wal import memory_wal_row

    target, cfg = _unsafe_engine_target(tmp_path, monkeypatch)
    monkeypatch.setattr(_doctor_memory_wal, "qualifying_interpreters", lambda: [("python3.14", "3.53.4")])

    status, message = memory_wal_row(target, cfg)

    assert status == "WARN"
    assert WAL_RESET_UNSAFE_REMEDY in message
    assert "Qualifying interpreters found here: python3.14 (SQLite 3.53.4)." in message


def test_unsafe_engine_row_says_when_no_candidate_qualified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It reports the CANDIDATES it probed, never a search of PATH it did not do."""
    from trw_mcp.server import _doctor_memory_wal
    from trw_mcp.server._doctor_memory_wal import memory_wal_row

    target, cfg = _unsafe_engine_target(tmp_path, monkeypatch)
    monkeypatch.setattr(_doctor_memory_wal, "qualifying_interpreters", list)

    _status, message = memory_wal_row(target, cfg)

    assert "None of the probed candidates (python3.14, python3.13, python3.12, python3)" in message
    assert "no interpreter on PATH" not in message


def test_a_safe_engine_probes_no_interpreters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """PRD-INFRA-185 NFR01 — the probe is paid for only on the branch that needs it."""
    import time as _time

    from trw_memory.storage import _dbapi

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server import _doctor_memory_wal
    from trw_mcp.server._doctor_memory_wal import memory_wal_row
    from trw_mcp.state._wal_triggers import record_effective_checkpoint

    monkeypatch.setattr(_dbapi, "is_wal_reset_safe", lambda: True)
    calls: list[int] = []
    monkeypatch.setattr(_doctor_memory_wal, "qualifying_interpreters", lambda: calls.append(1) or [])
    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    record_effective_checkpoint(target / ".trw" / "memory" / "memory.db", now=_time.time())

    status, _message = memory_wal_row(target, cfg)

    assert status == "WARN", "a safe engine can still warn; it just gets different advice"
    assert calls == [], "a capable engine must not pay for four subprocess probes"


class TestQualifyingInterpreters:
    """The probe itself: read-only, bounded, isolated, and never raising."""

    def test_isolated_flags_are_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A plain -c run puts CWD first on sys.path; a repo sqlite3.py would answer."""
        import subprocess

        from trw_mcp.server import _doctor_memory_wal

        seen: list[tuple[str, ...]] = []

        def _fake_run(argv: tuple[str, ...], **kwargs: object) -> object:
            seen.append(argv)
            assert kwargs["stdin"] is subprocess.DEVNULL
            assert kwargs["timeout"] == _doctor_memory_wal._PROBE_TIMEOUT_SECONDS
            return SimpleNamespace(returncode=0, stdout="3.53.4\n", stderr="")

        monkeypatch.setattr(_doctor_memory_wal.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(_doctor_memory_wal.subprocess, "run", _fake_run)

        found = _doctor_memory_wal.qualifying_interpreters(("python3.14",))

        assert found == [("python3.14", "3.53.4")]
        assert seen[0][1:5] == ("-I", "-S", "-B", "-c")

    def test_absent_timed_out_failed_and_garbage_candidates_are_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import subprocess

        from trw_mcp.server import _doctor_memory_wal

        outcomes: dict[str, object] = {
            "/bin/gone": None,
            "/bin/slow": subprocess.TimeoutExpired(cmd="x", timeout=2.0),
            "/bin/broken": SimpleNamespace(returncode=1, stdout="", stderr="boom"),
            "/bin/garbage": SimpleNamespace(returncode=0, stdout="not-a-version", stderr=""),
            "/bin/old": SimpleNamespace(returncode=0, stdout="3.50.4", stderr=""),
            "/bin/unspawnable": OSError("exec format error"),
            "/bin/good": SimpleNamespace(returncode=0, stdout="3.51.3", stderr=""),
        }

        def _which(name: str) -> str | None:
            path = f"/bin/{name}"
            return None if outcomes.get(path) is None else path

        def _run(argv: tuple[str, ...], **_kwargs: object) -> object:
            outcome = outcomes[argv[0]]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(_doctor_memory_wal.shutil, "which", _which)
        monkeypatch.setattr(_doctor_memory_wal.subprocess, "run", _run)

        found = _doctor_memory_wal.qualifying_interpreters(
            ("gone", "slow", "broken", "garbage", "old", "unspawnable", "good")
        )

        assert found == [("good", "3.51.3")]

    def test_a_duplicate_resolution_is_probed_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """python3 is usually a symlink to python3.X; do not pay for it twice."""
        from trw_mcp.server import _doctor_memory_wal

        runs: list[str] = []

        def _run(argv: tuple[str, ...], **_kwargs: object) -> object:
            runs.append(argv[0])
            return SimpleNamespace(returncode=0, stdout="3.53.4", stderr="")

        monkeypatch.setattr(_doctor_memory_wal.shutil, "which", lambda _name: "/usr/bin/python3.14")
        monkeypatch.setattr(_doctor_memory_wal.subprocess, "run", _run)

        found = _doctor_memory_wal.qualifying_interpreters(("python3.14", "python3"))

        assert runs == ["/usr/bin/python3.14"]
        assert len(found) == 1

    def test_the_aggregate_budget_stops_the_sweep(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from trw_mcp.server import _doctor_memory_wal

        clock = iter([0.0, 0.0, 99.0, 99.0, 99.0])
        monkeypatch.setattr(_doctor_memory_wal.time, "monotonic", lambda: next(clock))
        monkeypatch.setattr(_doctor_memory_wal.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            _doctor_memory_wal.subprocess,
            "run",
            lambda *_a, **_k: SimpleNamespace(returncode=0, stdout="3.53.4", stderr=""),
        )

        found = _doctor_memory_wal.qualifying_interpreters(("python3.14", "python3.13", "python3.12"))

        assert len(found) == 1, "the budget must cut the sweep short"

    def test_an_old_trw_memory_degrades_instead_of_raising(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """trw-mcp's declared floor still admits a release without the predicate."""
        import builtins

        from trw_mcp.server import _doctor_memory_wal

        real_import = builtins.__import__

        def _no_predicate(name: str, *args: object, **kwargs: object) -> object:
            if name == "trw_memory.storage._dbapi" and args and "wal_reset_safe_version" in (args[2] or ()):
                raise ImportError("cannot import name 'wal_reset_safe_version'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", _no_predicate)
        assert _doctor_memory_wal.qualifying_interpreters(("python3.14",)) == []


def test_warn_names_the_engine_remedy_when_the_engine_cannot_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OQ-1 reversal: the operator is told WHY the WAL cannot shrink, and what to do."""
    from trw_memory.storage import _dbapi
    from trw_memory.storage._wal_checkpoint import WAL_RESET_UNSAFE_REMEDY

    from trw_mcp.models.config import TRWConfig
    from trw_mcp.server._doctor_memory_wal import memory_wal_row
    from trw_mcp.state._wal_triggers import record_effective_checkpoint

    monkeypatch.setattr(_dbapi, "is_wal_reset_safe", lambda: False)
    monkeypatch.setattr(_dbapi, "sqlite_version", lambda: "3.50.4")
    cfg = TRWConfig()
    target = _seed_project(tmp_path, wal_bytes=(cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024)
    # The engine remedy answers "why is this never RECLAIMED", not "why is it
    # behind": an unsafe engine does not cause a frame backlog -- PASSIVE writes
    # frames back perfectly well -- readers do. So the store that earns this
    # advice has a FRESH backlog clock and no reset, which is exactly the
    # unsafe-engine steady state. Telling a behind store to upgrade SQLite would
    # be advice that cannot clear what it is warning about.
    record_effective_checkpoint(target / ".trw" / "memory" / "memory.db", now=time.time())

    status, message = memory_wal_row(target, cfg)

    assert status == "WARN", "never reclaimed is a warning however fresh the backlog clock is"
    assert "not been reclaimed" in message
    # The driver is forced below the 3.51.3 fix above, so the engine branch is live.
    assert WAL_RESET_UNSAFE_REMEDY in message
    assert "journal_size_limit" in message, "the row must reconcile the 10 MB trigger with the 64 MiB cap"


def test_a_store_checkpointing_to_no_effect_eventually_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The WARN this row exists for, driven through the REAL checkpoint path.

    Every other test here reaches WARN by hand-writing a stale effective marker,
    which proves the predicate but not that anything can ever satisfy it. Until
    2026-09-10 nothing could: ``maybe_checkpoint_wal`` advanced the effective
    clock whenever frames were written back, which PASSIVE does on every run,
    so a store that checkpointed forever and reclaimed nothing reported a fresh
    effective age forever and this row was unreachable at any WAL size --
    disarmed by the exact failure it exists to catch.

    Attribution: revert the ``effective_due`` change in
    ``trw_mcp/state/_memory_lookups.py`` and this test goes red.
    """
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import TRWConfig, get_config
    from trw_mcp.state._wal_triggers import effective_checkpoint_marker_path
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    cfg = TRWConfig()
    oversized = (cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024
    target = _seed_project(tmp_path, wal_bytes=oversized)
    trw_dir = target / ".trw"
    db_path = trw_dir / "memory" / "memory.db"

    backend = SQLiteBackend(db_path)
    mc._backend = backend
    try:
        # The observed steady state: frames written back, file never shrinks.
        monkeypatch.setattr(
            backend,
            "checkpoint_wal",
            lambda *a, **k: {"busy": 0, "checkpointed": 112, "log_frames": 3379, "mode": "PASSIVE"},
        )
        monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)
        for _ in range(3):
            maybe_checkpoint_wal(trw_dir)

        assert not effective_checkpoint_marker_path(db_path).exists(), (
            "three checkpoints that freed nothing must leave the effective clock unset"
        )
    finally:
        mc.reset_backend()

    # Opening a real SQLiteBackend initialises the db and replaces the seeded
    # placeholder WAL, so restore the oversized file before reading the row.
    # Size is an independent precondition here; the claim under test is that
    # repeated no-reclaim checkpoints leave the EFFECTIVE clock unset, which is
    # what makes the WARN reachable at all.
    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * oversized)

    row = _row(target)
    assert row.status == "WARN", (
        "an oversized WAL that repeated checkpoints cannot reclaim is exactly the condition this row exists to surface"
    )
    assert "cleared the WAL backlog" in row.message


def test_a_store_that_clears_its_backlog_but_never_reclaims_still_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store this row exists for, and which every previous predicate missed.

    Below SQLite 3.51.3 a resetting checkpoint is refused, so PASSIVE runs and
    writes back the WHOLE backlog every time. checkpointed == log_frames, the
    backlog clock stays fresh forever, and the WAL is never reclaimed. Under a
    backlog-only predicate that reads PASS -- which is the original defect back
    again, by a third route, on this repository's own engine.

    This is why reclamation gets its own clock instead of a fourth proxy:
    ``record_reset_checkpoint`` is written only when a reset actually ran, so a
    store that never resets can be seen not to.

    Attribution: drop the `unreclaimed` disjunct from the doctor predicate and
    this test goes green while the store stays broken.
    """
    from trw_memory.storage.sqlite_backend import SQLiteBackend

    import trw_mcp.state._memory_connection as mc
    from trw_mcp.models.config import TRWConfig, get_config
    from trw_mcp.state._wal_triggers import (
        last_effective_checkpoint_age_seconds,
        reset_checkpoint_marker_path,
    )
    from trw_mcp.state.memory_adapter import maybe_checkpoint_wal

    cfg = TRWConfig()
    oversized = (cfg.wal_checkpoint_threshold_mb + 5) * 1024 * 1024
    target = _seed_project(tmp_path, wal_bytes=oversized)
    trw_dir = target / ".trw"
    db_path = trw_dir / "memory" / "memory.db"

    backend = SQLiteBackend(db_path)
    mc._backend = backend
    try:
        # The unsafe-engine steady state: the entire backlog written back, every
        # time, and not a byte reclaimed.
        monkeypatch.setattr(
            backend,
            "checkpoint_wal",
            lambda *a, **k: {"busy": 0, "checkpointed": 3379, "log_frames": 3379, "mode": "PASSIVE"},
        )
        monkeypatch.setattr(get_config(), "wal_checkpoint_threshold_mb", 1)
        for _ in range(3):
            maybe_checkpoint_wal(trw_dir)

        assert last_effective_checkpoint_age_seconds(db_path) is not None, (
            "the backlog IS being cleared -- that is exactly what makes this store invisible "
            "to a backlog-only predicate"
        )
        assert not reset_checkpoint_marker_path(db_path).exists(), (
            "no reset ever ran, so the reclamation clock must stay unset"
        )
    finally:
        mc.reset_backend()

    db_path.with_suffix(".db-wal").write_bytes(b"\x00" * oversized)

    row = _row(target)
    assert row.status == "WARN", (
        "a WAL that is never reclaimed is the condition this row exists for, however diligently its backlog is cleared"
    )
    assert "not been reclaimed" in row.message
