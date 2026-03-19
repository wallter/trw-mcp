"""PRD auto-progression — advance PRD statuses on phase gate pass.

Implements auto_progress_prds() which evaluates state-machine transitions
for governing PRDs when a phase gate is passed (PRD-CORE-025).

FR05 (PRD-FIX-053): Multi-step BFS traversal — when the target status is
not directly reachable, compute the shortest valid path and apply each
intermediate transition in sequence, stopping at the first guard failure.
"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import cast

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus
from trw_mcp.models.typed_dicts import ProgressionItem
from trw_mcp.state.prd_utils import (
    VALID_TRANSITIONS,
    check_transition_guards,
    discover_governing_prds,
    is_valid_transition,
    parse_frontmatter,
    update_frontmatter,
)

logger = structlog.get_logger(__name__)

# PRD-CORE-025: Phase-to-Status Mapping (FR01)
PHASE_STATUS_MAPPING: dict[str, PRDStatus] = {
    "plan": PRDStatus.REVIEW,
    "implement": PRDStatus.IMPLEMENTED,
    "validate": PRDStatus.DONE,
    "deliver": PRDStatus.DONE,
}

# Terminal statuses that should never be auto-progressed.
_TERMINAL_STATUSES: frozenset[PRDStatus] = frozenset({PRDStatus.DONE, PRDStatus.MERGED, PRDStatus.DEPRECATED})


def _compute_transition_path(
    current: PRDStatus,
    target: PRDStatus,
) -> list[PRDStatus] | None:
    """BFS to find the shortest path from current to target via VALID_TRANSITIONS.

    FR05 (PRD-FIX-053): Used when ``is_valid_transition(current, target)``
    returns False — finds the sequence of intermediate states to step through.

    Only follows forward-progression edges (skips backwards/merge/deprecate
    edges like REVIEW→DRAFT) to avoid unintended regressions.

    Args:
        current: Starting PRD status.
        target: Desired PRD status.

    Returns:
        List of intermediate statuses to traverse (NOT including current,
        but INCLUDING target), or None if no path exists.
    """
    # Forward-only edges: statuses that represent genuine progression
    _FORWARD_STATUSES = {PRDStatus.REVIEW, PRDStatus.APPROVED, PRDStatus.IMPLEMENTED, PRDStatus.DONE}

    # BFS: queue of (node, path_to_node)
    queue: deque[tuple[PRDStatus, list[PRDStatus]]] = deque([(current, [])])
    visited: set[PRDStatus] = {current}

    while queue:
        node, path = queue.popleft()
        for neighbor in VALID_TRANSITIONS.get(node, set()):
            if neighbor in visited:
                continue
            new_path = [*path, neighbor]
            if neighbor == target:
                return new_path
            # Only continue through forward-progression statuses
            if neighbor in _FORWARD_STATUSES:
                visited.add(neighbor)
                queue.append((neighbor, new_path))

    return None


def _parse_current_status(
    prd_id: str,
    fm: dict[str, object],
) -> PRDStatus | None:
    """Parse and validate current PRD status from frontmatter.

    Returns PRDStatus or None if invalid.
    """
    current_str = str(fm.get("status", "draft")).lower()
    try:
        return PRDStatus(current_str)
    except ValueError:
        logger.warning(
            "auto_progress_invalid_status",
            prd_id=prd_id,
            status=current_str,
        )
        return None


def _apply_transition_steps(
    prd_file: Path,
    transition_path: list[PRDStatus],
    current_status: PRDStatus,
    config: TRWConfig,
    dry_run: bool,
) -> tuple[bool, str, str | None, str | None]:
    """Apply each step in the transition path, checking guards.

    Returns: (final_applied, final_status_str, stopped_at, stop_reason).
    """
    step_current = current_status
    step_current_str = current_status.value
    final_applied = False
    stopped_at: str | None = None
    stop_reason: str | None = None

    for step_target in transition_path:
        content = prd_file.read_text(encoding="utf-8")
        guard = check_transition_guards(
            step_current,
            step_target,
            content,
            config,
        )
        if not guard.allowed:
            stopped_at = step_current.value
            stop_reason = guard.reason
            break

        if not dry_run:
            update_frontmatter(prd_file, {"status": step_target.value})

        step_current = step_target
        step_current_str = step_target.value
        final_applied = True

    return final_applied, step_current_str, stopped_at, stop_reason


def _build_progression_result(
    prd_id: str,
    current_str: str,
    target_status: PRDStatus,
    final_applied: bool,
    final_status_str: str,
    stopped_at: str | None,
    stop_reason: str | None,
    dry_run: bool,
) -> ProgressionItem:
    """Build the progression result dict based on application outcome."""
    if final_applied:
        entry: dict[str, object] = {
            "prd_id": prd_id,
            "from_status": current_str,
            "to_status": final_status_str,
            "applied": True,
        }
        if dry_run:
            entry["applied"] = False
            entry["would_apply"] = True
        if stopped_at is not None:
            entry["partial"] = True
            entry["stopped_at"] = stopped_at
            entry["stop_reason"] = stop_reason
        return cast("ProgressionItem", entry)

    if stopped_at is not None:
        entry = {
            "prd_id": prd_id,
            "from_status": current_str,
            "to_status": target_status.value,
            "applied": False,
            "guard_failed": True,
            "reason": stop_reason or "guard_failed",
        }
        if dry_run:
            entry["would_apply"] = False
        return cast("ProgressionItem", entry)

    return cast(
        "ProgressionItem",
        {
            "prd_id": prd_id,
            "from_status": current_str,
            "to_status": target_status.value,
            "applied": False,
            "reason": "no_steps_applied",
        },
    )


def _sync_indices_if_progressed(
    results: list[ProgressionItem],
    prds_dir: Path,
) -> None:
    """Trigger index sync as best-effort side effect if any PRDs progressed."""
    if not any(r.get("applied") for r in results):
        return
    try:
        from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md
        from trw_mcp.state.persistence import FileStateWriter

        writer = FileStateWriter()
        aare_dir = prds_dir.parent
        sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=writer)
        sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=writer)
    except Exception:
        logger.debug("index_sync_failed", exc_info=True)


def auto_progress_prds(
    run_path: Path,
    phase: str,
    prds_dir: Path,
    config: TRWConfig,
    *,
    dry_run: bool = False,
) -> list[ProgressionItem]:
    """Automatically advance PRD statuses when a phase gate passes.

    PRD-CORE-025-FR02: For each PRD in the run's ``prd_scope``, evaluate the
    state-machine transition implied by the completed phase exit, check
    transition guards, and (unless *dry_run*) write the new status.

    FR05 (PRD-FIX-053): When the target status is not directly reachable from
    the current status, compute the BFS shortest path and apply each
    intermediate transition in sequence. Stops at the first guard failure and
    reports the partial progression result.

    Args:
        run_path: Path to the active run directory.
        phase: Phase that just passed exit (e.g., ``"plan"``).
        prds_dir: Directory containing PRD markdown files.
        config: Framework configuration.
        dry_run: When True, evaluate transitions without writing files.

    Returns:
        List of dicts with keys ``prd_id``, ``from_status``, ``to_status``,
        ``applied``, and optionally ``guard_failed``, ``would_apply``, ``reason``.
    """
    target_status = PHASE_STATUS_MAPPING.get(phase)
    if target_status is None:
        return []

    prd_ids = discover_governing_prds(run_path, config)
    if not prd_ids:
        return []

    results: list[ProgressionItem] = []

    for prd_id in prd_ids:
        prd_file = prds_dir / f"{prd_id}.md"
        if not prd_file.exists():
            logger.warning("auto_progress_prd_missing", prd_id=prd_id)
            continue

        try:
            content = prd_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            current_str = str(fm.get("status", "draft")).lower()

            current_status = _parse_current_status(prd_id, fm)
            if current_status is None:
                continue

            if current_status in _TERMINAL_STATUSES or current_status == target_status:
                continue

            # Determine the transition path to apply
            if is_valid_transition(current_status, target_status):
                transition_path = [target_status]
            else:
                bfs_path = _compute_transition_path(current_status, target_status)
                if bfs_path is None:
                    logger.warning(
                        "auto_progress_no_path",
                        prd_id=prd_id,
                        from_status=current_str,
                        to_status=target_status.value,
                    )
                    results.append(
                        cast(
                            "ProgressionItem",
                            {
                                "prd_id": prd_id,
                                "from_status": current_str,
                                "to_status": target_status.value,
                                "applied": False,
                                "reason": "no_transition_path",
                            },
                        )
                    )
                    continue
                transition_path = bfs_path

            final_applied, final_status_str, stopped_at, stop_reason = _apply_transition_steps(
                prd_file,
                transition_path,
                current_status,
                config,
                dry_run,
            )

            result = _build_progression_result(
                prd_id,
                current_str,
                target_status,
                final_applied,
                final_status_str,
                stopped_at,
                stop_reason,
                dry_run,
            )
            results.append(result)

        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "auto_progress_error",
                prd_id=prd_id,
                error=str(exc),
            )
            continue

    if not dry_run:
        _sync_indices_if_progressed(results, prds_dir)

    logger.info(
        "auto_progress_complete",
        phase=phase,
        total=len(results),
        applied=sum(1 for r in results if r.get("applied")),
    )
    return results
