"""Surface content resolver -- unified function for surface-gated content.

resolve_surface() is the single call site pattern for all surface content.
In this initial implementation (PRD-CORE-125), it checks config.surfaces
and delegates to existing hardcoded content functions.  PRD-CORE-126 will
migrate content to YAML data files loaded through this resolver.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# Map surface_id -> dotted attribute path on SurfaceConfig to the enabled flag.
# Dotted paths are resolved via getattr chain (e.g. "nudge.enabled" ->
# surfaces.nudge.enabled).
_SURFACE_ENABLED_MAP: dict[str, str] = {
    "nudge": "nudge.enabled",
    "recall": "recall.enabled",
    "hooks": "hooks_enabled",
    "mcp_instructions": "mcp_instructions_enabled",
    "skills": "skills_enabled",
    "agents": "agents_enabled",
    "framework_ref": "framework_ref_enabled",
}


def resolve_surface(surface_id: str) -> str:
    """Resolve surface content through the config layer.

    Returns empty string if the surface is disabled.
    Returns ``"__ENABLED__"`` sentinel if enabled (currently delegates
    to hardcoded functions).  PRD-CORE-126 will replace the sentinel
    with YAML-loaded content.

    Args:
        surface_id: Surface identifier (e.g. ``"nudge"``, ``"recall"``).

    Returns:
        Empty string if disabled, ``"__ENABLED__"`` if enabled.
    """
    try:
        from trw_mcp.models.config._loader import get_config

        config = get_config()
        surfaces = config.surfaces
    except Exception:  # justified: fail-open -- resolver must never block callers
        logger.debug("surface_resolver_config_failed", exc_info=True)
        return "__ENABLED__"

    attr_path = _SURFACE_ENABLED_MAP.get(surface_id)
    if attr_path is None:
        return "__ENABLED__"  # Unknown surface = enabled (permissive)

    # Resolve dotted path (e.g. "nudge.enabled" -> surfaces.nudge.enabled)
    obj: object = surfaces
    for part in attr_path.split("."):
        obj = getattr(obj, part)

    if not obj:
        return ""

    return "__ENABLED__"
