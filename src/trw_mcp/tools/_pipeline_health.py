"""Unified compounding-pipeline health surface — PRD-FIX-COMPOUNDING-6.

Five read-only probes (sync_push, graph_edges, embedding_coverage,
recall_feedback, bandit_state) aggregated by step_pipeline_health().

Design constraints (from the PRD-INFRA-068 lesson):
- All probes are read-only. No writes to memory.db or state files.
- Each probe is individually fail-open, but a probe that CRASHED is reported as
  ``measured: False`` with a reason, never as a healthy default
  (PRD-CORE-263-FR03). Four of the five used to collapse an exception into
  ``degraded: False`` with an empty advisory that the aggregator then stripped,
  so a probe that died on a locked database and one that measured a healthy
  corpus produced byte-identical payload entries.
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


def _unmeasured(probe: str, reason: str, **fields: Any) -> SignalResult:
    """The shape every probe returns when it could not take its measurement.

    PRD-CORE-263-FR03 generalises the shape the ``graph_edges`` probe already
    used. ``degraded`` stays ``False`` — an unreadable store is not evidence of a
    broken pipeline — but ``measured`` says the verdict rests on nothing, and the
    advisory is non-empty so the aggregator's healthy-case compaction cannot
    strip it back into silence.

    ``fields`` carries the probe's own zero-valued keys so the entry keeps its
    shape for a caller that indexes them; they are meaningless while
    ``measured`` is False, which is exactly what that flag is for.
    """
    return {
        "degraded": False,
        "measured": False,
        **fields,
        "advisory": f"{probe} not measured: {reason}",
    }


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
        "measured": True,
        "consecutive_failures": 0,
        "last_push_at": None,
        "advisory": "",
    }
    try:
        state_path = trw_dir / "sync-state.json"
        if not state_path.is_file():
            # DEF-07: an absent state file used to report the SAME
            # ``measured: True`` shape as a genuinely healthy push, even
            # though nothing was read. Unlike a probe that counts rows in a
            # database that legitimately has none (``graph_edges``,
            # ``embedding_coverage``, ``recall_feedback`` — see their own
            # ``not db_path.is_file()`` branches), a missing sync-state.json
            # means the push subsystem has left no trace at all: it could be
            # "sync was never configured" or "sync ran and never wrote
            # state", and this probe cannot tell which. Report not-measured.
            return _unmeasured("sync_push", "state_file_missing", consecutive_failures=0, last_push_at=None)

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
            "measured": True,
            "consecutive_failures": consecutive_failures,
            "last_push_at": last_push_at,
            "advisory": advisory,
        }
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_sync_push_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("sync_push", type(exc).__name__, consecutive_failures=0, last_push_at=None)


def probe_graph_edges(trw_dir: Path) -> SignalResult:
    """Report whether the corpus holds any knowledge-graph relation.

    ``edge_count`` still reports the MATERIALISED half alone, because that is
    what the number means and a derived relation has no row to count. The
    ``degraded`` verdict does not: after PRD-CORE-245 FR07 tag co-occurrence is
    derived from ``memory_tags`` at query time, so a healthy tag-related corpus
    with no embeddings reads ``edge_count == 0`` and this probe would have
    reported it degraded forever. It asks
    :func:`trw_mcp.state._graph_relations.graph_has_relations` instead.

    ``measured`` states whether the store was actually read. A probe that could
    not read the store used to return the same ``degraded=False, edge_count=0``
    shape as a healthy one, so "we did not look" and "we looked and it is fine"
    were the same answer. ``degraded`` stays ``False`` when unmeasured — an
    unreadable store is not evidence of a dead graph — but the advisory now says
    so instead of being empty.

    Returns:
        ``{"degraded": bool, "measured": bool, "edge_count": int,
        "corpus_count": int, "advisory": str}``
    """

    try:
        from trw_memory.models.config import MemoryConfig

        from trw_mcp.state._constants import DEFAULT_NAMESPACE
        from trw_mcp.state._graph_relations import graph_has_relations

        db_path = trw_dir / "memory" / "memory.db"
        if not db_path.is_file():
            # A store that does not exist yet is a MEASURED empty corpus, not an
            # unreadable one: there is nothing to read and nothing to warn about.
            return {"degraded": False, "measured": True, "edge_count": 0, "corpus_count": 0, "advisory": ""}

        with sqlite3.connect(str(db_path), check_same_thread=False, timeout=2.0) as conn:
            edge_count = conn.execute("SELECT COUNT(*) FROM memory_graph_edges").fetchone()[0]
            corpus_count = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            has_relations = graph_has_relations(
                conn,
                namespace=DEFAULT_NAMESPACE,
                config=MemoryConfig(storage_path=str(trw_dir / "memory")),
            )

        degraded = not has_relations and corpus_count >= _GRAPH_MIN_CORPUS
        advisory = ""
        if degraded:
            advisory = (
                f"graph_edges degraded: no materialised edge and no derived tag relation "
                f"for {corpus_count} memories — knowledge graph is empty"
            )

        return {
            "degraded": degraded,
            "measured": True,
            "edge_count": edge_count,
            "corpus_count": corpus_count,
            "advisory": advisory,
        }
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_graph_edges_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("graph_edges", type(exc).__name__, edge_count=0, corpus_count=0)


def probe_embedding_coverage(trw_dir: Path) -> SignalResult:
    """Query vec_memories vs memories ratio via a short-lived connection.

    Returns:
        ``{"degraded": bool, "coverage_ratio": float|None, "embedded": int, "total": int, "advisory": str}``
    """
    safe_default: SignalResult = {
        "degraded": False,
        "measured": True,
        "coverage_ratio": None,
        "embedded": 0,
        "total": 0,
        "advisory": "",
    }
    # sqlite_vec missing means the coverage ratio was never computed — the
    # advisory said so, but ``measured`` is what a caller can branch on.
    try:
        db_path = trw_dir / "memory" / "memory.db"
        if not db_path.is_file():
            # DEF-09 (audited, then REFUTED on re-verification): a database
            # that does not exist yet has genuinely zero embedded and zero
            # total entries — the same "measured: True, count: 0" shape
            # ``probe_graph_edges`` uses for the identical condition, which
            # FR03's own text names as the in-repo precedent this PRD
            # generalises rather than a defect. The repo's own
            # ``_healthy_trw_dir`` test fixture (no ``memory.db`` created)
            # confirms this is the established, deliberate contract: it is
            # asserted healthy/measured across the whole probe suite.
            return safe_default

        conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=2.0)
        try:
            try:
                _load_sqlite_vec(conn)
            except Exception:  # justified: fail-open, but the failure is REPORTED, not erased
                # sqlite_vec missing means the coverage ratio was never computed.
                conn.close()
                return _unmeasured(
                    "embedding_coverage", "sqlite_vec_unavailable", coverage_ratio=None, embedded=0, total=0
                )

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
            "measured": True,
            "coverage_ratio": coverage_ratio,
            "embedded": embedded,
            "total": total,
            "advisory": advisory,
        }
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_embedding_coverage_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("embedding_coverage", type(exc).__name__, coverage_ratio=None, embedded=0, total=0)


def probe_recall_feedback(trw_dir: Path) -> SignalResult:
    """Query MAX(recall_count) from memories via a short-lived connection.

    Returns:
        ``{"degraded": bool, "max_recall_count": int, "corpus_count": int, "advisory": str}``
    """
    safe_default: SignalResult = {
        "degraded": False,
        "measured": True,
        "max_recall_count": 0,
        "corpus_count": 0,
        "advisory": "",
    }
    try:
        db_path = trw_dir / "memory" / "memory.db"
        if not db_path.is_file():
            # DEF-09 (audited, then REFUTED on re-verification): see the
            # identical rationale in ``probe_embedding_coverage`` — a missing
            # database is a genuinely measured zero, matching the
            # ``graph_edges`` precedent FR03 generalises.
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
            "measured": True,
            "max_recall_count": max_recall,
            "corpus_count": corpus_count,
            "advisory": advisory,
        }
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_recall_feedback_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("recall_feedback", type(exc).__name__, max_recall_count=0, corpus_count=0)


def _bandit_probe_config() -> tuple[bool, float]:
    """Resolve (probe_enabled, stale_days) from config.

    PRD-FIX-105-FR02: ``bandit_state.json`` is written by the BACKEND meta-tune
    policy, not the MCP runtime, so a stale file is expected wherever the backend
    bandit is not actively driven. Operators tune/disable via config.

    PRD-CORE-263-NFR02: this used to fail open to ``(True, _BANDIT_STALE_DAYS)``
    on any exception, which means a config the process could not read produced a
    probe that then reported a staleness verdict against a threshold nobody set.
    It raises now, and ``probe_bandit_state``'s own handler turns that into a
    not-measured entry — the one place that decides what a probe failure means.
    """
    from trw_mcp.models.config import get_config

    cfg = get_config()
    enabled = bool(getattr(cfg, "pipeline_health_bandit_probe_enabled", True))
    stale_days = float(getattr(cfg, "pipeline_health_bandit_stale_days", _BANDIT_STALE_DAYS))
    return enabled, stale_days


def probe_bandit_state(trw_dir: Path) -> SignalResult:
    """Check .trw/meta/bandit_state.json mtime against the configured staleness SLA.

    The file is written by the backend meta-tune policy, not the MCP runtime
    (PRD-FIX-105-FR02). The probe is config-gated so it does not cry wolf in
    deployments where no local writer keeps the file fresh.

    Returns:
        ``{"degraded": bool, "age_days": float, "advisory": str}``
    """
    try:
        probe_enabled, stale_days = _bandit_probe_config()
        if not probe_enabled:
            # Operator disabled the probe (no local bandit writer). Deliberately
            # NOT measured: nothing was read, and reporting a healthy 0.0-day age
            # for a probe that never ran is the defect this PRD removes.
            return _unmeasured("bandit_state", "probe_disabled", age_days=0.0)

        bandit_path = trw_dir / "meta" / "bandit_state.json"
        if not bandit_path.is_file():
            # DEF-08: this used to return ``safe_default`` — ``measured: True,
            # age_days: 0.0`` — for a file that has never existed. ``0.0`` is
            # not a genuine zero-count measurement (unlike ``edge_count: 0``
            # on an absent memory.db, which is a true fact about an empty
            # corpus); it is a FABRICATED "just refreshed" timestamp for a
            # probe that read nothing. That is exactly the defect this
            # module's ``_unmeasured()`` shape exists to remove, and the one
            # this function's own docstring already applies to the disabled
            # case two lines above — it had just not been applied here too.
            return _unmeasured("bandit_state", "state_missing", age_days=None)

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
            "measured": True,
            "age_days": age_days,
            "advisory": advisory,
        }
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_bandit_state_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("bandit_state", type(exc).__name__, age_days=0.0)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def step_pipeline_health(trw_dir: Path) -> PipelineHealthResult:
    """Run all five compounding-pipeline probes and aggregate the result.

    Each probe is individually fail-open: an exception returns a safe default
    and does not prevent the other probes from running.

    An UNMEASURED probe (PRD-CORE-263-FR03) neither sets the aggregate
    ``degraded`` verdict nor counts toward health: it is listed under
    ``unmeasured`` instead. Folding it into ``degraded`` would make an
    unreadable database indistinguishable from a broken pipeline, which is the
    same conflation this requirement removes in the other direction.

    Returns:
        PipelineHealthResult with keys:
        ``{"degraded": bool, "advisory": str, "unmeasured": list[str] (omitted
           when empty), "sync_push": SignalResult, "graph_edges": SignalResult,
           "embedding_coverage": SignalResult, "recall_feedback": SignalResult,
           "bandit_state": SignalResult}``
    """

    def _run_probe(name: str, fn: Any) -> SignalResult:
        try:
            result: SignalResult = fn(trw_dir)
        except Exception as exc:  # trw-fail-silent-allow: the probes catch their own failures, so this is the belt to their braces; it cannot classify a failure it was never designed to see, so it reports not-measured rather than inventing a verdict
            logger.warning("pipeline_probe_failed", probe=name, error=type(exc).__name__, exc_info=True)
            return _unmeasured(name, f"aggregator_caught_{type(exc).__name__}")
        # Compact the healthy case: a probe's ``advisory`` is empty unless the
        # probe is degraded, so drop the empty string from the aggregate response
        # (5x ``"advisory": ""`` is pure null-noise). A caller drilling into a
        # specific probe treats a missing key the same as empty. Non-empty
        # advisories (degraded / sentinel strings) are preserved.
        #
        # The ``measured`` guard is PRD-CORE-263-FR03's other half: this strip is
        # what made a crashed probe byte-identical to a healthy one, because the
        # crash default's advisory was the empty string. An unmeasured entry
        # keeps its advisory whatever it says.
        if result.get("advisory") == "" and result.get("measured", True):
            result = {k: v for k, v in result.items() if k != "advisory"}
        return result

    sync_push = _run_probe("sync_push", probe_sync_push)
    graph_edges = _run_probe("graph_edges", probe_graph_edges)
    embedding_coverage = _run_probe("embedding_coverage", probe_embedding_coverage)
    recall_feedback = _run_probe("recall_feedback", probe_recall_feedback)
    bandit_state = _run_probe("bandit_state", probe_bandit_state)

    named_signals = (
        ("sync_push", sync_push),
        ("graph_edges", graph_edges),
        ("embedding_coverage", embedding_coverage),
        ("recall_feedback", recall_feedback),
        ("bandit_state", bandit_state),
    )
    # An unmeasured probe is excluded from BOTH lists it could join: it is not
    # degraded, and it is not healthy either (PRD-CORE-263-FR03 / OQ-03).
    unmeasured_signals = [name for name, signal in named_signals if not signal.get("measured", True)]
    degraded_signals = [
        name for name, signal in named_signals if signal.get("measured", True) and bool(signal.get("degraded"))
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

    result: PipelineHealthResult = {
        "degraded": degraded,
        "advisory": advisory,
        "sync_push": sync_push,
        "graph_edges": graph_edges,
        "embedding_coverage": embedding_coverage,
        "recall_feedback": recall_feedback,
        "bandit_state": bandit_state,
    }
    # Omitted when empty, so a fully-measured aggregate is byte-identical to the
    # pre-263 payload apart from the per-probe ``measured`` flags.
    #
    # DEF-10: this used to ALSO emit ``pipeline_health_unmeasured`` here, a
    # second structured event for the exact condition each probe's own
    # ``except`` handler (or ``_run_probe``'s belt-and-braces catch above)
    # already logged with the real error class and traceback — the identical
    # occurrence under two event names, which NFR03 requires be exactly one.
    # The per-probe log is the higher-value one (it names the failure); the
    # aggregate view is the ``unmeasured`` list below, read from the payload
    # rather than grepped from a second log line.
    if unmeasured_signals:
        result["unmeasured"] = unmeasured_signals
    return result
