"""Session recall helpers for ceremony.py — live session-start recall logic."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.config._defaults import LIGHT_MODE_RECALL_CAP
from trw_mcp.models.typed_dicts import SessionRecallExtrasDict
from trw_mcp.state import memory_adapter
from trw_mcp.state._origin_project import demote_unattributable
from trw_mcp.state._recall_gate import learnings_injection_allowed
from trw_mcp.state._session_id import resolve_effective_session_id
from trw_mcp.state._store_counts import store_entry_count
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.state.receipts import log_recall_receipt
from trw_mcp.state.surface_tracking import log_surface_event

logger = structlog.get_logger(__name__)

_ANTIPATTERN_KEYWORDS: tuple[str, ...] = (
    "facade",
    "wiring gap",
    "unwired",
    "dead code",
    "false completion",
    "not wired",
    "integration gap",
)

_SYSTEM_TASK_KEYWORDS: tuple[str, ...] = (
    "model",
    "system",
    "profile",
    "adapter",
    "framework",
    "registry",
)

#: PRD-CORE-278 FR09: acquire twice the cap so the attribution partition has
#: something to promote. Partitioning AFTER a cap cannot recover a local row the
#: cap already excluded — with 1,330 synced rows against 13 local ones, every
#: slot was spent before this project's own knowledge was reached (L-XIhp). Two
#: is a bounded factor, not a heuristic: it is the smallest multiple that lets a
#: fully foreign first page be replaced by a fully local second one.
_ATTRIBUTION_OVERFETCH = 2


def dedupe_learning_ids(learning_ids: list[str]) -> list[str]:
    """Preserve order while removing duplicate/empty learning IDs."""

    seen: set[str] = set()
    unique_ids: list[str] = []
    for learning_id in learning_ids:
        if not learning_id or learning_id in seen:
            continue
        seen.add(learning_id)
        unique_ids.append(learning_id)
    return unique_ids


def _log_session_start_surfaces(trw_dir: Path, learning_ids: list[str]) -> None:
    """Best-effort session-start surface logging with structured observability."""

    try:
        sid = resolve_effective_session_id(trw_dir)
        for learning_id in learning_ids:
            log_surface_event(
                trw_dir,
                learning_id=learning_id,
                surface_type="session_start",
                session_id=sid,
            )
    except (ImportError, OSError, RuntimeError, ValueError, TypeError):
        logger.warning(
            "session_start_surface_log_failed",
            op="session_recall",
            outcome="fail_open",
            exc_info=True,
        )


def record_session_start_surfaces(trw_dir: Path, learning_ids: list[str]) -> list[str]:
    """Record shared session-start side effects; return the deduped ids recorded."""

    unique_ids = dedupe_learning_ids(learning_ids)
    if not unique_ids:
        return []
    # Looked up at call time so a patch on memory_adapter reaches this path.
    memory_adapter.record_surfaced(trw_dir, unique_ids, session_start=True)
    _log_session_start_surfaces(trw_dir, unique_ids)
    return unique_ids


def _apply_antipattern_alerts(
    learnings: list[dict[str, object]],
    query: str,
    is_focused: bool,
) -> list[dict[str, object]]:
    """Prepend anti-pattern alert prefix to matching learning summaries."""

    if not is_focused or not learnings:
        return learnings

    query_lower = query.lower()
    if not any(keyword in query_lower for keyword in _SYSTEM_TASK_KEYWORDS):
        return learnings

    alert_prefix = "⚠ ANTI-PATTERN ALERT: "
    result: list[dict[str, object]] = []
    for entry in learnings:
        summary = str(entry.get("summary", "") or "")
        if any(keyword in summary.lower() for keyword in _ANTIPATTERN_KEYWORDS):
            entry = {**entry, "summary": alert_prefix + summary}
        result.append(entry)
    return result


def perform_session_recalls(
    trw_dir: Path,
    query: str,
    config: TRWConfig,
    reader: FileStateReader,
    *,
    verbose: bool = False,
) -> tuple[list[dict[str, object]], SessionRecallExtrasDict]:
    """Run the ONE session_start recall and present it (PRD-CORE-294 FR02).

    Returns ``(learnings, extra)``. By default ``learnings`` holds at most
    ``SESSION_MAX_STUBS`` stubs from the FR01 presenter and the whole learning
    block (the stubs plus the ``extra`` keys) renders within
    ``SESSION_BYTE_BUDGET`` bytes; ``verbose=True`` returns the full ranked rows
    instead, internal fields stripped as ``trw_recall`` strips them. Rank is
    unchanged by either form.

    PRD-CORE-263-FR01 / DEF-01: exceptions (canary tamper included) propagate to
    :func:`trw_mcp.tools._ceremony_session_start_steps.step_recall_learnings`,
    whose critical branch converts them into ``success: False`` with a typed
    reason. Store failures already classified for background recovery are
    handled in :mod:`trw_mcp.state._memory_recall` (FR07) and never reach here.
    """

    if not learnings_injection_allowed(config, "session_start"):
        logger.debug("session_recall_gated", reason="learnings_injection_allowed=False")
        return [], {}

    is_focused = query.strip() not in ("", "*")
    extra: SessionRecallExtrasDict = {}

    effective_max = (
        min(config.recall_max_results, LIGHT_MODE_RECALL_CAP)
        if config.effective_ceremony_mode == "light"
        else config.recall_max_results
    )
    overfetch = _ATTRIBUTION_OVERFETCH

    from trw_mcp.state.recall_factories import FOCUSED_ZERO_MATCH_ADVISORY, recall_session_start
    from trw_mcp.tools._session_recall_content import carry_full_rows

    learnings = carry_full_rows(
        recall_session_start(trw_dir, query if is_focused else "*", max_results=effective_max * overfetch)
    )
    if is_focused:
        extra["query"] = query
        if not learnings:
            extra["query_advisory"] = FOCUSED_ZERO_MATCH_ADVISORY

    from trw_mcp.scoring import rank_targeted_by_utility
    from trw_mcp.tools._recall_assertion_verification import _verify_assertions

    # Qualify every acquired candidate before the final startup result cap.
    learnings = _verify_assertions(
        learnings, query.lower().split() if is_focused else [], config, rank_targeted_by_utility
    )
    # PRD-CORE-278 FR09: this project's own knowledge takes the slots first.
    # Ordering, not filtering — a repository whose store is thin still sees the
    # rest, just after what is actually about the checkout in front of it.
    learnings = demote_unattributable(learnings)
    if effective_max > 0:
        learnings = learnings[:effective_max]
    try:
        learnings = _apply_antipattern_alerts(learnings, query, is_focused)
    except (RuntimeError, ValueError, TypeError):
        logger.warning("antipattern_alert_failed", op="session_recall", outcome="fail_open", exc_info=True)

    # PRD-FIX-141-FR05: the corpus size, omitted rather than zeroed when unread.
    store_count = store_entry_count(trw_dir)
    if store_count is not None:
        extra["store_count"] = store_count

    from trw_mcp.tools._session_recall_content import full_rows

    if verbose:
        shown = learnings
        presented = full_rows(learnings, config.recall_internal_fields)
    else:
        from trw_mcp.tools._recall_presenter import SESSION_BYTE_BUDGET, SESSION_MAX_STUBS, present

        # The block is the stubs plus every ``extra`` key above: the budget covers all of them.
        block = cast("dict[str, object]", extra)
        presented = present(
            block,
            learnings,
            query_tokens=query.lower().split() if is_focused else (),
            byte_budget=SESSION_BYTE_BUDGET,
            max_stubs=SESSION_MAX_STUBS,
        )
        shown = learnings[: len(presented)]
        block.pop("learnings", None)
        omitted = block.pop("omitted", 0)
        if isinstance(omitted, int) and omitted > 0:
            extra["learnings_omitted"] = omitted

    recorded_ids = record_session_start_surfaces(
        trw_dir,
        [str(entry.get("id", "")) for entry in shown if entry.get("id")],
    )
    log_recall_receipt(trw_dir, query if is_focused else "*", recorded_ids)

    logger.info(
        "session_recalls_complete",
        ranked=len(learnings),
        shown=len(shown),
        is_focused=is_focused,
    )
    return presented, extra
