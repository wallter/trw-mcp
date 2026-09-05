"""Arm machinery for the PRD-CORE-262 stdio handshake benchmark.

Test-private, split out of ``test_stdio_n_server_handshake.py`` so the benchmark
module holds the bounds and the assertions and this one holds the plumbing that
produces the numbers they judge. The subprocess plumbing itself lives one layer
further down in ``tests/_stdio_harness.py``.
"""

from __future__ import annotations

import os
import random
import sqlite3
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests._stdio_harness import StdioServerHarness, writer_lock_pids

_FIX_130_BUDGET_FIELD = "learn_journal_drain_budget_ms"

# Synthetic word list -- seeded journal payloads carry no operator or repository
# content (PRD-CORE-262-NFR04).
_SYNTHETIC_WORDS = (
    "sqlite wal checkpoint daemon stdio handshake pytest fixture ruff mypy "
    "embedding recall journal lock pid heartbeat pin namespace migration schema "
    "hook nudge ceremony phase review deliver budget latency timeout retry"
).split()


@dataclass(frozen=True)
class ArmRecord:
    """One timing record per case: numbers, a fixed label, and a temporary path.

    ``deferral_names`` is the SINGLE deferral set every repeat agreed on
    (CORE262-02): ``run_arm`` asserts per-repeat equality before this record is
    built, so a stored value here is a verified invariant, not a union that
    could hide within-arm variability.
    """

    label: str
    n_background: int
    wal_bytes: int
    writer_census: int
    initialize_ms: tuple[float, ...]
    session_start_ms: tuple[float, ...]
    deferral_names: tuple[str, ...]
    wall_s: float
    store_path: str

    @property
    def median_initialize_ms(self) -> float:
        return statistics.median(self.initialize_ms)

    @property
    def median_session_start_ms(self) -> float:
        return statistics.median(self.session_start_ms)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_background": self.n_background,
            "wal_bytes": self.wal_bytes,
            "writer_census": self.writer_census,
            "initialize_ms": [round(v, 1) for v in self.initialize_ms],
            "session_start_ms": [round(v, 1) for v in self.session_start_ms],
            "median_initialize_ms": round(self.median_initialize_ms, 1),
            "initialize_range_ms": [round(min(self.initialize_ms), 1), round(max(self.initialize_ms), 1)],
            "median_session_start_ms": round(self.median_session_start_ms, 1),
            "deferral_names": list(self.deferral_names),
            "wall_s": round(self.wall_s, 2),
            "store_path": self.store_path,
        }


RECORD_FIELDS = frozenset(
    {
        "label",
        "n_background",
        "wal_bytes",
        "writer_census",
        "initialize_ms",
        "session_start_ms",
        "median_initialize_ms",
        "initialize_range_ms",
        "median_session_start_ms",
        "deferral_names",
        "wall_s",
        "store_path",
    }
)


@dataclass
class BenchmarkResult:
    root: Path
    arms: dict[str, ArmRecord] = field(default_factory=dict)
    module_wall_s: float = 0.0


def median_and_range(observations: tuple[float, ...]) -> tuple[float, float, float]:
    if not observations:
        raise ValueError("median_and_range needs at least one observation")
    return statistics.median(observations), min(observations), max(observations)


def deferral_names(payload: object) -> tuple[str, ...]:
    """Normalize a ``deferred`` payload (None | list | dict) to sorted task names.

    RISK-005: under writer pressure the measured runs returned ``deferred`` sets
    naming auto_upgrade_check, embeddings_backfill, side_effects and stale_runs,
    which is why contended arms can be FASTER than the uncontended baseline --
    work was skipped, not accelerated. Comparing the SET across arms is what
    keeps a latency assertion from passing because the server did less.
    """
    names: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            names.add(str(key))
            if isinstance(value, list):
                names.update(str(item) for item in value)
    elif isinstance(payload, list):
        names.update(str(item) for item in payload)
    return tuple(sorted(names))


