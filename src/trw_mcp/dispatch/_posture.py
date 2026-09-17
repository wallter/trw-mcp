"""Reviewer posture: the argv + env channel that BOUNDS a dispatched reviewer.

Belongs to the ``trw_mcp.dispatch`` package. Before this module a "reviewer" was
a sentence: ``_roles.apply_role`` prepended a read-only preamble to the prompt
and nothing else changed, so the child's MCP server was whatever the REVIEWED
repository's own config gave it — measured at 13 ``trw_*`` tools including
``trw_deliver`` and ``trw_learn`` (probe 1, codex-cli 0.153.2, 2026-09-04,
recorded in ``scripts/audit-external.sh``). A model told to behave is not a
control.

What a reviewer posture actually does (OD-6, PRD-SEC-015-FR06/FR07):

1. TRW renders the child's trw-mcp server INTO ARGV — command, args, env and
   (where the client enforces one) the ``enabled_tools`` allowlist — so the
   transport comes from the dispatching process, never from the reviewed repo's
   project config. That is the OD-6 decision verbatim.
2. The rendered env carries ``TRW_SURFACE_ROLE=reviewer``, which
   ``middleware/surface_authority`` reads to REPLACE the child's tool surface
   with :data:`trw_mcp.models.surface_packs.REVIEWER_TOOLS`. That server-side
   role is the actual control; the client-side allowlist is defense in depth.
3. A client whose spec has no template is REFUSED before spawn
   (:class:`ReviewerPostureError`). Silently running it would hand the caller a
   prompt-only "reviewer" while the posture field claimed containment.

Why literal replacement instead of ``str.format``: claude's ``--mcp-config``
value is a JSON document, so the token is full of braces that ``format`` would
read as fields. :func:`render_reviewer_argv` replaces exactly the three known
placeholders and leaves every other brace alone.

Why ``sys.executable -m trw_mcp.server`` rather than a PATH lookup of the
``trw-mcp`` console script: a PATH lookup inside the reviewed repository can
resolve to that repository's own ``.venv`` (L-XW1l — a strict child must source
NOTHING from the reviewed repo), and the audited project could therefore supply
the very server meant to bound its auditor. The dispatching interpreter is the
one component the reviewed repo cannot influence. Consequence recorded rather
than hidden: the child's server resolves ``trw_mcp`` from THAT interpreter's
environment, so a source checkout that runs trw-mcp off ``PYTHONPATH`` alone
needs the package importable for the interpreter it dispatches with.
"""

from __future__ import annotations

import json
import os
import sys

from trw_mcp.dispatch._client_spec_types import REVIEWER_ARGV_PLACEHOLDERS, ClientSpec
from trw_mcp.dispatch._client_specs import UnknownClientError, client_spec_for
from trw_mcp.models.surface_packs import reviewer_tools_toml_array

__all__ = [
    "REVIEWER_POSTURE",
    "ReviewerPostureError",
    "mcp_server_launcher",
    "render_reviewer_argv",
    "reviewer_env_for",
    "reviewer_posture_enforced",
    "verify_reviewer_posture",
]

#: The one posture literal this module acts on. Named rather than repeated so a
#: comparison cannot drift from the ``DispatchPosture`` Literal member.
REVIEWER_POSTURE = "reviewer"

#: The child MCP server's argv tail. ``-m trw_mcp.server`` for the same reason
#: ``tests/_stdio_harness`` uses it: a console-script name is a PATH lookup and a
#: PATH lookup can select a different, globally installed trw-mcp.
_MCP_SERVER_ARGS: tuple[str, ...] = ("-m", "trw_mcp.server")

#: Characters that must never appear in a substituted value. The rendered token
#: is embedded in a TOML string (codex ``-c``) or a JSON document (claude
#: ``--mcp-config``); a quote, backslash or control character would either break
#: the child's parser or, worse, close the literal and append a second setting.
#: Fail closed: refuse to render rather than emit an ambiguous command line.
_UNSAFE_VALUE_CHARS: frozenset[str] = frozenset('"\\\n\r\t')


class ReviewerPostureError(ValueError):
    """A reviewer posture was requested but cannot be DELIVERED as argv.

    A distinct type so every call site — resolution, the runner's pre-spawn
    guard, the background job path — refuses with one identifiable reason
    instead of a bare ``ValueError`` that a broad ``except`` could absorb into a
    generic failure and a caller could mistake for a transient error.
    """


def mcp_server_launcher() -> tuple[str, tuple[str, ...]]:
    """Return ``(interpreter, args)`` for the child's own trw-mcp server.

    ``abspath``, deliberately NOT ``Path.resolve()``: a virtualenv interpreter is
    normally a symlink to a system python, and resolving it would hand the child
    an interpreter OUTSIDE the venv — one that cannot import ``trw_mcp`` at all,
    turning a bounded reviewer into a child with no TRW server. ``abspath`` only
    normalizes a relative invocation path (``trw-mcp/../.venv/bin/python``).
    """
    return os.path.abspath(sys.executable), _MCP_SERVER_ARGS


