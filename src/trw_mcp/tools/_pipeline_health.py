"""Unified compounding-pipeline health surface — PRD-FIX-COMPOUNDING-6.

Five read-only probes (sync_push, graph_edges, embedding_coverage,
recall_feedback, bandit_state) aggregated by step_pipeline_health().

Design constraints (from the PRD-INFRA-068 lesson):
- All probes are read-only. No writes to memory.db or state files.
- Each probe is individually fail-open: any exception returns a safe default.
- The aggregator (step_pipeline_health) is also fail-open to the caller.
- Uses own short-lived sqlite3 connection (NOT get_backend singleton) to
  avoid WAL-lock contention with the running backend.
- Module stays under the 350 effective-LOC gate.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (per PRD §6 §Thresholds table)
# ---------------------------------------------------------------------------

_SYNC_FAILURE_THRESHOLD: int = 10
_SYNC_STALE_HOURS: float = 6.0
_GRAPH_MIN_CORPUS: int = 100
_EMBED_COVERAGE_THRESHOLD: float = 0.10
_RECALL_MIN_CORPUS: int = 100
_BANDIT_STALE_DAYS: float = 7.0

# Type alias for probe results
SignalResult = dict[str, Any]
PipelineHealthResult = dict[str, Any]


# ---------------------------------------------------------------------------
# sqlite_vec loader (isolated so tests can patch it)
# ---------------------------------------------------------------------------


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Attempt to load the sqlite_vec extension.

    Raises if sqlite_vec is not available.
    """
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
    except (ImportError, AttributeError, sqlite3.OperationalError) as exc:
        raise RuntimeError(f"sqlite_vec unavailable: {exc}") from exc


# ---------------------------------------------------------------------------
# Individual probes
# ---------------------------------------------------------------------------


def probe_sync_push(trw_dir: Path) -> SignalResult:
    """Read sync-state.json and check consecutive_failures + last_push_at age.

    Returns:
        ``{"degraded": bool, "consecutive_failures": int, "last_push_at": str|None, "advisory": str}``
    """
    safe_default: SignalResult = {
        "degraded": False,
        "consecutive_failures": 0,
        "last_push_at": None,
        "advisory": "",
    }
    try:
        state_path = trw_dir / "sync-state.json"
        if not state_path.is_file():
            return safe_default

        raw = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return safe_default

        failures_raw = raw.get("consecutive_failures", 0)
        consecutive_failures = int(failures_raw) if isinstance(failures_raw, (int, float)) else 0

        last_push_raw = raw.get("last_push_at")
        last_push_at: str | None = last_push_raw if isinstance(last_push_raw, str) and last_push_raw else None

        last_push_age_hours: float | None = None
        if last_push_at is not None:
            try:
                dt = datetime.fromisoformat(last_push_at)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                last_push_age_hours = (datetime.now(tz=timezone.utc) - dt).total_seconds() / 3600.0
            except ValueError:
                last_push_age_hours = None

        failure_degraded = consecutive_failures >= _SYNC_FAILURE_THRESHOLD
        stale_degraded = last_push_age_hours is None or last_push_age_hours > _SYNC_STALE_HOURS
        degraded = failure_degraded or stale_degraded

        advisory = ""
        if degraded:
            push_desc = "never" if last_push_at is None else last_push_at
            advisory = f"sync_push degraded: {consecutive_failures} consecutive failures; last push: {push_desc}"

        return {
            "degraded": degraded,
            "consecutive_failures": consecutive_failures,
            "last_push_at": last_push_at,
            "advisory": advisory,
        }
    except Exception:  # justified: fail-open, probe must never raise to aggregator
        logger.debug("pipeline_probe_sync_push_failed", exc_info=True)
        return safe_default


def probe_graph_edges(trw_dir: Path) -> SignalResult:
    """Query memory_graph_edges and memories counts via a short-lived connection.

    Returns:
        ``{"degraded": bool, "edge_count": int, "corpus_count": int, "advisory": str}``
    """
    safe_default: SignalResult = {
        "degraded": False,
        "edge_count": 0,
        "corpus_count": 0,
        "advisory": "",
    }
    try:
        db_path = trw_dir / "memory" / "memory.db"
        if not db_path.is_file():
            return safe_default

        with sqlite3.connect(str(db_path), check_same_thread=False, timeout=2.0) as conn:
            edge_count = conn.execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0]
            corpus_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

        degraded = edge_count == 0 and corpus_count >= _GRAPH_MIN_CORPUS
        advisory = ""
        if degraded:
            advisory = f"graph_edges degraded: 0 edges for {corpus_count} memories — knowledge graph is empty"

        return {
            "degraded": degraded,
            "edge_count": edge_count,
            "corpus_count": corpus_count,
            "advisory": advisory,
        }
    except Exception:  # justified: fail-open
        logger.debug("pipeline_probe_graph_edges_failed", exc_info=True)
        return safe_default


