"""Dispatched-child marker (PRD-CORE-281 nested-launch guard).

Belongs to the ``trw_mcp.dispatch`` package. The ``with_trw`` argv templates set
this variable in the environment of the TRW MCP server entry they render for a
child. A server that carries it refuses to launch: ``tools/dispatch.py`` refuses
``trw_dispatch`` before anything else, and ``_runner.dispatch`` refuses every
other in-process caller.

Scope: this bounds the MCP server entry TRW renders. It is not an OS sandbox;
a child that can run a client CLI directly is outside it, and it assumes the
client merges the rendered env key into, rather than dropping it from, the
server entry it starts.
"""

from __future__ import annotations

import json
import os
from itertools import pairwise

#: Name of the marker. The rendered templates carry it as a static literal.
CHILD_MARKER_ENV = "TRW_DISPATCH_CHILD"


def dispatched_child_active() -> bool:
    """True when this process is a TRW server started for a dispatched child.

    # trw:intentional Presence, not value: an empty, "0" or garbled value still
    # marks a child. A parser here would be a way to talk the guard open.
    """
    return CHILD_MARKER_ENV in os.environ


#: The one codex override that sets the marker on TRW's server entry.
CODEX_MARKER_OVERRIDE = f'mcp_servers.trw.env.{CHILD_MARKER_ENV}="1"'


def _json_entry_is_marked(token: str) -> bool:
    """True when ``token`` is an MCP-config JSON whose every server env carries the marker."""
    probe = token.replace("{mcp_args}", "[]").replace("{mcp_command}", "x")
    try:
        servers = json.loads(probe)["mcpServers"]
    except (ValueError, KeyError, TypeError):
        # trw-fail-silent-allow: False REJECTS the template — unparseable means unmarked (fail closed).
        return False
    if not isinstance(servers, dict) or not servers:
        return False
    return all(isinstance(v, dict) and v.get("env") == {CHILD_MARKER_ENV: "1"} for v in servers.values())


#: Codex override keys that would replace or duplicate the marker's table.
_CODEX_CONFLICT_KEYS = frozenset(
    {"mcp_servers", "mcp_servers.trw", "mcp_servers.trw.env", f"mcp_servers.trw.env.{CHILD_MARKER_ENV}"}
)


def template_marks_child(template: tuple[str, ...]) -> bool:
    """True when a logical MCP template sets the marker UNAMBIGUOUSLY.

    The dotted shape uses the registry's logical ``trw`` name, before transport
    rendering assigns a fresh server ID. This is a construction-time validator,
    not a verifier for the final fresh-namespace argv.

    Shapes: exactly one ``--mcp-config`` whose JSON value gives every server an
    env that is exactly the marker; or exactly one ``-c`` dotted override equal
    to ``CODEX_MARKER_OVERRIDE`` with no other ``-c`` assigning the marker key or
    one of its ancestor tables. Ambiguity is refused rather than resolved: this
    does not model how a client merges repeated options. The marker name
    appearing anywhere else (a prompt, an unrelated flag) does not count.
    """
    pairs = list(pairwise(template))
    mcp_configs = [value for flag, value in pairs if flag == "--mcp-config"]
    if mcp_configs:
        return len(mcp_configs) == 1 and _json_entry_is_marked(mcp_configs[0])
    overrides = [value for flag, value in pairs if flag == "-c"]
    if overrides.count(CODEX_MARKER_OVERRIDE) != 1:
        return False
    others = [value for value in overrides if value != CODEX_MARKER_OVERRIDE]
    return not any(value.split("=", 1)[0].strip() in _CODEX_CONFLICT_KEYS for value in others)


__all__ = ["CHILD_MARKER_ENV", "CODEX_MARKER_OVERRIDE", "dispatched_child_active", "template_marks_child"]
