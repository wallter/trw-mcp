"""PRD-FIX-131: session start reads the store's namespace health once, not once per probe.

Measured on a copy of a 3,277-row project store (2026-10-01): each ``memory_status``
health round trip to the daemon cost ~60 ms, and one session start made eight of
them -- three per pipeline-health aggregate, an aggregate computed twice (advisory
plus fail-closed gate), the graph-health advisory's own, and recall's store count.
These tests pin the round-trip count; the ``requires_local_timing`` twin pins the
wall-clock consequence with a store whose health call costs what the daemon's did.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests._layout import requires_local_timing
from tests._memory_store_fake import FakeMemoryStore
from tests._timing import assert_budget
from tests.conftest import extract_tool_fn, make_test_server

#: Median daemon ``health`` round trip measured on the 3,277-row store copy.
_MEASURED_HEALTH_MS = 60.0


def _health_calls(store: FakeMemoryStore) -> int:
    return sum(1 for call in store.calls if call[0] == "health")


def test_store_health_outside_a_block_asks_the_store_every_time(
    fake_memory_store: FakeMemoryStore, tmp_path: Path
) -> None:
    from trw_mcp.state._store_counts import store_health

    store_health(tmp_path / ".trw")
    store_health(tmp_path / ".trw")
    assert _health_calls(fake_memory_store) == 2


def test_one_health_reading_shares_one_reading_per_trw_dir(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    from trw_mcp.state._store_counts import one_health_reading, store_health

    with one_health_reading():
        first = store_health(tmp_path / ".trw")
        second = store_health(tmp_path / ".trw")
        store_health(tmp_path / "other" / ".trw")
    assert first == second
    assert _health_calls(fake_memory_store) == 2  # one per distinct trw_dir
    store_health(tmp_path / ".trw")
    assert _health_calls(fake_memory_store) == 3  # the block's reading does not outlive it


def test_a_failed_reading_is_not_kept(fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch) -> None:
    from trw_mcp.state._store_counts import one_health_reading, store_health

    real = fake_memory_store.health
    attempts: list[str] = []

    def flaky(namespace: str) -> Any:
        attempts.append(namespace)
        if len(attempts) == 1:
            raise RuntimeError("daemon busy")
        return real(namespace)

    monkeypatch.setattr(fake_memory_store, "health", flaky)
    with one_health_reading():
        with pytest.raises(RuntimeError):
            store_health(tmp_path / ".trw")
        store_health(tmp_path / ".trw")
        store_health(tmp_path / ".trw")
    assert len(attempts) == 2


def test_an_explicit_readings_dict_spans_two_blocks(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    from trw_mcp.state._store_counts import one_health_reading, store_health

    shared: dict[str, Any] = {}
    with one_health_reading(shared):
        store_health(tmp_path / ".trw")
    with one_health_reading(shared):
        store_health(tmp_path / ".trw")
    assert _health_calls(fake_memory_store) == 1


def test_pipeline_aggregate_reads_health_once(fake_memory_store: FakeMemoryStore, tmp_path: Path) -> None:
    from trw_mcp.tools._pipeline_health import step_pipeline_health

    result = step_pipeline_health(tmp_path / ".trw")
    assert result["status"] in {"healthy", "degraded"}
    assert _health_calls(fake_memory_store) == 1


def test_pipeline_health_probe_runs_once_per_session_start_step(
    fake_memory_store: FakeMemoryStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: Any
) -> None:
    """PRD-FIX-131-FR05: the fail-closed gate judges the advisory's aggregate."""
    from trw_mcp.tools import _ceremony_session_start_steps, _pipeline_health, _pipeline_health_gate
    from trw_mcp.tools._ceremony_pipeline_advisory import step_pipeline_health_advisory

    calls: list[Path] = []

    def counting(trw_dir: Path, cfg: Any = None, **kwargs: Any) -> Any:
        calls.append(trw_dir)
        return _pipeline_health.step_pipeline_health(trw_dir, cfg, **kwargs)

    monkeypatch.setattr(_ceremony_session_start_steps, "step_pipeline_health", counting)
    monkeypatch.setattr(_pipeline_health_gate, "step_pipeline_health", counting)
    step_pipeline_health_advisory(tmp_path / ".trw", {}, config)
    assert len(calls) == 1

    # A caller outside session start still gets a gate that probes for itself.
    calls.clear()
    verdict = _pipeline_health_gate.check_pipeline_health(tmp_path / ".trw", config)
    assert len(calls) == 1
    assert verdict["status"] in {"healthy", "not_measured", "degraded"}


