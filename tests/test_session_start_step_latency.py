"""PRD-FIX-084: per-step latency telemetry on trw_session_start.

The five regressions of the "step in step_sanitize_and_maintain
accidentally O(corpus)" class (commits c7ff20f84, ba328b177, d67ad5651,
27e4e4562, a65427847) were each only diagnosable via py-spy on a live
server because end-to-end latency was visible but per-step duration was
not. session_start_ok now carries step_durations_ms so the slow step
name is in the log line.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests._layout import requires_local_timing
from tests._timing import assert_budget
from tests.conftest import extract_tool_fn, make_test_server

#: PRD-CORE-280 slice e (batch 23b): ``_run_learn_journal_drain``'s replay now
#: DOES go through ``_store_selection.selected_store`` (via ``store_learning``),
#: so the drain-latency fixtures route through ``fake_memory_store`` (instant,
#: in-memory) instead of a real daemon -- the daemon's ~230ms/write RPC cost
#: would blow every ms budget these NFR tests assert, for reasons that have
#: nothing to do with the drain logic under test. ``_fixture_trw_dir`` seeds
#: the synthetic corpus directly into the fake store's ``rows`` dict (bypassing
#: even the fake's ``.put()`` call overhead) since the row COUNT, not the write
#: path, is what makes the O(rows) vs O(records) difference measurable.


def _get_session_start_fn() -> Any:
    """Extract the trw_session_start tool function via shared conftest helpers."""
    return extract_tool_fn(make_test_server("ceremony"), "trw_session_start")


def test_session_start_emits_step_durations_ms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """trw_session_start result includes step_durations_ms with all named keys."""
    fn = _get_session_start_fn()

    # Run the function. Many steps may exit fast; what matters is that
    # the result includes the step_durations_ms key with float values.
    # verbose=True: step_durations_ms is a diagnostic sub-block that
    # compact-by-default (PRD-IMPROVE-MCP-04) folds into health_summary; the
    # full per-step telemetry is only returned in verbose mode.
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)

    assert "step_durations_ms" in result, (
        f"trw_session_start result must include step_durations_ms; got keys: {sorted(result.keys())}"
    )
    durations = result["step_durations_ms"]
    assert isinstance(durations, dict)

    # Every named step that did not partial-fail should have a duration.
    expected_keys = {
        "recall",
        "run_resolve",
        "surface_stamp",
        "log_event",
        "telemetry",
        "counter",
        "sanitize_maintain",
        "assertion_health",
        "finalize",
        "total",
    }
    present_keys = set(durations.keys())
    assert "total" in present_keys, "total must always be recorded"
    # Most other steps may legitimately be absent under partial-failure;
    # but in a clean run with no errors, all named steps run.
    if not result.get("errors"):
        missing = expected_keys - present_keys
        assert not missing, f"Clean session_start should record all named steps; missing: {missing}"

    # Every duration is a non-negative float.
    for key, value in durations.items():
        assert isinstance(value, (int, float)), f"{key} duration not numeric: {value!r}"
        assert float(value) >= 0.0, f"{key} duration negative: {value}"


def test_session_start_total_is_at_least_sum_of_named_steps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """total_ms must be >= sum of the named step durations (within tolerance).

    They should be approximately equal in a clean run; total may be
    slightly larger because some bookkeeping happens outside the
    timed blocks (e.g. assertion health, ceremony status injection,
    deferral checks). It must never be smaller.
    """
    fn = _get_session_start_fn()
    # verbose=True so the diagnostic step_durations_ms block is returned
    # (compact-by-default folds it into health_summary — PRD-IMPROVE-MCP-04).
    result: dict[str, Any] = fn(ctx=None, query="*", verbose=True)
    durations = result["step_durations_ms"]
    if "total" not in durations:
        pytest.skip("total not recorded (partial failure path)")

    total = float(durations["total"])
    named_sum = sum(
        float(durations[k])
        for k in (
            "recall",
            "run_resolve",
            "surface_stamp",
            "log_event",
            "telemetry",
            "counter",
            "sanitize_maintain",
        )
        if k in durations
    )
    # total must include the named steps. Allow 1ms slack for floating point.
    assert total + 1.0 >= named_sum, (
        f"total ({total:.2f} ms) < sum of named steps ({named_sum:.2f} ms); durations: {durations}"
    )


def test_session_start_step_durations_logged_with_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """session_start_ok event payload includes step_durations_ms field."""
    from structlog.testing import capture_logs

    fn = _get_session_start_fn()

    with capture_logs() as logs:
        fn(ctx=None, query="*")

    ok_events = [e for e in logs if e.get("event") == "session_start_ok"]
    assert ok_events, "session_start_ok event must fire on success"
    payload = ok_events[-1]
    assert "step_durations_ms" in payload, (
        f"session_start_ok event must include step_durations_ms; got keys: {sorted(payload.keys())}"
    )
    assert isinstance(payload["step_durations_ms"], dict)
    assert "total" in payload["step_durations_ms"]


def test_finalize_and_payload_trim_are_included_in_latency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from trw_mcp.tools import ceremony

    real_finalize = ceremony.finalize_session_start
    real_trim = ceremony.trim_session_start_payload

    def slow_finalize(*args: Any, **kwargs: Any) -> None:
        time.sleep(0.03)
        real_finalize(*args, **kwargs)

    def slow_trim(*args: Any, **kwargs: Any) -> Any:
        time.sleep(0.03)
        return real_trim(*args, **kwargs)

    monkeypatch.setattr(ceremony, "finalize_session_start", slow_finalize)
    monkeypatch.setattr(ceremony, "trim_session_start_payload", slow_trim)

    result: dict[str, Any] = _get_session_start_fn()(ctx=None, query="*", verbose=True)
    durations = result["step_durations_ms"]
    assert float(durations["finalize"]) >= 25.0
    assert float(durations["total"]) >= float(durations["finalize"]) + 25.0


def _collect_warm_session_start_samples() -> list[float]:
    """Call session_start 10 times and return the "warm" (calls 2-10) totals.

    Call 1 is excluded because it pays embedder cold-load and any one-time
    maintenance.
    """
    fn = _get_session_start_fn()

    call_total_ms: list[float] = []
    for _ in range(10):
        # verbose=True surfaces step_durations_ms in the result (compact mode
        # folds it into health_summary — PRD-IMPROVE-MCP-04).
        result: dict[str, Any] = fn(ctx=None, query="warm-perf", verbose=True)
        durations = result.get("step_durations_ms", {})
        if "total" in durations:
            call_total_ms.append(float(durations["total"]))

    return sorted(call_total_ms[1:])


def test_session_start_warm_p95_under_5_seconds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup sanity: enough warm samples are collected for a p95 measurement.

    The actual SLO regression check (p95 < 5000 ms) is the ``_budget`` twin
    below — this is the durable defense against the regression class that ate
    2026-05-03, minus the host-resource threshold.
    """
    warm = _collect_warm_session_start_samples()
    assert len(warm) >= 5, f"need at least 5 warm samples; got {len(warm)}"


