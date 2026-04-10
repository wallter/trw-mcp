"""Surface content resolver -- unified function for surface-gated content.

resolve_surface() is the single call site pattern for all surface content.
In this initial implementation (PRD-CORE-125), it checks config.surfaces
and delegates to existing hardcoded content functions.  PRD-CORE-126 will
migrate content to YAML data files loaded through this resolver.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def resolve_surface(surface_id: str, **context: object) -> str:
    """Resolve surface content through the config layer.

    Returns empty string if the surface is disabled.
    Returns ``"__ENABLED__"`` sentinel if enabled (currently delegates
    to hardcoded functions).  PRD-CORE-126 will replace the sentinel
    with YAML-loaded content.

    Args:
        surface_id: Surface identifier (e.g. ``"nudge"``, ``"recall"``).
        **context: Additional context for surface resolution (reserved
            for PRD-CORE-126).

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

    if surface_id == "nudge" and not surfaces.nudge.enabled:
        return ""
    if surface_id == "recall" and not surfaces.recall.enabled:
        return ""
    if surface_id == "hooks" and not surfaces.hooks_enabled:
        return ""
    if surface_id == "mcp_instructions" and not surfaces.mcp_instructions_enabled:
        return ""
    if surface_id == "skills" and not surfaces.skills_enabled:
        return ""
    if surface_id == "agents" and not surfaces.agents_enabled:
        return ""
    if surface_id == "framework_ref" and not surfaces.framework_ref_enabled:
        return ""

    # Surface is enabled -- return sentinel indicating "use existing content"
    # PRD-CORE-126 will replace this with YAML-loaded content
    return "__ENABLED__"
