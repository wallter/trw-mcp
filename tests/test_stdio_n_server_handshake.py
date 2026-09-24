"""PRD-CORE-262: N-server stdio contention benchmark for the cold handshake.

Spawns N REAL ``trw-mcp`` subprocesses over stdio against one temporary store,
warms each to a writer lock, and times a cold ``initialize`` plus a first
``trw_session_start`` on a fresh client. The property under test is
PRD-CORE-248-FR01 -- nothing opens SQLite, takes a writer lock, checkpoints or
reads the pin store synchronously before the initialize reply -- enforced OUT of
process. ``tests/test_boot_initialize_ordering.py`` already asserts it against a
client built inside the test interpreter, which cannot see process spawn cost,
interpreter import cost, the writer-lock population on the shared store, or a
WAL pinned by a live reader.

The reported incident is asymmetric in exactly the way that matters: a tool call
that overruns is backgrounded and recovers, while a CONNECT that overruns removes
the whole tool surface for the session with no recovery path. So the bound
guarded here is the handshake, not throughput.

Marker placement: this module is listed in ``_SLOW_FILES`` in
``tests/conftest.py``, so it carries ``slow`` additively on top of the default
``integration`` marker and collects ZERO items under ``-m unit``.

Note on FR05: ``tests/test_bootstrap_codex_split.py`` PRE-EXISTED this PRD (426
lines, 21 passing tests). PRD-CORE-262-FR05 INSERTS its attribution test there
and in the new ``tests/test_init_scaffold_containment.py`` sibling; it does not
rewrite the pre-existing module.
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from tests import _stdio_harness
from tests._layout import requires_local_timing
from tests._stdio_benchmark_support import (
    build_temp_project,
    fix_130_budget_ms,
    measure_pending_arm,
    measure_pending_control_repeats,
)
from tests._stdio_harness import (
    ChildLeak,
    HarnessError,
    StdioServerHarness,
    stdio_import_skip_reason,
)
from tests._timing import assert_budget, on_ci_runner

# ── Bounds (module constants, deliberately NOT config fields) ────────────────
# A bound that lives in ``TRWConfig`` can be loosened by a production config
# edit, which would let a deployment change silently disarm a test gate.

# 12.5% of the 120,000 ms client connect ceiling that terminated the reporter's
# session, 12.4x the worst measured median (1,207 ms), and below the 120 s
# pytest timeout in pyproject.toml so a breach fails as an assertion rather than
# as a timeout kill.
_HANDSHAKE_ABS_CEILING_MS = 15_000.0

# The per-case median must ALSO stay at or below this multiple of the N=1
# median, which on the measurement host binds first.
_HANDSHAKE_REL_MULTIPLE = 5.0

# The N=12 median and the pinned-WAL median must each stay at or below this
# multiple of the N=1 median. Measured worst ratio was 1.10. 3.0 rather than 2.0
# because the N=12 arm starts twelve interpreters whose import CPU can overlap
# the measured client's own import on a loaded host, and a tighter bound turns
# host load into a false regression.
_INDEPENDENCE_MAX_RATIO = 3.0

_REPEATS = 3
_WAL_TARGET_BYTES = 64 * 1024 * 1024
_PENDING_RECORDS = 27
_PENDING_CONTROL_RECORDS = 1
# Measured at HEAD before PRD-FIX-130: 33,552 ms for 27 records against a 248 MB
# store. Named in the expected-failure reason so the gap stays visible.
_MEASURED_PENDING_DRAIN_MS = 33_552
# CORE262-07: no fixed slack constant. The host-variance allowance is DERIVED
# at runtime from two repeats of the K=1 control arm (see
# ``measure_pending_control_repeats``) -- the same "measured zero-pending
# overhead" technique PRD-FIX-130-FR04 uses -- rather than an unsubstantiated
# 5,000 ms guess.
_PENDING_DRAIN_SLACK_FLOOR_MS = 1.0  # avoids a literal-zero tolerance if two repeats tie exactly
_PER_ARM_WALL_CEILING_S = (
    90.0  # measured 2026-09-05: n12 = 49.2 s (12 sequential warm-ups at ~4 s each); 45 s was under the measured floor
)
#: PLATFORM-AWARE, for the same reason the hook budgets are: this bounds a
#: benchmark that spawns N stdio servers, and macOS charges ~10x for a process
#: spawn. Measured 2026-09-17 on this arm64 Mac, same module, four conditions:
#: 139.7 s running alone; 172.1 s under `-n 4` with only its own siblings;
#: 196.5 s and 216.5 s inside the full `-n 4` suite with another agent's suite
#: on the box. The Linux ceiling is untouched and is where the NFR is
#: calibrated; the Darwin value is the worst measurement with ~40% margin, so a
#: real regression in the handshake still fails it while box contention does
#: not. If you are raising this again, measure the module ALONE first -- a
#: number taken under load is the box's, not the server's.
_MODULE_WALL_CEILING_S = 300.0 if sys.platform == "darwin" else 180.0

_SKIP_REASON = stdio_import_skip_reason()

pytestmark = [
    pytest.mark.timeout(600),
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
    # PRD-QUAL-141: the module-wide timing skip that used to live here is gone.
    # The tests in this module that assert a fixed wall-clock ceiling measured
    # against THIS box (calibrated on a known local Mac; see
    # _MODULE_WALL_CEILING_S above) now carry ``requires_local_timing``
    # individually as ``*_budget`` tests, so a CI runner (not that box) skips
    # only those and keeps every deterministic assertion in the gating suite.
    # The autouse module-wall-budget fixture below enforces the same ceiling
    # but bypasses it on a CI runner directly (``on_ci_runner()``), since it
    # measures across the whole module rather than one test.
    # Keep the WHOLE module on ONE xdist worker (`--dist loadgroup` is in
    # addopts). Without a group, its tests scatter and EVERY worker that
    # receives one re-runs the module-scoped ``benchmark`` fixture: the N/WAL
    # arm sweep ran four times CONCURRENTLY in a `-n 4` run on 2026-09-17, the
    # four copies contended with each other, and the module-wall finalizer
    # failed on four workers at 191-196 s against the 180 s ceiling. The same
    # module measured 139.7 s when it ran as one unit. The budget below is
    # therefore untouched -- what was wrong was measuring four concurrent
    # copies of a server benchmark and calling the result NFR01. Same remedy
    # the sibling latency modules already use (`core_247_hook_latency`,
    # `degenerate_result_latency`).
    pytest.mark.xdist_group(name="stdio_n_server_handshake"),
]


@pytest.fixture(scope="module", autouse=True)
def _enforce_module_wall_budget() -> Iterator[None]:
    """NFR01/CORE262-06: bound TOTAL module wall time, not just the arm sweep.

    ``BenchmarkResult.module_wall_s`` (used by
    ``test_module_budget_and_marker_placement`` below) only covers the
    ``benchmark`` fixture's own body -- the N/WAL arm sweep -- and therefore
    excludes the pending-drain case and every teardown/fault-injection test in
    this module. Autouse + module scope means this fixture's setup runs before
    the module's FIRST test and its finalizer runs after the LAST, which is
    the only way to bound wall time for the WHOLE module (every test and every
    fixture phase) against NFR01's 180 s ceiling.
    """
    started = time.monotonic()
    yield
    if on_ci_runner():
        # This fixture spans the WHOLE module (every test and fixture phase),
        # not one test, so it cannot be skipped via ``requires_local_timing``
        # on an individual item; bypass the same way that marker's policy
        # would on a CI runner (not the box this ceiling is calibrated for).
        return
    elapsed = time.monotonic() - started
    assert_budget("module_wall_time", elapsed, _MODULE_WALL_CEILING_S, "s")


# FR01's N-server/WAL-contention benchmark (the ``benchmark`` fixture and its
# 9 dependent tests below and further down: cold-initialize independence,
# deferral-set invariance, session_start-latency-on-undeferred-arm, the module
# wall-budget twin, the no-leak/no-live-store-touch check, and the
# no-environment-content check) is DELETED (PRD-CORE-280 slice e, batch 23b).
#
# The incident it guarded -- many local ``trw-mcp`` processes each opening the
# SAME per-checkout ``memory.db`` and contending for its SQLite writer lock --
# is now architecturally impossible: no ``trw-mcp`` process opens SQLite for a
# migrated checkout at all; every write and read goes through ONE shared
# memory daemon via ``selected_store``. ``grow_pinned_wal`` asserted
# ``.trw/memory/memory.db`` exists after N real stdio servers ran
# ``trw_session_start`` against an unmigrated ``build_temp_project`` fixture --
# it no longer does, because nothing in this process writes it. Rebuilding the
# benchmark against a real daemon would prove the DAEMON's own writer-lock
# behavior, which is trw-memory's concern; the client-side claim PRD-CORE-248-
# FR01 makes (nothing opens SQLite, takes a writer lock, checkpoints, or reads
# the pin store synchronously before the initialize reply) is if anything
# STRONGER now that this process never opens SQLite at all, and remains
# covered in-process by ``tests/test_boot_initialize_ordering.py`` (see this
# module's own docstring for why that coverage does not see process-spawn
# cost -- a claim the daemon split does not change).
#
# ``test_module_budget_and_marker_placement`` below is KEPT, but no longer
# takes the ``benchmark`` fixture: its assertion (module carries ``slow``, not
# ``unit``) never read benchmark data.


# ── FR02 ─────────────────────────────────────────────────────────────────────


def test_harness_reaps_every_child_on_failure(tmp_path: Path) -> None:
    """FR02/NFR02: an induced mid-case failure leaves no child alive."""
    project, user_dir = build_temp_project(tmp_path)
    harness = StdioServerHarness(project, user_dir, tmp_path / "stderr")
    spawned: list[int] = []
    with pytest.raises(RuntimeError, match="induced mid-case failure"):
        try:
            for index in range(2):
                server, _ = harness.cold_initialize(f"reap-{index}")
                harness.call(server, "trw_session_start", {})
                spawned.append(server.pid)
            assert len(harness.live_children()) == 2
            raise RuntimeError("induced mid-case failure")
        finally:
            harness.teardown()

    assert len(spawned) == 2
    assert harness.live_children() == []
    for pid in spawned:
        assert not _stdio_harness.pid_is_live(pid), f"pid {pid} survived teardown"


def test_teardown_survives_a_raising_terminate_and_still_reaps(tmp_path: Path) -> None:
    """FR02: a raising ``terminate()`` must not abandon the remaining children.

    Teardown is fault-TOLERANT on the way down and fault-INTOLERANT at the end:
    errors are collected so the sweep completes, SIGKILL is the fallback, the
    OS-level pid check is authoritative, and the collected error is re-raised. A
    green report over a leaked child is the failure mode this covers.
    """
    project, user_dir = build_temp_project(tmp_path)
    harness = StdioServerHarness(project, user_dir, tmp_path / "stderr")
    first, _ = harness.cold_initialize("raise-0")
    second, _ = harness.cold_initialize("raise-1")
    pids = [first.pid, second.pid]

    def _boom() -> None:
        raise OSError("simulated terminate failure")

    for server in (first, second):
        server.proc.terminate = _boom  # type: ignore[method-assign]

    with pytest.raises(HarnessError) as excinfo:
        harness.teardown()
    assert "simulated terminate failure" in str(excinfo.value)
    assert not isinstance(excinfo.value, ChildLeak), "the SIGKILL fallback did not reap the children"
    for pid in pids:
        assert not _stdio_harness.pid_is_live(pid), f"pid {pid} survived a raising teardown"


def test_teardown_survives_raising_waits_with_multiple_children(tmp_path: Path) -> None:
    """FR02/CORE262-05: BOTH the post-terminate AND post-SIGKILL ``wait()`` can raise.

    HEAD's sole fault-injection test (above) only replaces ``terminate()``; no
    test makes either ``wait()`` call raise, so regressing the wait-error
    continuation path -- collect and keep going, SIGKILL as the fallback, the
    OS-level pid check as the authority -- would leave every existing test
    green. Two children exercise both raising shapes at once: one whose
    ``wait()`` raises on BOTH calls (post-terminate and post-SIGKILL), and one
    whose ``wait()`` raises only on the first call and succeeds on the second
    (the SIGKILL-recovers case; the second call is only reachable once the
    first has raised).
    """
    project, user_dir = build_temp_project(tmp_path)
    harness = StdioServerHarness(project, user_dir, tmp_path / "stderr")
    both_raise, _ = harness.cold_initialize("wait-raises-both")
    first_raises, _ = harness.cold_initialize("wait-raises-first-only")
    pids = [both_raise.pid, first_raises.pid]

    # Each wrapper performs the REAL wait() first (so the process is actually
    # reaped, avoiding a race against the OS in the assertions below) and THEN
    # raises -- modelling a wait() call that reports failure even though the
    # child did in fact exit, which is the shape ``_stop`` must not lose track
    # of a sibling child over.
    real_wait_both = both_raise.proc.wait

    def _always_raise(timeout: float | None = None) -> int:
        with suppress(Exception):
            real_wait_both(timeout=timeout)
        raise OSError("simulated wait failure (both calls)")

    real_wait_first = first_raises.proc.wait
    call_count = {"n": 0}

    def _raise_once_then_delegate(timeout: float | None = None) -> int:
        call_count["n"] += 1
        if call_count["n"] == 1:
            with suppress(Exception):
                real_wait_first(timeout=timeout)
            raise OSError("simulated wait failure (first call only)")
        return real_wait_first(timeout=timeout)

    both_raise.proc.wait = _always_raise  # type: ignore[method-assign]
    first_raises.proc.wait = _raise_once_then_delegate  # type: ignore[method-assign]

    with pytest.raises(HarnessError) as excinfo:
        harness.teardown()
    assert not isinstance(excinfo.value, ChildLeak), "the SIGKILL fallback did not reap every child"
    assert "simulated wait failure (both calls)" in str(excinfo.value)
    assert "simulated wait failure (first call only)" in str(excinfo.value)
    for pid in pids:
        assert not _stdio_harness.pid_is_live(pid), f"pid {pid} survived teardown despite a raising wait()"
    assert harness.live_children() == []


# ── FR03 ─────────────────────────────────────────────────────────────────────


def _measure_pending_drain(tmp_path: Path) -> tuple[float | None, Any, Any, float]:
    """Shared setup+operation for the pending-drain tests (real server spawns).

    The claim under test is that the first ``trw_session_start`` is bounded
    INDEPENDENT of K, so the measured K=27 arm is compared against a control arm
    run identically at K=1 rather than against an absolute number: both pay the
    one-time embedding-model initialization a first learn triggers, so it
    cancels and what remains is the cost of the extra 26 records.
    """
    budget_ms = fix_130_budget_ms()
    # CORE262-07: two control repeats give an ACTUAL measured host-variance
    # figure instead of an unsubstantiated flat constant -- the same
    # "measured zero-pending overhead" technique PRD-FIX-130-FR04 uses for its
    # own bound. ``control`` (the first repeat) still supplies the baseline
    # the K=27 arm is compared against.
    control_repeats = measure_pending_control_repeats(tmp_path / "control", _PENDING_CONTROL_RECORDS, repeats=2)
    control = control_repeats[0]
    measured_host_variance_ms = abs(
        control_repeats[0].first_session_start_ms - control_repeats[-1].first_session_start_ms
    )
    slack_ms = max(measured_host_variance_ms, _PENDING_DRAIN_SLACK_FLOOR_MS)
    measured = measure_pending_arm(tmp_path / "measured", _PENDING_RECORDS)

    print(
        json.dumps(
            {
                "label": "pending_drain",
                "control_records": control.seeded_records,
                "control_first_session_start_ms": round(control.first_session_start_ms, 1),
                "control_repeat_session_start_ms": [round(r.first_session_start_ms, 1) for r in control_repeats],
                "measured_host_variance_ms": round(measured_host_variance_ms, 1),
                "slack_ms": round(slack_ms, 1),
                "seeded_records": measured.seeded_records,
                "initialize_ms": round(measured.initialize_ms, 1),
                "first_session_start_ms": round(measured.first_session_start_ms, 1),
                "k_delta_ms": round(measured.first_session_start_ms - control.first_session_start_ms, 1),
                "drain_rounds": measured.drain_rounds,
                "remaining_pending": measured.remaining_pending,
                "fix_130_budget_ms": budget_ms,
            }
        )
    )
    return budget_ms, control, measured, slack_ms


def test_pending_drain_case_tracks_fix_130(tmp_path: Path) -> None:
    """FR03: every seeded pending record is consumed where the PRD-FIX-130 budget exists.

    No branch reports success without a measurement behind it: present asserts
    full consumption, absent reports expected-failure naming PRD-FIX-130 and
    the measured cost, and an indeterminate probe raises out of
    ``fix_130_budget_ms`` and FAILS the case.
    """
    budget_ms, _control, measured, _slack_ms = _measure_pending_drain(tmp_path)

    if budget_ms is None:
        pytest.xfail(
            "PRD-FIX-130 [planned] is absent from this build: learn_journal_drain_budget_ms is not a "
            "TRWConfig field, so the first trw_session_start is unbounded in K. Measured at HEAD: "
            f"{_MEASURED_PENDING_DRAIN_MS} ms for K={_PENDING_RECORDS} against a 248 MB store."
        )

    consumed_records = measured.seeded_records - measured.remaining_pending
    assert consumed_records == measured.seeded_records, (
        f"{measured.remaining_pending} of {measured.seeded_records} seeded records were never "
        f"consumed after {measured.drain_rounds} sweeps"
    )


@requires_local_timing
def test_pending_drain_case_tracks_fix_130_budget(tmp_path: Path) -> None:
    """FR03: assert the PRD-FIX-130 bound where the budget exists, report xfail where it does not."""
    budget_ms, control, measured, slack_ms = _measure_pending_drain(tmp_path)

    assert_budget("pending_drain_initialize", measured.initialize_ms, _HANDSHAKE_ABS_CEILING_MS, "ms")

    if budget_ms is None:
        pytest.xfail(
            "PRD-FIX-130 [planned] is absent from this build: learn_journal_drain_budget_ms is not a "
            "TRWConfig field, so the first trw_session_start is unbounded in K. Measured at HEAD: "
            f"{_MEASURED_PENDING_DRAIN_MS} ms for K={_PENDING_RECORDS} against a 248 MB store."
        )

    bound_ms = control.first_session_start_ms + budget_ms + slack_ms
    assert_budget("pending_drain_first_session_start", measured.first_session_start_ms, bound_ms, "ms")


# ── NFR01 / NFR02 / NFR03 / NFR04 ────────────────────────────────────────────


def test_module_budget_and_marker_placement() -> None:
    """NFR01: the module stays out of the fast lane."""
    from tests.conftest import _SLOW_FILES, _UNIT_FILES

    assert "test_stdio_n_server_handshake.py" in _SLOW_FILES
    assert "test_stdio_n_server_handshake.py" not in _UNIT_FILES


def test_no_process_or_lock_leak_and_no_live_store_touch(tmp_path: Path) -> None:
    """NFR02: no child survives and every resolved root is under the tmp dir.

    PRD-CORE-280 slice e (batch 23b): built its own ``project``/``user_dir``
    via ``build_temp_project`` instead of reading them off the deleted
    ``benchmark`` fixture -- this check is about child env var isolation, not
    the N-server/WAL contention benchmark that fixture ran.
    """
    project, user_dir = build_temp_project(tmp_path)
    harness = StdioServerHarness(project, user_dir, tmp_path / "stderr")
    env = harness.child_env("probe")
    assert Path(env["TRW_PROJECT_ROOT"]) == project
    assert Path(env["TRW_USER_DIR"]) == user_dir
    assert env["TRW_PROJECT_ROOT"].startswith(str(tmp_path))
    assert env["TRW_USER_DIR"].startswith(str(tmp_path))
    # No inherited TRW_* root can slip a second store in behind these two.
    assert {key for key in env if key.startswith("TRW_")} == {
        "TRW_PROJECT_ROOT",
        "TRW_USER_DIR",
        "TRW_SESSION_ID",
        "TRW_HOT_PATH_STRICT",
    }


def test_skip_reason_is_visible_and_narrow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR03: an import failure skips and names it; every other failure FAILS."""
    assert stdio_import_skip_reason() is None

    monkeypatch.setattr(_stdio_harness.importlib.util, "find_spec", lambda name: None)
    reason = stdio_import_skip_reason()
    assert reason is not None and "trw_mcp.server" in reason
    monkeypatch.undo()

    # A child that exits non-zero is a FAILURE, never a skip.
    project, user_dir = build_temp_project(tmp_path)
    monkeypatch.setattr(_stdio_harness, "_SERVER_ARGV", ("-c", "import sys; sys.exit(3)"))
    harness = StdioServerHarness(project, user_dir, tmp_path / "stderr")
    try:
        with pytest.raises(HarnessError):
            harness.cold_initialize("broken")
    finally:
        harness.teardown()