def _safe_value(name: str, value: str) -> str:
    """Return *value*, or raise if it could break out of a TOML/JSON literal."""
    if not value:
        raise ReviewerPostureError(f"reviewer posture: placeholder {{{name}}} resolved to an empty value")
    bad = sorted(_UNSAFE_VALUE_CHARS & set(value))
    if bad:
        raise ReviewerPostureError(
            f"reviewer posture: placeholder {{{name}}} value {value!r} contains {bad!r}, "
            "which cannot be embedded in the child's TOML/JSON config literal"
        )
    return value


def _substitutions() -> dict[str, str]:
    """Resolve every placeholder in :data:`REVIEWER_ARGV_PLACEHOLDERS`.

    ``mcp_args`` is rendered with :func:`json.dumps`, which is exact for TOML
    too: a TOML array of basic strings and a JSON array of strings have the same
    text for these values, so ONE rendering serves the codex ``-c`` and claude
    ``--mcp-config`` layers and the two cannot drift.
    """
    command, args = mcp_server_launcher()
    values = {
        "reviewer_tools": reviewer_tools_toml_array(),
        "mcp_command": _safe_value("mcp_command", command),
        "mcp_args": json.dumps(list(args)),
    }
    missing = REVIEWER_ARGV_PLACEHOLDERS - set(values)
    if missing:  # pragma: no cover - guarded by the placeholder-vocabulary test
        raise ReviewerPostureError(f"reviewer posture: no value resolved for placeholders {sorted(missing)}")
    return values


def render_reviewer_argv(spec: ClientSpec) -> list[str]:
    """Render *spec*'s reviewer template into concrete argv tokens.

    Raises:
        ReviewerPostureError: when the client declares no reviewer template, or
            a substituted value could not be embedded safely. Both are refusals
            BEFORE any argv exists, which is the property that keeps a failed
            posture from degrading into an unbounded run.
    """
    if not spec.supports_reviewer_posture:
        raise ReviewerPostureError(
            f"client {spec.client_id!r} has no reviewer posture: TRW cannot place its own MCP "
            "server into that client's argv, so a reviewer dispatch to it would be bounded only "
            "by the prompt. No substitute posture was applied."
        )
    values = _substitutions()
    rendered: list[str] = []
    for token in spec.reviewer_argv_template:
        out = token
        for name, value in values.items():
            out = out.replace("{" + name + "}", value)
        rendered.append(out)
    return rendered


def reviewer_env_for(client: str, posture: str) -> dict[str, str]:
    """The env overlay for *client* under *posture* (empty for ``default``).

    An unregistered client yields ``{}`` — the fail-closed direction: an id TRW
    does not know is never MARKED as contained.
    """
    if posture != REVIEWER_POSTURE:
        return {}
    try:
        spec = client_spec_for(client)
    except UnknownClientError:  # pragma: no cover - guarded by the Literal upstream
        # trw-fail-silent-allow: no overlay is the fail-closed answer for an unknown id (it is never MARKED as contained); verify_reviewer_posture raises the refusal on the same lookup
        return {}
    return dict(spec.reviewer_env)


def reviewer_posture_enforced(client: str, posture: str) -> bool:
    """True iff a reviewer posture was requested AND the spec can deliver it.

    This is the value a result reports. It is derived from the registry at the
    moment of the run, never hardcoded: ``posture_enforced=True`` beside an argv
    that carries no MCP override is exactly the "delivered ≠ wired" claim the
    reviewer surface exists to make impossible.
    """
    if posture != REVIEWER_POSTURE:
        return False
    try:
        spec = client_spec_for(client)
    except UnknownClientError:  # pragma: no cover - guarded by the Literal upstream
        # trw-fail-silent-allow: posture_enforced=False is the fail-closed claim for an unknown id; verify_reviewer_posture raises the refusal on the same lookup
        return False
    return spec.supports_reviewer_posture


def verify_reviewer_posture(client: str, posture: str, *, read_only: bool) -> None:
    """Raise unless *client* can run under *posture* with these permissions.

    The two refusals, in the order a caller needs them:

    * writes + reviewer — a reviewer that can edit the work it reviews is not a
      reviewer, and the read-only lane is what makes the bounded tool surface
      meaningful in the first place;
    * an unsupported client — see :func:`render_reviewer_argv`.

    Called from resolution (so the CLI/MCP paths refuse before a request object
    exists) AND from the runner (so a request built by any other path still
    cannot reach ``subprocess.Popen``).
    """
    if posture != REVIEWER_POSTURE:
        return
    if not read_only:
        raise ReviewerPostureError(
            "posture='reviewer' cannot be combined with writes (allow_writes / read_only=False): "
            "the reviewer surface is a READ-ONLY bound and a writable reviewer would be able to "
            "modify the work it is reviewing. No write permission was granted."
        )
    try:
        spec = client_spec_for(client)
    except UnknownClientError:
        raise ReviewerPostureError(
            f"client {client!r} has no dispatch client spec registered; reviewer posture refused."
        ) from None
    if not spec.supports_reviewer_posture:
        raise ReviewerPostureError(
            f"client {client!r} has no reviewer posture: TRW cannot place its own MCP server into "
            "that client's argv, so the reviewer tool bound would not exist. Dispatch it with "
            "posture='default' and treat its output as unbounded, or use a client that supports "
            "the posture."
        )