def build_temp_project(root: Path) -> tuple[Path, Path]:
    """Create a temporary project + user dir. Never a live store.

    The store carries ``learnings/dedup_migration.yaml`` and ``memory/.migrated``
    up front: without those sentinels every open pays a one-time YAML migration
    and the first learn pays an unbounded batch dedup, and the benchmark would
    be measuring the sentinels rather than the handshake.

    ``session_start_writer_pressure_threshold`` is pinned to its ceiling for one
    reason, stated here so it is never mistaken for convenience. Its default of
    8 PEER writers means the N=12 arm would run under writer pressure while the
    N=1 baseline would not: session_start would compact its response and skip
    auto_recall and ceremony_status decoration, so the contended arm would be
    faster because it did LESS, and comparing the two would be a tautology
    (RISK-005). Pinning holds that confounder constant so every arm performs the
    same work. It cannot weaken the property under test: the cold ``initialize``
    reply is produced by the boot-deferral middleware before any census is taken.
    """
    project = root / "project"
    user_dir = root / "userdir"
    for directory in (project / ".git", project / ".trw" / "learnings", project / ".trw" / "memory", user_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (project / ".trw" / "config.yaml").write_text(
        "framework_version: v26.2_TRW\n"
        "meta_tune_enabled: false\n"
        "session_start_writer_pressure_threshold: 64\n"
        "target_platforms:\n- claude-code\n",
        encoding="utf-8",
    )
    (project / ".trw" / "learnings" / "index.yaml").write_text("entries: []\n", encoding="utf-8")
    (project / ".trw" / "learnings" / "dedup_migration.yaml").write_text(
        "completed_at: '2026-01-01T00:00:00Z'\n", encoding="utf-8"
    )
    (project / ".trw" / "memory" / ".migrated").write_text("migrated_at=2026-01-01T00:00:00Z", encoding="utf-8")
    return project, user_dir


def wal_bytes(project: Path) -> int:
    wal = project / ".trw" / "memory" / "memory.db-wal"
    return wal.stat().st_size if wal.exists() else 0


def settle_writer_census(project: Path, expected: int, *, timeout_s: float = 10.0) -> list[int]:
    """Poll the PRODUCTION census until it reaches *expected*, or give up and return it.

    Registration happens when a server opens the memory backend, which is a few
    milliseconds behind the ``trw_session_start`` reply that triggered it. A
    bounded poll removes that race without ever inventing a pid: the caller
    still asserts on whatever this returns.
    """
    deadline = time.monotonic() + timeout_s
    census = list(writer_lock_pids(project / ".trw"))
    while len(census) != expected and time.monotonic() < deadline:
        time.sleep(0.2)
        census = list(writer_lock_pids(project / ".trw"))
    return census


def run_arm(
    label: str,
    project: Path,
    user_dir: Path,
    stderr_dir: Path,
    *,
    n_background: int,
    repeats: int,
) -> ArmRecord:
    """Warm *n_background* servers to a writer lock, then time *repeats* cold clients.

    CORE262-01: each measured client also calls ``trw_session_start`` and so
    becomes a writer itself. Left alive across repeats (the pre-fix
    behaviour), repeat 2 of a 3-repeat arm ran against N+1 writers and repeat
    3 against N+2, while the record's ``writer_census`` field kept reporting
    only the pre-loop value -- the arm never measured the fixed writer count
    it claimed to. Reaping the measured client via ``harness.reap_one``
    immediately after its call, and re-asserting the census before every
    repeat (not just once before the loop), is what keeps every repeat
    comparable.
    """
    started = time.monotonic()
    harness = StdioServerHarness(project, user_dir, stderr_dir / label)
    try:
        for index in range(n_background):
            server, _ = harness.cold_initialize(f"{label}-bg-{index}")
            harness.call(server, "trw_session_start", {})
        census = settle_writer_census(project, n_background)
        # Fails BEFORE any timing is recorded: a census that disagrees with N
        # means the arm never established the contention it claims to measure.
        assert len(census) == n_background, (
            f"{label}: writer census {census} != {n_background} warmed background servers. "
            "Check the server stderr for writer_registry_pid_reuse_ghost: the census excludes a "
            "lock whose /proc birth time postdates its registration epoch, and a /proc dentry that "
            "is evicted and re-instantiated reports a fresh ctime for a process that never restarted."
        )
        recorded_wal = wal_bytes(project)
        initializes: list[float] = []
        session_starts: list[float] = []
        deferral_sets: list[tuple[str, ...]] = []
        for repeat in range(repeats):
            # CORE262-01: re-assert the census before EVERY repeat, not once
            # before the loop -- a prior repeat's measured client that failed
            # to reap would otherwise inflate this repeat's writer population
            # silently.
            pre_repeat = settle_writer_census(project, n_background, timeout_s=5.0)
            assert len(pre_repeat) == n_background, (
                f"{label} repeat {repeat}: writer census {pre_repeat} != {n_background} "
                "before this repeat's measured client -- a prior measured client was not reaped"
            )
            server, initialize_ms = harness.cold_initialize(f"{label}-measured-{repeat}")
            session_ms, payload = harness.call(server, "trw_session_start", {})
            harness.reap_one(server)
            initializes.append(initialize_ms)
            session_starts.append(session_ms)
            deferral_sets.append(deferral_names(payload.get("deferred")))
        # CORE262-02: compare PER-REPEAT, never union. A union of different
        # per-repeat deferral sets can equal another arm's union by
        # coincidence while individual repeats did different amounts of work,
        # defeating the anti-tautology check in
        # test_deferral_set_is_invariant_across_writer_counts.
        baseline_deferrals = deferral_sets[0]
        for repeat, names in enumerate(deferral_sets):
            assert names == baseline_deferrals, (
                f"{label} repeat {repeat}: deferred {names!r} differs from repeat 0's "
                f"{baseline_deferrals!r}; within-arm deferral variability makes the repeats "
                "incomparable and the latency ratio a tautology"
            )
    finally:
        harness.teardown()
    return ArmRecord(
        label=label,
        n_background=n_background,
        wal_bytes=recorded_wal,
        writer_census=len(census),
        initialize_ms=tuple(initializes),
        session_start_ms=tuple(session_starts),
        deferral_names=baseline_deferrals,
        wall_s=time.monotonic() - started,
        store_path=str(project / ".trw" / "memory" / "memory.db"),
    )


# CORE262-08: the grower MUST be a separately spawned process, not a second
# in-process connection. The PRD's own field measurement used two helper
# SCRIPTS under a run scratch dir; a same-process ``sqlite3.Connection`` proves
# only that two connections in ONE interpreter can grow a WAL, not that a
# genuinely external writer can while this benchmark's own reader pins it --
# which is the shape a real multi-process contention incident has.
_WAL_GROWER_SCRIPT = """
import os
import sqlite3
import sys

db_path, target_bytes = sys.argv[1], int(sys.argv[2])
wal_path = db_path + "-wal"
conn = sqlite3.connect(db_path, timeout=30)
try:
    conn.execute("CREATE TABLE IF NOT EXISTS _walpad(id INTEGER PRIMARY KEY, blob BLOB)")
    chunk = os.urandom(1 << 20)
    while not (os.path.exists(wal_path) and os.path.getsize(wal_path) >= target_bytes):
        conn.execute("INSERT INTO _walpad(blob) VALUES (?)", (chunk,))
        conn.commit()
    conn.execute("DROP TABLE _walpad")
    conn.commit()
finally:
    conn.close()
"""


def grow_pinned_wal(project: Path, target_bytes: int) -> sqlite3.Connection:
    """Grow the WAL past *target_bytes* from a SEPARATE process, pinned open here.

    Returns the holder connection; the caller closes it. A checkpoint cannot
    reset or truncate a WAL while a reader holds a snapshot, which is what makes
    the byte count survive into the measured arm rather than collapsing the
    moment a server runs its PASSIVE checkpoint. The parent process holds the
    reader transaction throughout; the growing itself happens in a child whose
    pid is asserted distinct from ours (CORE262-08).
    """
    db = project / ".trw" / "memory" / "memory.db"
    if not db.exists():
        raise AssertionError(f"no store at {db}; an N arm must run before the WAL arm")
    holder = sqlite3.connect(str(db), isolation_level=None, timeout=30)
    holder.execute("BEGIN")
    holder.execute("SELECT count(*) FROM sqlite_master").fetchone()
    grower = subprocess.Popen(
        [sys.executable, "-c", _WAL_GROWER_SCRIPT, str(db), str(target_bytes)],
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert grower.pid != os.getpid(), "the WAL grower must run in a separately spawned process"
    _stdout, stderr = grower.communicate(timeout=60)
    assert grower.returncode == 0, (
        f"WAL grower subprocess (pid {grower.pid}) exited {grower.returncode}: {stderr.decode('utf-8', 'replace')}"
    )
    return holder


def seed_pending_records(project: Path, count: int) -> list[str]:
    """Seed *count* synthetic journal records through the PRODUCTION journal writer."""
    from trw_mcp.state import learn_journal

    trw_dir = project / ".trw"
    ids: list[str] = []
    for index in range(count):
        rng = random.Random(index * 7919 + 13)
        topic = " ".join(rng.sample(_SYNTHETIC_WORDS, 6))
        learning_id = f"L-bench{index:04d}"
        payload: dict[str, object] = {
            "summary": f"When {topic} fails, check {topic.split()[0]} first",
            "detail": f"Synthetic benchmark record {index}: {topic}.",
            "tags": ["benchmark", "journal-drain"],
            "impact": 0.3,
            "type": "pattern",
            "confidence": "low",
            "evidence": [],
            "source_type": "agent",
        }
        assert learn_journal.journal_pending(trw_dir, learning_id, payload) is not None
        ids.append(learning_id)
    return ids


@dataclass(frozen=True)
class PendingArmRecord:
    """One pending-journal arm: K seeded records, one measured client."""

    seeded_records: int
    initialize_ms: float
    first_session_start_ms: float
    drain_rounds: int
    remaining_pending: int


def measure_pending_arm(root: Path, records: int, *, max_rounds: int = 20) -> PendingArmRecord:
    """Seed *records* pending journal entries into a FRESH store and measure one client.

    A control arm at K=1 and a measured arm at K=27 are run the same way so the
    one-time costs a first ``trw_session_start`` pays -- embedding-model
    initialization above all -- appear on BOTH sides and cancel. Without the
    control the case would be measuring the model loader, and would report a
    drain regression on any host where that load is slow.
    """
    from trw_mcp.state import learn_journal

    root.mkdir(parents=True, exist_ok=True)
    project, user_dir = build_temp_project(root)
    trw_dir = project / ".trw"
    seeded = seed_pending_records(project, records)
    assert learn_journal.pending_count(trw_dir) == len(seeded) == records
    harness = StdioServerHarness(project, user_dir, root / "stderr")
    try:
        server, initialize_ms = harness.cold_initialize("pending-measured")
        session_ms, _payload = harness.call(server, "trw_session_start", {})
        rounds = 0
        while learn_journal.pending_count(trw_dir) and rounds < max_rounds:
            harness.call(server, "trw_session_start", {})
            rounds += 1
        remaining = learn_journal.pending_count(trw_dir)
    finally:
        harness.teardown()
    return PendingArmRecord(
        seeded_records=len(seeded),
        initialize_ms=initialize_ms,
        first_session_start_ms=session_ms,
        drain_rounds=rounds,
        remaining_pending=remaining,
    )


def measure_pending_control_repeats(root: Path, records: int, *, repeats: int = 2) -> tuple[PendingArmRecord, ...]:
    """Run the control arm *repeats* times so host-variance is MEASURED, not guessed.

    CORE262-07: the drain bound's host-variance slack must be derived from an
    actual measurement (mirroring PRD-FIX-130-FR04's "fixture's measured
    zero-pending overhead"), not a fixed, unsubstantiated constant. Each
    repeat runs the identical K=*records* case against a fresh store, so the
    spread across repeats is attributable to host jitter alone.
    """
    return tuple(measure_pending_arm(root / f"control-{index}", records) for index in range(repeats))


def fix_130_budget_ms() -> int | None:
    """Runtime capability probe for the PRD-FIX-130 drain budget.

    Resolves through ``TRWConfig``'s PUBLIC field surface. Reaching into a
    private module for the knob would let the probe answer "present" for an
    internal that is not wired to config at all -- the probe would then be
    reporting on itself. Returns ``None`` when the field is absent; RAISES when
    the field exists but its bound is unreadable, so an indeterminate probe
    FAILS the case rather than reporting a comfortable expected-failure.
    """
    from trw_mcp.models.config import TRWConfig

    info = TRWConfig.model_fields.get(_FIX_130_BUDGET_FIELD)
    if info is None:
        return None
    default = info.default
    if isinstance(default, bool) or not isinstance(default, int) or default < 0:
        raise AssertionError(
            f"{_FIX_130_BUDGET_FIELD} exists but its default {default!r} is not a readable "
            "millisecond bound; the drain surface has moved and this case can decide neither way"
        )
    return default
