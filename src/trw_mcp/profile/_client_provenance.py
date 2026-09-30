"""Which client profile a session resolved, and from what (INC-078).

Belongs to the ``trw_mcp.profile`` package; used by :func:`trw_mcp.profile.explain.explain_surface`.
The client profile is selected by ``target_platforms[0]`` in ``.trw/config.yaml`` alone (``TRWConfig.client_profile``);
an unknown or retired id falls back to ``claude-code``. ``TRW_CLIENT_PROFILE`` is a different mechanism -- it tags
distill telemetry with a client (``channels/_distill_telemetry.py``) -- and does not select the profile, so explain
says so instead of leaving a set variable silently without effect.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

__all__ = ["client_profile_provenance"]

_ENV_VAR = "TRW_CLIENT_PROFILE"
_REMEDY = "To change the profile, set target_platforms in .trw/config.yaml or run init-project --ide <id>."


def client_profile_provenance(config: TRWConfig) -> dict[str, object]:
    """``{"id", "source", "ceremony_mode"}`` plus ``notes`` when the selection needs explaining."""
    from trw_mcp.models.config._profiles import _PROFILES

    valid = ", ".join(sorted(_PROFILES))
    platforms = list(config.target_platforms or [])
    profile = config.client_profile
    # INC-126 (a): the field has a default (['claude-code']), so a non-empty list is not evidence of a config file.
    # Only a value the loaded settings actually set is named as coming from .trw/config.yaml (or the env var).
    configured = "target_platforms" in config.model_fields_set and bool(platforms)
    from_env = configured and bool(os.environ.get("TRW_TARGET_PLATFORMS", "").strip())
    out: dict[str, object] = {
        "id": profile.client_id,
        "source": (
            "TRW_TARGET_PLATFORMS[0] (environment)"
            if from_env
            else "target_platforms[0] in .trw/config.yaml"
            if configured
            else "default: no target_platforms set"
        ),
        "ceremony_mode": profile.ceremony_mode,
    }
    notes: list[str] = []
    if platforms and platforms[0] not in _PROFILES:
        notes.append(
            f"target_platforms[0] is {platforms[0]!r}, not a known client profile, so {profile.client_id!r} "
            f"is used. Valid ids: {valid}."
        )
    if len(platforms) > 1:
        notes.append(f"Only the first of target_platforms {platforms} selects the profile.")
    env_value = os.environ.get(_ENV_VAR, "").strip()
    if env_value:
        unknown = "" if env_value in _PROFILES else f" (and {env_value!r} is not a known client id; valid: {valid})"
        notes.append(
            f"{_ENV_VAR}={env_value!r} is set: it tags distill telemetry only and does not select this "
            f"profile{unknown}. {_REMEDY}"
        )
    if notes:
        out["notes"] = notes
    return out
