"""PRD-CORE-349 read side of the AHR store: the fetched record and the body-free view (FR07).

Belongs with :mod:`trw_mcp.comms._ahr_events` (the one writer of ``ahr_events``); split out of it to keep
the writer under the module size gate. Nothing here writes: it reads stored rows and replays them.
"""

from __future__ import annotations

import json
import sqlite3

from trw_mcp.comms._ahr_events import handoff_of, rebuild
from trw_mcp.comms._ahr_state import AhrLog, JsonDoc

#: States that end a record's currency for its subject (R-SUP-1); ``completed`` stays current.
_ENDED = frozenset({"declined", "withdrawn", "expired", "superseded"})
#: A subject with more admitted records than this is not replayed for the fork check (reported as unknown).
_MAX_SUBJECT_RECORDS = 64


def fetch_record(conn: sqlite3.Connection, message_id: str) -> JsonDoc | None:
    """The offered record for the receiver's ``fetch`` (the carrier body is only the §14 pointer).

    The stored bytes are returned as data only while they fit the group's body limit, the bound a
    fetch page is sized for (``6 * body_limit + 4096``), so a large record can never make a page
    unpackable; a larger one is named and omitted. Runtime caller: ``_inbox_page._project``.
    """
    row = conn.execute(
        "SELECT e.handoff_id,e.record,g.body_limit FROM ahr_events e JOIN admissions a ON a.message_id=e.message_id "
        "JOIN groups g ON g.group_id=a.group_id WHERE e.message_id=? AND e.seq=1",
        (message_id,),
    ).fetchone()
    if row is None or row[1] is None:
        return None
    raw = bytes(row[1])
    if len(raw) > int(row[2]):
        return {"handoff_id": str(row[0]), "omitted": "larger than comms_body_max_bytes; ask the sender for the file"}
    return dict(json.loads(raw))


def _owner(log: AhrLog) -> str | None:
    """R-LC-14: the sender while offered, the receiver while accepted, the completer while reported."""
    if log.state == "offered":
        return log.sender
    if log.state == "accepted":
        return log.receiver
    if log.state == "reported":
        return log.completer
    return None


def view(conn: sqlite3.Connection, message_id: str, group_id: str) -> JsonDoc | None:
    """The ``ahr`` block of a handoff view: ids, tier, state, owner, read-back disposition, fork. No content."""
    handoff_id = handoff_of(conn, message_id)
    if handoff_id is None:
        return None
    log = rebuild(conn, handoff_id)
    block: JsonDoc = {"handoff_id": handoff_id, "tier": log.tier, "state": log.state, "owner": _owner(log)}
    mine = log.latest_rb.get(str(log.handoff["to"].get("id")))
    if mine is not None:
        block["readback"] = mine[1]["disposition"]
    if log.state not in _ENDED:
        current = _current_set(conn, str(log.handoff["subject"]), group_id)
        if current is None:
            block["fork_unknown"] = True  # more records for one subject than one view replays
        elif handoff_id in current and len(current) > 1:
            block["fork"] = True  # R-SUP-3: two current records for one subject are shown, never resolved silently
    return block


def _current_set(conn: sqlite3.Connection, subject: str, group_id: str) -> set[str] | None:
    """R-SUP-1 currency for one subject: not ended, and not listed by a successor that did not itself end
    declined, withdrawn or expired. An accepted predecessor listed by its receiver's onward record keeps its
    open duties (R-LC-14) but is no longer current, so it is not a fork. ``None`` past the replay bound."""
    rows = conn.execute(
        "SELECT DISTINCT e.handoff_id FROM ahr_events e JOIN admissions a ON a.message_id=e.message_id "
        "WHERE e.subject=? AND a.group_id=? LIMIT ?",
        (subject, group_id, _MAX_SUBJECT_RECORDS + 1),
    ).fetchall()
    if len(rows) > _MAX_SUBJECT_RECORDS:
        return None
    logs: dict[str, AhrLog] = {str(row[0]): rebuild(conn, str(row[0])) for row in rows}
    listed = {
        str(prior["handoff_id"])
        for lister in logs.values()
        if lister.state not in ("declined", "withdrawn", "expired")
        for prior in lister.handoff.get("supersedes", [])
    }
    return {hid for hid, entry in logs.items() if entry.state not in _ENDED and hid not in listed}
