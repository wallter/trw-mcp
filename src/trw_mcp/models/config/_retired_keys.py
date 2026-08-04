"""Tell the operator when ``.trw/config.yaml`` names a key that does nothing.

PRD-QUAL-131-FR04. ``TRWConfig`` is declared ``extra="ignore"``, so a key it
does not define is dropped without a word. Measured at HEAD with
``TRW_CONFIG_STRICT=1`` exported: constructing ``TRWConfig`` with an undefined
keyword returns normally, emits nothing, and leaves ``model_extra`` as None. The
documented strict-mode escape hatch does not help, because its fail-closed branch
lives inside an ``except`` that ``extra="ignore"`` never enters.

That silence is what makes removing a field a hidden cost. An operator who tuned
a knob gets no signal when the knob is taken away, which is exactly the failure
``trw-mcp/CHANGELOG.md`` names for ``operator_tier_override_key`` -- "the one
with real user cost", because a document told operators to set it and setting it
produced silence.

This is a WARNING, not a rejection. Flipping to ``extra="forbid"`` would break
every user holding a stale key at once; that decision is deferred (OQ-02) until
there is at least one release of warning data behind it.

**The key name is printed and the value never is.** Config keys and
credential-adjacent values share this file, and a user may hold a secret under a
key that has since been retired.
"""

from __future__ import annotations

import functools
import json
import sys
from collections.abc import Iterable
from importlib.resources import files as _pkg_files

import structlog

logger = structlog.get_logger(__name__)

#: Bundled artifact naming keys this project has removed, and what replaced
#: them. Shipped data rather than a monorepo path so the warning survives in the
#: wheel (the same reasoning as ``_unread_fields.py``).
RETIRED_KEYS_RESOURCE = "config-retired-keys.json"

#: Keys already warned about in this process. The machine-level defaults file
#: (``~/.trw/config.yaml``) merges under the project file, and config is rebuilt
#: on reload, so an unbounded warning would repeat for the life of the session.
_WARNED: set[str] = set()


@functools.cache
def retired_config_keys() -> dict[str, str]:
    """``{retired key: replacement key, or "" when there is none}``.

    An empty replacement is a real answer, not missing data: several of these
    knobs were never wired to anything, so there is nothing to point at and
    saying so is more useful than silence.
    """
    resource = _pkg_files("trw_mcp.data") / RETIRED_KEYS_RESOURCE
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        # Fail OPEN, deliberately, and unlike ``unread_config_fields``. This map
        # only enriches a warning that fires either way; refusing to boot because
        # an advisory lookup table is unreadable would trade a real capability
        # for a cosmetic one.
        logger.debug("retired_key_map_unavailable", exc_info=True)
        return {}
    entries = payload.get("retired") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {str(key): str(value or "") for key, value in entries.items()}


@functools.cache
def externally_owned_config_keys() -> dict[str, str]:
    """``{config key: the subsystem that owns it}`` for non-``TRWConfig`` keys.

    ``.trw/config.yaml`` is not TRWConfig's private file. Other subsystems read
    and write their own keys in it — ``cli/auth.py`` round-trips
    ``platform_org_name``/``platform_user_email`` there to render auth status —
    so "not a TRWConfig field" is NOT the same question as "does nothing".

    Warning on a key that works is worse than the silence this module exists to
    fix: it teaches the operator that the warning is noise, and the next one
    (about a genuinely dead knob) gets ignored too. Same reasoning the tier-floor
    check in ``trw-distill`` records for its own false-alarm case.

    Fail-open for the same reason as ``retired_config_keys``: an unreadable
    advisory table must not make the warning louder than it should be, but it
    must not break boot either.
    """
    resource = _pkg_files("trw_mcp.data") / RETIRED_KEYS_RESOURCE
    try:
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        logger.debug("owned_elsewhere_key_map_unavailable", exc_info=True)
        return {}
    entries = payload.get("owned_elsewhere") if isinstance(payload, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {str(key): str(value or "") for key, value in entries.items()}


def _reset_warned_keys() -> None:
    """Clear the once-per-process dedup set (tests only)."""
    _WARNED.clear()


def warn_unrecognised_config_keys(keys: Iterable[str], defined: Iterable[str]) -> list[str]:
    """Warn once per key for every config key ``TRWConfig`` does not define.

    Args:
        keys: The merged ``.trw/config.yaml`` keys about to be handed to the
            constructor.
        defined: ``TRWConfig.model_fields``.

    Returns:
        The keys warned about on this call, sorted. Callers that have already
        warned about a key get it back only once, which is what keeps the
        machine-defaults + project-file merge from double-reporting.
    """
    # Externally-owned keys are subtracted BEFORE the dedup set, not filtered
    # after: they are not "already warned", they are never warnable.
    unrecognised = sorted(set(keys) - set(defined) - set(externally_owned_config_keys()) - _WARNED)
    if not unrecognised:
        return []

    retired = retired_config_keys()
    for key in unrecognised:
        _WARNED.add(key)
        if key in retired:
            replacement = retired[key]
            detail = (
                f"it was retired; use {replacement} instead" if replacement else "it was retired and has no replacement"
            )
        else:
            detail = "TRWConfig does not define it; check for a typo"
        logger.warning(
            "config_key_not_recognised",
            config_key=key,
            retired=key in retired,
            replacement=retired.get(key, "") or None,
        )
        # stderr as well as the log: config warnings that only reach structlog
        # are invisible to an operator whose logs are routed elsewhere, and this
        # is the first time this project tells a user that a knob they set does
        # not exist. Key name only — never the value they set.
        print(
            f"TRW: WARNING — .trw/config.yaml sets '{key}', which has no effect: {detail}.",
            file=sys.stderr,
        )
    return unrecognised