def probe_embedding_coverage(trw_dir: Path) -> SignalResult:
    """Query vec_memories vs memories ratio via a short-lived connection.

    Returns:
        ``{"degraded": bool, "coverage_ratio": float|None, "embedded": int, "total": int, "advisory": str}``
    """
    safe_default: SignalResult = {
        "degraded": False,
        "coverage_ratio": None,
        "embedded": 0,
        "total": 0,
        "advisory": "",
    }
    unavailable_result: SignalResult = {
        "degraded": False,
        "coverage_ratio": None,
        "embedded": 0,
        "total": 0,
        "advisory": "sqlite_vec_unavailable",
    }
    try:
        db_path = trw_dir / "memory" / "memory.db"
        if not db_path.is_file():
            return safe_default

        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=2.0)
        try:
            try:
                _load_sqlite_vec(conn)
            except Exception:  # justified: sqlite_vec unavailable => fail-open
                conn.close()
                return unavailable_result

            embedded = conn.execute("SELECT COUNT(*) FROM vec_memories").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        finally:
            conn.close()

        if total == 0:
            return safe_default

        coverage_ratio = embedded / total
        degraded = coverage_ratio < _EMBED_COVERAGE_THRESHOLD
        advisory = ""
        if degraded:
            advisory = f"embedding_coverage degraded: {embedded}/{total} entries embedded ({coverage_ratio:.1%})"

        return {
            "degraded": degraded,
            "coverage_ratio": coverage_ratio,
            "embedded": embedded,
            "total": total,
            "advisory": advisory,
        }
    except Exception:  # justified: fail-open
        logger.debug("pipeline_probe_embedding_coverage_failed", exc_info=True)
        return safe_default


def probe_recall_feedback(trw_dir: Path) -> SignalResult:
    """Query MAX(recall_count) from memories via a short-lived connection.

    Returns:
        ``{"degraded": bool, "max_recall_count": int, "corpus_count": int, "advisory": str}``
    """
    safe_default: SignalResult = {
        "degraded": False,
        "max_recall_count": 0,
        "corpus_count": 0,
        "advisory": "",
    }
    try:
        db_path = trw_dir / "memory" / "memory.db"
        if not db_path.is_file():
            return safe_default

        with sqlite3.connect(str(db_path), check_same_thread=False, timeout=2.0) as conn:
            max_recall_row = conn.execute("SELECT MAX(recall_count) FROM memories").fetchone()
            max_recall = int(max_recall_row[0]) if max_recall_row[0] is not None else 0
            corpus_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

        degraded = max_recall == 0 and corpus_count >= _RECALL_MIN_CORPUS
        advisory = ""
        if degraded:
            advisory = (
                f"recall_feedback degraded: all {corpus_count} entries have recall_count=0 — "
                "recall feedback loop is dead"
            )

        return {
            "degraded": degraded,
            "max_recall_count": max_recall,
            "corpus_count": corpus_count,
            "advisory": advisory,
        }
    except Exception:  # justified: fail-open
        logger.debug("pipeline_probe_recall_feedback_failed", exc_info=True)
        return safe_default


def _bandit_probe_config() -> tuple[bool, float]:
    """Resolve (probe_enabled, stale_days) from config, fail-open to defaults.

    PRD-FIX-105-FR02: ``bandit_state.json`` is written by the BACKEND meta-tune
    policy, not the MCP runtime, so a stale file is expected wherever the backend
    bandit is not actively driven. Operators tune/disable via config.
    """
    try:
        from trw_mcp.models.config import get_config

        cfg = get_config()
        enabled = bool(getattr(cfg, "pipeline_health_bandit_probe_enabled", True))
        stale_days = float(getattr(cfg, "pipeline_health_bandit_stale_days", _BANDIT_STALE_DAYS))
        return enabled, stale_days
    except Exception:  # justified: fail-open, config load must not break the probe
        logger.debug("pipeline_probe_bandit_config_failed", exc_info=True)
        return True, _BANDIT_STALE_DAYS


