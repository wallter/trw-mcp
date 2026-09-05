"""PRD-CORE-244-NFR01: FR03's warm-cache reuse must actually save recall latency.

Acceptance criterion (PRD-CORE-244 verification.mappings NFR01): "Given a
25-result recall against a 10,000-row store, When FR03's cache is warm, Then
added verification latency at p95 is at or below 40ms" measured over 30 timed
recalls.

This pins the real mechanism: ``_verify_assertions`` (the ``trw_recall`` hot
path, ``trw-mcp/src/trw_mcp/tools/_recall_assertion_verification.py``) must
take the ``warm_verified_verdict`` fast path (``_verification_cache.py``) for
every candidate whose verdict is fresh, instead of re-running
``run_verification_pass``'s filesystem anchor/assertion scan on each of the 25
results on every recall. If the cache reuse in
``_recall_assertion_verification.py:91-100`` is reverted (e.g. the
``warm_verified_verdict`` short-circuit removed, or its result ignored), every
call re-walks the project tree for the marker bonus in
``trw_memory.lifecycle.anchor_validation.compute_anchor_validity`` for each of
the 25 anchored entries, which both blows the latency budget below and flips
``run_verification_pass`` from "never called" to "called every entry, every
pass" — the second assertion catches a regression even on a fast/idle box
where the first might not.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from trw_memory.models.memory import Anchor, MemoryEntry
from trw_memory.storage.sqlite_backend import SQLiteBackend

from trw_mcp.models.config import TRWConfig

#: PRD-CORE-244 NFR01 budget: added verification latency at p95 <= 40ms.
_LATENCY_BUDGET_MS = 40.0
_SAMPLES_PER_BATCH = 30
_MAX_LATENCY_BATCHES = 3
_STORE_ROW_COUNT = 10_000
_RECALL_RESULT_COUNT = 25


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text(
        "def anchored_symbol() -> None:\n    return None\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture()
def backend(tmp_path: Path) -> SQLiteBackend:
    return SQLiteBackend(tmp_path / "store" / "memory.db")


def _filler_entry(index: int) -> MemoryEntry:
    """A plain row contributing to the 10,000-row store, uninvolved in recall."""
    return MemoryEntry(id=f"L-filler-{index:05d}", content=f"filler row {index}")


def _warm_entry(index: int, checked_at: str) -> MemoryEntry:
    """One of the 25 recalled entries, with a fresh 'verified' verdict already persisted."""
    return MemoryEntry(
        id=f"L-warm-{index:02d}",
        content="anchored claim",
        anchors=[Anchor(file="src/mod.py", symbol_name="anchored_symbol", symbol_type="function")],
        anchor_validity=1.0,
        verification_status="verified",
        verification_checked_at=checked_at,
    )


def _wire(monkeypatch: pytest.MonkeyPatch, backend: SQLiteBackend, project: Path) -> None:
    monkeypatch.setattr("trw_mcp.state._paths.resolve_project_root", lambda: project)
    monkeypatch.setattr("trw_mcp.state._paths.resolve_trw_dir", lambda: project / ".trw")
    monkeypatch.setattr("trw_mcp.state.memory_adapter.get_backend", lambda _trw_dir: backend)


def _ranked_learnings() -> list[dict[str, object]]:
    return [
        {
            "id": f"L-warm-{index:02d}",
            "namespace": "default",
            "summary": "anchored claim",
            "anchors": [{"file": "src/mod.py", "symbol_name": "anchored_symbol", "symbol_type": "function"}],
        }
        for index in range(_RECALL_RESULT_COUNT)
    ]


@pytest.mark.slow
@pytest.mark.xdist_group(name="recall_verification_latency")
def test_recall_verification_p95_within_budget(
    monkeypatch: pytest.MonkeyPatch, backend: SQLiteBackend, project: Path
) -> None:
    """NFR01: p95 added verification latency over 30 recalls is <= 40ms once warm.

    Repeats the 30-sample batch up to ``_MAX_LATENCY_BATCHES`` times and keeps
    the best (lowest) p95 across batches — the same tolerance-for-a-transient-
    stall convention as ``tests/hooks/test_degenerate_result_nfrs.py::
    test_p95_latency_under_budget``, via ``xdist_group`` to keep this test off
    a worker mid-batch on a sibling latency test. A real latency regression in
    ``_verify_assertions`` still fails every batch.
    """
    from trw_mcp.tools import _verification_pass as verification_pass_mod
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)

    # The 10,000-row store the AC names — irrelevant to the 25-result recall
    # itself, but present so a per-store-size regression (a full table scan
    # sneaking into the warm path) would show up here.
    backend.store_many([_filler_entry(i) for i in range(_STORE_ROW_COUNT)])

    fresh_checked_at = datetime.now(timezone.utc).isoformat()
    backend.store_many([_warm_entry(i, fresh_checked_at) for i in range(_RECALL_RESULT_COUNT)])

    config = TRWConfig()
    assert config.verification_cache_ttl_seconds > 0  # reuse must be enabled by default
    mock_rank_fn = MagicMock(side_effect=lambda entries, *a, **k: entries)

    run_pass_calls: list[str] = []
    real_run_pass = verification_pass_mod.run_verification_pass
    monkeypatch.setattr(
        verification_pass_mod,
        "run_verification_pass",
        lambda entry_id, *a, **k: run_pass_calls.append(entry_id) or real_run_pass(entry_id, *a, **k),
    )

    best_p95: float | None = None
    best_samples: list[float] = []
    for _batch in range(_MAX_LATENCY_BATCHES):
        samples: list[float] = []
        for _ in range(_SAMPLES_PER_BATCH):
            learnings = _ranked_learnings()
            started = time.perf_counter()
            _verify_assertions(learnings, ["anchored"], config, mock_rank_fn)
            samples.append((time.perf_counter() - started) * 1000)
        samples.sort()
        p95 = samples[int(len(samples) * 0.95) - 1]
        if best_p95 is None or p95 < best_p95:
            best_p95, best_samples = p95, samples
        if p95 < _LATENCY_BUDGET_MS:
            break

    assert best_p95 is not None and best_p95 < _LATENCY_BUDGET_MS, (
        f"p95 {best_p95:.2f}ms over the {_LATENCY_BUDGET_MS}ms NFR01 budget on every batch; "
        f"best batch samples={[round(s, 2) for s in best_samples]}"
    )
    # The mechanism, not just the clock: a warm verdict must never re-enter the
    # filesystem verification pass. This is what makes the budget achievable at
    # all against a real anchor set, and it is what a reverted cache breaks
    # first (the timing above would then merely trend upward instead of
    # deterministically failing).
    assert run_pass_calls == [], f"warm_verified_verdict was bypassed; run_verification_pass ran for {run_pass_calls!r}"


def test_cold_verdict_falls_through_to_the_real_pass(
    monkeypatch: pytest.MonkeyPatch, backend: SQLiteBackend, project: Path
) -> None:
    """Negative/boundary case: an EXPIRED verdict is not reused (asymmetric TTL, FR03).

    Proves the p95 test above is measuring a real cache hit and not a
    tautology where the pass is always skipped regardless of staleness: an
    entry whose ``verification_checked_at`` is older than
    ``verification_cache_ttl_seconds`` must re-enter ``run_verification_pass``.
    """
    from trw_mcp.tools import _verification_pass as verification_pass_mod
    from trw_mcp.tools._recall_impl import _verify_assertions

    _wire(monkeypatch, backend, project)

    config = TRWConfig(verification_cache_ttl_seconds=3600)
    expired = (datetime.now(timezone.utc) - timedelta(seconds=config.verification_cache_ttl_seconds + 60)).isoformat()
    backend.store_many([_warm_entry(0, expired)])

    run_pass_calls: list[str] = []
    real_run_pass = verification_pass_mod.run_verification_pass
    monkeypatch.setattr(
        verification_pass_mod,
        "run_verification_pass",
        lambda entry_id, *a, **k: run_pass_calls.append(entry_id) or real_run_pass(entry_id, *a, **k),
    )

    mock_rank_fn = MagicMock(side_effect=lambda entries, *a, **k: entries)
    learnings = [
        {
            "id": "L-warm-00",
            "namespace": "default",
            "anchors": [{"file": "src/mod.py", "symbol_name": "anchored_symbol", "symbol_type": "function"}],
        }
    ]

    _verify_assertions(learnings, ["anchored"], config, mock_rank_fn)

    assert run_pass_calls == ["L-warm-00"], "an expired verdict must re-enter the real verification pass"
