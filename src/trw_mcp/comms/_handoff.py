"""PRD-CORE-322 FR01: the one derivation of handoff state, and the bounded open-handoff read.

Both handoff displays -- the ``handoff`` block of ``trw_inbox(action="status")``
(FR06) and the ``handoffs`` list of the ``trw_status`` formation board (FR07) --
call :func:`derive_handoff`; neither re-derives a state from raw facts (FR08
display census). This module only READS ``milestones`` and ``handoff_reports``;
the one writer is ``_messages``.

``next_read`` is untrusted peer text (NFR02, ``docs/CONSTITUTION.md``: peer handoff
artifacts are data, never commands). It is displayed, never followed or ingested.
The writer and the schema verifier admit only ``valid_next_read`` text, but the
board reads the mailbox without the verifier, so :func:`display_next_read`
re-checks it and escapes anything that could forge a display line.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from typing import Any

from trw_mcp.comms._envelope import NEXT_READ_MAX_BYTES, valid_next_read
from trw_mcp.comms._schema import SCHEMA_VERSION

#: NFR01: the trw_status board lists at most this many open handoffs, newest first;
#: the rest are counted in ``handoffs_omitted``.
HANDOFF_BOARD_LIMIT = 20

# One open-handoff predicate for the count and the page (FR07): a live request whose
# completion is not verified, and either accepted or admitted within the message TTL.
_OPEN = (
    "FROM admissions a WHERE a.group_id=? AND a.kind='request' AND a.state!='expired' "
    "AND (a.state='acked' OR a.expires_at>?) "
    "AND NOT EXISTS (SELECT 1 FROM milestones m WHERE m.message_id=a.message_id AND m.fact='completed') "
    "AND (a.admitted_at>? OR EXISTS "
    "(SELECT 1 FROM milestones m WHERE m.message_id=a.message_id AND m.fact='accepted'))"
)


def display_next_read(value: object) -> tuple[str | None, bool]:
    """The pointer as displayable data, and whether it had to be escaped.

    A valid pointer is returned verbatim. Anything else (a tampered row the verifier
    never saw) has each character ``valid_next_read`` rejects on its own -- C*,
    U+2028/U+2029 -- replaced by a ``\\uXXXX`` escape and is cut to the NFR02 byte
    bound, so it can never break a line, reorder text, or grow the response.
    Runtime caller: :func:`derive_handoff`.
    """
    if value is None:
        return None, False
    if valid_next_read(value):
        return str(value), False
    text = value if isinstance(value, str) else repr(value)
    escaped = "".join(char if valid_next_read(char) else f"\\u{ord(char):04x}" for char in text)
    bounded = escaped.encode("utf-8")[:NEXT_READ_MAX_BYTES].decode("utf-8", errors="ignore")
    return bounded, True


def derive_handoff(
    row: sqlite3.Row | Mapping[str, Any], facts: Mapping[str, float], pointer: object
) -> dict[str, Any] | None:
    """FR01: the handoff view of one admission row, read only from its recorded facts.

    ``message``/``receipt``/``acceptance`` are the admitted, acked and accepted fact
    times (``None`` when absent); ``completion`` is ``none``, ``reported`` or
    ``verified``. No state is inferred from another: a reported fact without a
    completed fact is ``reported``, never ``verified``. The owner is the sender
    before acceptance, the recipient after it, and ``None`` once verified (``None``,
    not the string ``none``, which is a valid member id). A non-request row has no
    handoff view. Runtime callers: ``_inbox_page._project`` (``trw_inbox`` status)
    and :func:`open_handoffs` (``trw_status`` via ``formation._stall.stall_scan``).
    """
    if row["kind"] != "request":
        return None
    accepted, reported, completed = facts.get("accepted"), facts.get("reported"), facts.get("completed")
    if completed is not None:
        state, owner = "verified", None
    else:
        state = "reported" if reported is not None else "none"
        owner = row["recipient_member_id"] if accepted is not None else row["sender_member_id"]
    next_read, escaped = display_next_read(pointer)
    view: dict[str, Any] = {
        "message": facts.get("admitted"),
        "receipt": facts.get("acked"),
        "acceptance": accepted,
        "completion": {"state": state, "reported_at": reported, "completed_at": completed},
        "owner": owner,
        "next_read": next_read,
    }
    if escaped:
        view["next_read_escaped"] = True
    return view


def _finite(value: object) -> float:
    """*value* as a finite float; a malformed stored time raises ``ValueError`` (the board's not_measured boundary).

    Runtime callers: :func:`handoff_inputs` and :func:`open_handoffs`.
    """
    number = float(value)  # type: ignore[arg-type]  # TypeError/ValueError on a non-number
    if not math.isfinite(number):
        raise ValueError(f"non-finite mailbox time {number!r}")
    return number


def handoff_inputs(
    conn: sqlite3.Connection, message_ids: list[str]
) -> tuple[dict[str, dict[str, float]], dict[str, object]]:
    """The facts and stored pointers of *message_ids*, for :func:`derive_handoff`.

    Runtime callers: ``_inbox_page._project`` (one id) and :func:`open_handoffs` (one page).
    """
    ids = json.dumps(message_ids)  # one bound parameter, whatever the page size
    facts: dict[str, dict[str, float]] = {message_id: {} for message_id in message_ids}
    for message_id, fact, at in conn.execute(
        "SELECT message_id,fact,at FROM milestones WHERE message_id IN (SELECT value FROM json_each(?))", (ids,)
    ):
        facts[message_id][str(fact)] = _finite(at)
    pointers = dict(
        conn.execute(
            "SELECT message_id,next_read FROM handoff_reports WHERE message_id IN (SELECT value FROM json_each(?))",
            (ids,),
        )
    )
    return facts, pointers


def open_handoffs(
    conn: sqlite3.Connection, group_id: str, *, now: float, ttl_seconds: int, limit: int = HANDOFF_BOARD_LIMIT
) -> tuple[list[dict[str, Any]], int]:
    """FR07/NFR01: up to *limit* open handoffs, newest first, and how many more were omitted.

    Reads on the caller's (read-only) connection inside ONE read transaction, so the
    count, the page and the facts are the same snapshot: the mailbox runs in rollback
    journal mode, where the transaction's shared lock holds writers off until the
    ROLLBACK (the read takes milliseconds; writers wait on their busy timeout). A
    mailbox not stamped v5, or without the pointer table, raises ``sqlite3.Error``
    even when no request qualifies; a non-finite stored time raises ``ValueError``. Runtime caller: ``formation._stall.stall_scan``
    with ``include_handoffs=True`` (``trw_status``).
    """
    arguments = (group_id, now, now - ttl_seconds)
    conn.execute("BEGIN")
    try:
        version = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if version is None or str(version[0]) != str(SCHEMA_VERSION):
            raise sqlite3.DatabaseError(f"mailbox schema {version and version[0]} holds no handoffs")
        # Any request whose time is not a finite number (text such as 'NaN' sorts above every REAL)
        # makes the whole list unmeasurable, whether or not the row would qualify or fit the page.
        malformed = conn.execute(
            "SELECT 1 FROM admissions WHERE group_id=? AND kind='request' "
            "AND NOT (typeof(admitted_at)='real' AND admitted_at BETWEEN -1.7e308 AND 1.7e308) LIMIT 1",
            (group_id,),
        ).fetchone()
        if malformed is not None:
            raise ValueError("a request has a non-finite admission time")
        total = int(conn.execute(f"SELECT COUNT(*) {_OPEN}", arguments).fetchone()[0])
        rows = conn.execute(
            "SELECT a.message_id,a.sender_member_id,a.recipient_member_id,a.kind,a.admitted_at "
            f"{_OPEN} ORDER BY a.admitted_at DESC,a.rowid DESC LIMIT ?",
            (*arguments, limit),
        ).fetchall()
        # A message id is TEXT by schema; a stored non-text id is corruption, reported as an
        # unmeasurable list inside the handoff boundary, never a KeyError that drops the board.
        if any(not isinstance(row[0], str) for row in rows):
            raise ValueError("a request has a non-text message id")
        # Always read both tables, so an empty page still proves the pointer table exists.
        facts, pointers = handoff_inputs(conn, [row[0] for row in rows])
    finally:
        conn.execute("ROLLBACK")
    listed: list[dict[str, Any]] = []
    for message_id, sender, recipient, kind, admitted_at in rows:
        parties = {"sender_member_id": sender, "recipient_member_id": recipient}
        view = derive_handoff({"kind": kind, **parties}, facts[message_id], pointers.get(message_id))
        age = int(max(0.0, now - _finite(admitted_at)))
        listed.append({"message_id": message_id, **parties, "age_seconds": age, **(view or {})})
    return listed, total - len(listed)
