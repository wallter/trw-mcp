"""Platform-egress trust gate — W38 (7.0.0 security P1).

Confirmed defect (learning L-s8Hm): the session-start update check
(``state/auto_upgrade.py``) and the team-sync pull loop (``sync/pull.py``)
each attached the platform bearer API key to every configured
``platform_urls`` / ``backend_url`` entry with only a scheme check. Because
those URLs are read from a PROJECT's tracked ``.trw/config.yaml``, a cloned
repo could point them at an attacker host and any user whose key came from
the ``TRW_PLATFORM_API_KEY`` env var would send it there, and there was no
switch to disable either contact.

This module is the ONE shared decision point every platform-egress call site
uses — there is no second place that decides whether the bearer may leave
the box. ``platform_auth_headers()`` is the single function that builds the
``Authorization`` header; every module that contacts the platform
(``sync/pull.py``, ``sync/push.py``, ``state/auto_upgrade.py``,
``tools/submit_feedback.py``, ``telemetry/sender.py``) calls it instead of
formatting the header itself — enforced by
``tests/test_platform_trust.py``'s census check, which fails if any other
module under ``trw_mcp/`` contains a raw ``Bearer`` header literal. The
trusted-host allowlist is deliberately sourced from places a project's
TRACKED config cannot reach: the built-in official host, the machine-level
``~/.trw/config.yaml``, and environment variables. A project's own
``.trw/config.yaml`` may still point ``platform_urls``/``backend_url`` at any
host it likes (self-hosted deployments stay possible) — it just never causes
that host to receive the credential.

Confirmed defect #2 (2026-09, pre-7.0.0-freeze release verify, P1-C): three
more call sites (``sync/push.py``, ``tools/submit_feedback.py``,
``telemetry/sender.py``) attached the bearer with NO trust check at all —
whenever an API key was configured, it went to whatever ``backend_url`` /
``platform_urls`` the project's tracked config named. ``platform_auth_headers``
closes all five sites through one function.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import structlog

logger = structlog.get_logger(__name__)

#: The official platform host. Always trusted over https, regardless of what
#: a project's tracked config claims ``platform_urls``/``platform_url`` is.
DEFAULT_TRUSTED_PLATFORM_HOST = "api.trwframework.com"

#: Loopback hosts may receive the bearer over plain http — dev only.
_DEV_LOCALHOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: Comma-separated additional trusted hostnames (machine/operator controlled).
_ENV_TRUSTED_HOSTS = "TRW_PLATFORM_TRUSTED_HOSTS"


def _user_config_trusted_hosts() -> frozenset[str]:
    """Hosts from ``~/.trw/config.yaml`` ``platform_trusted_hosts`` — machine layer ONLY.

    Reads the user-level file directly rather than through the merged
    ``TRWConfig`` singleton. The merged config's ``platform_urls`` can be set
    entirely by a project's tracked file (``_deep_merge`` replaces list
    values wholesale, it does not union them), so deriving trust from the
    merged config would let a poisoned tracked file add itself to its own
    trust list. The allowlist source must stay project-uncontrollable.
    """
    try:
        from trw_mcp.state.persistence import FileStateReader

        path = Path.home() / ".trw" / "config.yaml"
        if not path.exists():
            return frozenset()
        data = FileStateReader().read_yaml(path)
        if not isinstance(data, dict):
            return frozenset()
        raw = data.get("platform_trusted_hosts", [])
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return frozenset()
        return frozenset(str(h).strip().lower() for h in raw if str(h).strip())
    except Exception:  # justified: boundary, a malformed machine config must not crash the trust gate
        logger.debug("platform_trusted_hosts_read_failed", exc_info=True)
        return frozenset()


def _env_trusted_hosts() -> frozenset[str]:
    raw = os.environ.get(_ENV_TRUSTED_HOSTS, "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def trusted_platform_hosts() -> frozenset[str]:
    """The full https trusted-host allowlist: default + user config + env.

    Never includes anything derived from a project's tracked
    ``.trw/config.yaml`` — see module docstring.
    """
    return frozenset({DEFAULT_TRUSTED_PLATFORM_HOST}) | _user_config_trusted_hosts() | _env_trusted_hosts()


def bearer_allowed_for(url: str) -> bool:
    """Return True iff the platform bearer API key may be attached to *url*.

    - ``https`` to a trusted host (default/user-config/env, never project
      config): allowed.
    - ``http`` to a loopback dev host: allowed (dev only).
    - Everything else — ``https`` to an untrusted host, or ``http`` to
      anything non-loopback — the bearer is withheld. Callers proceed
      unauthenticated (the version-check target does not require auth) or
      skip the request (see ``platform_contact_enabled`` for the global
      switch); either way the credential never leaves the box.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    scheme = parts.scheme.lower()
    if scheme == "http" and host in _DEV_LOCALHOSTS:
        return True
    if scheme != "https":
        return False
    return host in trusted_platform_hosts()