def probe_bandit_state(trw_dir: Path) -> SignalResult:
    """Check .trw/meta/bandit_state.json mtime against the configured staleness SLA.

    The file is written by the backend meta-tune policy, not the MCP runtime
    (PRD-FIX-105-FR02). The probe is config-gated so it does not cry wolf in
    deployments where no local writer keeps the file fresh.

    Returns:
        ``{"degraded": bool, "age_days": float, "advisory": str}``
    """
    safe_default: SignalResult = {
        "degraded": False,
        "age_days": 0.0,
        "advisory": "",
    }
    try:
        probe_enabled, stale_days = _bandit_probe_config()
        if not probe_enabled:
            # Operator disabled the probe (no local bandit writer) — never degraded.
            return {"degraded": False, "age_days": 0.0, "advisory": "probe_disabled"}

        bandit_path = trw_dir / "meta" / "bandit_state.json"
        if not bandit_path.is_file():
            # Fresh install without bandit activity — not degraded
            return safe_default

        mtime = os.path.getmtime(str(bandit_path))
        age_days = (time.time() - mtime) / 86400.0

        degraded = age_days > stale_days
        advisory = ""
        if degraded:
            advisory = (
                f"bandit_state degraded: last refresh {age_days:.1f} days ago "
                f"(threshold: {stale_days} days). The bandit_state.json file is "
                "written by the backend meta-tune policy; if no backend bandit is "
                "active here, set pipeline_health_bandit_probe_enabled=false."
            )

        return {
            "degraded": degraded,
            "age_days": age_days,
            "advisory": advisory,
        }
    except Exception:  # justified: fail-open
        logger.debug("pipeline_probe_bandit_state_failed", exc_info=True)
        return safe_default


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def step_pipeline_health(trw_dir: Path) -> PipelineHealthResult:
    """Run all five compounding-pipeline probes and aggregate the result.

    Each probe is individually fail-open: an exception returns a safe default
    and does not prevent the other probes from running.

    Returns:
        PipelineHealthResult with keys:
        ``{"degraded": bool, "advisory": str,
           "sync_push": SignalResult, "graph_edges": SignalResult,
           "embedding_coverage": SignalResult, "recall_feedback": SignalResult,
           "bandit_state": SignalResult}``
    """
    _safe_signal: SignalResult = {"degraded": False, "advisory": "probe_error"}

    def _run_probe(name: str, fn: Any) -> SignalResult:
        try:
            result: SignalResult = fn(trw_dir)
        except Exception as exc:  # justified: fail-open, individual probe failure must not block others
            logger.debug("pipeline_probe_failed", probe=name, error=str(exc))
            return {"degraded": False, "advisory": f"probe_error: {exc}"}
        # Compact the healthy case: a probe's ``advisory`` is empty unless the
        # probe is degraded, so drop the empty string from the aggregate response
        # (5x ``"advisory": ""`` is pure null-noise). A caller drilling into a
        # specific probe treats a missing key the same as empty. Non-empty
        # advisories (degraded / sentinel strings) are preserved.
        if result.get("advisory") == "":
            result = {k: v for k, v in result.items() if k != "advisory"}
        return result

    sync_push = _run_probe("sync_push", probe_sync_push)
    graph_edges = _run_probe("graph_edges", probe_graph_edges)
    embedding_coverage = _run_probe("embedding_coverage", probe_embedding_coverage)
    recall_feedback = _run_probe("recall_feedback", probe_recall_feedback)
    bandit_state = _run_probe("bandit_state", probe_bandit_state)

    degraded_signals = [
        name
        for name, signal in (
            ("sync_push", sync_push),
            ("graph_edges", graph_edges),
            ("embedding_coverage", embedding_coverage),
            ("recall_feedback", recall_feedback),
            ("bandit_state", bandit_state),
        )
        if bool(signal.get("degraded"))
    ]

    degraded = len(degraded_signals) > 0
    advisory = ""
    if degraded:
        signals_str = ", ".join(degraded_signals)
        advisory = f"pipeline degraded: {signals_str} — call trw_pipeline_health() for details"
        logger.warning(
            "pipeline_health_degraded",
            signals=degraded_signals,
            count=len(degraded_signals),
        )

    return {
        "degraded": degraded,
        "advisory": advisory,
        "sync_push": sync_push,
        "graph_edges": graph_edges,
        "embedding_coverage": embedding_coverage,
        "recall_feedback": recall_feedback,
        "bandit_state": bandit_state,
    }