def test_skip_reason_names_missing_stdio_transport_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR03/CORE262-09: a PRESENT ``fastmcp.client.transports`` missing the class must skip and name it.

    HEAD's probe stopped at ``find_spec``, which only proves the module NAME
    resolves -- a module that imports fine but no longer defines
    ``StdioTransport`` (renamed, moved, or dropped upstream) is exactly the
    "stdio contract moved" case NFR03 exists to surface, and the pre-fix probe
    never noticed.
    """
    assert stdio_import_skip_reason() is None

    class _FakeTransportsModule:
        """A module that imports successfully but has no StdioTransport."""

    real_import_module = _stdio_harness.importlib.import_module

    def _fake_import_module(name: str) -> Any:
        if name == _stdio_harness._TRANSPORT_MODULE:
            return _FakeTransportsModule()
        return real_import_module(name)

    monkeypatch.setattr(_stdio_harness.importlib, "import_module", _fake_import_module)
    reason = stdio_import_skip_reason()
    assert reason is not None and "StdioTransport" in reason


def test_skip_reason_names_transport_module_import_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """NFR03/CORE262-09: a transports module that RAISES during import must skip and name it, not be missed."""
    assert stdio_import_skip_reason() is None

    real_import_module = _stdio_harness.importlib.import_module

    def _boom(name: str) -> Any:
        if name == _stdio_harness._TRANSPORT_MODULE:
            raise ImportError("simulated transports import failure")
        return real_import_module(name)

    monkeypatch.setattr(_stdio_harness.importlib, "import_module", _boom)
    reason = stdio_import_skip_reason()
    assert reason is not None and _stdio_harness._TRANSPORT_MODULE in reason


# test_timing_records_carry_no_environment_content DELETED (batch 23b): it
# scanned ``ArmRecord.as_dict()`` output from the deleted N-server/WAL
# benchmark for leaked environment content. ``ArmRecord``/``RECORD_FIELDS``
# were deleted with the benchmark; the property (no timing record leaks an
# environment value) has no producer left to check.
