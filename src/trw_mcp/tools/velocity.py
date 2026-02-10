"""TRW velocity tool — compute, persist, and compare velocity metrics.

PRD-CORE-015: Single tool with 3 modes (current, compare, trend).
Persists to .trw/context/velocity.yaml. Atomic writes via FileStateWriter.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import structlog
from fastmcp import FastMCP

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.velocity import (
    VelocityHistory,
    VelocitySnapshot,
)
from trw_mcp.state._paths import resolve_project_root, resolve_run_path, resolve_trw_dir
from trw_mcp.state.persistence import (
    FileStateReader,
    FileStateWriter,
    model_to_dict,
)
from trw_mcp.velocity import (
    compute_debt_indicators,
    compute_learning_effectiveness,
    compute_overhead_ratio,
    compute_run_velocity,
    compute_trend,
)

logger = structlog.get_logger()

_config = TRWConfig()
_reader = FileStateReader()
_writer = FileStateWriter()


def _velocity_yaml_path() -> Path:
    """Resolve path to .trw/context/velocity.yaml."""
    trw_dir = resolve_trw_dir()
    return trw_dir / _config.context_dir / "velocity.yaml"


def _load_history() -> VelocityHistory:
    """Load velocity history from disk, or return empty."""
    path = _velocity_yaml_path()
    if not _reader.exists(path):
        return VelocityHistory()
    try:
        data = _reader.read_yaml(path)
        return VelocityHistory.model_validate(data)
    except (ValueError, TypeError, KeyError, OSError):
        return VelocityHistory()


def _save_history(history: VelocityHistory) -> None:
    """Atomically save velocity history to disk."""
    path = _velocity_yaml_path()
    _writer.write_yaml(path, model_to_dict(history))


def _compute_snapshot(run_path: Path) -> VelocitySnapshot:
    """Compute a full velocity snapshot for a run.

    Args:
        run_path: Path to the run directory.

    Returns:
        VelocitySnapshot with all metrics populated.
    """
    meta_path = run_path / "meta"

    # Read events
    events_path = meta_path / "events.jsonl"
    events = _reader.read_jsonl(events_path) if _reader.exists(events_path) else []

    # Read wave manifest
    wave_manifest: dict[str, object] | None = None
    wave_path = meta_path / "wave_manifest.yaml"
    if _reader.exists(wave_path):
        wave_manifest = _reader.read_yaml(wave_path)

    # Read run state
    run_yaml = meta_path / "run.yaml"
    state: dict[str, object] = {}
    if _reader.exists(run_yaml):
        state = _reader.read_yaml(run_yaml)

    run_id = str(state.get("run_id", "unknown"))
    task = str(state.get("task", "unknown"))
    framework = str(state.get("framework", _config.framework_version))

    # Compute velocity metrics
    metrics = compute_run_velocity(events, wave_manifest)

    # Learning effectiveness
    trw_dir = resolve_trw_dir()
    entries_dir = trw_dir / _config.learnings_dir / _config.entries_dir
    learning_snap = compute_learning_effectiveness(
        entries_dir,
        cold_start_threshold=_config.q_cold_start_threshold,
        effective_q_threshold=_config.velocity_effective_q_threshold,
    )

    # Debt indicators
    project_root = resolve_project_root()
    src_dir = project_root / _config.source_package_path
    tests_dir = project_root / _config.tests_relative_path
    debt = compute_debt_indicators(src_dir, tests_dir)

    # Overhead ratio
    overhead = compute_overhead_ratio(events)

    return VelocitySnapshot(
        run_id=run_id,
        task=task,
        timestamp=datetime.now(timezone.utc).isoformat(),
        framework_version=framework,
        metrics=metrics,
        learning_snapshot=learning_snap,
        debt_indicators=debt,
        overhead=overhead,
    )


def register_velocity_tools(server: FastMCP) -> None:
    """Register velocity tools on the MCP server.

    Args:
        server: FastMCP server instance.
    """

    @server.tool()
    def trw_velocity(
        run_path: str | None = None,
        mode: str = "current",
    ) -> dict[str, object]:
        """Compute, persist, and analyze velocity metrics for TRW runs.

        Args:
            run_path: Path to the run directory. Auto-detects if not provided.
            mode: Operation mode — "current" (compute for run), "compare"
                (delta vs previous), or "trend" (statistical analysis).
        """
        if mode == "current":
            return _mode_current(run_path)
        if mode == "compare":
            return _mode_compare(run_path)
        if mode == "trend":
            return _mode_trend()

        return {"error": f"Unknown mode: {mode!r}. Valid: current, compare, trend"}

    def _mode_current(run_path: str | None) -> dict[str, object]:
        """Compute velocity for a run and persist to history."""
        resolved = resolve_run_path(run_path)
        snapshot = _compute_snapshot(resolved)

        # Load history and check for duplicate
        history = _load_history()
        existing_ids = {s.run_id for s in history.history}
        if snapshot.run_id not in existing_ids:
            history.history.append(snapshot)
            # Prune if over max entries
            max_entries = _config.velocity_history_max_entries
            if len(history.history) > max_entries:
                history.history = history.history[-max_entries:]
            _save_history(history)

        logger.info("trw_velocity_current", run_id=snapshot.run_id)

        result = model_to_dict(snapshot)
        result["mode"] = "current"
        return result

    def _mode_compare(run_path: str | None) -> dict[str, object]:
        """Compare current run velocity with previous run."""
        resolved = resolve_run_path(run_path)
        snapshot = _compute_snapshot(resolved)

        history = _load_history()

        # Find previous run (not the current one)
        previous: VelocitySnapshot | None = None
        for h in reversed(history.history):
            if h.run_id != snapshot.run_id:
                previous = h
                break

        result: dict[str, object] = {
            "mode": "compare",
            "current": model_to_dict(snapshot),
        }

        if previous is not None:
            prev_dict = model_to_dict(previous)
            delta: dict[str, float] = {
                "shard_throughput": round(
                    snapshot.metrics.shard_throughput - previous.metrics.shard_throughput, 4,
                ),
                "total_duration_minutes": round(
                    snapshot.metrics.total_duration_minutes - previous.metrics.total_duration_minutes, 4,
                ),
                "completion_rate": round(
                    snapshot.metrics.completion_rate - previous.metrics.completion_rate, 4,
                ),
                "learning_effectiveness": round(
                    snapshot.learning_snapshot.effectiveness_ratio
                    - previous.learning_snapshot.effectiveness_ratio, 4,
                ),
            }
            result["previous"] = prev_dict
            result["delta"] = delta
        else:
            result["previous"] = None
            result["delta"] = None
            result["note"] = "No previous run in history for comparison"

        return result

    def _mode_trend() -> dict[str, object]:
        """Compute statistical trend from velocity history."""
        history = _load_history()
        snapshots = history.history

        if len(snapshots) < 3:
            return {
                "mode": "trend",
                "direction": "insufficient_data",
                "data_points": len(snapshots),
                "message": f"Need >= 3 runs for trend analysis, have {len(snapshots)}",
            }

        # Convert to dicts for compute_trend
        history_dicts: list[dict[str, object]] = [
            model_to_dict(s) for s in snapshots
        ]

        stable_threshold = _config.velocity_stable_threshold
        trend = compute_trend(
            history_dicts,
            stable_threshold=stable_threshold,
            sign_test_alpha=_config.velocity_sign_test_alpha,
            confounder_jump_ratio=_config.velocity_confounder_jump_ratio,
        )

        result = model_to_dict(trend)
        result["mode"] = "trend"
        result["runs_in_history"] = len(snapshots)

        # Add overhead warning if applicable
        if snapshots:
            latest = snapshots[-1]
            threshold = _config.framework_overhead_threshold
            if latest.overhead.framework_overhead_ratio > threshold:
                result["overhead_warning"] = (
                    f"Framework overhead ratio {latest.overhead.framework_overhead_ratio:.2%} "
                    f"exceeds threshold {threshold:.0%}"
                )

        logger.info(
            "trw_velocity_trend",
            direction=trend.direction,
            data_points=trend.data_points,
        )

        return result
