"""Unified compounding-pipeline health surface — PRD-FIX-COMPOUNDING-6.

Four read-only probes (sync_push, graph_edges, embedding_coverage,
recall_feedback) aggregated by step_pipeline_health().

Design constraints (from the PRD-INFRA-068 lesson):
- All probes are read-only. No writes to memory.db or state files.
- Each probe is individually fail-open, but a probe that CRASHED is reported as
  ``measured: False`` with a reason, never as a healthy default
  (PRD-CORE-263-FR03). Four of the then five used to collapse an exception into
  ``degraded: False`` with an empty advisory that the aggregator then stripped,
  so a probe that died on a locked database and one that measured a healthy
  corpus produced byte-identical payload entries.
- The aggregator (step_pipeline_health) is also fail-open to the caller.
- The graph, embedding and recall probes read the store's own ``health``
  block (``memory_status``, via ``state._store_counts.store_health``); no probe
  opens a checkout's ``memory.db`` (PRD-CORE-280).
- Module stays under the 350 effective-LOC gate.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from trw_mcp.state._store_counts import store_health

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (per PRD §6 §Thresholds table)
# ---------------------------------------------------------------------------

_SYNC_FAILURE_THRESHOLD: int = 10
_SYNC_STALE_HOURS: float = 6.0
_RECALL_MIN_CORPUS: int = 100

# PRD-FIX-141-FR02/FR03: the graph and embedding verdicts used to carry PRIVATE
# thresholds here (100 memories, 10% coverage) while the two other surfaces
# asking the same questions read CONFIG fields that mean the same thing. That is
# how the pipeline-health probe reported ``graph_edges.degraded=false`` in the
# same second ``trw_session_start`` reported "knowledge graph dead" at severity error
# (learning L-Rikf). One threshold per question, resolved from config, read by
# every consumer of this module's verdict.

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


def _resolve_config(config: Any | None) -> Any:
    """Return *config*, or the live ``TRWConfig`` when the caller passed none.

    The gate (``_pipeline_health_gate.check_pipeline_health``) hands its own
    already-resolved config down so its thresholds and the probe's are the same
    object; session start and the tool surface pass nothing and get the live one.
    """
    if config is not None:
        return config
    from trw_mcp.models.config import get_config

    return get_config()


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
            # though nothing was read. A missing sync-state.json means the push subsystem has left no trace at all: it could be
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


def probe_graph_edges(trw_dir: Path, config: Any | None = None) -> SignalResult:
    """Report whether the corpus holds any knowledge-graph relation.

    ``edge_count`` reports the MATERIALISED half alone, because that is what the
    number means and a derived relation has no row to count. The ``degraded``
    verdict does not: after PRD-CORE-245 FR07 tag co-occurrence is derived from
    ``memory_tags`` at query time, so a healthy tag-related corpus reads
    ``edge_count == 0``. The store's ``has_relations`` answers for both halves.

    Returns:
        ``{"degraded": bool, "measured": bool, "edge_count": int,
        "corpus_count": int, "has_relations": bool, "min_corpus": int, "advisory": str}``
    """
    min_corpus = int(getattr(_resolve_config(config), "pipeline_health_gate_graph_min_corpus", 10))
    try:
        health = store_health(trw_dir)
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_graph_edges_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("graph_edges", type(exc).__name__, edge_count=0, corpus_count=0, has_relations=False)
    corpus_count = health["entries"]
    degraded = not health["has_relations"] and corpus_count > min_corpus
    advisory = ""
    if degraded:
        advisory = (
            f"knowledge graph dead: no materialised edge and no derived tag relation "
            f"for {corpus_count} memories (min corpus {min_corpus})"
        )
    return {
        "degraded": degraded,
        "measured": True,
        "edge_count": health["edges"],
        "corpus_count": corpus_count,
        "has_relations": health["has_relations"],
        "min_corpus": min_corpus,
        "advisory": advisory,
    }


def probe_embedding_coverage(trw_dir: Path, config: Any | None = None) -> SignalResult:
    """Report the share of the namespace's entries the store holds a vector for.

    Returns:
        ``{"degraded": bool, "measured": bool, "coverage_ratio": float|None,
        "embedded": int, "total": int, "coverage_threshold": float, "advisory": str}``
    """
    threshold = float(getattr(_resolve_config(config), "embeddings_coverage_warn_threshold", 0.10))
    unmeasured = {"coverage_ratio": None, "embedded": 0, "total": 0, "coverage_threshold": threshold}
    try:
        health = store_health(trw_dir)
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_embedding_coverage_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("embedding_coverage", type(exc).__name__, **unmeasured)
    embedded, total = health["embedded"], health["entries"]
    if embedded is None:
        # A store that keeps no vectors never computed a ratio: not measured, never zero.
        return _unmeasured("embedding_coverage", "store_keeps_no_vectors", **unmeasured)
    coverage_ratio = embedded / total if total else None
    degraded = coverage_ratio is not None and coverage_ratio < threshold
    advisory = ""
    if degraded:
        advisory = (
            f"embedding_coverage degraded: {embedded}/{total} entries embedded "
            f"({coverage_ratio:.1%}, threshold {threshold:.1%})"
        )
    return {
        "degraded": degraded,
        "measured": True,
        "coverage_ratio": coverage_ratio,
        "embedded": embedded,
        "total": total,
        "coverage_threshold": threshold,
        "advisory": advisory,
    }


def probe_recall_feedback(trw_dir: Path) -> SignalResult:
    """Report whether recall ever fed back: the namespace's highest ``recall_count``.

    Returns:
        ``{"degraded": bool, "measured": bool, "max_recall_count": int, "corpus_count": int, "advisory": str}``
    """
    try:
        health = store_health(trw_dir)
    except Exception as exc:  # justified: fail-open, but the failure is REPORTED, not erased
        logger.warning("pipeline_probe_recall_feedback_failed", error=type(exc).__name__, exc_info=True)
        return _unmeasured("recall_feedback", type(exc).__name__, max_recall_count=0, corpus_count=0)
    max_recall, corpus_count = health["max_recall_count"], health["entries"]
    degraded = max_recall == 0 and corpus_count >= _RECALL_MIN_CORPUS
    advisory = ""
    if degraded:
        advisory = (
            f"recall_feedback degraded: all {corpus_count} entries have recall_count=0 — recall feedback loop is dead"
        )
    return {
        "degraded": degraded,
        "measured": True,
        "max_recall_count": max_recall,
        "corpus_count": corpus_count,
        "advisory": advisory,
    }


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------


def step_pipeline_health(trw_dir: Path, config: Any | None = None) -> PipelineHealthResult:
    """Run all four compounding-pipeline probes and aggregate the result.

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
           "embedding_coverage": SignalResult, "recall_feedback": SignalResult}``
    """

    def _run_probe(name: str, fn: Any, *, takes_config: bool = False) -> SignalResult:
        try:
            result: SignalResult = fn(trw_dir, config) if takes_config else fn(trw_dir)
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
    graph_edges = _run_probe("graph_edges", probe_graph_edges, takes_config=True)
    embedding_coverage = _run_probe("embedding_coverage", probe_embedding_coverage, takes_config=True)
    recall_feedback = _run_probe("recall_feedback", probe_recall_feedback)

    named_signals = (
        ("sync_push", sync_push),
        ("graph_edges", graph_edges),
        ("embedding_coverage", embedding_coverage),
        ("recall_feedback", recall_feedback),
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
        advisory = f"pipeline degraded: {signals_str} — run `trw-mcp telemetry pipeline-health` for details"
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
