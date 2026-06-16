"""Session-start profile-resolution step — PRD-HPO-PROF-001 FR-4.

Belongs to the ``ceremony.py`` facade (re-exported there for the monkeypatch
indirection used by the session-start flow). Kept in its own sibling file so
``_ceremony_session_start_steps.py`` stays under the 350 effective-LOC gate.

``step_resolve_profile`` is the FR-4 consumer wiring: it composes the
hierarchical profile on every ``trw_session_start`` and writes the resolved
surface onto the session-start result dict so every downstream tool (and the
session payload itself) reads the same ``ResolvedProfile``.

Result keys written:
  * ``resolved_profile``     — effective surface (non-None fields only).
  * ``profile_layers_applied`` — layers that contributed, in chain order.
  * ``profile_snapshot_id``  — persistent-surface content hash (FR-13).
  * ``session_override_hash`` — session-layer delta hash (FR-13).
  * ``profile_explanation``  — per-field attribution (FR-11 input).

Fail-open (PRD NFRs / Behavior Switch Matrix): a missing/invalid layer (or a
disabled feature flag) degrades to omitting the block — session start NEVER
crashes on a profile error. The artifact-registry ``surface_snapshot_id``
(MEAS-001) written by ``step_surface_stamp`` is a DISTINCT key and is left
untouched.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.models.typed_dicts._tools import SessionStartResultDict

logger = structlog.get_logger(__name__)


def step_resolve_profile(
    config: TRWConfig,
    run_dir: Path | None,
    results: SessionStartResultDict,
) -> None:
    """Resolve the hierarchical profile and write it onto ``results`` (FR-4).

    No-op (block omitted) when ``profile_system_enabled`` is False or when any
    error occurs during resolution. Reads the SCALE-001 session-layer file at
    ``{run_dir}/meta/session_profile.yaml`` when present.
    """
    if not getattr(config, "profile_system_enabled", True):
        return
    try:
        from trw_mcp.profile import build_explanation, resolve_session_profile
        from trw_mcp.profile.loader import LayerLoadError
        from trw_mcp.state._paths import resolve_trw_dir

        trw_dir = resolve_trw_dir()
        try:
            resolved = resolve_session_profile(
                config,
                run_dir=run_dir,
                trw_dir=trw_dir,
            )
        except LayerLoadError as exc:
            # FR-12 fail-closed-with-visibility: a malformed/invalid layer must
            # not silently degrade to defaults NOR crash session start. Surface
            # the failure as a STRUCTURED key so the operator sees which layer
            # file is broken and why — session start still succeeds.
            results["profile_resolution_error"] = {
                "path": exc.path,
                "reason": exc.reason,
            }
            logger.warning(
                "profile_resolution_layer_error",
                path=exc.path,
                reason=exc.reason,
            )
            return
        results["resolved_profile"] = resolved.profile.model_dump(exclude_none=True, mode="json")
        results["profile_layers_applied"] = list(resolved.layers_applied)
        results["profile_snapshot_id"] = resolved.surface_snapshot_id
        results["session_override_hash"] = resolved.session_override_hash
        results["profile_explanation"] = build_explanation(resolved)
        logger.debug(
            "profile_resolved",
            layers_applied=resolved.layers_applied,
            profile_snapshot_id=resolved.surface_snapshot_id,
        )
    except Exception:  # justified: fail-open, profile resolution must not block session start
        logger.warning("profile_resolution_failed", exc_info=True)


__all__ = ["step_resolve_profile"]
