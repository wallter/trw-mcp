"""The argv + env channel that decides a dispatched child's TRW MCP server.

Two postures share this one channel, and sharing it is the point: whichever
server the child talks to comes from the DISPATCHING process's argv, never from
the repository being worked on.

* ``posture='reviewer'`` BOUNDS the child (the original purpose, below);
* ``with_trw=True`` CONNECTS it — the same injection with no role marking and no
  tool allowlist, so a dispatched peer gets an ordinary TRW session against this
  project instead of no TRW tools at all (PRD-CORE-281-FR02).

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

Why ``sys.executable -I -m trw_mcp.server`` rather than a PATH lookup of the
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
import re
import sys
import uuid

from trw_mcp.dispatch._client_spec_types import REVIEWER_ARGV_PLACEHOLDERS, ClientSpec
from trw_mcp.dispatch._client_specs import UnknownClientError, client_spec_for
from trw_mcp.dispatch._confine import confinement_prefix
from trw_mcp.models.surface_packs import reviewer_tools_toml_array

__all__ = [
    "REVIEWER_POSTURE",
    "ReviewerPostureError",
    "TrwAccessError",
    "mcp_server_launcher",
    "render_reviewer_argv",
    "render_trw_access_argv",
    "reviewer_env_for",
    "reviewer_posture_enforced",
    "trw_access_enforced",
    "verify_reviewer_posture",
    "verify_trw_access",
]

#: The one posture literal this module acts on. Named rather than repeated so a
#: comparison cannot drift from the ``DispatchPosture`` Literal member.
REVIEWER_POSTURE = "reviewer"
ISOLATED_REVIEW_POSTURE = "isolated-review"

#: Isolated mode excludes cwd, PYTHONPATH and user-site packages: the reviewed
#: repository must not shadow the trusted interpreter's installed trw_mcp.
#: The child MCP server's argv tail. ``-m trw_mcp.server`` for the same reason
#: ``tests/_stdio_harness`` uses it: a console-script name is a PATH lookup and a
#: PATH lookup can select a different, globally installed trw-mcp.
_MCP_SERVER_ARGS: tuple[str, ...] = ("-I", "-m", "trw_mcp.server")

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


#: Matches exactly the known placeholders, derived from the one vocabulary the
#: template validator also uses, so the two cannot disagree about what a
#: placeholder is. Every other brace in a template (claude's JSON) is left alone.
_PLACEHOLDER_RE = re.compile(r"\{(" + "|".join(sorted(map(re.escape, REVIEWER_ARGV_PLACEHOLDERS))) + r")\}")


def _render_template(template: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """Substitute placeholders in ONE left-to-right pass over each token.

    A substituted value is never rescanned (REPAIR-DESIGN-01). With successive
    ``str.replace`` calls, an interpreter path that legitimately contains the text
    ``{mcp_args}`` was expanded a second time and injected ``json.dumps`` quotes
    into the child's JSON/TOML literal — the break ``_safe_value`` exists to
    prevent. Quote, backslash and control characters are still refused there.
    """
    return [_PLACEHOLDER_RE.sub(lambda match: values[match.group(1)], token) for token in template]


def _render_mcp_template(spec: ClientSpec, template: tuple[str, ...], values: dict[str, str]) -> list[str]:
    """Give Codex a fresh server table: its CLI recursively merges config tables.

    Overriding command/args alone leaves enabled=false, cwd, env and tool filters
    under the old name intact. A per-launch unpredictable name cannot inherit
    those project-controlled leaves. Disable the legacy entry as well; supply a
    transport so an absent legacy entry remains valid. An incompatible legacy
    HTTP transport fails config parsing closed rather than running unbounded.
    Other configured servers remain outside this TRW-only guarantee.
    """
    if not spec.fresh_mcp_server_table:
        return _render_template(template, values)
    required = {
        'mcp_servers.trw.command="{mcp_command}"',
        "mcp_servers.trw.args={mcp_args}",
    }
    if template == spec.reviewer_argv_template:
        required |= {
            'mcp_servers.trw.env.TRW_SURFACE_ROLE="reviewer"',
            "mcp_servers.trw.enabled_tools={reviewer_tools}",
            'mcp_servers.trw.default_tools_approval_mode="approve"',
        }
    else:
        required |= {
            'mcp_servers.trw.env.TRW_DISPATCH_CHILD="1"',
            'mcp_servers.trw.env_vars=["TRW_PROJECT_ROOT"]',
        }
    if len(template) != 2 * len(required) or set(template[::2]) != {"-c"} or set(template[1::2]) != required:
        raise ReviewerPostureError("codex MCP template does not match the controlled transport contract")
    prefix = "mcp_servers.trw."
    fresh = f"mcp_servers.trw_dispatch_{uuid.uuid4().hex}."
    renamed = tuple(fresh + tok[len(prefix) :] if tok.startswith(prefix) else tok for tok in template)
    return _render_template(renamed, values) + _render_template(
        (
            "-c",
            'mcp_servers.trw.command="{mcp_command}"',
            "-c",
            "mcp_servers.trw.args={mcp_args}",
            "-c",
            "mcp_servers.trw.enabled=false",
        ),
        values,
    )


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
    # reviewer_extra_argv (PRD-SEC-015-FR10/FR11) is appended AFTER the rendered
    # MCP transport, never merged into it: these tokens bound the CHILD
    # PROCESS's own tool/config surface (codex --ignore-user-config/--disable
    # apps, claude --tools) rather than the trw-mcp transport the template
    # above renders, so they carry no placeholders and skip the codex
    # fresh-mcp-table structural check entirely.
    return _render_mcp_template(spec, spec.reviewer_argv_template, _substitutions()) + list(spec.reviewer_extra_argv)


class TrwAccessError(ValueError):
    """``with_trw=True`` was requested but cannot be DELIVERED as argv.

    A distinct type, not a reuse of :class:`ReviewerPostureError`, because the
    two refusals mean opposite things: a refused reviewer posture would have
    CONTAINED the child, a refused TRW access would have CONNECTED it. A caller
    that conflates them reports a missing bound as a missing feature.
    """


def render_trw_access_argv(spec: ClientSpec) -> list[str]:
    """Render *spec*'s ``with_trw`` template into concrete argv tokens.

    Same substitution vocabulary and the same fail-closed value check as
    :func:`render_reviewer_argv` — the two templates configure the same thing
    (which MCP server the child talks to) and must not be able to disagree about
    how a command path is embedded.

    Raises:
        TrwAccessError: when the client declares no template, or a substituted
            value could not be embedded safely. Refusing here, before any argv
            exists, is what stops a caller receiving a child that silently has
            no TRW tools and reading its answer as "TRW said nothing useful".
    """
    if not spec.supports_trw_access:
        raise TrwAccessError(
            f"client {spec.client_id!r} cannot be given TRW access: TRW cannot place its own MCP "
            "server into that client's argv, and this layer will not write into that client's own "
            "config files to do it. Dispatch it without with_trw and treat the child as having no "
            "TRW memory, or use a client that supports it."
        )
    try:
        values = _substitutions()
    except ReviewerPostureError as exc:
        raise TrwAccessError(str(exc).replace("reviewer posture:", "trw access:")) from exc
    try:
        return _render_mcp_template(spec, spec.trw_access_argv_template, values)
    except ReviewerPostureError as exc:
        raise TrwAccessError(str(exc)) from exc


def trw_access_enforced(client: str, with_trw: bool) -> bool:
    """True iff TRW access was requested AND the spec can deliver it.

    Derived per run from the registry for the same reason
    :func:`reviewer_posture_enforced` is: the value a result REPORTS must be the
    value the argv DELIVERED.
    """
    if not with_trw:
        return False
    try:
        spec = client_spec_for(client)
    except UnknownClientError:  # pragma: no cover - guarded by the Literal upstream
        # trw-fail-silent-allow: False is the honest claim for an unknown id; verify_trw_access raises on the same lookup
        return False
    return spec.supports_trw_access


def verify_trw_access(client: str, with_trw: bool) -> None:
    """Raise unless *client* can actually be handed TRW's MCP server.

    Called from resolution (so the CLI and MCP paths refuse before a request
    object exists) AND from the runner (so a request built by any other path
    still cannot reach ``subprocess.Popen`` with the flag silently dropped).
    """
    if not with_trw:
        return
    try:
        spec = client_spec_for(client)
    except UnknownClientError:
        raise TrwAccessError(f"client {client!r} has no dispatch client spec registered; TRW access refused.") from None
    if not spec.supports_trw_access:
        raise TrwAccessError(
            f"client {client!r} has no TRW-access argv channel: this layer can only inject TRW's "
            "MCP server through a client's own command line, and that client exposes none. No "
            "substitute was applied and no config file was written."
        )


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
    cannot reach ``subprocess.Popen``). ``isolated-review`` shares the writes
    refusal, then needs an ``isolated_review`` spec, a confinable client and a
    wrapper on this host (PRD-CORE-297-FR02).
    """
    if posture not in (REVIEWER_POSTURE, ISOLATED_REVIEW_POSTURE):
        return
    if not read_only:
        raise ReviewerPostureError(
            f"posture={posture!r} cannot be combined with writes (allow_writes / read_only=False): "
            "the reviewer surface is a READ-ONLY bound and a writable reviewer would be able to "
            "modify the work it is reviewing. No write permission was granted."
        )
    try:
        spec = client_spec_for(client)
    except UnknownClientError:
        raise ReviewerPostureError(
            f"client {client!r} has no dispatch client spec registered; {posture} posture refused."
        ) from None
    if posture == ISOLATED_REVIEW_POSTURE:
        missing = [
            reason
            for reason, held in (
                ("its spec declares no isolated_review lane", spec.isolated_review is not None),
                ("its spec has no host write-denial (host_confinement)", spec.host_confinement),
                ("this host has no write-denial wrapper", bool(confinement_prefix())),
            )
            if not held
        ]
        if missing:
            raise ReviewerPostureError(f"client {client!r} cannot run posture='isolated-review': {'; '.join(missing)}.")
        return
    if not spec.supports_reviewer_posture:
        raise ReviewerPostureError(
            f"client {client!r} has no reviewer posture: TRW cannot place its own MCP server into "
            "that client's argv, so the reviewer tool bound would not exist. Dispatch it with "
            "posture='default' and treat its output as unbounded, or use a client that supports "
            "the posture."
        )
