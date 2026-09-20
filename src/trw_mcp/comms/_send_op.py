"""One trw_send attempt: addressed admission or a bounded scoped notify (PRD-CORE-274).

Belongs to the ``trw_mcp.comms`` facade; ``comms.send`` wraps it. Split out of the
facade by ledger N7. Only the transaction helpers (_operation, _recorded_action,
_refused, _exception_refused, _CALL_BINDING) are reached through the facade module
at call time, so patches of those on ``trw_mcp.comms`` still apply. The operation
helpers (resolve_authority_snapshot, admit, observe_recipient, notify, parse_scope,
touch) are imported directly: patch them on this module, not on the facade.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from trw_mcp.comms._admission import admit, observe_recipient
from trw_mcp.comms._endpoints import touch
from trw_mcp.comms._envelope import AdmissionError, DeliveryClass, Envelope, MessageKind
from trw_mcp.comms._identity import IdentityError, resolve_authority_snapshot
from trw_mcp.comms._notify import notify
from trw_mcp.comms._scope import parse as parse_scope
from trw_mcp.comms._store import StoreError

if TYPE_CHECKING:
    from fastmcp import Context


def send_once(
    recipient_member_id: str | None = None,
    request_key: str = "",
    body: str = "",
    kind: MessageKind = "request",
    delivery_class: DeliveryClass = "on_demand",
    ctx: Context | None = None,
    *,
    scope: str | None = None,
) -> dict[str, Any]:
    """Admit one addressed message, or one bounded scoped notify, or refuse.

    Addressing is exclusive: a message goes to a NAME or to declared GROUND,
    never to both and never to neither. Supplying both is ambiguous rather than
    additive, and resolving the ambiguity by preferring one would make the other
    silently ignored.
    """
    from trw_mcp import comms as facade
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    config = get_config()
    if not config.comms_enabled:
        return {"status": "disabled", "reason": "comms_disabled", "delivery": "pull_only"}
    if not config.ctx_isolation_enabled:
        return facade._refused("context_isolation_disabled")
    try:
        snapshot = resolve_authority_snapshot(ctx, trw_dir=resolve_trw_dir(), project_root=resolve_project_root())
        facade._CALL_BINDING.set(snapshot.binding)
        if not snapshot.all_terminal:
            snapshot.assert_eligible()
        result: dict[str, Any] = {}
        with (
            facade._operation(snapshot, config) as (conn, now, _closed),
            facade._recorded_action(conn, snapshot.binding.group_id) as rejection,
        ):
            # trw:intentional Closure precedes semantic argument validation.
            # A terminal caller cannot use malformed bytes to avoid closure.
            if snapshot.all_terminal:
                raise AdmissionError("group_closed")
            if (scope is None) == (recipient_member_id is None):
                raise AdmissionError("ambiguous_addressing")
            # FR12: a displaced sender is refused; a current one renews by sending.
            touch(conn, snapshot.binding, now, lease_ttl_seconds=config.comms_lease_ttl_seconds)
            if scope is None:
                assert recipient_member_id is not None  # noqa: S101 - narrowed by the check above
                envelope = Envelope(recipient_member_id, request_key, body, kind, delivery_class)
                ttl = config.comms_message_ttl_seconds
                admitted = admit(conn, snapshot, envelope, now, ttl_seconds=ttl)
                result = {
                    "receipt": admitted,
                    # FR15: an exact retry of an expired/acked/tombstoned message reports its state.
                    "message_state": conn.execute(
                        "SELECT state FROM admissions WHERE message_id=?", (admitted["message_id"],)
                    ).fetchone()[0],
                    "recipient": observe_recipient(
                        conn, snapshot.binding.group_id, recipient_member_id, now, idle_horizon_seconds=ttl
                    ),
                }
            else:
                result = notify(
                    conn,
                    snapshot,
                    parse_scope(scope, max_bytes=config.comms_scope_max_bytes),
                    request_key,
                    body,
                    kind,
                    delivery_class,
                    now,
                    max_recipients=config.comms_scope_max_recipients,
                    ttl_seconds=config.comms_message_ttl_seconds,
                )
        if rejection:
            return facade._refused(rejection["reason"])
        return {"status": "ok", "delivery": "pull_only", **result}
    except (IdentityError, StoreError) as exc:
        return facade._exception_refused(exc)


__all__ = ["send_once"]
