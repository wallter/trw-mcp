"""Platform-egress trust gate — W38 (7.0.0 security P1).

Confirmed defect (learning L-s8Hm): the session-start update check (retired
in 8.0.0) and the team-sync pull loop (``sync/pull.py``) each attached the platform bearer API key to every configured
``platform_urls`` / ``backend_url`` entry with only a scheme check. Because
those URLs are read from a PROJECT's tracked ``.trw/config.yaml``, a cloned
repo could point them at an attacker host and any user whose key came from
the ``TRW_PLATFORM_API_KEY`` env var would send it there, and there was no
switch to disable either contact.

This module is the ONE shared decision point every platform-egress call site
uses — there is no second place that decides whether the bearer may leave
the box. ``platform_auth_headers()`` is the single function that builds the
``Authorization`` header; every module that contacts the platform
(``sync/pull.py``, ``sync/push.py``, ``tools/submit_feedback.py``, ``telemetry/sender.py``) calls it instead of
formatting the header itself — enforced by
``tests/test_platform_trust.py``'s census check, which fails if any other
module under ``trw_mcp/`` contains a raw ``Bearer`` header literal. The
trusted-host allowlist (``trw_memory.sync._remote_common.bearer_allowed_for``,
the one implementation both packages use) is deliberately sourced from places a
project's TRACKED config cannot reach: the built-in official host, the
machine-level ``~/.trw/config.yaml``, and environment variables. A project's own
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

import contextlib
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass, fields
from pathlib import Path

import structlog
from trw_memory.sync._remote_common import bearer_allowed_for

logger = structlog.get_logger(__name__)


def platform_auth_headers(url: str, api_key: str, *, source_trw_dir: Path | None) -> dict[str, str]:
    """Build the ``Authorization`` header for a platform request, or ``{}``.

    This is the ONLY function in the codebase that may construct a
    ``Bearer`` header for platform egress — every call site listed in the
    module docstring uses it instead of formatting the header inline, and
    ``tests/test_platform_trust.py`` census-checks that no other module does.

    Returns ``{}`` (never a partially-built header) when:
    - *api_key* is empty (nothing to attach), or
    - platform egress is disabled for *source_trw_dir* (``platform_contact_enabled``
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
    if not platform_contact_enabled(source_trw_dir):
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


def payload_trw_dir(path: Path | str) -> Path | None:
    """The ``.trw`` that owns the payload FILE at *path*: the one policy root for sending it.

    A send's policy root is derived from the payload's own resolved path and carried with it, never
    resolved separately (MRD S2/S3 review r3: an absolute ``telemetry_file`` and a relocated
    ``MEMORY_STORAGE_PATH`` each put the payload in project B while the policy came from A).
    Symlinks and ``..`` are resolved first, then the nearest ancestor that IS a ``.trw`` (a queue or
    entry inside it: ``<p>/.trw/logs/tool-telemetry.jsonl``) or HOLDS one (a store beside it:
    ``<p>/.memory/<ns>/memory.db``) wins. HOME's ``.trw`` (the machine tier) and a temp root's own
    no payload, as in trw-memory's project finder. ``None`` (no owning project) sends nothing.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):  # justified: fail closed, a path that cannot be resolved has no owner
        logger.warning("payload_trw_dir_unresolvable", outcome="contact_off", exc_info=True)
        return None
    not_projects = {Path(tempfile.gettempdir()).resolve(), Path("/tmp").resolve()}  # noqa: S108 -- never written
    with contextlib.suppress(RuntimeError):  # no resolvable home: nothing to skip there
        not_projects.add(Path.home().resolve())
    for directory in (resolved, *resolved.parents):
        candidate = directory if directory.name == ".trw" else directory / ".trw"
        if candidate.parent not in not_projects and candidate.is_dir():
            return candidate
    return None


@dataclass(frozen=True)
class SendPolicy:
    """The whole send policy of ONE payload, read from the ``.trw`` the payload was read from.

    ``contact`` is the platform contact switch; the rest are the consent flags. Every field
    defaults to ``False``: a payload with no ``.trw`` (or one whose config cannot be read) has
    no policy, so nothing is sent (fail closed). A sender may AND its own caller-side flags on
    top, which can only restrict; it never sends on a flag this policy does not also grant.
    """

    contact: bool = False
    learning_sharing: bool = False
    platform_telemetry: bool = False
    backup_remote: bool = False


def send_policy(source_trw_dir: Path | None) -> SendPolicy:
    """Load the send policy from *source_trw_dir*, the ``.trw`` the payload was read from.

    The config is built from that directory's own ``config.yaml`` (over the machine layer, under
    the environment) by ``config_for_trw_dir``, never taken from the cached process config, so a
    process whose config was loaded in project A cannot send project B's content under A's
    consent. ``tests/test_platform_contact_governing_root.py`` census-checks that every gate's
    argument is bound from a payload source, never from the cwd or the cached config.
    """
    if source_trw_dir is None or not Path(source_trw_dir).is_dir():
        logger.debug("send_policy_no_source_project", outcome="contact_off")
        return SendPolicy()
    try:
        from trw_mcp.models.config._loader import config_for_trw_dir

        cfg = config_for_trw_dir(Path(source_trw_dir))
    except Exception:  # justified: fail closed, a policy that cannot be read grants nothing
        logger.warning("send_policy_unreadable", outcome="contact_off", exc_info=True)
        return SendPolicy()
    return SendPolicy(
        contact=platform_contact_enabled(source_trw_dir),
        learning_sharing=cfg.learning_sharing_enabled is True,
        platform_telemetry=cfg.platform_telemetry_enabled is True,
        backup_remote=cfg.backup_remote_enabled is True,
    )


def send_policy_all(roots: Iterable[Path | None]) -> SendPolicy:
    """The field-wise AND of ``send_policy`` over EVERY root a payload is associated with.

    A payload tied to more than one project (its origin stamp, the project that invoked the send,
    the owner of the file it is read from) is sent only when each of them allows it, each read
    independently. A relocation or a symlink can therefore only restrict a send, never substitute
    one project's permission for another's (MRD S3 classfix r1). No roots, or any ``None`` root,
    grants nothing.
    """
    policies = [send_policy(root) for root in roots]
    if not policies:
        return SendPolicy()
    return SendPolicy(**{f.name: all(getattr(p, f.name) for p in policies) for f in fields(SendPolicy)})


def platform_contact_enabled(source_trw_dir: Path | None) -> bool:
    """Return False when platform egress is disabled for *source_trw_dir*, read live (B71-106/107).

    *source_trw_dir* is the ``.trw`` the payload was read from. ``None``, or a directory that
    does not exist, is no project: contact is refused (fail closed), never allowed by default.
    The switch (``TRW_PLATFORM_CONTACT_ENABLED``, then that project's and the machine's
    ``.trw/config.yaml``) is resolved by ``trw_memory.platform_contact``, the one implementation
    both packages share; it fails closed on an invalid value or an unreadable file. A
    ``TRWConfig(platform_contact_enabled=False)`` built in code can only veto on top of it.
    """
    from trw_memory.platform_contact import platform_contact_enabled as switch_on

    if source_trw_dir is None or not Path(source_trw_dir).is_dir():
        logger.debug("platform_contact_no_source_project", outcome="contact_off")
        return False
    try:
        from trw_mcp.models.config import get_config

        vetoed = get_config().platform_contact_enabled is False  # restrict-only: it can deny, never allow
    except Exception:  # justified: the live switch below still decides; a config fault is not a veto
        logger.debug("platform_contact_config_unavailable", exc_info=True)
        vetoed = False
    return not vetoed and switch_on(Path(source_trw_dir).parent)
