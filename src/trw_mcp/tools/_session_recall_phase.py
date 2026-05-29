"""Phase-contextual auto-recall — extracted from _session_recall_helpers.py.

Belongs to the ``_session_recall_helpers.py`` facade. Re-exported there
for back-compat.

Single helper:
- ``_phase_contextual_recall`` — PRD-CORE-049 phase-contextual auto-recall
  with bandit-aware ranking via ``IntelligenceCache``.

Extracted as DIST-243 batch 39 to keep the parent
``_session_recall_helpers.py`` module under the 350 effective-LOC ceiling.
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import AutoRecalledItemDict, RunStatusDict
from trw_mcp.scoring import rank_by_utility
from trw_mcp.scoring._recall import RecallContext
from trw_mcp.state.propensity_log import log_ranked_selections

logger = structlog.get_logger(__name__)


def _phase_contextual_recall(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    run_dir: Path | None,
    run_status: RunStatusDict | None,
) -> list[AutoRecalledItemDict]:
    """Execute phase-contextual auto-recall (PRD-CORE-049)."""
    del run_dir  # accepted for API parity; not currently used
    from trw_mcp.state.memory_adapter import recall_learnings as adapter_recall_ar
    from trw_mcp.tools._session_recall_helpers import _phase_to_tags

    is_focused = query.strip() not in ("", "*")
    query_tokens: list[str] = []
    if is_focused:
        query_tokens.extend(query.strip().split())

    phase_tags: list[str] | None = None
    phase = ""
    if run_status is not None:
        task_name = str(run_status.get("task_name", ""))
        phase = str(run_status.get("phase", ""))
        if task_name:
            query_tokens.append(task_name)
        if phase:
            query_tokens.append(phase)
            phase_tag_list = _phase_to_tags(phase)
            if phase_tag_list:
                phase_tags = phase_tag_list

    ar_query = " ".join(query_tokens) if query_tokens else "*"
    ar_entries = adapter_recall_ar(
        trw_dir,
        query=ar_query,
        tags=phase_tags,
        min_impact=0.5,
        max_results=config.auto_recall_max_results * 3,
        compact=True,
        allow_cold_embedding_init=False,
    )
    if not ar_entries:
        return []

    intel_cache = None
    try:
        from trw_mcp.sync.cache import IntelligenceCache

        cache = IntelligenceCache(
            trw_dir,
            ttl_seconds=getattr(config, "intel_cache_ttl_seconds", 3600),
        )
        if cache.get_bandit_params() is not None:
            intel_cache = cache
    except Exception:  # justified: fail-open, auto-recall must work without cache access
        intel_cache = None

    context = RecallContext(
        current_phase=phase.upper() if phase else None,
        intel_cache=intel_cache,
    )
    ranked = rank_by_utility(
        ar_entries,
        query_tokens,
        lambda_weight=config.recall_utility_lambda,
        context=context,
    )
    capped = ranked[: config.auto_recall_max_results]
    try:
        log_ranked_selections(
            trw_dir,
            capped,
            context_phase=phase.upper() if phase else "",
            context_task_type="phase_auto_recall",
            context_session_progress=phase.lower() if phase else "",
        )
    except (OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "phase_auto_recall_propensity_log_failed",
            op="session_recall",
            outcome="fail_open",
            exc_info=True,
        )
    return [
        {
            "id": str(entry.get("id", "")),
            "summary": str(entry.get("summary", "")),
            "impact": float(str(entry.get("impact", 0.0))),
        }
        for entry in capped
    ]
