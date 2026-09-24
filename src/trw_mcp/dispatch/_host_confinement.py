"""Request-level host confinement for dispatched clients (PRD-CORE-291, PRD-CORE-297).

``_confine`` owns the macOS seatbelt mechanism; this module decides, per
request, whether that wrapper applies, and whether a read-only request was
actually enforced as a write denial rather than merely requested.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.dispatch._client_specs import UnknownClientError, client_spec_for
from trw_mcp.dispatch._confine import CONFINEMENT_MECHANISM, confinement_prefix, confinement_unavailable_reason
from trw_mcp.dispatch._types import DispatchRequest


def _read_only_enforced(req: DispatchRequest, confine_argv: list[str]) -> bool:
    """Whether this dispatch actually denied writes, not merely requested it.

    ``req.read_only`` is the request; this is the measured outcome, and the two
    diverge for exactly one class of client (PRD-CORE-291). A client that
    declares ``host_confinement`` (agy) has its OWN read-only flag denying reads
    as well as writes (the registry's own comment on ``agy.sandbox``: ``--sandbox``
    "restricts SHELL COMMANDS, not agy's file-edit tool"), so ``build_command``
    now always adds the read-enabling ``confined_read_only_argv`` fragment on
    the read-only path. That fragment grants agy read access but denies nothing
    by itself -- the write denial for that client comes ONLY from the host
    wrapper (``_confinement_for`` / ``confinement_prefix``), so
    ``read_only_enforced`` for such a client must be True only when that wrapper
    was actually applied to this run (``confine_argv`` non-empty), never merely
    because the request asked for read-only.

    Every other client's read-only mechanism (an explicit sandbox flag, or the
    omission of ``allow_writes_argv``) is the verified denial on its own, so
    ``read_only_enforced`` there continues to reflect the request.
    """
    if not req.read_only:
        return False
    try:
        spec = client_spec_for(req.client)
    except UnknownClientError:  # pragma: no cover - guarded by the Literal upstream
        return True
    if spec.host_confinement:
        return bool(confine_argv)
    return True


def _needs_host_confinement(req: DispatchRequest) -> bool:
    try:
        return bool(client_spec_for(req.client).host_confinement)
    except UnknownClientError:  # pragma: no cover  # trw-fail-silent-allow: the client Literal makes this unreachable; an unknown client has no host confinement to require
        return False


def _confinement_for(req: DispatchRequest, writable: Path | None = None) -> tuple[list[str], str]:
    """Return the host write-denial prefix for *req* and the note explaining it.

    Only a READ-ONLY request for a client that declares ``host_confinement`` is
    wrapped. A write run is not wrapped on purpose: the caller asked for writes,
    and a wrapper that denied them would make ``--allow-writes`` a silent no-op.
    """
    try:
        spec = client_spec_for(req.client)
    except UnknownClientError:  # pragma: no cover - guarded by the Literal upstream
        return [], "unknown client: no confinement"
    if not req.read_only or not spec.host_confinement:
        return [], ""
    prefix = confinement_prefix(writable)
    if not prefix:
        return [], f"host write-denial wrapper unavailable ({confinement_unavailable_reason()})"
    return prefix, CONFINEMENT_MECHANISM + (f", except the per-run HOME {writable}" if writable else "")