def test_a_failed_aggregate_is_recorded_once_and_not_reprobed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, config: Any
) -> None:
    from trw_mcp.tools import _ceremony_session_start_steps, _pipeline_health_gate
    from trw_mcp.tools._ceremony_pipeline_advisory import step_pipeline_health_advisory

    gate_probes: list[Path] = []

    def boom(trw_dir: Path, cfg: Any = None) -> Any:
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(_ceremony_session_start_steps, "step_pipeline_health", boom)
    monkeypatch.setattr(_pipeline_health_gate, "step_pipeline_health", lambda d, c=None: gate_probes.append(d))
    results: dict[str, object] = {}
    step_pipeline_health_advisory(tmp_path / ".trw", results, config)
    assert gate_probes == []
    assert "pipeline_health_warning" not in results
    degradations = results.get("degradations")
    assert isinstance(degradations, list)
    assert [entry["step"] for entry in degradations if isinstance(entry, dict)] == ["pipeline_health"]


def _session_start(**kwargs: Any) -> dict[str, Any]:
    fn = extract_tool_fn(make_test_server("ceremony"), "trw_session_start")
    result: dict[str, Any] = fn(ctx=None, verbose=True, **kwargs)
    return result


def test_session_start_reads_namespace_health_twice(fake_memory_store: FakeMemoryStore) -> None:
    """Recall's store count, then ONE reading shared by graph_health and pipeline_health (was 8)."""
    result = _session_start()
    assert not result.get("errors"), result.get("errors")
    assert _health_calls(fake_memory_store) == 2


def test_every_session_start_step_is_timed(fake_memory_store: FakeMemoryStore) -> None:
    """PRD-FIX-131-FR04: the two formerly untimed steps now report a duration."""
    from trw_mcp.tools._ceremony_step_table import SESSION_START_STEPS

    durations = _session_start()["step_durations_ms"]
    assert {"first_session_marker", "graph_health"} <= set(durations)
    assert all(step.timed for step in SESSION_START_STEPS)


class _SlowHealthStore(FakeMemoryStore):
    """A store whose health round trip costs what the daemon's did on the measured store."""

    def health(self, namespace: str) -> Any:
        # trw:intentional the latency IS the fixture: it stands in for the measured daemon round trip
        time.sleep(_MEASURED_HEALTH_MS / 1000.0)
        return super().health(namespace)


@requires_local_timing
def test_graph_and_pipeline_health_steps_pay_one_health_round_trip_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before PRD-FIX-131 these two steps made 4 health round trips (>= 240 ms here); now 1.

    Budget 150 ms: one 60 ms reading plus the steps' own work, with headroom that
    still fails at two readings.
    """
    from tests._memory_fixtures import FAKE_NAMESPACE
    from trw_mcp.state import _store_selection

    store = _SlowHealthStore()
    monkeypatch.setenv("TRW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(_store_selection, "selected_store", lambda _trw_dir: (store, FAKE_NAMESPACE))
    samples = []
    for _ in range(5):
        durations = _session_start()["step_durations_ms"]
        samples.append(float(durations["graph_health"]) + float(durations["pipeline_health"]))
    median = sorted(samples)[len(samples) // 2]
    assert_budget("session_start_graph_plus_pipeline_health_ms", median, 150.0, "ms")
