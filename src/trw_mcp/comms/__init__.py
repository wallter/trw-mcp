"""Public peer-communications facade (PRD-CORE-274).

Owns trusted binding, configuration guards and the operation transaction.
The membership snapshot is fresh per call, not locked atomically with SQLite.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal

import structlog

from trw_mcp.comms._admission import admit
from trw_mcp.comms._endpoints import (
    EndpointError,
    confirm_enrollment,
    enroll,
    heartbeat,
    list_endpoints,
    receiver_incarnation,
)
from trw_mcp.comms._envelope import AdmissionError, Envelope
from trw_mcp.comms._envelope import DeliveryClass as DeliveryClass
from trw_mcp.comms._envelope import InboxAction as InboxAction
from trw_mcp.comms._envelope import MessageKind as MessageKind
from trw_mcp.comms._identity import CallerBinding as CallerBinding
from trw_mcp.comms._identity import CallerSnapshot, resolve_snapshot
from trw_mcp.comms._identity import IdentityError as IdentityError
from trw_mcp.comms._identity import IdentityRefusal as IdentityRefusal
from trw_mcp.comms._identity import derive_group_id as derive_group_id
from trw_mcp.comms._identity import resolve_caller as resolve_caller
from trw_mcp.comms._inbox_page import inbox_action
from trw_mcp.comms._notify import notify
from trw_mcp.comms._peers_page import PageError, decode_cursor, pack_page
from trw_mcp.comms._policy import AdmissionPolicy, count_refusal, ensure_group
from trw_mcp.comms._scope import parse as parse_scope
from trw_mcp.comms._store import StoreError, connect, effective_time, immediate, touch_group_time, validate_operation
from trw_mcp.comms._wait import check_cancelled_cooperatively, run_bounded_wait

if TYPE_CHECKING:
    from fastmcp import Context

    from trw_mcp.models.config import TRWConfig

PeerAction = Literal["enroll", "list", "heartbeat"]
_logger = structlog.get_logger(__name__)

#: One bounded wait per serving process (FR11). Non-blocking acquire; a second
#: concurrent waiter refuses rather than queueing behind the first.
_WAIT_GUARD = threading.Lock()
#: The identity a wait was admitted under; any later attempt must match it.
_WaitOwner = tuple[str, str, str, str, str | None]
#: FR11 refusals are counted in the LEGACY persisted bucket so the stored
#: vocabulary (and every old-version reader of the mailbox) is unchanged; the
#: public ``reason`` stays precise. Closed by design: an unknown reason must
#: still fail closed in ``count_refusal``. Trade-off accepted by the lead
#: (board seq 148): the bucket is a compatible aggregate, not a diagnosis that
#: an owner change or a disabled policy was malformed input.
_PERSISTED_REFUSAL_BUCKET: dict[str, str] = {
    "wait_disabled": "invalid_inbox_arguments",
    "invalid_wait_seconds": "invalid_inbox_arguments",
    "wait_requires_fresh_fetch": "invalid_inbox_arguments",
    "wait_owner_changed": "invalid_inbox_arguments",
}


def _refused(reason: str) -> dict[str, Any]:
    # Do not reflect exception text or manifest-controlled data to peer agents.
    result: dict[str, Any] = {
        "status": "refused",
        "reason": reason,
        "detail": "Peer operation refused.",
        "delivery": "pull_only",
    }
    if reason == "storage_contended":
        result["retryable"] = True
    return result


def _exception_refused(exc: IdentityError | EndpointError | StoreError) -> dict[str, Any]:
    """Refuse with a closed reason, and leave a server-side record of WHY.

    The reason CATEGORY is logged, never the detail: a detail can carry an
    absolute path or a peer's own input, and this record outlives the call.

    Three categories, because they are three different operator problems and an
    independent audit found only the first was recorded:

    * identity — a caller could not be bound. A stamp mismatch or an uncanonical
      formation registration is an authority anomaly, and these are raised
      BEFORE any transaction, so they reach no refusal counter either. Without
      this line they exist nowhere but the calling agent's response.
    * endpoint — two live incarnations collided, or a replaced one came back.
    * storage — the mailbox is contended, corrupt, or on a schema this build
      does not speak. Silent storage corruption is how evidence is lost, so it
      is the single most important of the three to record.

    `logger.info`, not `debug`: under a default install `debug` is filtered
    before any processor runs, so a diagnostic moved there is deleted rather
    than demoted.
    """
    if isinstance(exc, IdentityError):
        _logger.info("comms_identity_refused", reason=exc.refusal.value)
    elif isinstance(exc, EndpointError):
        _logger.info("comms_endpoint_refused", reason=exc.refusal.value)
    else:
        _logger.info("comms_storage_refused", reason=exc.refusal.value, retryable=exc.retryable)
    return _refused(exc.refusal.value)


def peers(action: PeerAction, ctx: Context | None = None, *, cursor: str | None = None) -> dict[str, Any]:
    """Perform one trusted peer operation, including irreversible closure."""
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    config = get_config()
    if not config.comms_enabled:
        return {"status": "disabled", "reason": "comms_disabled", "delivery": "pull_only"}
    if not config.ctx_isolation_enabled:
        return _refused("context_isolation_disabled")
    try:
        snapshot = resolve_snapshot(ctx, trw_dir=resolve_trw_dir(), project_root=resolve_project_root())
        # Invalid binding never gets here. Pending/terminal callers have no read
        # privilege; only a validated all-terminal snapshot can record closure.
        if not snapshot.all_terminal:
            snapshot.assert_eligible()
        binding = snapshot.binding
        # trw:intentional A trusted terminal observation only closes the group;
        # cursor errors cannot skip that bookkeeping or authorize peer reads.
        after = "" if snapshot.all_terminal else decode_cursor(cursor, binding, action=action)
        incarnation = None
        endpoint = None
        payload: dict[str, Any] = {}
        with _operation(snapshot, config) as (conn, now, closed), _recorded_action(conn, binding.group_id) as rejection:
            if closed:
                raise AdmissionError("group_closed")
            if action == "enroll":
                endpoint, incarnation = enroll(conn, binding, now=now, lease_ttl_seconds=config.comms_lease_ttl_seconds)
            elif action == "heartbeat":
                endpoint = heartbeat(conn, binding, now=now, lease_ttl_seconds=config.comms_lease_ttl_seconds)
            rows = list_endpoints(conn, binding.group_id, limit=config.comms_fetch_max_items + 1, after_member=after)
            payload = pack_page(
                binding,
                rows,
                now=now,
                own_endpoint=endpoint,
                poll_seconds=config.comms_poll_interval_seconds,
                limit=config.comms_fetch_max_items,
                max_bytes=config.comms_response_max_bytes,
            )
        if rejection:
            return _refused(rejection["reason"])
        if incarnation is not None:
            confirm_enrollment(binding, incarnation)
    except PageError as exc:
        return _refused(exc.reason)
    except (IdentityError, EndpointError, StoreError) as exc:
        return _exception_refused(exc)

    return payload


@contextmanager
def _operation(snapshot: CallerSnapshot, config: TRWConfig) -> Iterator[tuple[sqlite3.Connection, float, bool]]:
    """Validate again under the write lock; group birth/closure survive ordinary refusals."""
    binding = snapshot.binding
    with connect(binding.manifest_path, busy_timeout_ms=config.comms_sqlite_busy_timeout_ms) as conn, immediate(conn):
        validate_operation(conn)
        now = effective_time(conn, binding.group_id)
        ensure_group(conn, binding, now, AdmissionPolicy.from_config(config))
        touch_group_time(conn, binding.group_id, now)
        if snapshot.all_terminal:
            conn.execute("UPDATE groups SET closed=1 WHERE group_id=?", (binding.group_id,))
        closed = bool(conn.execute("SELECT closed FROM groups WHERE group_id=?", (binding.group_id,)).fetchone()[0])
        yield conn, now, closed


@contextmanager
def _recorded_action(conn: sqlite3.Connection, group_id: str) -> Iterator[dict[str, str]]:
    """Roll back action writes on an expected refusal, then commit one bounded counter.

    Unexpected storage failures escape and roll back the entire transaction;
    counting them here could recursively write into unavailable/corrupt state.
    """
    rejection: dict[str, str] = {}
    conn.execute("SAVEPOINT comms_action")
    try:
        yield rejection
    except (AdmissionError, EndpointError, PageError) as exc:
        conn.execute("ROLLBACK TO comms_action")
        reason = exc.refusal.value if isinstance(exc, EndpointError) else exc.reason
        count_refusal(conn, group_id, _PERSISTED_REFUSAL_BUCKET.get(reason, reason))
        rejection["reason"] = reason
    finally:
        conn.execute("RELEASE comms_action")


def send(
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
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    config = get_config()
    if not config.comms_enabled:
        return {"status": "disabled", "reason": "comms_disabled", "delivery": "pull_only"}
    if not config.ctx_isolation_enabled:
        return _refused("context_isolation_disabled")
    try:
        snapshot = resolve_snapshot(ctx, trw_dir=resolve_trw_dir(), project_root=resolve_project_root())
        if not snapshot.all_terminal:
            snapshot.assert_eligible()
        result: dict[str, Any] = {}
        with (
            _operation(snapshot, config) as (conn, now, _closed),
            _recorded_action(conn, snapshot.binding.group_id) as rejection,
        ):
            # trw:intentional Closure precedes semantic argument validation.
            # A terminal caller cannot use malformed bytes to avoid closure.
            if snapshot.all_terminal:
                raise AdmissionError("group_closed")
            if (scope is None) == (recipient_member_id is None):
                raise AdmissionError("ambiguous_addressing")
            if scope is None:
                assert recipient_member_id is not None  # noqa: S101 - narrowed by the check above
                envelope = Envelope(recipient_member_id, request_key, body, kind, delivery_class)
                result = {"receipt": admit(conn, snapshot, envelope, now)}
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
                )
        if rejection:
            return _refused(rejection["reason"])
        return {"status": "ok", "delivery": "pull_only", **result}
    except (IdentityError, StoreError) as exc:
        return _exception_refused(exc)


def _validate_wait(
    wait_seconds: object, action: InboxAction, message_ids: list[str] | None, cursor: str | None, config: TRWConfig
) -> None:
    """FR11 argument rules, applied AFTER closure and endpoint verification, in this order."""
    # trw:intentional bool is an int subclass; a direct caller passing True must not become a 1 s wait.
    positive = type(wait_seconds) is int and wait_seconds > 0
    if positive and config.comms_wait_max_seconds == 0:
        raise AdmissionError("wait_disabled")
    if type(wait_seconds) is not int or not 0 <= wait_seconds <= config.comms_wait_max_seconds:
        raise AdmissionError("invalid_wait_seconds")
    if positive and (action != "fetch" or message_ids is not None or cursor is not None):
        raise AdmissionError("wait_requires_fresh_fetch")


def _inbox_attempt(
    action: InboxAction,
    message_ids: list[str] | None,
    cursor: str | None,
    ctx: Context | None,
    wait_seconds: int,
    owner: dict[str, _WaitOwner],
) -> tuple[dict[str, Any], bool]:
    """One complete ordinary inbox operation; the bool asks the caller to wait again.

    Everything is re-resolved per call — effective config, binding, lease and
    incarnation — so a wait observes changes exactly as a fresh fetch would.
    """
    from trw_mcp.models.config import get_config
    from trw_mcp.state._paths import resolve_project_root, resolve_trw_dir

    config = get_config()
    if not config.comms_enabled:
        return {"status": "disabled", "reason": "comms_disabled", "delivery": "pull_only"}, False
    if not config.ctx_isolation_enabled:
        return _refused("context_isolation_disabled"), False
    try:
        snapshot = resolve_snapshot(ctx, trw_dir=resolve_trw_dir(), project_root=resolve_project_root())
        if not snapshot.all_terminal:
            snapshot.assert_eligible()
        binding = snapshot.binding
        result: dict[str, Any] = {}
        with (
            _operation(snapshot, config) as (conn, now, closed),
            _recorded_action(conn, binding.group_id) as rejection,
        ):
            if snapshot.all_terminal or (closed and action != "status"):
                raise AdmissionError("group_closed")
            incarnation = receiver_incarnation(conn, binding, now) if action in ("fetch", "ack") else None
            _validate_wait(wait_seconds, action, message_ids, cursor, config)
            if wait_seconds > 0:
                # trw:intentional Owner is frozen by the FIRST attempt and compared BEFORE any message
                # page is selected or prepared; a changed pin/run/incarnation cannot retarget a wait.
                mine: _WaitOwner = (
                    binding.group_id,
                    binding.member_id,
                    str(binding.run_path.resolve()),
                    binding.session_id,
                    incarnation,
                )
                if owner.setdefault("tuple", mine) != mine:
                    raise AdmissionError("wait_owner_changed")
            result = inbox_action(
                conn,
                binding,
                action,
                message_ids,
                cursor,
                incarnation,
                now=now,
                limit=config.comms_fetch_max_items,
                max_bytes=config.comms_response_max_bytes,
            )
        if rejection:
            return _refused(rejection["reason"]), False
        return result, wait_seconds > 0 and not result["items"]
    except (IdentityError, EndpointError, StoreError) as exc:
        return _exception_refused(exc), False


def inbox(
    action: InboxAction = "fetch",
    message_ids: list[str] | None = None,
    cursor: str | None = None,
    ctx: Context | None = None,
    wait_seconds: int = 0,
) -> dict[str, Any]:
    """Read pending traffic, acknowledge receipt, or inspect body-free facts.

    A positive ``wait_seconds`` (FR11) repeats an EMPTY fresh fetch inside this
    process until a page arrives, a refusal occurs, or a monotonic retry
    deadline passes. The first attempt is always ordinary; the payload shape and
    every zero-wait byte are unchanged.
    """
    from trw_mcp.models.config import get_config

    # The entry instant is captured now so the budget spans the first attempt too,
    # but the deadline is only DERIVED after that attempt has admitted a bounded
    # positive request: an unbounded integer must reach the ordinary refusal
    # path (after closure) as invalid_wait_seconds, never overflow the clock here.
    entry = time.monotonic()
    owner: dict[str, _WaitOwner] = {}
    payload, retry = _inbox_attempt(action, message_ids, cursor, ctx, wait_seconds, owner)
    if type(wait_seconds) is int and wait_seconds > 0:
        # Cooperative checkpoint after the first attempt of a positive wait, whatever
        # it returned; zero-wait calls keep the pre-amendment path untouched.
        check_cancelled_cooperatively()
    if not retry:
        return payload
    if not _WAIT_GUARD.acquire(blocking=False):
        return _refused("wait_already_active")
    try:
        return run_bounded_wait(
            lambda: _inbox_attempt(action, message_ids, cursor, ctx, wait_seconds, owner),
            last_empty=payload,
            deadline=entry + wait_seconds,
            interval_seconds=lambda: get_config().comms_wait_interval_ms / 1000.0,
        )
    finally:
        _WAIT_GUARD.release()


__all__ = [
    "CallerBinding",
    "DeliveryClass",
    "IdentityError",
    "IdentityRefusal",
    "InboxAction",
    "MessageKind",
    "PeerAction",
    "derive_group_id",
    "inbox",
    "peers",
    "resolve_caller",
    "send",
]
