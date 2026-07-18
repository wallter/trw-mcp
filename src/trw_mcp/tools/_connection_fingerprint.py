"""Session-start connection fingerprint (PRD-CORE-215 FR01).

Belongs to the ``ceremony.py`` / ``_ceremony_session_start_steps.py`` facade.
The public block is EMITTED by the session-start finalizer
(``finalize_session_start``); this module only builds the immutable value.

Production transport is stdio-only. The fingerprint describes exactly ONE stdio
process and never claims proxy or shared-server identity: ``transport`` is a
frozen ``"stdio"`` constant. Server identity (protocol version, build identity,
project identity, result schema) is stable within a process; the connection
nonce is generated once per process (a fresh import / new process yields a new
nonce) so a caller can distinguish two distinct stdio processes.

Fail-open: version and project resolution degrade to ``"unknown"`` rather than
raising, so a missing package metadata never breaks session start (NFR-safe:
no credentials, prompts, environment dumps, or absolute paths are emitted —
project identity is the project-root basename only).
"""

from __future__ import annotations

import secrets

from typing_extensions import TypedDict

# Contract version of the connection-fingerprint block defined by FR01. Bumped
# only when the emitted field set changes; stable within a process and across
# same-process calls. Not a magic number — a documented schema knob.
_CONNECTION_PROTOCOL_VERSION = "2"

# Stable identifier of the session-start result schema this fingerprint annotates.
_CONNECTION_RESULT_SCHEMA = "trw.session_start.v1"

# Production transport is stdio-only (PRD-CORE-215 §1). Frozen so no field can
# ever claim an HTTP proxy, shared server, or reconnecting broker identity.
_CONNECTION_TRANSPORT = "stdio"

# Connection nonce: generated ONCE at import time via ``secrets.token_hex`` so it
# is new per stdio process (a reload / new process gets a fresh value) yet stable
# for every call within the same process. It identifies this connection instance,
# not the caller — it carries no secret material.
_CONNECTION_NONCE = secrets.token_hex(16)


class ConnectionFingerprintDict(TypedDict):
    """Typed FR01 connection fingerprint emitted by the session-start finalizer."""

    protocol_version: str
    build_identity: str
    project_identity: str
    connection_nonce: str
    result_schema: str
    transport: str
    owner_status_capability: bool
    request_identity_capability: bool
    process_fingerprint_digest: str
    loaded_module_digest: str


def _resolve_build_identity() -> str:
    """Resolve the trw-mcp build identity (package version). Typed fail-open.

    A missing/uninstalled package metadata entry falls back to the in-tree
    ``__version__`` and finally to ``"unknown"`` — it never raises.
    """
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("trw-mcp")
    except Exception:  # justified: editable/uninstalled -> in-tree __version__
        try:
            from trw_mcp import __version__

            return str(__version__)
        except Exception:  # justified: last-resort, fingerprint must never raise
            return "unknown"


def _resolve_project_identity() -> str:
    """Resolve the repository-relative project identity (project-root basename).

    Basename only — never the absolute checkout path — so the fingerprint leaks
    no environment/path detail (NFR03). Fail-open to ``"unknown"``.
    """
    try:
        from trw_mcp.state._paths import resolve_project_root

        return resolve_project_root().name or "unknown"
    except Exception:  # justified: fail-open, project resolution must not block session start
        return "unknown"


def build_connection_fingerprint() -> ConnectionFingerprintDict:
    """Build the FR01 connection fingerprint for the current stdio process.

    Server identity (protocol/build/project/schema/transport) and the process
    nonce are stable across calls within one process; only a new process yields
    a new nonce.
    """
    from trw_mcp.canons.fingerprint import get_frozen_fingerprint

    frozen = get_frozen_fingerprint()
    return {
        "protocol_version": _CONNECTION_PROTOCOL_VERSION,
        "build_identity": _resolve_build_identity(),
        "project_identity": _resolve_project_identity(),
        "connection_nonce": _CONNECTION_NONCE,
        "result_schema": _CONNECTION_RESULT_SCHEMA,
        "transport": _CONNECTION_TRANSPORT,
        "owner_status_capability": True,
        "request_identity_capability": True,
        "process_fingerprint_digest": frozen.digest if frozen is not None else "unknown",
        "loaded_module_digest": frozen.loaded_module_digest if frozen is not None else "unknown",
    }


__all__ = [
    "ConnectionFingerprintDict",
    "build_connection_fingerprint",
]
