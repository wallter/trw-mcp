"""Process-wide reviewer-role predicate (PRD-SEC-015 round-2 audit fix).

A single ``reviewer_role_active()`` boolean any call site can consult to
decide whether THIS server process runs the read-only reviewer role
(PRD-SEC-015). Mirrors the identity resolution already landed in
``middleware.surface_authority._is_reviewer_role`` / ``_env_marks_reviewer``
(env-first, config-second, warn-once-on-typo) so a follow-up can make that
middleware import this predicate instead of carrying its own copy. Deliberately
NOT imported by (or importing) ``surface_authority`` today -- this module is
the seam a separate change wires in, not a replacement landed in the same
commit.

Fail-closed direction: a caller gates its OWN conservative branch (deny /
abort / skip-write) on this predicate returning ``True`` -- but the predicate
itself only returns ``True`` on the EXACT ``reviewer`` sentinel. An unset,
misspelled, or unreadable role resolves to ``False`` (agent); it never guesses
reviewer from ambiguity. This matches the surface-authority twin: a genuinely
unrecognized ``TRW_SURFACE_ROLE`` env value is now rejected by the typed
``Literal["agent", "reviewer"]`` field at ``TRWConfig()`` construction (the
process fails to boot rather than silently resolving either role), so this
predicate's own "unrecognized -> False" branch is reached only via the raw,
config-independent env check -- before that crash, or when no config read is
attempted at all.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)

_REVIEWER_SENTINEL = "reviewer"

#: Set once ``TRW_SURFACE_ROLE`` has been observed holding a non-empty value
#: that is not the ``reviewer`` sentinel, so the warning fires exactly once
#: per process rather than once per call site that consults this predicate.
_warned_unrecognized_surface_role_env: bool = False


def reviewer_role_active() -> bool:
    """True when this PROCESS runs the PRD-SEC-015 reviewer role.

    Env-first, config-second -- same order as
    ``surface_authority._is_reviewer_role`` (FR14: the spawning parent's
    environment marker must not be downgradable by the reviewed repository's
    own ``.trw/config.yaml``). Never raises: an unreadable or not-yet-built
    config resolves to ``False`` (agent) rather than propagating, so a call
    site that gates fail-closed behaviour on this predicate does not become a
    new crash surface for something as ordinary as "config hasn't loaded yet".
    """
    if _env_marks_reviewer():
        return True
    try:
        from trw_mcp.models.config import get_config

        config = get_config()
    except Exception:  # justified: an unreadable config resolves to agent, not a crash
        logger.debug("reviewer_role_active_config_read_failed", exc_info=True)
        return False
    return str(getattr(config, "surface_role", "agent")) == _REVIEWER_SENTINEL


def _env_marks_reviewer() -> bool:
    """Read ``TRW_SURFACE_ROLE`` straight from the environment.

    Same normalisation as ``surface_authority._env_marks_reviewer``:
    case/whitespace tolerant, warns exactly once per process on a
    set-but-unrecognized value, and never raises. Deliberately does NOT go
    through the config object -- this is the predicate a fail-closed path
    consults when a config read has already failed, so a config fault can
    never silently un-bound a subordinate lane.
    """
    raw = os.environ.get("TRW_SURFACE_ROLE")
    if raw is None:
        return False
    if raw.strip().lower() == _REVIEWER_SENTINEL:
        return True
    _warn_unrecognized_surface_role_env(raw)
    return False


def _warn_unrecognized_surface_role_env(value: str) -> None:
    """Log once per process that ``TRW_SURFACE_ROLE`` held an unrecognized value.

    Guarded by :data:`_warned_unrecognized_surface_role_env` rather than
    re-warning on every call site that consults :func:`reviewer_role_active`
    in a long-lived session.
    """
    global _warned_unrecognized_surface_role_env
    if _warned_unrecognized_surface_role_env:
        return
    _warned_unrecognized_surface_role_env = True
    logger.warning("surface_role_env_value_unrecognized", value=value, component="state._surface_role")


def reset_surface_role_state() -> None:
    """Clear the once-per-process warning latch. Testing only."""
    global _warned_unrecognized_surface_role_env
    _warned_unrecognized_surface_role_env = False


__all__ = ["reset_surface_role_state", "reviewer_role_active"]