def platform_auth_headers(url: str, api_key: str) -> dict[str, str]:
    """Build the ``Authorization`` header for a platform request, or ``{}``.

    This is the ONLY function in the codebase that may construct a
    ``Bearer`` header for platform egress — every call site listed in the
    module docstring uses it instead of formatting the header inline, and
    ``tests/test_platform_trust.py`` census-checks that no other module does.

    Returns ``{}`` (never a partially-built header) when:
    - *api_key* is empty (nothing to attach), or
    - platform egress is globally disabled (``platform_contact_enabled``
      config field / ``TRW_PLATFORM_CONTACT_ENABLED`` env var is false), or
    - *url*'s host is not on the trusted-host allowlist (see
      ``bearer_allowed_for``) — a project's tracked config cannot add itself
      to that allowlist.

    Callers proceed with the request unauthenticated when this returns
    ``{}`` rather than skipping it outright: the credential is what must
    never leave the box, not the request itself.
    """
    if not api_key:
        return {}
    if not platform_contact_enabled():
        logger.debug("credential_withheld_contact_disabled", url=url)
        return {}
    if not bearer_allowed_for(url):
        logger.warning("credential_withheld_untrusted_host", url=url)
        return {}
    return {"Authorization": f"Bearer {api_key}"}


def operator_release_bearer_value(api_key: str) -> str:
    """The ``Bearer <key>`` value for an OPERATOR-RUN release/publish tool.

    Named exception (2026-09-25 sol re-review, P1-C round 2 finding #4) to the
    "route every bearer attach through platform_auth_headers" rule, for the
    narrow case where NEITHER input is a project's tracked config:

    - ``server/_subcommands_release.py::_push_release`` — *backend_url* is a
      literal ``--backend-url`` CLI argument; *api_key* comes from the
      operator's shell environment at invocation time.
    - ``scripts/_publish_guard/network.py`` — *api_url* is
      ``TRW_PUBLISH_API_URL``/``API_URL`` read directly in the CLI's
      ``main()``; *api_key* is ``TRW_API_KEY``/``API_KEY``, likewise.

    The trust-gate threat model this module exists for — a cloned repo's
    TRACKED ``.trw/config.yaml`` silently redirecting an automatic contact to
    an attacker host — cannot apply here: nothing in this call path ever
    reads ``TRWConfig``/``MemoryConfig``, so there is no config-driven URL for
    a poisoned project file to redirect. A caller MUST get both the URL and
    the key from an explicit operator-supplied CLI argument or an env var read
    directly in a script's own entrypoint — never from ``get_config()`` — or
    this exception does not apply and ``platform_auth_headers`` must be used
    instead. ``tests/test_platform_trust.py``'s census enumerates every
    caller of this function so a new one is a visible, reviewable diff line.
    """
    return f"Bearer {api_key}"


def platform_contact_enabled() -> bool:
    """Return False when platform egress is globally disabled.

    Reads the ``platform_contact_enabled`` config field (default True; the
    ``TRW_PLATFORM_CONTACT_ENABLED`` env var overrides it like any field). It is
    the one switch for both automatic contacts, the update check and the
    team-sync pull; uploads have their own consent gate
    (``platform_telemetry_enabled``) and models never download at runtime.
    """
    try:
        from trw_mcp.models.config import get_config

        return bool(get_config().platform_contact_enabled)
    except Exception:  # justified: boundary, a config load failure must not silently disable the gate check itself
        logger.debug("platform_contact_enabled_check_failed", exc_info=True)
        return True
