"""Shared outbound-HTTP trust helper for PRD-SEC-021 FR04/FR05.

Centralizes the ONE destination-trust decision every migrated trw-mcp package call
site (``cli/auth.py::_post_json``, ``server/_doctor_backend_connectivity.py::probe_backend_url``,
``server/_subcommands_release.py::_push_release``) makes before sending a request:
https unless the resolved host is loopback (``127.0.0.1``/``::1``/``localhost``), refused
before any request is attempted (FR05) — mirrors ``trw_memory.sync._remote_common``'s
``_DEV_LOCALHOSTS`` without importing that module's bearer-specific trust-allowlist
machinery (a distinct problem: which hosts may receive the platform bearer, not which
scheme any given outbound call may use).

Each call site still builds its own request body/headers and decides whether a
credential is attached at all — this module never sees or attaches one — but every
site gets its ``httpx.Client`` from :func:`outbound_http_client`, so
``follow_redirects=False`` is enforced in ONE place and can never silently drift per
site (FR04): a future httpx version change, or a future call site copy-pasting an
older pattern, cannot reintroduce redirect-following without editing this module.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import httpx

__all__ = [
    "LOOPBACK_HOSTS",
    "DisallowedDestinationError",
    "outbound_http_client",
    "require_https_or_loopback",
]

#: Loopback hosts may be reached over plain http (local dev backends only).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class DisallowedDestinationError(ValueError):
    """*url* failed the https-or-loopback destination policy (FR05).

    A typed subclass of ``ValueError`` (not a bare ``ValueError``) so a call site's
    error boundary can catch this specific policy refusal and turn it into a clean
    user-facing message, distinct from any other ``ValueError`` its own code might
    raise for an unrelated reason.
    """


def require_https_or_loopback(url: str) -> None:
    """Raise ``DisallowedDestinationError`` before any request when *url* is
    non-https and non-loopback.

    A loopback ``http://`` URL (dev backend) and any ``https://`` URL (any host) are
    permitted; a non-loopback ``http://`` URL is refused outright.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" or host in LOOPBACK_HOSTS:
        return
    raise DisallowedDestinationError(f"refusing non-https request to non-loopback host: {url!r}")


def outbound_http_client(*, timeout: float) -> httpx.Client:
    """The one ``httpx.Client`` construction every migrated call site uses.

    Always ``follow_redirects=False`` — no call site can opt back into following
    redirects, and a bearer attached to the initial request (by the caller, on its own
    headers) is never resent to a redirect target because no redirect is ever followed.
    """
    return httpx.Client(timeout=timeout, follow_redirects=False)
