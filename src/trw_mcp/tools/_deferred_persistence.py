"""Persistence and budget helpers for deferred delivery."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


def _persist_session_metrics(
    metrics_result: dict[str, object],
    resolved_run: Path | None,
) -> None:
    """Persist session_metrics to run.yaml after delivery metrics step.

    PRD-CORE-104: Writes the delivery metrics result dict into
    run.yaml under the ``session_metrics`` key so that downstream
    consumers (meta-tune, dashboards) can access session-level
    reward signals without re-computing them.

    Fail-open: errors are logged but never raised.
    """
    if resolved_run is None:
        return
    if not isinstance(metrics_result, dict) or metrics_result.get("status") != "success":
        return
    try:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        writer = FileStateWriter()
        run_yaml_path = resolved_run / "meta" / "run.yaml"
        if run_yaml_path.exists():
            run_data = reader.read_yaml(run_yaml_path)
            run_data["session_metrics"] = metrics_result
            writer.write_yaml(run_yaml_path, run_data)
            logger.info("session_metrics_persisted", path=str(run_yaml_path))
    except Exception:  # justified: fail-open, session metrics persistence is best-effort
        logger.warning("session_metrics_persist_failed", exc_info=True)


def _persist_deferred_results(
    results: dict[str, object],
    resolved_run: Path | None,
) -> None:
    """Persist deferred delivery results to run.yaml for downstream consumers."""
    if resolved_run is None:
        return
    try:
        from trw_mcp.state.persistence import FileStateReader, FileStateWriter

        reader = FileStateReader()
        writer = FileStateWriter()
        run_yaml_path = resolved_run / "meta" / "run.yaml"
        if not run_yaml_path.exists():
            return

        run_data = reader.read_yaml(run_yaml_path)
        run_data["deferred_results"] = dict(results)

        # F19 (2026-06-04): the audit-pattern "promotion candidates" used to be
        # mirrored into dedicated ``audit_pattern_promotions`` /
        # ``promotion_candidates`` run.yaml keys here, tagged
        # ``promotion_path="metadata_only"`` / ``meta_tune_integration="tool_unavailable"``.
        # That was a self-documented no-op: nothing in the codebase ever read
        # those keys back (CORE-093 removed automatic CLAUDE.md learning
        # promotion, and no trw_meta_tune() tool ships), so the signal was
        # computed, written, and silently dropped. The arrays also ballooned
        # legacy run.yaml files to multiple MB and dominated boot-time YAML
        # parsing (see state/_run_gc.py). Wiring them into trw_instructions_sync
        # would re-introduce exactly the CLAUDE.md promotion CORE-093 deleted, so
        # the honest fix is to stop persisting the dead signal. The consolidation
        # step's status still flows through ``deferred_results`` above for audit.

        writer.write_yaml(run_yaml_path, run_data)
        logger.info("deferred_results_persisted", path=str(run_yaml_path))
    except Exception:  # justified: fail-open, deferred state persistence is best-effort
        logger.warning("deferred_results_persist_failed", exc_info=True)


def log_deferred_result(
    trw_dir: Path,
    results: dict[str, object],
    errors: list[str],
    lock_ex: Callable[[int], object],
    lock_un: Callable[[int], object],
) -> None:
    """Append deferred step results to an audit log."""
    log_path = trw_dir / "logs" / "deferred-deliver.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # PRD-FIX-085 FR04: rotate at 10 MB to match surface_tracking parity.
    # Pre-fix this file grew unbounded -- observed 25 MB on the dev repo.
    from trw_mcp.state._helpers import rotate_jsonl

    rotate_jsonl(log_path, max_bytes=10 * 1024 * 1024)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": {k: v for k, v in results.items() if k != "timestamp"},
        "errors": errors,
        "success": len(errors) == 0,
    }
    try:
        with log_path.open("a", encoding="utf-8") as f:
            lock_ex(f.fileno())
            f.write(json.dumps(entry, default=str) + "\n")
            f.flush()
            lock_un(f.fileno())
    except Exception:  # justified: fail-open, deferred log is diagnostic only
        logger.debug("deferred_log_write_failed", exc_info=True)


def _resolve_step_budgets() -> tuple[float, float]:
    """Read per-step and per-batch budgets from config, with safe defaults.

    Reading happens inside ``_run_deferred_steps`` so the config can be
    monkeypatched between tests. Defaults match the values in
    ``_fields_build.py``.
    """
    try:
        from trw_mcp.models.config import get_config

        cfg = get_config()
        step_s = float(getattr(cfg, "deferred_step_max_seconds", 60))
        batch_s = float(getattr(cfg, "deferred_batch_max_seconds", 300))
    except Exception:  # justified: fail-open, config load failure must not block the deferred batch
        step_s, batch_s = 60.0, 300.0
    # A non-positive budget disables the watchdog (escape hatch for ops
    # who need an unbounded batch). Mirror Python's ``threading.Timer``
    # behavior on 0.0 to mean "fire immediately"; we treat 0 as disabled.
    return max(step_s, 0.0), max(batch_s, 0.0)
