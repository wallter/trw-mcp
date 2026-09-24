"""trw-mcp adapter for the maintain-verify sweep (CORE268; PRD-CORE-294 FR07(b)).

The sweep itself lives in :mod:`trw_memory.lifecycle.verification_pass`, which
the daemon's ``memory_maintain`` also calls. This module only resolves the
framework host's inputs -- ``TRWConfig`` knobs, the project root and the
checkout's store -- and hands them to that one function through the store.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_memory.lifecycle.verification_pass import MaintainVerifySummary

logger = structlog.get_logger(__name__)


def run_maintain_verify_for_project(namespace: str | None = None) -> MaintainVerifySummary:
    """Resolve the live config, the checkout's store and the project root, then sweep.

    Args:
        namespace: Namespace to sweep; ``None`` is the checkout's project namespace.
            ``user:local`` rows anchor to other projects' files, so they are never
            verified against this root.
    """
    from trw_memory.lifecycle.verification_pass import VerifySettings

    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir
    from trw_mcp.state._store_selection import selected_store

    config = get_config()
    project_root: Path | None
    try:
        project_root = resolve_project_root()
    except Exception:  # justified: fail-open, an unresolved root verifies nothing
        logger.debug("maintain_verify_project_root_unresolved", exc_info=True)
        project_root = None

    store, project_namespace = selected_store(resolve_trw_dir())
    settings = VerifySettings(
        assertion_failure_penalty=config.assertion_failure_penalty,
        assertion_stale_threshold_days=config.assertion_stale_threshold_days,
        anchor_validity_verified_floor=config.anchor_validity_verified_floor,
        batch_limit=config.maintain_verify_batch_limit,
    )
    return store.verify(namespace or project_namespace, project_root, settings)


__all__ = ["run_maintain_verify_for_project"]