@requires_local_timing
def test_session_start_warm_p95_under_5_seconds_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SLO regression test: warm trw_session_start p95 < 5000 ms.

    The threshold is intentionally generous (5 s) -- in steady state the
    measured value is ~1 s. The test fires on regressions that push the
    warm budget into the danger zone.
    """
    warm = _collect_warm_session_start_samples()
    p95_index = max(0, int(0.95 * len(warm)) - 1)
    p95 = warm[p95_index]
    assert_budget("session_start_warm_p95", p95, 5000.0, "ms")


# ---------------------------------------------------------------------------
# PRD-FIX-130-FR04 / NFR01: the drain's wall time must not track the backlog
#
# Module marker: this file is listed in conftest._SLOW_FILES, so everything here
# carries the ``slow`` marker and is excluded from the unit-marked fast loop.
# ---------------------------------------------------------------------------

_FIXTURE_ROWS = 2000
_BUDGET_MS = 250


@pytest.fixture()
def fake_store_router(monkeypatch: pytest.MonkeyPatch) -> dict[Path, Any]:
    """One ``FakeMemoryStore`` per distinct ``trw_dir``, keyed on identity.

    The shared ``fake_memory_store`` fixture routes every ``trw_dir`` to the
    SAME store instance, which would collapse this file's several
    independent per-arm fixture directories (k0, k1-a, k5, k50, ...) into one
    shared corpus and one shared pending-id namespace -- exactly the
    cross-arm contamination the K-in-{0,5,50} backlog arms must not have.
    """
    from tests._memory_fixtures import FAKE_NAMESPACE
    from tests._memory_store_fake import FakeMemoryStore
    from trw_mcp.state import _store_selection

    stores: dict[Path, Any] = {}

    def _select(trw_dir: Path) -> tuple[Any, str]:
        return stores.setdefault(trw_dir, FakeMemoryStore()), FAKE_NAMESPACE

    monkeypatch.setattr(_store_selection, "selected_store", _select)
    return stores


def _fixture_trw_dir(root: Path, stores: dict[Path, Any], rows: int = _FIXTURE_ROWS) -> Path:
    """A synthetic store of a few thousand active rows.

    The row count is the point: the per-record active-set materialization FR03
    removes is O(rows), so a store this size makes the difference between
    per-record and per-sweep work measurable without any artificial sleep.
    Seeded directly into the fake's ``rows`` dict, bypassing even the fake's
    ``.put()`` call overhead, since row COUNT is what is under test.
    """
    from trw_memory.models.memory import MemoryEntry

    from tests._memory_fixtures import FAKE_NAMESPACE
    from tests._memory_store_fake import FakeMemoryStore

    trw_dir = root / ".trw"
    (trw_dir / "learnings" / "entries").mkdir(parents=True)
    store = stores.setdefault(trw_dir, FakeMemoryStore())
    for i in range(rows):
        entry = MemoryEntry(
            id=f"L-fix{i:05d}",
            content=f"synthetic fixture row number {i} for the bounded drain latency test",
            detail=f"detail body for synthetic fixture row {i}",
            namespace=FAKE_NAMESPACE,
            importance=0.5,
        )
        store.rows[(FAKE_NAMESPACE, entry.id)] = entry
    return trw_dir


def _seed_pending(trw_dir: Path, count: int) -> list[str]:
    from trw_mcp.state import learn_journal

    ids: list[str] = []
    for i in range(count):
        learning_id = f"L-pend{i:03d}"
        learn_journal.journal_pending(
            trw_dir,
            learning_id,
            {
                "summary": f"bounded drain latency probe record {i:03d} with a distinct summary body",
                "detail": f"detail body for bounded drain latency probe record {i:03d}",
                "impact": 0.5,
            },
        )
        ids.append(learning_id)
    return ids


def _run_drain(trw_dir: Path, budget_ms: int) -> tuple[float, dict[str, Any]]:
    """Run the inline drain step and return (elapsed_ms, maintenance payload)."""
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    config = TRWConfig(
        embeddings_enabled=False,
        dedup_enabled=False,
        learn_journal_drain_budget_ms=budget_ms,
    )
    maintenance: dict[str, Any] = {}
    started = time.monotonic()
    steps._run_learn_journal_drain(
        trw_dir,
        config,
        maintenance,
    )
    return (time.monotonic() - started) * 1000.0, maintenance


def _join_background(timeout: float = 120.0) -> None:
    """Join the FR02 continuation with an EXPLICIT finite timeout; a hang is a failure."""
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    thread = steps._DRAIN_THREAD
    if thread is not None:
        thread.join(timeout)
        assert not thread.is_alive(), f"background drain still alive after {timeout}s"
    steps._DRAIN_THREAD = None


def _run_pending_backlog_arms(tmp_path: Path, stores: dict[Path, Any]) -> dict[str, Any]:
    """Run the K in {0, 5, 50} backlog arms once; return every measured value
    and every correctness fact, without asserting on any of them.

    Shared by the correctness twin and the ``_budget`` timing twin below, so
    each repeats this exact setup and operation rather than splitting a
    single run's results across two tests.
    """
    from trw_mcp.state import learn_journal
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    steps._DRAIN_THREAD = None

    # Zero-pending overhead on this fixture, measured rather than assumed.
    baseline_dir = _fixture_trw_dir(tmp_path / "k0", stores)
    baseline_ms, baseline_payload = _run_drain(baseline_dir, _BUDGET_MS)
    _join_background()

    # One record's replay on the same fixture, with a budget that cannot bite.
    # This is the bound's only free term, and it is RE-MEASURED before each K arm
    # rather than sampled once up front.
    #
    # Why: the bound is budget + one-record + overhead, every term measured on
    # this box, and the box's contention CHANGES during the run. A single sample
    # taken in a quiet moment and then spent as the margin for an arm that lands
    # in a busy one is not a measurement of the same thing. The earlier
    # worst-of-two-samples version still flaked in a full `make check` survey
    # (K=5 took 543 ms against a 303 ms bound, with one-record measured at 53 ms
    # moments earlier) because the whole trw-mcp suite was running 3.3x slower
    # than in isolation — contention that arrived between the sample and the arm.
    #
    # Re-measuring per arm and keeping the running maximum tracks that drift
    # while keeping every term honest. It cannot rescue the defect this test
    # exists to catch: remove the elapsed-time break and K=50 replays all fifty
    # records inline, an order of magnitude over any per-record margin.
    one_record_pending_counts: list[int] = []

    def _measure_one_record(label: str) -> float:
        one_dir = _fixture_trw_dir(tmp_path / f"k1-{label}", stores)
        _seed_pending(one_dir, 1)
        sample_ms, _ = _run_drain(one_dir, 120_000)
        _join_background()
        one_record_pending_counts.append(learn_journal.pending_count(one_dir))
        return sample_ms

    one_record_ms = max(_measure_one_record("a"), _measure_one_record("b"))
    measured: dict[int, float] = {0: baseline_ms}
    bounds: dict[int, float] = {}
    payloads: dict[int, dict[str, Any]] = {}
    pending_left_by_k: dict[int, int] = {}
    unaccounted_by_k: dict[int, list[str]] = {}

    for k in (5, 50):
        one_record_ms = max(one_record_ms, _measure_one_record(f"pre{k}"))
        bounds[k] = baseline_ms + _BUDGET_MS + one_record_ms

        trw_dir = _fixture_trw_dir(tmp_path / f"k{k}", stores)
        ids = _seed_pending(trw_dir, k)
        elapsed_ms, maintenance = _run_drain(trw_dir, _BUDGET_MS)
        measured[k] = elapsed_ms
        payloads[k] = maintenance["pending_learns_replayed"]

        _join_background()
        pending_left_by_k[k] = learn_journal.pending_count(trw_dir)

        from trw_mcp.state.memory_adapter import list_active_learnings

        stored = {str(e.get("id", "")) for e in list_active_learnings(trw_dir)}
        unaccounted_by_k[k] = [lid for lid in ids if lid not in stored]

    return {
        "baseline_ms": baseline_ms,
        "baseline_payload": baseline_payload,
        "one_record_ms": one_record_ms,
        "one_record_pending_counts": one_record_pending_counts,
        "measured": measured,
        "bounds": bounds,
        "payloads": payloads,
        "pending_left_by_k": pending_left_by_k,
        "unaccounted_by_k": unaccounted_by_k,
    }


@pytest.mark.timeout(600)
def test_drain_wall_time_is_bounded_independent_of_pending_backlog(tmp_path: Path, fake_store_router: dict) -> None:
    """FIX130-11 correctness twin: exact ID/count accounting across K in {0, 5, 50}.

    The wall-time bound itself (why the FR01 budget condition exists) is
    asserted by the ``_budget`` twin below.
    """
    run = _run_pending_backlog_arms(tmp_path, fake_store_router)

    assert "pending_learns_replayed" not in run["baseline_payload"], run["baseline_payload"]
    assert all(count == 0 for count in run["one_record_pending_counts"]), run["one_record_pending_counts"]

    for k in (5, 50):
        payload = run["payloads"][k]
        inline = int(payload["replayed_inline"])
        background = int(payload["deferred_to_background"])
        # Exact accounting, not a tautology (FIX130-11): the two counts partition
        # the records the sweep was allowed to attempt, the inline count matches
        # what the driver actually replayed, and at K=50 the budget MUST have cut
        # the sweep short — that is the whole claim FR01 makes.
        assert inline + background == min(k, 50), payload
        assert inline == int(payload["replayed"]), payload
        assert background == int(payload.get("deferred", 0)), payload
        if k == 50:
            assert inline < k, f"the {_BUDGET_MS} ms budget did not bite at K=50: {payload}"
            assert payload.get("budget_exhausted") is True, payload

        assert run["pending_left_by_k"][k] == 0, "records were lost by being deferred"
        assert not run["unaccounted_by_k"][k], f"K={k} left ids unaccounted for: {run['unaccounted_by_k'][k]}"


@pytest.mark.timeout(600)
@requires_local_timing
def test_drain_wall_time_is_bounded_independent_of_pending_backlog_budget(
    tmp_path: Path, fake_store_router: dict
) -> None:
    """The bound holds at K in {0, 5, 50} and its spread does not grow with K.

    REVERT CHECK: this test is why the FR01 budget condition exists. Remove the
    elapsed-time break in ``state/learn_journal.drain_pending`` and the K=50 arm
    replays all fifty records inline, blowing the bound by an order of magnitude.
    """
    run = _run_pending_backlog_arms(tmp_path, fake_store_router)
    measured = run["measured"]
    bounds = run["bounds"]

    for k in (5, 50):
        assert_budget(
            f"drain_wall_time_k{k}",
            measured[k],
            bounds[k],
            "ms",
        )

    # The property a count limit cannot deliver: 10x the backlog is not 10x the
    # inline time. Allow one budget-plus-one-record of slack for measurement noise.
    slack = _BUDGET_MS + run["one_record_ms"]
    assert_budget("drain_wall_time_k50_vs_k5", measured[50], measured[5] + slack, "ms")


@pytest.mark.timeout(600)
def test_zero_budget_defers_all_fifty_and_still_consumes_them(tmp_path: Path, fake_store_router: dict) -> None:
    """FR04 negative arm: budget 0 at K=50 yields 0 inline / 50 background, all consumed."""
    from trw_mcp.state import learn_journal
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    steps._DRAIN_THREAD = None
    trw_dir = _fixture_trw_dir(tmp_path / "k50zero", fake_store_router)
    ids = _seed_pending(trw_dir, 50)

    elapsed_ms, maintenance = _run_drain(trw_dir, 0)

    payload = maintenance["pending_learns_replayed"]
    assert int(payload["replayed_inline"]) == 0, payload
    assert int(payload["deferred_to_background"]) == 50, payload
    assert payload.get("budget_exhausted") is True, payload

    _join_background()
    assert learn_journal.pending_count(trw_dir) == 0

    from trw_mcp.state.memory_adapter import list_active_learnings

    stored = {str(e.get("id", "")) for e in list_active_learnings(trw_dir)}
    assert all(lid in stored for lid in ids), sorted(set(ids) - stored)
    assert elapsed_ms >= 0.0


def test_zero_pending_drain_adds_no_io(tmp_path: Path, fake_store_router: dict) -> None:
    """NFR01: an empty pending directory costs no extra file or database I/O.

    The pending-record iterator is the only I/O the drain step performs before
    its early return, and the budget is resolved strictly after it — so a
    zero-pending sweep scans once, reads no clock, and stops.
    """
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state import learn_journal
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    trw_dir = _fixture_trw_dir(tmp_path / "noio", fake_store_router, rows=10)
    config = TRWConfig(embeddings_enabled=False, dedup_enabled=False, learn_journal_drain_budget_ms=_BUDGET_MS)
    maintenance: dict[str, Any] = {}

    scans: list[int] = []
    clock_reads: list[int] = []
    real_iter = learn_journal._iter_pending_records
    real_monotonic = time.monotonic

    def _counting_iter(*args: Any, **kwargs: Any) -> Any:
        scans.append(1)
        return real_iter(*args, **kwargs)

    def _counting_monotonic() -> float:
        clock_reads.append(1)
        return real_monotonic()

    learn_journal._iter_pending_records = _counting_iter  # type: ignore[assignment]
    learn_journal.time.monotonic = _counting_monotonic  # type: ignore[assignment]
    try:
        steps._run_learn_journal_drain(
            trw_dir,
            config,
            maintenance,
        )
    finally:
        learn_journal.time.monotonic = real_monotonic  # type: ignore[assignment]
        learn_journal._iter_pending_records = real_iter  # type: ignore[assignment]

    assert "pending_learns_replayed" not in maintenance, maintenance
    assert scans == [1], f"the pending directory must be scanned exactly once, got {len(scans)}"
    assert clock_reads == [], "the zero-pending path must not read the clock"


# ---------------------------------------------------------------------------
# PRD-CORE-263-NFR01 — the wiring fixes add no measurable hot-path cost
# ---------------------------------------------------------------------------


def test_wiring_fixes_add_no_measurable_hot_path_cost(tmp_path: Path) -> None:
    """PRD-CORE-263-NFR01 — bounded I/O, no database handle, bounded JSON size growth.

    The only work FR01-FR10 add to the HEALTHY path is a handful of boolean
    flags, three previously-dropped maintenance keys, and one previously-
    conditional file write. This asserts the three bounds the PRD states:

    1. the injected-ids write performs one bounded read and one atomic rewrite,
       and opens no memory-database handle;
    2. the added ``measured`` flags and reasons cost at most 2 percent of the
       healthy payload's four-character JSON size heuristic;
    3. no step gained a database or network call.
    """
    import sqlite3
    from unittest.mock import patch

    from tests._ceremony_helpers import payload_size_units
    from trw_mcp.tools._ceremony_session_start_steps import _MAX_INJECTED_IDS, _write_session_start_ids

    # (1) One bounded read, one atomic rewrite, zero database handles.
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    state_file = trw_dir / "context" / "injected_learning_ids.txt"
    state_file.write_text("".join(f"L-old-{i}\n" for i in range(_MAX_INJECTED_IDS)), encoding="utf-8")

    reads: list[str] = []
    writes: list[str] = []
    real_read, real_write = Path.read_text, Path.write_text

    def _counting_read(self: Path, *a: object, **kw: object) -> str:
        reads.append(self.name)
        return real_read(self, *a, **kw)  # type: ignore[arg-type]

    def _counting_write(self: Path, *a: object, **kw: object) -> int:
        writes.append(self.name)
        return real_write(self, *a, **kw)  # type: ignore[arg-type]

    with (
        patch.object(Path, "read_text", _counting_read),
        patch.object(Path, "write_text", _counting_write),
        patch.object(sqlite3, "connect", side_effect=AssertionError("the injected-ids write must open no database")),
    ):
        _write_session_start_ids(trw_dir, [{"id": "L-new"}])

    assert reads == ["injected_learning_ids.txt"], reads
    assert writes == ["injected_learning_ids.txt.tmp"], writes
    # The cap still holds, so the rewrite stays bounded however long the session
    # history is.
    assert len(state_file.read_text(encoding="utf-8").split()) == _MAX_INJECTED_IDS

    # (2) The added flags cost at most 2 percent of a healthy payload.
    #
    # Measured against the payload the tool ACTUALLY returns, with one stated
    # adjustment: the fixture store is empty, so the real call returns no
    # learnings and the denominator is ~1080 size units — roughly the payload
    # overhead alone. A healthy session on a real store carries the shipped
    # default of eight learnings, which is most of the payload an agent pays
    # for, so the same eight are added to BOTH sides here. Raw numbers on the
    # empty-store payload are asserted separately below as an absolute bound, so
    # neither denominator is doing the work on its own.
    payload = _get_session_start_fn()(ctx=None, query="*", verbose=True)
    empty_after = payload_size_units(payload)
    empty_before = payload_size_units(_strip_263_additions(payload))
    # Absolute bound, denominator-free: the added keys are flags and one small
    # dict, not a new data block.
    assert empty_after - empty_before <= 60, f"added {empty_after - empty_before} four-character size units of payload"

    representative_learnings = [
        {
            "id": f"L-{i:04d}",
            "summary": "A representative learning summary of the length recall returns",
            # Sized from a live measurement rather than guessed: a wildcard
            # recall of the shipped default (8 entries) against this
            # repository's own store on 2026-09-04 estimated 11,388 four-character size units,
            # i.e. ~876 size units per surfaced learning. A fixture with 60-unit
            # learnings would understate the denominator by an order of
            # magnitude and turn this bound into a statement about the fixture.
            "detail": "x " * _MEASURED_SIZE_UNITS_PER_LEARNING * 2,
            "tags": ["trw-mcp", "ceremony", "session-start"],
            "impact": 0.7,
        }
        for i in range(8)
    ]
    with_learnings_after = dict(payload)
    with_learnings_after["learnings"] = representative_learnings
    with_learnings_before = dict(_strip_263_additions(with_learnings_after))  # type: ignore[arg-type]

    before = payload_size_units(with_learnings_before)
    after = payload_size_units(with_learnings_after)
    growth = (after - before) / before
    assert growth <= 0.02, f"healthy payload grew {growth:.1%}, above the 2% bound (before={before}, after={after})"


def _strip_263_additions(node: object) -> object:
    """The payload as it would be WITHOUT this PRD's added healthy-path keys."""
    if isinstance(node, dict):
        return {k: _strip_263_additions(v) for k, v in node.items() if k not in _FR03_ADDED_KEYS}
    if isinstance(node, list):
        return [_strip_263_additions(item) for item in node]
    return node


