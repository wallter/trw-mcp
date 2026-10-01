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

from trw_mcp.comms import _ahr_events
from trw_mcp.comms._admission import admit, observe_recipient
from trw_mcp.comms._endpoints import touch
from trw_mcp.comms._envelope import AdmissionError, DeliveryClass, Envelope, MessageKind
from trw_mcp.comms._identity import IdentityError, resolve_authority_snapshot
from trw_mcp.comms._notify import notify
from trw_mcp.comms._pause_state import send_refusal
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
    handoff: object = None,
) -> dict[str, Any]:
    """Admit one addressed message, or one bounded scoped notify, or refuse.

    Addressing is exclusive: a message goes to a NAME or to declared GROUND,
    never to both and never to neither. Supplying both is ambiguous rather than
    additive, and resolving the ambiguity by preferring one would make the other
    silently ignored.

    ``handoff={"path": ...}`` (PRD-CORE-349 FR02) offers a sealed AHR: the record is the content, so
    the body must be empty and the stored body becomes the §14 pointer ``ahr:1 <id> sha256:<hex>``.
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
            # Enforced pause (lead 957a6567): checked before the transaction, so never counted.
            paused = send_refusal(snapshot, recipient_member_id, kind)
            if paused is not None:
                return facade._refused(paused)
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
            record = _offer_record(handoff, snapshot.binding.member_id, recipient_member_id, scope, kind, body)
            if record is not None:
                body = _ahr_events.pointer(record)
            if scope is None:
                assert recipient_member_id is not None  # noqa: S101  # trw:intentional narrowed by the XOR admission check above (scope is None) == (recipient_member_id is None)
                envelope = Envelope(recipient_member_id, request_key, body, kind, delivery_class)
                ttl = config.comms_message_ttl_seconds
                admitted = admit(conn, snapshot, envelope, now, ttl_seconds=ttl)
                if record is not None:
                    _ahr_events.offer(conn, snapshot.binding.group_id, admitted["message_id"], record, now)
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


def _offer_record(
    handoff: object, sender: str, recipient: str | None, scope: str | None, kind: str, body: str
) -> dict[str, Any] | None:
    """The checked AHR a ``trw_send(handoff=...)`` offers, or None for a plain message (PRD-CORE-349 FR02)."""
    if handoff is None:
        return None
    if not isinstance(handoff, dict) or set(handoff) != {"path"}:
        raise AdmissionError("invalid_inbox_arguments")
    if scope is not None or recipient is None:
        raise AdmissionError("ahr_unaddressed_not_supported")
    if kind != "request":
        raise AdmissionError("not_a_handoff")
    if body:
        raise AdmissionError("ahr_body_conflict")  # exactly one source of content
    return _ahr_events.read_offer(handoff["path"], sender=sender, recipient=recipient)


__all__ = ["send_once"]
