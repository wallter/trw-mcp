"""Anchor resolution for the ``trw_learn()`` flow (PRD-CORE-111 FR04).

Belongs to the ``_learn_impl.py`` flow; extracted so ``_learn_impl`` stays under
the module-size gate and anchor derivation is unit-testable in isolation.

Composes the session-scoped inputs in :mod:`trw_mcp.tools._learn_anchor_sources`
(PRD-CORE-267 FR01: the caller's OWN pinned run, never the mtime-newest events
file anywhere under ``.trw``) with the overlap-gated generator in
:mod:`trw_mcp.state.anchor_generation` (FR02: no anchor without demonstrated
relevance, and no first-symbol fallback).

Fail-open throughout: any failure yields ``([], None)`` so a learning is still
created without anchors and without a fabricated validity score
(PRD-CORE-244 FR01).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.state._backend_id_lookup import resolve_entry_in_backend
from trw_mcp.tools._learn_anchor_sources import (
    git_diff_line_ranges,
    learning_mentions,
    modified_files_for_run,
    resolve_session_run,
)

if TYPE_CHECKING:
    from trw_mcp.state._paths import TRWCallContext

logger = structlog.get_logger(__name__)


def _absolute(project_root: Path, recorded: str) -> str:
    """Resolve a recorded event path against *project_root*.

    Hook-written events carry absolute paths; tool-written ones are relative.
    ``Path.__truediv__`` already returns the absolute operand unchanged, so one
    expression handles both.
    """
    return str(project_root / recorded)


def resolve_learn_anchors(
    project_root: Path,
    learning_id: str,
    *,
    session_id: str | None = None,
    context: TRWCallContext | None = None,
    summary: str = "",
    detail: str = "",
    evidence: list[str] | None = None,
) -> tuple[list[dict[str, object]], float | None]:
    """Resolve code anchors + initial validity for a new learning.

    Returns ``(anchors, anchor_validity)``. ``anchor_validity`` is ``None``
    whenever no anchor was actually scored — PRD-CORE-244 FR01: an unanchored
    learning has never been assessed, and reporting a perfect ``1.0`` for it is
    the defect that put a top anchor score on rows that never earned one.

    PRD-CORE-267 FR01: candidate files come ONLY from the run this caller has
    pinned. With no resolvable run the answer is no anchors — deriving nothing
    is truthful, whereas deriving from a peer session's working tree is not.
    """
    anchors: list[dict[str, object]] = []
    try:
        run_dir = resolve_session_run(context=context, session_id=session_id)
        if run_dir is None:
            logger.debug("anchor_generation_no_session_run", entry_id=learning_id)
            return [], None

        modified_rel = modified_files_for_run(run_dir)
        if not modified_rel:
            return [], None

        mentions = learning_mentions(summary, detail, evidence)

        # FR02 predicate 3 inputs: changed line ranges for the caller's own
        # files. The diff is repo-wide, but only files the run named are ever
        # looked up, so a peer's hunks in an untouched file cannot be read.
        line_ranges_rel = git_diff_line_ranges(project_root, session_id=session_id)

        modified_abs = [_absolute(project_root, rel) for rel in modified_rel]
        ranges_abs: dict[str, list[tuple[int, int]]] = {
            _absolute(project_root, rel): rng for rel, rng in line_ranges_rel.items()
        }
        mentioned_files = frozenset(
            abs_path
            for abs_path, rel in zip(modified_abs, modified_rel, strict=True)
            if mentions.mentions_file(Path(rel))
        )

        from trw_mcp.state.anchor_generation import generate_anchors

        raw_anchors = generate_anchors(
            modified_abs,
            ranges_abs,
            mentioned_names=mentions.names,
            mentioned_files=mentioned_files,
        )
        if raw_anchors:
            anchors = [dict(a) for a in raw_anchors]
    except Exception:  # justified: fail-open, anchor generation is best-effort
        logger.debug("anchor_generation_skipped", exc_info=True)
        return [], None

    if not anchors:
        return [], None

    anchor_validity: float | None = None
    try:
        from trw_memory.lifecycle.anchor_validation import compute_anchor_validity

        anchor_validity = compute_anchor_validity(anchors, str(project_root), learning_id=learning_id)
    except Exception:  # justified: fail-open, validity computation is best-effort
        logger.debug("anchor_validity_computation_skipped", exc_info=True)

    return anchors, anchor_validity


def reverify_entry_anchors(trw_dir: Path, project_root: Path, learning_id: str) -> float | None:
    """Recompute + persist an existing entry's ``anchor_validity`` (FR03).

    PRD-CORE-231-FR03: ``compute_anchor_validity()`` used to run only at
    ``trw_learn()`` write time, so a learning anchored to code that has since
    moved kept its write-time score (usually 1.0) and an undeserved recall
    ranking boost forever. This re-runs the SAME pure function against the
    CURRENT tree and writes the fresh score through.

    Returns the refreshed score, or ``None`` when there was nothing to do (no
    such entry, no anchors) or the recomputation could not run. Fail-open: an
    error never propagates to the caller's update.
    """
    try:
        from trw_memory.lifecycle.anchor_validation import compute_anchor_validity

        from trw_mcp.state.memory_adapter import get_backend

        backend = get_backend(trw_dir)
        entry = resolve_entry_in_backend(backend, learning_id)
        anchors = list(getattr(entry, "anchors", []) or []) if entry is not None else []
        if entry is None or not anchors:
            return None

        validity = compute_anchor_validity(
            [a.model_dump() if hasattr(a, "model_dump") else a for a in anchors],
            str(project_root),
            learning_id=learning_id,
        )
        backend.update(learning_id, namespace=entry.namespace, anchor_validity=validity)
        logger.info("anchor_validity_reverified", entry_id=learning_id, anchor_validity=validity)
        return validity
    except Exception:  # justified: fail-open, re-verification must not block the update
        logger.debug("anchor_reverification_skipped", entry_id=learning_id, exc_info=True)
        return None


__all__ = ["resolve_learn_anchors", "reverify_entry_anchors"]
