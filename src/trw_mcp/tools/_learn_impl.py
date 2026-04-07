"""Core learn logic — extracted from learning.py for module-size compliance.

Dependencies that test suites patch at ``trw_mcp.tools.learning.*`` are
injected as parameters by the closure in ``learning.py`` so that patches
remain effective without needing to know about this module.
"""

from __future__ import annotations

import contextlib
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import structlog

from trw_mcp.exceptions import StateError
from trw_mcp.models.config import TRWConfig
from trw_mcp.models.learning import (
    LearningConfidence,
    LearningProtectionTier,
    LearningType,
)
from trw_mcp.models.typed_dicts import LearnResultDict
from trw_mcp.state.persistence import FileStateReader, FileStateWriter
from trw_mcp.tools._learning_helpers import (
    LearningParams,
    _validate_source_type,
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
        except Exception:  # noqa: PERF203  # per-item error handling: skip failing obsolete-mark, continue with next ref
            logger.warning(
                "auto_obsolete_failed",
                ref_id=ref_id,
                compendium_id=learning_id,
                exc_info=True,
            )


def execute_learn(  # noqa: C901
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
    client_profile: str = "",
    model_id: str = "",
    consolidated_from: list[str] | None = None,
    assertions: list[dict[str, str]] | None = None,
    is_solution_fn: Callable[[str], bool] | None = None,
    # PRD-CORE-110: Typed learning fields
    type: str = "pattern",
    nudge_line: str = "",
    expires: str = "",
    confidence: str = "unverified",
    task_type: str = "",
    domain: list[str] | None = None,
    phase_origin: str = "",
    phase_affinity: list[str] | None = None,
    team_origin: str = "",
    protection_tier: str = "normal",
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

    # PRD-CORE-110: Auto-detect phase_origin if not explicitly provided
    if not phase_origin:
        try:
            from trw_mcp.state._paths import detect_current_phase

            detected = detect_current_phase()
            if detected:
                phase_origin = detected.upper()
            else:
                logger.warning("phase_origin_no_active_run")
        except Exception:  # justified: fail-open
            logger.warning("phase_origin_detection_failed", exc_info=True)

    # PRD-CORE-110: Auto-generate nudge_line from summary if not provided
    from trw_mcp.tools._learning_helpers import truncate_nudge_line

    if nudge_line:
        nudge_line = truncate_nudge_line(nudge_line)
    else:
        nudge_line = truncate_nudge_line(summary)

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
            client_profile=client_profile,
            model_id=model_id,
            assertions=assertions,
            type=type,
            nudge_line=nudge_line,
            expires=expires,
            confidence=confidence,
            task_type=task_type,
            domain=domain,
            phase_origin=phase_origin,
            phase_affinity=phase_affinity,
            team_origin=team_origin,
            protection_tier=protection_tier,
        ),
        entries_dir,
        reader,
        writer,
        config,
    )
    if dedup_result is not None:
        return cast("LearnResultDict", dedup_result)

    # PRD-CORE-111: Generate code-grounded anchors from recently modified files
    anchors: list[dict[str, object]] = []
    anchor_validity = 1.0
    try:
        project_root = trw_dir.parent if trw_dir.name == ".trw" else trw_dir
        git_result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=5,
            cwd=str(project_root),
        )
        if git_result.returncode == 0:
            modified_rel = [f.strip() for f in git_result.stdout.strip().split("\n") if f.strip()]
            if modified_rel:
                # Resolve relative paths against project root for file reading
                modified_abs = [str(project_root / f) for f in modified_rel]
                from trw_mcp.state.anchor_generation import generate_anchors

                raw_anchors = generate_anchors(modified_abs, {})
                if raw_anchors:
                    anchors = [dict(a) for a in raw_anchors]
    except Exception:  # justified: fail-open, anchor generation is best-effort
        logger.debug("anchor_generation_skipped", exc_info=True)

    # PRD-CORE-111: Compute initial anchor validity
    if anchors:
        try:
            from trw_memory.lifecycle.anchor_validation import compute_anchor_validity as _cav

            anchor_validity = _cav(anchors, str(project_root))
        except Exception:  # justified: fail-open, validity computation is best-effort
            logger.debug("anchor_validity_computation_skipped", exc_info=True)

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
            client_profile=client_profile,
            model_id=model_id,
            assertions=assertions,
            type=type,
            nudge_line=nudge_line,
            expires=expires,
            confidence=confidence,
            task_type=task_type,
            domain=domain,
            phase_origin=phase_origin,
            phase_affinity=phase_affinity,
            team_origin=team_origin,
            protection_tier=protection_tier,
            anchors=anchors,
            anchor_validity=anchor_validity,
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
    params = LearningParams(
        summary=summary,
        detail=detail,
        learning_id=learning_id,
        tags=safe_tags,
        evidence=safe_evidence,
        impact=calibrated_impact,
        shard_id=shard_id,
        source_type=source_type,
        source_identity=source_identity,
        client_profile=client_profile,
        model_id=model_id,
        assertions=assertions,
        type=type,
        nudge_line=nudge_line,
        expires=expires,
        confidence=confidence,
        task_type=task_type,
        domain=domain,
        phase_origin=phase_origin,
        phase_affinity=phase_affinity,
        team_origin=team_origin,
        protection_tier=protection_tier,
        anchors=anchors,
        anchor_validity=anchor_validity,
    )
    entry_path = _save_yaml_backup(
        params,
        consolidated_from=consolidated_from,
        trw_dir=trw_dir,
        entries_dir=entries_dir,
        save_entry_fn=save_entry_fn,
        update_analytics_fn=update_analytics_fn,
    )

    # Forced distribution enforcement (PRD-CORE-034)
    distribution_warning, _demoted_ids = enforce_distribution(
        impact,
        calibrated_impact,
        learning_id,
        all_active,
        trw_dir,
        config,
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

    return result_dict


def _default_is_solution(summary: str) -> bool:
    """Fallback solution detection."""
    from trw_mcp.tools.learning import _is_solution_summary

    return _is_solution_summary(summary)


def _save_yaml_backup(
    params: LearningParams,
    *,
    consolidated_from: list[str] | None,
    trw_dir: Path,
    entries_dir: Path,
    save_entry_fn: Callable[..., Path],
    update_analytics_fn: Callable[..., None],
) -> Path:
    """Save YAML backup via analytics (dual-write for rollback safety)."""
    try:
        from trw_mcp.models.learning import LearningEntry

        entry = LearningEntry(
            id=params.learning_id,
            summary=params.summary,
            detail=params.detail,
            tags=params.tags,
            evidence=params.evidence,
            impact=params.impact,
            shard_id=params.shard_id,
            source_type=_validate_source_type(params.source_type),
            source_identity=params.source_identity,
            client_profile=params.client_profile,
            model_id=params.model_id,
            consolidated_from=consolidated_from or [],
            type=LearningType(params.type) if isinstance(params.type, str) else params.type,
            nudge_line=params.nudge_line,
            expires=params.expires,
            confidence=LearningConfidence(params.confidence)
            if isinstance(params.confidence, str)
            else params.confidence,
            task_type=params.task_type,
            domain=params.domain or [],
            phase_origin=params.phase_origin,
            phase_affinity=params.phase_affinity or [],
            team_origin=params.team_origin,
            protection_tier=LearningProtectionTier(params.protection_tier)
            if isinstance(params.protection_tier, str)
            else params.protection_tier,
            anchors=params.anchors or [],
            anchor_validity=params.anchor_validity,
        )
        entry_path: Path = Path(str(save_entry_fn(trw_dir, entry)))
        update_analytics_fn(trw_dir, 1)
    except (OSError, ValueError, TypeError) as _save_exc:
        logger.warning(
            "learn_db_write_failed",
            summary=params.summary[:50],
            error=str(_save_exc),
        )
        entry_path = entries_dir / f"{params.learning_id}.yaml"

    return entry_path
