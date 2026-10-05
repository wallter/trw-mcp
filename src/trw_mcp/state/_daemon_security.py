"""Whether the memory daemon's security settings meet this client's (PRD-CORE-298 FR07).

One daemon serves every checkout and enforces the security settings it resolved
from its own environment. The client's resolved values are a floor, not a demand
for equality: a daemon value at least as strict on an ordered key is adopted with a
warning, because using it never weakens filtering. A weaker or unrankable value
would silently drop the policy this client resolved, so the attach refuses.

Equality was demanded until 9.0.x, and a daemon still running trw-memory 5.2.0 or
older (same major, so not replaced on upgrade) reports its old default
``recall_filter_mode="redact"`` while the upgraded client resolves ``strict``:
every learn and recall was refused with a remedy an agent cannot take.
"""

from __future__ import annotations

from typing import Any

import structlog

from trw_mcp.state._store_selection import StoreUnavailableError

logger = structlog.get_logger(__name__)

#: How strongly each value of an ordered daemon-wide key filters or gates: higher is stricter.
#: A key absent here (a role, the namespace role map) has no safe order and must match exactly.
#: ``recall_filter_mode="redact"`` is the pre-5.2.1 default an older daemon reports: it blocked a
#: hash-pin-drifted entry exactly as ``strict`` does and also redacted injection patterns, so it
#: enforces at least everything today's ``strict`` asks for.
_STRICTNESS: dict[str, dict[object, int]] = {
    "rbac_enabled": {False: 0, True: 1},
    "enable_recall_filter": {False: 0, True: 1},
    "recall_filter_mode": {"observe": 0, "strict": 1, "redact": 1},
    "canary_fail_mode": {"log-only": 0, "degrade": 1, "halt": 2},
    "provenance_required": {False: 0, True: 1},
}


def _same(daemon: object, local: object) -> bool:
    """Equal in type as well as value, so a reported ``1`` never matches ``True``."""
    if isinstance(daemon, dict) and isinstance(local, dict):
        return daemon.keys() == local.keys() and all(_same(daemon[key], local[key]) for key in local)
    return type(daemon) is type(local) and daemon == local


def _at_least_as_strict(key: str, daemon: object, local: object) -> bool:
    """Whether the daemon's value for *key* filters at least as strictly as *local*.

    An unknown key or value, or a differing type (a reported ``1`` for ``True``, a list for a
    string), is never ranked: the client cannot prove the daemon keeps its policy.
    """
    order = _STRICTNESS.get(key)
    if order is None or type(daemon) is not type(local) or isinstance(local, (dict, list)):
        return False
    return daemon in order and local in order and order[daemon] >= order[local]


def require_daemon_security_floor(daemon: dict[str, Any], local: dict[str, Any]) -> None:
    """Raise :class:`StoreUnavailableError` unless every *daemon* setting is at least as strict as *local*'s."""
    from trw_memory.daemon._discovery import AGENT_MUST_NOT_STOP

    for key, value in local.items():
        theirs = daemon.get(key)
        if _same(theirs, value):
            continue
        if _at_least_as_strict(key, theirs, value):
            logger.warning(
                "daemon_security_setting_stricter", key=key, daemon_value=theirs, local_value=value, adopted=theirs
            )
            continue
        raise StoreUnavailableError(
            f"memory security setting {key} is {theirs!r} in the daemon but {value!r} here, and the daemon's "
            f"value is not at least as strict; it is daemon-wide: the user sets MEMORY_{key.upper()} the same "
            f"for both and restarts the daemon. {AGENT_MUST_NOT_STOP}"
        )
