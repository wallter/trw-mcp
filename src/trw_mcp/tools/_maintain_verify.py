"""Explicit assertion/anchor evidence refresh (CORE268; existing maintain-verify).

Traverse a stable finite eligible corpus in bounded namespace/id pages, preserving
progress even when verification or persistence fails. This is not an automatic
schedule, global snapshot, hard timeout or bounded-staleness guarantee.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(slots=True)
class MaintainVerifySummary:
    """Result of one sweep — the NFR04 audit record, in typed form."""

    entries_processed: int = 0
    stale_transitions: int = 0
    cleared_transitions: int = 0
    persist_failures: int = 0
    duration_ms: int = 0

    def as_dict(self) -> dict[str, int]:
        """Plain mapping for CLI/JSON output."""
        return {
            "entries_processed": self.entries_processed,
            "stale_transitions": self.stale_transitions,
            "cleared_transitions": self.cleared_transitions,
            "persist_failures": self.persist_failures,
            "duration_ms": self.duration_ms,
        }


def _serialized(items: list[Any]) -> list[object]:
    """Normalize stored pydantic models to the dict shape the pass consumes."""
    out: list[object] = []
    for item in items:
        dump = getattr(item, "model_dump", None)
        out.append(dump() if callable(dump) else item)
    return out


def run_maintain_verify(
    backend: Any,
    *,
    assertion_failure_penalty: float,
    assertion_stale_threshold_days: int,
    anchor_validity_verified_floor: float,
    batch_limit: int,
    project_root: Path | None,
    namespace: str | None = None,
) -> MaintainVerifySummary:
    """Verify every active assertion/anchor entry and persist observed verdicts.

    Uses bounded keyset ``entries_with_assertions`` pages (no N+1 acquisition
    fetch). A per-entry failure is logged and skipped so one bad row cannot
    abort the sweep.

    Args:
        backend: Memory backend exposing ``entries_with_assertions`` + ``update``.
        assertion_failure_penalty: ``TRWConfig.assertion_failure_penalty``.
        assertion_stale_threshold_days: ``TRWConfig.assertion_stale_threshold_days``.
        anchor_validity_verified_floor:
            ``TRWConfig.anchor_validity_verified_floor`` (PRD-CORE-244 FR03).
        batch_limit: ``TRWConfig.maintain_verify_batch_limit``.
        project_root: Repo root for filesystem-scoped verification, or ``None``.
        namespace: Optional namespace scope; ``None`` sweeps every namespace.

    Returns:
        A :class:`MaintainVerifySummary` describing the sweep.
    """
    from trw_mcp.tools._verification_pass import (
        persist_verification_outcome,
        run_verification_pass,
    )

    started = time.monotonic()
    summary = MaintainVerifySummary()

    cursor: tuple[str, str] | None = None
    while batch_limit > 0:
        entries = backend.entries_with_assertions(
            namespace=namespace, limit=batch_limit, include_anchors=True, after=cursor
        )
        if not entries:
            break
        next_cursor = (str(entries[-1].namespace), str(entries[-1].id))
        if cursor is not None and next_cursor <= cursor:
            raise ValueError("Maintenance backend did not advance its keyset cursor")
        # Advance independently of verification/persistence success.
        cursor = next_cursor
        for entry in entries:
            entry_id = str(getattr(entry, "id", ""))
            prior = getattr(entry, "verification_status", None)
            try:
                outcome = run_verification_pass(
                    entry_id,
                    _serialized(list(getattr(entry, "assertions", []) or [])),
                    _serialized(list(getattr(entry, "anchors", []) or [])),
                    namespace=str(getattr(entry, "namespace", namespace)),
                    assertion_failure_penalty=assertion_failure_penalty,
                    assertion_stale_threshold_days=assertion_stale_threshold_days,
                    anchor_validity_verified_floor=anchor_validity_verified_floor,
                    project_root=project_root,
                )
            except Exception:  # justified: sweep-resilience, one bad row must not abort the run
                logger.debug("maintain_verify_entry_failed", entry_id=entry_id, exc_info=True)
                continue

            summary.entries_processed += 1
            if not outcome.verifiable:
                # Nothing could be checked (unresolvable root) — skip, do not
                # convict the entry on historical timestamps alone.
                continue
            if not persist_verification_outcome(backend, outcome):
                summary.persist_failures += 1
                continue
            if outcome.verification_status == "stale" and prior != "stale":
                summary.stale_transitions += 1
            elif prior == "stale" and outcome.verification_status != "stale":
                # PRD-CORE-244 FR03: clearing a stale verdict now usually lands on
                # "verified" rather than None, so keying this on ``is None`` stopped
                # counting the very transition it exists to report.
                summary.cleared_transitions += 1

        if len(entries) < batch_limit:
            break

    summary.duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "maintain_verify_sweep_complete",
        entries_processed=summary.entries_processed,
        stale_transitions=summary.stale_transitions,
        cleared_transitions=summary.cleared_transitions,
        persist_failures=summary.persist_failures,
        duration_ms=summary.duration_ms,
    )
    return summary


def run_maintain_verify_for_project() -> MaintainVerifySummary:
    """Resolve the live config, memory backend, and project root, then sweep.

    CLI entry seam.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
    from trw_mcp.state.memory_adapter import get_backend

    config = get_config()
    project_root: Path | None
    try:
        project_root = resolve_project_root()
    except Exception:  # justified: fail-open, matches verify_assertions(project_root=None)
        logger.debug("maintain_verify_project_root_unresolved", exc_info=True)
        project_root = None

    return run_maintain_verify(
        get_backend(resolve_trw_dir()),
        assertion_failure_penalty=config.assertion_failure_penalty,
        assertion_stale_threshold_days=config.assertion_stale_threshold_days,
        anchor_validity_verified_floor=config.anchor_validity_verified_floor,
        batch_limit=config.maintain_verify_batch_limit,
        project_root=project_root,
    )


__all__ = [
    "MaintainVerifySummary",
    "run_maintain_verify",
    "run_maintain_verify_for_project",
]
