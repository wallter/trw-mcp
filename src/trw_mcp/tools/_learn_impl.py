"""Core learn logic — extracted from learning.py for module-size compliance.

Dependencies that test suites patch at ``trw_mcp.tools.learning.*`` are
injected as parameters by the closure in ``learning.py`` so that patches
remain effective without needing to know about this module.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.typed_dicts import LearnResultDict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._learning_helpers import (
    LearningParams,
    calibrate_impact,
    check_soft_cap,
    enforce_distribution,
    is_noise_summary,
)

logger = structlog.get_logger(__name__)


def _handle_consolidation(
    learning_id: str,
    consolidated_from: list[str] | None,
    entries_dir: Path,
    reader: FileStateReader,
    writer: FileStateWriter,
    trw_dir: Path,
) -> None:
    """Handle auto-obsolete of superseded entries (PRD-FIX-052-FR04)."""
    if not consolidated_from:
        return

    from datetime import datetime, timezone

    from trw_mcp.state.analytics import find_entry_by_id
    from trw_mcp.state.memory_adapter import update_learning as adapter_update

    for ref_id in consolidated_from:
        try:
            update_result = adapter_update(
                trw_dir,
                learning_id=ref_id,
                status="obsolete",
            )
            if update_result.get("status") == "updated":
                try:
                    found = find_entry_by_id(entries_dir, ref_id)
                    if found is not None:
                        entry_path_ref, data_ref = found
                        data_ref["status"] = "obsolete"
                        _today = datetime.now(tz=timezone.utc).date().isoformat()
                        data_ref["resolved_at"] = _today
                        data_ref["updated"] = _today
                        writer.write_yaml(entry_path_ref, data_ref)
                except (OSError, ValueError, TypeError):
                    logger.debug(
                        "auto_obsolete_yaml_backup_failed",
                        ref_id=ref_id,
                        exc_info=True,
                    )
                logger.info(
                    "auto_obsolete_marked",
                    ref_id=ref_id,
                    compendium_id=learning_id,
                )
            else:
                logger.warning(
                    "auto_obsolete_not_found",
                    ref_id=ref_id,
                    compendium_id=learning_id,
                )
        except Exception:  # per-item error handling: skip failing obsolete-mark, continue with next ref  # noqa: PERF203
            logger.warning(
                "auto_obsolete_failed",
                ref_id=ref_id,
                compendium_id=learning_id,
                exc_info=True,
            )


def execute_learn(  # noqa: C901 — orchestrates validation, dedup, store, distribution
    summary: str,
    detail: str,
    trw_dir: Path,
    config: TRWConfig,
    *,
    tags: list[str] | None = None,
    evidence: list[str] | None = None,
    impact: float = 0.5,
    shard_id: str | None = None,
    source_type: str = "agent",
    source_identity: str = "",
    consolidated_from: list[str] | None = None,
    assertions: list[dict[str, str]] | None = None,
    is_solution_fn: Callable[[str], bool] | None = None,
    # Injected deps (patched at trw_mcp.tools.learning.* in tests)
    _adapter_store: Any = None,
    _generate_learning_id: Any = None,
    _save_learning_entry: Any = None,
    _update_analytics: Any = None,
    _list_active_learnings: Any = None,
    _check_and_handle_dedup: Any = None,
) -> LearnResultDict:
    """Execute the core learn workflow: validate, dedup, store, distribute.

    Args:
        summary: One-line summary.
        detail: Full context.
        trw_dir: Resolved .trw directory path.
        config: TRW configuration.
        tags: Categorization tags.
        evidence: Supporting evidence.
        impact: Impact score 0.0-1.0.
        shard_id: Optional shard identifier.
        source_type: Learning provenance.
        source_identity: Name of source.
        consolidated_from: IDs of superseded entries.
        assertions: Machine-verifiable assertions.
        is_solution_fn: Callable to detect solution patterns.
        _adapter_store: Injected store_learning function.
        _generate_learning_id: Injected ID generator.
        _save_learning_entry: Injected YAML backup writer.
        _update_analytics: Injected analytics updater.
        _list_active_learnings: Injected active learnings lister.
        _check_and_handle_dedup: Injected dedup checker.
    """
    # Resolve injected deps with fallbacks
    from trw_mcp.state.analytics import generate_learning_id as _default_gen_id
    from trw_mcp.state.analytics import save_learning_entry as _default_save
    from trw_mcp.state.analytics import update_analytics as _default_update_a
    from trw_mcp.state.memory_adapter import list_active_learnings as _default_list
    from trw_mcp.state.memory_adapter import store_learning as _default_store
    from trw_mcp.tools._learning_helpers import check_and_handle_dedup as _default_dedup

    store_fn = _adapter_store or _default_store
    gen_id_fn = _generate_learning_id or _default_gen_id
    save_entry_fn = _save_learning_entry or _default_save
    update_analytics_fn = _update_analytics or _default_update_a
    list_active_fn = _list_active_learnings or _default_list
    dedup_fn = _check_and_handle_dedup or _default_dedup

    # Input validation (PRD-QUAL-042-FR06): impact bounds
    impact = max(0.0, min(1.0, impact))

    # PRD-QUAL-032-FR09: Reject auto-generated noise entries early
    if is_noise_summary(summary):
        return {
            "status": "rejected",
            "reason": "noise_filter",
            "message": f"Summary matches noise pattern — not persisted: {summary[:60]}",
        }

    reader = FileStateReader()
    writer = FileStateWriter()
    entries_dir = trw_dir / config.learnings_dir / config.entries_dir
    writer.ensure_dir(entries_dir)

    # One-time batch dedup migration (PRD-CORE-042 FR05)
    if config.dedup_enabled:
        try:
            from trw_mcp.state.dedup import batch_dedup, is_migration_needed

            if is_migration_needed(trw_dir):
                batch_dedup(trw_dir, reader, writer, config=config)
        except (ImportError, OSError, ValueError, TypeError):
            logger.debug("learning_migration_failed", exc_info=True)

    # PRD-FIX-052-FR05: Pattern tag auto-suggestion for solution summaries
    safe_tags = list(tags or [])
    _is_sol = is_solution_fn if callable(is_solution_fn) else _default_is_solution
    if _is_sol(summary) and "pattern" not in safe_tags:
        safe_tags.append("pattern")
        logger.debug("pattern_tag_auto_added", summary=summary[:60])

    # Bayesian calibration of impact score (PRD-CORE-034)
    calibrated_impact = calibrate_impact(impact, config)

    # Fetch active learnings once -- reused by soft-cap and distribution
    all_active: list[dict[str, object]] = []
    with contextlib.suppress(OSError, StateError, ValueError, TypeError):
        all_active = list_active_fn(trw_dir)
    calibrated_impact, distribution_soft_cap_warning = check_soft_cap(
        calibrated_impact,
        all_active,
        config,
    )

    learning_id = gen_id_fn()

    # Semantic dedup check (PRD-CORE-042) -- must run BEFORE storing
    safe_evidence = evidence or []
    dedup_result = dedup_fn(
        LearningParams(
            summary=summary,
            detail=detail,
            learning_id=learning_id,
            tags=safe_tags,
            evidence=safe_evidence,
            impact=calibrated_impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
            assertions=assertions,
        ),
        entries_dir,
        reader,
        writer,
        config,
    )
    if dedup_result is not None:
        return cast("LearnResultDict", dedup_result)

    # Store via SQLite adapter (primary path)
    try:
        store_fn(
            trw_dir,
            learning_id=learning_id,
            summary=summary,
            detail=detail,
            tags=safe_tags,
            evidence=safe_evidence,
            impact=calibrated_impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
            assertions=assertions,
        )
    except Exception:  # justified: boundary, adapter may hit SQLite/network errors; fall through to YAML
        logger.warning(
            "learning_store_failed",
            learning_id=learning_id,
            summary=summary[:50],
            exc_info=True,
        )

    # PRD-FIX-052-FR04: Auto-obsolete superseded entries
    _handle_consolidation(learning_id, consolidated_from, entries_dir, reader, writer, trw_dir)

    # Save YAML backup via analytics (dual-write for rollback safety)
    entry_path = _save_yaml_backup(
        learning_id, summary, detail, safe_tags, safe_evidence,
        calibrated_impact, shard_id, source_type, source_identity,
        consolidated_from, trw_dir, entries_dir,
        save_entry_fn, update_analytics_fn,
    )

    # Forced distribution enforcement (PRD-CORE-034)
    distribution_warning, _demoted_ids = enforce_distribution(
        impact, calibrated_impact, learning_id, all_active, trw_dir, config,
    )

    logger.info(
        "learn_ok",
        summary_len=len(summary),
        tags=safe_tags,
        impact=calibrated_impact,
        id=learning_id,
    )
    result_dict: LearnResultDict = {
        "learning_id": learning_id,
        "path": str(entry_path),
        "status": "recorded",
        "distribution_warning": distribution_warning,
    }
    if distribution_soft_cap_warning:
        result_dict["distribution_warning"] = distribution_soft_cap_warning

    # Increment learnings count in ceremony state tracker (PRD-CORE-074 FR04)
    try:
        from trw_mcp.state.ceremony_nudge import increment_learnings

        increment_learnings(trw_dir)
    except Exception:  # justified: fail-open
        logger.debug("learn_ceremony_state_update_skipped", exc_info=True)

    # Inject ceremony nudge into response (PRD-CORE-074 FR01, PRD-CORE-084 FR02)
    try:
        from trw_mcp.state.ceremony_nudge import NudgeContext, ToolName
        from trw_mcp.tools._ceremony_helpers import append_ceremony_nudge

        ctx = NudgeContext(tool_name=ToolName.LEARN)
        append_ceremony_nudge(cast("dict[str, object]", result_dict), trw_dir, context=ctx)
    except Exception:  # justified: fail-open
        logger.debug("learn_nudge_injection_skipped", exc_info=True)

    return result_dict


def _default_is_solution(summary: str) -> bool:
    """Fallback solution detection."""
    from trw_mcp.tools.learning import _is_solution_summary

    return _is_solution_summary(summary)


def _save_yaml_backup(
    learning_id: str,
    summary: str,
    detail: str,
    tags: list[str],
    evidence: list[str],
    impact: float,
    shard_id: str | None,
    source_type: str,
    source_identity: str,
    consolidated_from: list[str] | None,
    trw_dir: Path,
    entries_dir: Path,
    save_entry_fn: Any,
    update_analytics_fn: Any,
) -> Path:
    """Save YAML backup via analytics (dual-write for rollback safety)."""
    try:
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id=learning_id,
            summary=summary,
            detail=detail,
            tags=tags,
            evidence=evidence,
            impact=impact,
            shard_id=shard_id,
            source_type=source_type,
            source_identity=source_identity,
            consolidated_from=consolidated_from or [],
        )
        entry_path: Path = Path(str(save_entry_fn(trw_dir, entry)))
        update_analytics_fn(trw_dir, 1)
    except (OSError, ValueError, TypeError) as _save_exc:
        logger.warning(
            "learn_db_write_failed",
            summary=summary[:50],
            error=str(_save_exc),
        )
        entry_path = entries_dir / f"{learning_id}.yaml"

    return entry_path
