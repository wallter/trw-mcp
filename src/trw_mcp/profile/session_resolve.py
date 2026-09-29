"""Session-start profile orchestration — PRD-HPO-PROF-001 FR-4/5/6/7/10/12.

Belongs to the ``trw_mcp.profile`` package facade. Re-exported there.

``resolve_session_profile`` is the consumer-facing entry point wired into
``trw_session_start`` (FR-4). It assembles the layer chain from live
runtime state:

  * ``defaults`` — projected from the global ``TRWConfig`` surface.
  * ``org`` / ``domain`` / ``task-type`` — discovered from ``.trw/profiles/``
    (FR-5/6/7) via inference + the loader.
  * ``client`` — re-homed from the resolved built-in ClientProfile (FR-10).

then ``compose``s them into a ``ResolvedProfile``.

Fail-closed boundary (PRD NFRs / Behavior Switch Matrix): a malformed
*persistent* layer (org/domain/task) fails closed via ``LayerLoadError`` —
that surfaces to the caller, which decides whether to abort or omit the
block. The session-start wiring catches everything so session start NEVER
crashes on a profile error.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.profile.inference import infer_domain, infer_task_type
from trw_mcp.profile.loader import LayerLoadError, discover_layers
from trw_mcp.profile.model import Profile, ProfileLayer, ResolvedProfile
from trw_mcp.profile.resolver import compose

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger(__name__)

#: Mapping of TRWConfig fields → the ``defaults`` Profile surface. Only fields
#: with a meaningful global analogue are projected; the rest stay unset so the
#: defaults layer is intentionally sparse (org/domain layers fill the gaps).
_CEREMONY_TIER_BY_MODE: dict[str, str] = {
    "light": "MINIMAL",
    "standard": "STANDARD",
    "full": "COMPREHENSIVE",
}


def _defaults_layer(config: TRWConfig) -> ProfileLayer:
    """Project the global ``TRWConfig`` into the ``defaults`` layer.

    Conservative: maps only the build-check scope and a ceremony tier derived
    from the active client's ceremony mode. Everything else is left unset so
    invariants don't trip on a default profile (e.g. review_threshold stays
    inherited rather than forced to NONE).
    """
    overrides: dict[str, object] = {}
    try:
        mode = config.client_profile.ceremony_mode
        tier = _CEREMONY_TIER_BY_MODE.get(mode)
        if tier is not None:
            overrides["ceremony_tier"] = tier
    except Exception:  # justified: fail-open, defaults projection must not crash resolve
        logger.debug("profile_defaults_ceremony_projection_failed", exc_info=True)
    return ProfileLayer(
        name="defaults",
        overrides=Profile.model_validate(overrides),
        source_path=".trw/config.yaml",
    )


def _client_layer(config: TRWConfig) -> ProfileLayer:
    """Build the ``client`` layer from the resolved built-in ClientProfile.

    The built-in registry owns transport concerns; this layer records the
    resolved client id as provenance (FR-10) without re-declaring surface
    keys, so it is the most-local layer but contributes no overridable policy.
    """
    try:
        client_id = config.client_profile.client_id
    except Exception:  # justified: fail-open, client resolution must not crash resolve
        client_id = "claude-code"
    return ProfileLayer(
        name="client",
        overrides=Profile(),
        source_path=f"client_profiles:{client_id}",
    )


def resolve_session_profile(
    config: TRWConfig,
    *,
    domain: str | None = None,
    task_type: str | None = None,
    prd_path: str | None = None,
    task_name: str | None = None,
    prd_category: str | None = None,
    trw_dir: Path | None = None,
) -> ResolvedProfile:
    """Resolve the layered profile for a session (FR-4).

    ``domain`` / ``task_type`` may be passed explicitly; otherwise they are
    inferred (FR-6/FR-7) from ``prd_path`` / ``task_name`` / ``prd_category``.
    When ``trw_dir`` is omitted, persistent-layer discovery is skipped (defaults +
    client only). Raises ``LayerLoadError`` if a persistent layer is
    malformed (FR-12) — the wiring layer decides how to surface it.
    """
    resolved_domain = infer_domain(
        explicit=domain,
        prd_path=prd_path,
        path_domain_map=config.profile_domain_path_map,
    )
    resolved_task = infer_task_type(explicit=task_type, task_name=task_name, prd_category=prd_category)

    layers: list[ProfileLayer] = [_defaults_layer(config)]

    if trw_dir is not None:
        layers.extend(discover_layers(trw_dir, domain=resolved_domain, task_type=resolved_task))

    layers.append(_client_layer(config))

    return compose(layers)


__all__ = ["LayerLoadError", "resolve_session_profile"]
