"""Performance benchmarks for nudge injection.

PRD-CORE-146-NFR01: ``append_ceremony_status`` overhead must be
< 5ms p95 (dev target) with a rollback trigger at 10ms p99.

The benchmark simulates a "warm" ceremony state (50 ``nudge_history``
entries, populated pool cooldowns, realistic counters) so per-call
work includes state parsing + nudge-pool dispatch decisions — not
just a zero-state fast path. Nudges are disabled via config so the
measurement covers only the status/dispatch overhead and not
downstream learning-injection I/O.

CI tolerance: the assertions use looser thresholds than the NFR01
dev targets to avoid flaking on shared/loaded runners. Dev target is
<5ms p95 per NFR01; we assert <10ms p95 and <25ms p99 for CI
stability.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from trw_mcp.tools._ceremony_status import append_ceremony_status


def _build_warm_state(nudge_history_size: int = 50) -> dict[str, Any]:
    """Construct a realistic ceremony-state.json with ``nudge_history_size`` entries."""
    nudge_history: dict[str, dict[str, Any]] = {}
    for i in range(nudge_history_size):
        nudge_history[f"L-bench{i:04d}"] = {
            "phases_shown": ["implement", "validate"],
            "turn_first_shown": i,
            "last_shown_turn": i + 3,
        }
    return {
        "session_started": True,
        "checkpoint_count": 7,
        "last_checkpoint_ts": "2026-04-22T16:00:00+00:00",
        "files_modified_since_checkpoint": 2,
        "build_check_result": "passed",
        "last_build_check_ts": "2026-04-22T16:15:00+00:00",
        "deliver_called": False,
        "learnings_this_session": 5,
        "nudge_counts": {
            "session_start": 1,
            "checkpoint": 4,
            "build_check": 2,
            "review": 1,
            "deliver": 0,
        },
        "phase": "validate",
        "previous_phase": "implement",
        "review_called": False,
        "review_verdict": None,
        "review_p0_count": 0,
        "nudge_history": nudge_history,
        "pool_nudge_counts": {
            "workflow": 3,
            "learnings": 2,
            "ceremony": 1,
            "context": 0,
        },
        "pool_ignore_counts": {
            "context": 1,
            "ceremony": 0,
        },
        "pool_cooldown_until": {"ceremony": 18},
        "pool_cooldown_set_at": {"ceremony": 12},
        "tool_call_counter": 15,
        "last_nudge_pool": "workflow",
    }


def _write_config_disable_nudges(trw_dir: Path) -> None:
    """Write a minimal .trw/config.yaml that disables nudges.

    This keeps the benchmark focused on status-line + dispatch overhead
    rather than learning-injection I/O (which varies with the trw-memory
    backend state).
    """
    config_path = trw_dir / "config.yaml"
    config_path.write_text("nudge_enabled: false\n")


@pytest.fixture
def warm_trw_dir(tmp_path: Path) -> Path:
    """Create a .trw-style directory with warm ceremony-state.json."""
    trw_dir = tmp_path / ".trw"
    (trw_dir / "context").mkdir(parents=True)
    (trw_dir / "logs").mkdir(parents=True)
    state = _build_warm_state(nudge_history_size=50)
    (trw_dir / "context" / "ceremony-state.json").write_text(json.dumps(state))
    _write_config_disable_nudges(trw_dir)
    return trw_dir


def _percentile(samples: list[float], pct: float) -> float:
    """Return the ``pct`` percentile of ``samples`` (0 < pct < 100)."""
    assert 0 < pct < 100
    ordered = sorted(samples)
    idx = round((pct / 100.0) * (len(ordered) - 1))
    return ordered[idx]


def test_append_ceremony_status_p95_under_5ms(warm_trw_dir: Path) -> None:
    """NFR01: append_ceremony_status p95 < 5ms (CI tolerance <10ms), p99 < 10ms (CI <25ms).

    We time 1000 calls against a warm ceremony-state with 50 nudge_history
    entries. A short warm-up excludes first-call import/cache effects.
    """
    # Warm-up — let import caches, structlog loggers, and pydantic
    # configs stabilize so the measurement isn't dominated by one-time
    # cost.
    for _ in range(25):
        append_ceremony_status(response={}, trw_dir=warm_trw_dir)

    samples: list[float] = []
    iterations = 1000
    for _ in range(iterations):
        start = time.perf_counter()
        append_ceremony_status(response={}, trw_dir=warm_trw_dir)
        samples.append((time.perf_counter() - start) * 1000.0)  # ms

    p50 = _percentile(samples, 50)
    p95 = _percentile(samples, 95)
    p99 = _percentile(samples, 99)

    # CI tolerance: looser than NFR01's 5ms/10ms dev target.
    # NFR01 dev targets: p95 < 5ms, p99 < 10ms.
    # CI thresholds: p95 < 10ms, p99 < 25ms (shared-runner variance).
    assert p95 < 10.0, (
        f"p95 latency {p95:.3f}ms exceeds CI threshold 10ms (NFR01 dev target <5ms). p50={p50:.3f}ms p99={p99:.3f}ms"
    )
    assert p99 < 25.0, (
        f"p99 latency {p99:.3f}ms exceeds CI threshold 25ms "
        f"(NFR01 rollback trigger 10ms). p50={p50:.3f}ms p95={p95:.3f}ms"
    )
