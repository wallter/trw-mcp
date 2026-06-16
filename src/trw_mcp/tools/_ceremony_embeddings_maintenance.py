"""Embeddings status + warm-up + backfill maintenance for session_start.

Belongs to the ``_ceremony_helpers.py`` facade. ``run_auto_maintenance``
delegates the embeddings-status portion of session_start maintenance here so the
parent stays under the 350 effective-LOC module gate. Extracted 2026-06-10 with
the Option A+ first-recall warm-up wiring.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoMaintenanceDict

logger = structlog.get_logger(__name__)

# Option A+ (council-ratified 2026-06-10): one-time low-coverage advisory guard.
# With embeddings ON by default, a fresh store has 0% vector coverage until the
# background backfill completes. The coverage advisory would otherwise surface on
# EVERY session_start until backfill finishes, crying wolf. We surface the
# human-facing advisory exactly ONCE per process (the background self-heal is
# still scheduled idempotently each session). Non-coverage advisories (e.g.
# "embeddings unavailable: deps missing") have no ``coverage_ratio`` and are NOT
# gated by this — they always surface.
_LOW_COVERAGE_ADVISORY_SHOWN = False


def reset_low_coverage_advisory_guard() -> None:
    """Reset the one-time low-coverage advisory guard (for tests)."""
    global _LOW_COVERAGE_ADVISORY_SHOWN
    _LOW_COVERAGE_ADVISORY_SHOWN = False


def run_embeddings_maintenance(
    trw_dir: Path,
    config: TRWConfig,
    maintenance: AutoMaintenanceDict,
    *,
    defer_memory_heavy: bool,
    defer_reason: str,
    writer_pids: list[int],
) -> None:
    """Probe embedding readiness; schedule warm-up / backfill as needed.

    Fail-open: any exception is logged and swallowed so the embeddings check
    never blocks ``trw_session_start``. Mutates *maintenance* in place.
    """
    try:
        if defer_memory_heavy:
            maintenance["embeddings_backfill_deferred"] = {
                "reason": defer_reason,
                "writer_pids": writer_pids,
                "writer_count": len(writer_pids),
                "threshold": config.session_start_writer_pressure_threshold,
            }
            logger.warning(
                "embeddings_backfill_deferred",
                reason=defer_reason,
                writer_pids=writer_pids,
                writer_count=len(writer_pids),
                threshold=config.session_start_writer_pressure_threshold,
            )
            return

        from trw_mcp.state.memory_adapter import check_embeddings_status

        # PRD-FIX-COMPOUNDING-3-FR02: Pass coverage_probe=True so session_start
        # surfaces the coverage_ratio advisory when vectors are missing post-recovery.
        global _LOW_COVERAGE_ADVISORY_SHOWN

        emb_status = check_embeddings_status(allow_initialize=False, coverage_probe=True)
        raw_ratio = emb_status.get("coverage_ratio")
        if raw_ratio is not None and isinstance(raw_ratio, float):
            maintenance["embeddings_coverage_ratio"] = raw_ratio

        if emb_status.get("advisory"):
            # The low-coverage nudge (has a coverage_ratio) is surfaced once per
            # process so the background self-heal isn't drowned in repeated
            # warnings. Other advisories (deps missing, etc.) always surface.
            is_low_coverage_nudge = raw_ratio is not None
            if not is_low_coverage_nudge:
                maintenance["embeddings_advisory"] = str(emb_status["advisory"])
            elif not _LOW_COVERAGE_ADVISORY_SHOWN:
                maintenance["embeddings_advisory"] = str(emb_status["advisory"])
                _LOW_COVERAGE_ADVISORY_SHOWN = True

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
            # trw_session_start is an MCP hot path. A shared server may already
            # have the local embedder initialized from a prior trw_learn call;
            # in that state the previous behavior kicked off a full synchronous
            # vector backfill here. On a large learning corpus that can run for
            # minutes, starving the shared HTTP server and making otherwise
            # healthy clients time out during session_start. Leave bulk embedding
            # maintenance to explicit install/update flows, not session startup.
            maintenance["embeddings_backfill_deferred"] = {
                "reason": "session_start_hot_path",
                "detail": (
                    "Bulk embedding backfill is skipped during trw_session_start; "
                    "run project update/bootstrap maintenance to backfill vectors."
                ),
            }
            logger.info("embeddings_backfill_deferred", reason="session_start_hot_path")
    except Exception:  # justified: fail-open, embeddings check must not block session start
        logger.warning("maintenance_embeddings_check_failed", exc_info=True)


__all__ = ["run_embeddings_maintenance"]
