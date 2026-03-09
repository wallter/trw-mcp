"""PRD auto-progression — advance PRD statuses on phase gate pass.

Implements auto_progress_prds() which evaluates state-machine transitions
for governing PRDs when a phase gate is passed (PRD-CORE-025).
"""

from __future__ import annotations

from pathlib import Path

import structlog

from trw_mcp.models.config import TRWConfig
from trw_mcp.models.requirements import PRDStatus

logger = structlog.get_logger()

# PRD-CORE-025: Phase-to-Status Mapping (FR01)
PHASE_STATUS_MAPPING: dict[str, PRDStatus] = {
    "plan": PRDStatus.REVIEW,
    "implement": PRDStatus.IMPLEMENTED,
    "validate": PRDStatus.DONE,
    "deliver": PRDStatus.DONE,
}

# Terminal statuses that should never be auto-progressed.
_TERMINAL_STATUSES: frozenset[PRDStatus] = frozenset(
    {PRDStatus.DONE, PRDStatus.MERGED, PRDStatus.DEPRECATED}
)


def auto_progress_prds(
    run_path: Path,
    phase: str,
    prds_dir: Path,
    config: TRWConfig,
    *,
    dry_run: bool = False,
) -> list[dict[str, object]]:
    """Automatically advance PRD statuses when a phase gate passes.

    PRD-CORE-025-FR02: For each PRD in the run's ``prd_scope``, evaluate the
    state-machine transition implied by the completed phase exit, check
    transition guards, and (unless *dry_run*) write the new status.

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
    from trw_mcp.state.prd_utils import (
        check_transition_guards,
        discover_governing_prds,
        is_valid_transition,
        parse_frontmatter,
        update_frontmatter,
    )

    target_status = PHASE_STATUS_MAPPING.get(phase)
    if target_status is None:
        return []

    prd_ids = discover_governing_prds(run_path, config)
    if not prd_ids:
        return []

    results: list[dict[str, object]] = []

    for prd_id in prd_ids:
        prd_file = prds_dir / f"{prd_id}.md"
        if not prd_file.exists():
            logger.warning("auto_progress_prd_missing", prd_id=prd_id)
            continue

        try:
            content = prd_file.read_text(encoding="utf-8")
            fm = parse_frontmatter(content)
            current_str = str(fm.get("status", "draft")).lower()
            try:
                current_status = PRDStatus(current_str)
            except ValueError:
                logger.warning(
                    "auto_progress_invalid_status",
                    prd_id=prd_id,
                    status=current_str,
                )
                continue

            # Skip terminal and identity transitions
            if current_status in _TERMINAL_STATUSES:
                continue
            if current_status == target_status:
                continue

            # Check state machine validity
            if not is_valid_transition(current_status, target_status):
                results.append({
                    "prd_id": prd_id,
                    "from_status": current_str,
                    "to_status": target_status.value,
                    "applied": False,
                    "reason": "invalid_transition",
                })
                continue

            # Check transition guards
            guard = check_transition_guards(
                current_status, target_status, content, config,
            )
            if not guard.allowed:
                entry: dict[str, object] = {
                    "prd_id": prd_id,
                    "from_status": current_str,
                    "to_status": target_status.value,
                    "applied": False,
                    "guard_failed": True,
                    "reason": guard.reason,
                }
                if dry_run:
                    entry["would_apply"] = False
                results.append(entry)
                continue

            if dry_run:
                results.append({
                    "prd_id": prd_id,
                    "from_status": current_str,
                    "to_status": target_status.value,
                    "applied": False,
                    "would_apply": True,
                })
            else:
                update_frontmatter(prd_file, {"status": target_status.value})
                results.append({
                    "prd_id": prd_id,
                    "from_status": current_str,
                    "to_status": target_status.value,
                    "applied": True,
                })

        except (OSError, ValueError, TypeError) as exc:
            logger.warning(
                "auto_progress_error", prd_id=prd_id, error=str(exc),
            )
            continue

    # FR06: Trigger index sync as best-effort side effect
    if not dry_run and any(r.get("applied") for r in results):
        try:
            from trw_mcp.state.index_sync import sync_index_md, sync_roadmap_md
            from trw_mcp.state.persistence import FileStateWriter

            writer = FileStateWriter()
            aare_dir = prds_dir.parent
            sync_index_md(aare_dir / "INDEX.md", prds_dir, writer=writer)
            sync_roadmap_md(aare_dir / "ROADMAP.md", prds_dir, writer=writer)
        except Exception:
            pass  # Best-effort — never fail auto-progression for sync issues

    logger.info(
        "auto_progress_complete",
        phase=phase,
        total=len(results),
        applied=sum(1 for r in results if r.get("applied")),
    )
    return results
