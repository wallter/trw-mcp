"""PRD-CORE-141 NFR01 latency bounds for pin and liveness primitives."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

from trw_mcp.state._paths import TRWCallContext, resolve_pin_key, touch_heartbeat
from trw_mcp.state._run_gc import sweep_stale_runs


def _p95(samples: list[float]) -> float:
    return sorted(samples)[round(0.95 * (len(samples) - 1))]


def test_pin_resolution_and_heartbeat_meet_hot_path_slos(tmp_path: Path) -> None:
    context = TRWCallContext(
        session_id="performance-session",
        client_hint=None,
        explicit=False,
        fastmcp_session=None,
    )
    pin_samples: list[float] = []
    for _ in range(1000):
        started = time.perf_counter()
        assert resolve_pin_key(context, explicit=None) == "performance-session"
        pin_samples.append((time.perf_counter() - started) * 1000)

    run_dir = tmp_path / "run"
    (run_dir / "meta").mkdir(parents=True)
    heartbeat_samples: list[float] = []
    with patch("trw_mcp.state._paths.get_pinned_run", return_value=run_dir):
        for _ in range(100):
            started = time.perf_counter()
            touch_heartbeat(context=context)
            heartbeat_samples.append((time.perf_counter() - started) * 1000)

    assert _p95(pin_samples) <= 2.0
    assert _p95(heartbeat_samples) <= 5.0


def test_typical_dry_run_stale_sweep_completes_under_200ms(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    for index in range(50):
        run_dir = runs_root / f"task-{index}" / f"20200101T000000Z-{index:08d}" / "meta"
        run_dir.mkdir(parents=True)
        (run_dir / "run.yaml").write_text(
            f"run_id: run-{index}\ntask: task-{index}\nstatus: active\nphase: implement\n",
            encoding="utf-8",
        )

    started = time.perf_counter()
    report = sweep_stale_runs(
        runs_root,
        staleness_hours=48,
        grace_hours=4,
        pinned_paths=(),
        dry_run=True,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert report.runs_scanned == 50
    assert elapsed_ms < 200.0
