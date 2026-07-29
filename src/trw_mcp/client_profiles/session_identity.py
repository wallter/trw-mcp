"""PRD-FIX-118 FR01 — per-client session identity, as a profile capability.

Why this module exists
----------------------
``.trw/runtime/pins.json`` is keyed by :func:`trw_mcp.state._paths.resolve_pin_key`.
Before FIX-118 that key was, in practice, the FastMCP ``ctx.session_id`` — a UUID
minted *inside* the MCP server that no shell hook can observe. Measured on
2026-07-24: none of the live pin keys, and none of the ``session_id`` values on
``session_start`` rows in ``.trw/context/session-events.jsonl``, existed as a
Claude Code transcript UUID. The two identifier namespaces were disjoint, so a
hook could never ask "which run is mine?" and fell back to "which run is newest?"
— which, under concurrency, is another instance's run.

The fix is not a new mechanism: several clients already publish their own session
identity into the environment of everything they launch, including both the MCP
server process and hook/tool shells. Naming that variable per profile — here,
once — makes the pin key *mutually knowable*:

* the server resolves it as ``resolve_pin_key`` layer 2b, and
* the generated ``.trw/runtime/hook-env.sh`` exports it as ``TRW_SESSION_ID``
  so hooks resolve the identical string (``resolve_pin_key`` layer 2 already
  consumed that variable; it simply had no writer).

NFR05 (client-agnostic) is satisfied by making this a *registry*, not a
Claude-Code special case: a profile with no known variable maps to ``()``,
emits no export, and every downstream hook takes the explicit unpinned path.

Empirical census (2026-07-24, this workstation)
-----------------------------------------------
=================  =========================  ===========================================
profile            variable                   evidence
=================  =========================  ===========================================
``claude-code``    ``CLAUDE_CODE_SESSION_ID``  present in ``/proc/<pid>/environ`` of every
                                               repo-local ``trw-mcp`` server (pids 2816972,
                                               3032087, 3081651, 3290337, 3455952) AND in
                                               hook/tool shells, with the *same* value for
                                               a given client session — including subagent
                                               shells, which carry
                                               ``CLAUDE_CODE_CHILD_SESSION=1`` but inherit
                                               the parent's ``CLAUDE_CODE_SESSION_ID``
                                               unchanged (Claude Code 2.1.219).
``codex``          none                        four codex-launched ``trw-mcp`` servers
                                               (pids 1085568, 1086311, 1543047, 3451984)
                                               exposed no ``*SESSION_ID*`` variable at all.
                                               ``CODEX_COMPANION_SESSION_ID`` is set by the
                                               Claude Code *codex plugin*, not by the codex
                                               CLI, and was absent from those servers.
others             unknown, assumed none       ``opencode``, ``cursor-ide``, ``cursor-cli``,
                                               ``copilot``, ``antigravity-cli`` had no live
                                               server to inspect. They are registered as
                                               ``()`` — the truthful default — so they
                                               degrade rather than guess. PRD-FIX-118 OQ-04
                                               tracks measuring them.
=================  =========================  ===========================================

Adding a profile later is a one-line edit here; the hook-env writer and the pin
resolver both read this table, so neither needs to change.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

__all__ = [
    "CLIENT_SESSION_ID_ENV_VARS",
    "known_session_id_env_vars",
    "render_hook_env_session_block",
    "resolve_client_session_id",
    "session_id_env_vars",
]


# The registry. Every ACTIVE profile id appears, so the table doubles as an
# auditable census: an empty tuple is a measured/declared "this client publishes
# no session identity", never an oversight.
CLIENT_SESSION_ID_ENV_VARS: dict[str, tuple[str, ...]] = {
    "claude-code": ("CLAUDE_CODE_SESSION_ID",),
    "opencode": (),
    "cursor-ide": (),
    "cursor-cli": (),
    "codex": (),
    "copilot": (),
    "antigravity-cli": (),
}

# Shell/JSON-safe environment variable names only. These are in-repo constants
# today, but the name is interpolated verbatim into a generated shell file that
# every hook ``source``s, so it is validated rather than trusted.
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

# A pin key becomes a JSON object key and a structured-log field. Accept a value
# unchanged or reject it outright -- never rewrite it, because the hook side reads
# the same variable raw and the two must agree byte for byte.
_MAX_SESSION_ID_LEN = 200
_UNSAFE_SESSION_ID_RE = re.compile(r"[\s\x00-\x1f\x7f]")


def session_id_env_vars(client_id: str) -> tuple[str, ...]:
    """Return the session-identity variables *client_id* publishes, if any.

    An unknown client id yields ``()`` -- the same truthful default as a known
    client with no variable.
    """
    return CLIENT_SESSION_ID_ENV_VARS.get(client_id, ())


def known_session_id_env_vars() -> tuple[str, ...]:
    """Return every registered variable name, de-duplicated, in stable order."""
    seen: dict[str, None] = {}
    for names in CLIENT_SESSION_ID_ENV_VARS.values():
        for name in names:
            if _ENV_NAME_RE.match(name):
                seen.setdefault(name, None)
    return tuple(seen)


def _accept(value: str | None) -> str | None:
    """Return *value* verbatim when usable as a pin key, else ``None``."""
    if not value:
        return None
    candidate = value.strip()
    if candidate != value:
        # Leading/trailing whitespace would make the server's key differ from the
        # hook's raw ``$TRW_SESSION_ID``. Reject instead of silently diverging.
        return None
    if not candidate or len(candidate) > _MAX_SESSION_ID_LEN:
        return None
    if _UNSAFE_SESSION_ID_RE.search(candidate):
        return None
    return candidate


def resolve_client_session_id(
    environ: Mapping[str, str] | None = None,
    *,
    candidates: Iterable[str] | None = None,
) -> str | None:
    """Return the launching client's own session id from the environment.

    The server generally cannot know *which* client launched it from config alone
    (a repo configured for one profile can be driven by another), so presence in
    the process environment is treated as ground truth: probe every registered
    variable in stable order and take the first usable value. Today exactly one
    variable is registered, so the order is not load-bearing; it is fixed anyway
    so the resolution stays deterministic when the table grows.

    Returns ``None`` when no client published a usable identity -- the caller
    then falls through to its existing layers, i.e. today's behavior.
    """
    env = os.environ if environ is None else environ
    names = tuple(candidates) if candidates is not None else known_session_id_env_vars()
    for name in names:
        accepted = _accept(env.get(name))
        if accepted is not None:
            return accepted
    return None


def render_hook_env_session_block(client_id: str) -> str:
    """Render the ``TRW_SESSION_ID`` stanza for ``.trw/runtime/hook-env.sh``.

    The generated file is written once per install/sync but *evaluated* every
    time a hook sources it, so the stanza must resolve the identity at source
    time. It therefore holds no session state itself -- the file stays a static,
    idempotent artifact while the exported value is always the live session's.

    Precedence inside the stanza mirrors ``resolve_pin_key``: an operator-forced
    ``TRW_SESSION_ID`` already in the environment wins and is never overwritten.

    For a profile with no known variable the stanza is a comment only: no export,
    so every consumer takes the explicit unpinned path (FR04) instead of guessing.
    """
    names = tuple(name for name in session_id_env_vars(client_id) if _ENV_NAME_RE.match(name))
    header = (
        "# PRD-FIX-118 FR01: make this session's identity readable by hooks so a\n"
        "# hook can resolve ITS OWN pinned run from .trw/runtime/pins.json instead\n"
        "# of guessing the newest run (which, under concurrency, belongs to another\n"
        "# instance). Evaluated at source time, so the value is always live.\n"
    )
    if not names:
        return header + (
            f"# Client profile {client_id!r} publishes no session identifier, so no\n"
            "# TRW_SESSION_ID is exported and hooks report an explicit unpinned state.\n"
        )
    lines = [header]
    lines.extend(f'if [ -z "${{TRW_SESSION_ID:-}}" ]; then TRW_SESSION_ID="${{{name}:-}}"; fi\n' for name in names)
    lines.append('if [ -n "${TRW_SESSION_ID:-}" ]; then export TRW_SESSION_ID; fi\n')
    return "".join(lines)
