"""Embeddings status + warm-up + backfill maintenance for session_start.

Belongs to the ``_ceremony_helpers.py`` facade. ``run_auto_maintenance``
delegates the embeddings-status portion of session_start maintenance here so the
parent stays under the 350 effective-LOC module gate. Extracted 2026-06-10 with
the Option A+ first-recall warm-up wiring.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoMaintenanceDict
from trw_mcp.state.deferral_ledger import DeferralDecision
from trw_mcp.state.memory_pressure import WriterCensus

logger = structlog.get_logger(__name__)

# Option A+ (council-ratified 2026-06-10): one-time low-coverage advisory guard.
# With embeddings ON by default, a fresh store has 0% vector coverage until the
# background backfill completes. The coverage advisory would otherwise surface on
# EVERY session_start until backfill finishes, crying wolf. We surface the
# human-facing advisory exactly once per project (the background self-heal is
# still scheduled idempotently each session). Non-coverage advisories (e.g.
# "embeddings unavailable: deps missing") have no ``coverage_ratio`` and are NOT
# gated by this — they always surface.
_MAX_LOW_COVERAGE_PROJECTS = 256
_low_coverage_projects: OrderedDict[str, None] = OrderedDict()
_low_coverage_projects_lock = threading.Lock()


def _claim_low_coverage_advisory(trw_dir: Path) -> bool:
    key = str(trw_dir.resolve())
    with _low_coverage_projects_lock:
        if key in _low_coverage_projects:
            _low_coverage_projects.move_to_end(key)
            return False
        _low_coverage_projects[key] = None
        while len(_low_coverage_projects) > _MAX_LOW_COVERAGE_PROJECTS:
            _low_coverage_projects.popitem(last=False)
        return True


def reset_low_coverage_advisory_guard() -> None:
    """Reset the one-time low-coverage advisory guard (for tests)."""
    with _low_coverage_projects_lock:
        _low_coverage_projects.clear()


def run_embeddings_maintenance(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    *,
    census: WriterCensus,
    defer_memory_heavy: bool,
) -> None:
    """Probe embedding readiness; schedule warm-up / backfill as needed.

    Fail-open: any exception is logged and swallowed so the embeddings check
    never blocks ``trw_session_start``. Mutates *maintenance* in place.

    PRD-CORE-257-FR08: writer pressure skips ONLY the post-recovery backfill
    schedule. The deferral branch used to return before three downstream calls
    while reporting one ``embeddings_backfill_deferred`` key, so a pressured
    session silently lost the read-only coverage probe (which populates
    ``embeddings_coverage_ratio`` and the coverage advisory) and the first-recall
    warm-up guard as well. SQLite in WAL mode serves readers concurrently with a
    single writer, so a read probe is not the lock-stacking risk these controls
    were built for, and the warm-up spawns a thread without touching the store.
    """
    from trw_mcp.tools._ceremony_maintenance_steps import (
        _record_step_outcome,
        _step_pressure_decision,
        _writer_pressure_details,
    )

    # The ledger is consulted only when the backfill would actually be
    # scheduled, so a healthy corpus never opens a phantom deferral streak.
    decision: DeferralDecision | None = None
    try:
        from trw_mcp.state.memory_adapter import check_embeddings_status

        # PRD-FIX-COMPOUNDING-3-FR02: Pass coverage_probe=True so session_start
        # surfaces the coverage_ratio advisory when vectors are missing post-recovery.
        emb_status = check_embeddings_status(allow_initialize=False, coverage_probe=True)
        raw_ratio = emb_status.get("coverage_ratio")
        if raw_ratio is not None and isinstance(raw_ratio, float):
            maintenance["embeddings_coverage_ratio"] = raw_ratio

        if emb_status.get("advisory"):
            # The low-coverage nudge (has a coverage_ratio) is surfaced once per
            # project so the background self-heal isn't drowned in repeated
            # warnings. Other advisories (deps missing, etc.) always surface.
            is_low_coverage_nudge = raw_ratio is not None
            if not is_low_coverage_nudge or _claim_low_coverage_advisory(trw_dir):
                maintenance["embeddings_advisory"] = str(emb_status["advisory"])

        # Option A+ (council-ratified 2026-06-10): first-recall download guard.
        # With embeddings ON by default, the hot path deferred cold init
        # (allow_initialize=False). On a never-cached box the FIRST trw_recall
        # that allows cold init would pay the all-MiniLM-L6-v2 *download*
        # synchronously, risking an MCP-client timeout. Kick a NON-BLOCKING
        # background warm-up so the download lands off the hot path; recall
        # degrades to keyword (get_initialized_embedder -> None) until it
        # completes. The single-flight guard makes repeated session_starts
        # idempotent. Only fire when init was deferred (model not yet loaded).
        if emb_status.get("enabled") and emb_status.get("initialization_deferred"):
            from trw_mcp.state._memory_connection import _schedule_embedder_warmup

            warmup_started = _schedule_embedder_warmup()
            maintenance["embedder_warmup_scheduled"] = {
                "reason": "first_recall_download_guard",
                "thread_started": warmup_started,
            }
            logger.info(
                "embedder_warmup_scheduled_session_start",
                thread_started=warmup_started,
            )

        # PRD-FIX-105-FR01: When coverage is LOW (advisory present), the prior
        # code only surfaced the warning and never remediated — so a
        # post-recovery vector loss (canonical rows salvaged, vec0 tables
        # reset) left the corpus stuck at ~4.6% coverage indefinitely while
        # the advisory cried wolf every session. Schedule a BACKGROUND backfill
        # (singleton thread guard, no-op while one is running) so the corpus
        # self-heals without starving the shared HTTP hot path.
        if (
            emb_status.get("advisory")
            and emb_status.get("enabled")
            and emb_status.get("available")
            and config.embeddings_auto_backfill_on_low_coverage
        ):
            decision = _step_pressure_decision(
                trw_dir, config, maintenance, "embeddings_backfill", defer_memory_heavy=defer_memory_heavy
            )
            if decision.defer:
                # Only the expensive self-heal is skipped, and the advisory says
                # so by NAME rather than implying the whole step was skipped.
                deferred_block = _writer_pressure_details(census, decision)
                deferred_block["detail"] = (
                    "Only the background post-recovery vector backfill is deferred; the coverage "
                    "probe and the embedder warm-up ran."
                )
                maintenance["embeddings_backfill_deferred"] = deferred_block
                logger.warning(
                    "embeddings_backfill_schedule_deferred",
                    reason="writer_pressure",
                    writer_count=census.writer_count,
                    peer_writer_count=census.peer_writer_count,
                    threshold=census.threshold,
                    deferral_age_hours=decision.age_hours,
                )
            else:
                from trw_mcp.state._memory_connection import _schedule_post_recovery_backfill

                started = _schedule_post_recovery_backfill(trw_dir)
                maintenance["embeddings_backfill_scheduled"] = {
                    "reason": "low_coverage",
                    "coverage_ratio": raw_ratio,
                    "thread_started": started,
                }
                logger.warning(
                    "embeddings_backfill_scheduled_low_coverage",
                    coverage_ratio=raw_ratio,
                    thread_started=started,
                )

        if not emb_status.get("advisory") and emb_status.get("enabled") and emb_status.get("available"):
            # trw_session_start is an MCP hot path. Within this stdio process
            # the local embedder may already be initialized from a prior
            # trw_learn call; in that state the previous behavior kicked off a
            # full synchronous vector backfill here. On a large learning corpus
            # that can run for minutes, blocking this client's stdio trw-mcp
            # process and making its session_start time out. Leave bulk
            # embedding maintenance to explicit install/update flows, not
            # session startup.
            #
            # DEF-11: this used to share the ``embeddings_backfill_deferred``
            # key with the writer-pressure branch above, which IS a real,
            # ledger-consulted deferral (``_step_pressure_decision`` /
            # ``_record_step_outcome`` / ``step_deferral_decision`` bound by
            # ``session_start_max_deferral_hours``, and forced to run once the
            # streak expires). This branch has no decision object, no ledger
            # entry, and no consumer that EVER performs a bulk backfill from
            # session start — coverage is healthy here, so there is nothing to
            # catch up on; the standing architectural choice is simply that
            # session start never does bulk backfill work. Named
            # ``_not_performed`` so it stops claiming a resumption no
            # consumer provides.
            maintenance["embeddings_backfill_not_performed"] = {
                "reason": "session_start_hot_path",
                "detail": (
                    "Bulk embedding backfill is skipped during trw_session_start; "
                    "run project update/bootstrap maintenance to backfill vectors."
                ),
            }
            logger.info("embeddings_backfill_not_performed", reason="session_start_hot_path")
        if decision is not None:
            _record_step_outcome(trw_dir, maintenance, "embeddings_backfill", decision)
    except Exception:  # justified: fail-open, embeddings check must not block session start
        logger.warning("maintenance_embeddings_check_failed", exc_info=True)
        if decision is not None:
            _record_step_outcome(trw_dir, maintenance, "embeddings_backfill", decision, failed=True)


__all__ = ["run_embeddings_maintenance"]