#: The keys PRD-CORE-263 adds to a HEALTHY payload. ``reason`` / ``status`` /
#: ``unmeasured`` appear only on a degraded or unmeasured result, whose growth
#: NFR01 deliberately leaves unbounded — that growth IS the information.
_FR03_ADDED_KEYS = frozenset({"measured", "unmeasured", "embeddings_coverage_ratio"})

#: Historical four-character size heuristic for one surfaced learning, measured on 2026-09-04 with a
#: wildcard recall of the shipped default against this repository's live store
#: (8 entries, 11,388 estimated size units). Used only to size the NFR01 denominator.
_MEASURED_SIZE_UNITS_PER_LEARNING = 876


@pytest.mark.timeout(600)
def test_trw_session_start_itself_drains_the_journal_and_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake_store_router: dict
) -> None:
    """FIX130-11: the bound is claimed for ``trw_session_start``, so drive THAT.

    Every other test in this block calls ``_run_learn_journal_drain`` directly.
    That proves the sub-step works and proves nothing about whether session_start
    still reaches it — delete the drain from the step table and they all stay
    green. This one goes through the registered tool and asserts the counts
    arrive in the RESPONSE, which additionally pins PC-8: the new keys are nested
    inside the already-allowlisted ``pending_learns_replayed`` mapping, so a
    top-level key would have been silently dropped by
    ``MAINTENANCE_PROPAGATED_KEYS``.
    """
    from trw_mcp.models import config as config_mod
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.state import _paths as paths_mod
    from trw_mcp.state import learn_journal
    from trw_mcp.tools import _ceremony_maintenance_steps as steps

    steps._DRAIN_THREAD = None
    trw_dir = _fixture_trw_dir(tmp_path / "wired", fake_store_router, rows=50)
    ids = _seed_pending(trw_dir, 4)
    monkeypatch.setattr(paths_mod, "resolve_trw_dir", lambda *_a, **_kw: trw_dir)
    monkeypatch.setattr("trw_mcp.tools.ceremony.resolve_trw_dir", lambda *_a, **_kw: trw_dir, raising=False)
    # Dedup OFF for this assertion only. The four seeded summaries differ by an
    # index digit, so with an embedder available (which depends on which other
    # test warmed the cache) semantic dedup legitimately collapses three of them
    # into the first — a correct outcome that would make the id accounting
    # nondeterministic. The property under test is the WIRING, not dedup.
    monkeypatch.setattr(
        config_mod,
        "get_config",
        lambda *_a, **_kw: TRWConfig(dedup_enabled=False, learn_journal_drain_budget_ms=_BUDGET_MS),
    )

    started = time.monotonic()
    result: dict[str, Any] = _get_session_start_fn()(ctx=None, query="*", verbose=True)
    elapsed_ms = (time.monotonic() - started) * 1000.0

    payload = result.get("pending_learns_replayed")
    assert isinstance(payload, dict), f"trw_session_start did not report the drain; keys: {sorted(result.keys())}"
    inline = int(payload["replayed_inline"])
    background = int(payload["deferred_to_background"])
    assert inline + background == 4, payload
    assert inline == int(payload["replayed"]), payload

    _join_background()
    assert learn_journal.pending_count(trw_dir) == 0, "session_start left records pending"
    from trw_mcp.state.memory_adapter import list_active_learnings

    stored = {str(e.get("id", "")) for e in list_active_learnings(trw_dir)}
    assert all(lid in stored for lid in ids), sorted(set(ids) - stored)
    assert elapsed_ms >= 0.0
