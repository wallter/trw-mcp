"""PRD-CORE-349: the comms mailbox as an L2 AHR store -- the ONE writer of ``ahr_events``.

Every AHR event is built here, stepped through the pure replay (``_ahr_state.AhrLog.step``) over the
handoff's stored log, and appended by :func:`_append`, the only statement that adds a log row in the
package (FR08 census, ``tests/comms/test_ahr_census.py``). The table is append-only: nothing here or
anywhere else updates or deletes a row, so supersession and expiry add events and never remove one.

The stored bytes are the authority (class G): the handoff rides on its ``offered`` row and each
read-back on its ``read_back`` row, as RFC 8785 bytes, and a file is never re-read after admission.
Records are data (R-SEC-1); refs are dereferenced only when local (NFR02). Actor identity is the comms
binding: the store admits a record only when ``from`` is the caller and ``to`` the addressed member,
so the comms role checks (``_messages.handoff_rows``) pin each event's actor.

All writes run inside the facade's ``BEGIN IMMEDIATE`` operation, so ``seq`` is assigned under the
write lock and an accept and a supersession of one subject serialize (R-SUP-6). A refusal raises
``AdmissionError``; the facade rolls the whole action back, so no partial event survives (NFR01).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trw_mcp.comms._ahr_state import AHR_MEDIA, TIER_RANK, AhrLog, JsonDoc, Target, start
from trw_mcp.comms._envelope import AdmissionError
from trw_mcp.comms._store import StoreError, StoreRefusal
from trw_mcp.handoff import AhrInputError, AhrParseError, digest, jcs, load, seal
from trw_mcp.handoff._rules import instant
from trw_mcp.handoff._validate import MAX_INPUT_BYTES
from trw_mcp.telemetry.otel_ahr import project_event

AHR_SPEC = "1.0-rc.1"
#: The store's own principal: the actor of ``expired`` (R-TIME-2).
STORE_PRINCIPAL: JsonDoc = {"id": "trw-comms-store", "kind": "service"}
#: The single admitted statement (FR08): pinned verbatim by the census.
_INSERT = (
    "INSERT INTO ahr_events(handoff_id,seq,message_id,event_id,event,actor,at,subject,event_digest,event_doc,record) "
    "VALUES (?,?,?,?,?,?,?,?,?,?,?)"
)
_BOUND = re.compile(r"^(?P<path>.+)#(?P<digest>sha256:[0-9a-f]{64})$")
#: States that end a record's currency for its subject (R-SUP-1); ``completed`` stays current.
_ENDED = frozenset({"declined", "withdrawn", "expired", "superseded"})
#: Replay rule prefixes that mean "the receiver has not shown understanding yet" (R-LC-11, R-RB-4).
_READBACK_RULES = ("R-LC-11 ", "R-RB-4 ")


def iso(now: float) -> str:
    """The store's RFC 3339 instant for group time *now* (microseconds, literal Z)."""
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def pointer(record: JsonDoc) -> str:
    """The §14 carrier body: ``ahr:1 <handoff_id> sha256:<hex>``."""
    return f"ahr:1 {record['handoff_id']} {digest(record)}"


def _refusal(errs: list[str], action: str) -> AdmissionError:
    first = errs[0]
    if action == "accepted" and first.startswith(_READBACK_RULES):
        return AdmissionError("ahr_readback_required")
    if ": readback " in first or first.startswith("handoff: "):
        return AdmissionError("ahr_invalid")
    return AdmissionError("ahr_lifecycle_refused")


# --- local files (NFR02) -----------------------------------------------------------------------


def local_file(text: object) -> tuple[str, Path]:
    """A ``file:``/repo-relative path to a regular file under the project root, as (relative, resolved); else refuse.

    Symlinks resolve before the containment check, so a link out of the project is refused like
    an absolute path. ``https`` and every other scheme are refused (NFR02).
    """
    from trw_mcp.state._paths import resolve_project_root

    if not isinstance(text, str) or not text.strip():
        raise AdmissionError("invalid_inbox_arguments")
    raw = text.strip().removeprefix("file:")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", raw):
        raise AdmissionError("ahr_ref_not_local")
    root = resolve_project_root().resolve()
    try:
        path = (root / raw).resolve()
        if root not in path.parents or not path.is_file():
            raise AdmissionError("ahr_ref_not_local")
        return path.relative_to(root).as_posix(), path
    except (OSError, ValueError, RuntimeError) as exc:
        raise AdmissionError("ahr_ref_not_local") from exc


def _hashed(text: object) -> tuple[str, Target]:
    """A local ref as (relative path, (sample bytes, ``sha256:`` digest)), hashed in bounded chunks (class B).

    Only a file small enough to be an AHR record keeps its bytes, for the replay's "an AHR record
    without its media type" check (R-INT-7); a larger file cannot be one, so its sample is empty.
    """
    relative, path = local_file(text)
    sha = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1 << 20):
                sha.update(chunk)
        sample = path.read_bytes() if path.stat().st_size <= MAX_INPUT_BYTES else b""
    except OSError as exc:
        raise AdmissionError("ahr_ref_not_local") from exc
    return relative, (sample, "sha256:" + sha.hexdigest())


def _load_record(text: object, kind: str) -> JsonDoc:
    """Read one local AHR document of *kind* (``handoff`` or ``readback``); refuse anything else."""
    _relative, path = local_file(text)
    try:
        doc = load(path)  # bounded at MAX_INPUT_BYTES
    except (AhrInputError, AhrParseError) as exc:
        raise AdmissionError("ahr_invalid") from exc
    if doc.get("type") != kind:
        raise AdmissionError("ahr_invalid")
    return doc


# --- the log -----------------------------------------------------------------------------------


def handoff_of(conn: sqlite3.Connection, message_id: str) -> str | None:
    """The handoff a carrier message offered, or None for a plain (PRD-CORE-322) request."""
    row = conn.execute("SELECT handoff_id FROM ahr_events WHERE message_id=? AND seq=1", (message_id,)).fetchone()
    return str(row[0]) if row is not None else None


def _offered_record(conn: sqlite3.Connection, handoff_id: str) -> JsonDoc | None:
    row = conn.execute("SELECT record FROM ahr_events WHERE handoff_id=? AND seq=1", (handoff_id,)).fetchone()
    return None if row is None or row[0] is None else dict(json.loads(bytes(row[0])))


def _stored_target(conn: sqlite3.Connection, ev: JsonDoc, record: bytes | None) -> Target | None:
    """A stored event's ref target, from stored bytes only: admission already checked the content."""
    ref = ev.get("ref")
    if ref is None:
        return None
    if ev["event"] == "read_back" and record is not None:
        readback = dict(json.loads(record))
        return readback, digest(readback)
    if ev["event"] == "superseded":
        successor = _offered_record(conn, str(ref["uri"]).removeprefix("trw:ahr/"))
        return (successor, digest(successor)) if successor is not None else None
    return b"", str(ref["digest"])  # a local file, hashed when the event was admitted


def rebuild(conn: sqlite3.Connection, handoff_id: str) -> AhrLog:
    """Replay one handoff's stored log; a stored log that no longer replays is corruption."""
    rows = conn.execute(
        "SELECT event_doc,record FROM ahr_events WHERE handoff_id=? ORDER BY seq", (handoff_id,)
    ).fetchall()
    if not rows or rows[0][1] is None:
        raise StoreError(StoreRefusal.CORRUPT, "an AHR log without its offered record")
    log, errs = start(dict(json.loads(bytes(rows[0][1]))))
    for event_doc, record in rows:
        if log is None or errs:
            raise StoreError(StoreRefusal.CORRUPT, "a stored AHR log does not replay")
        ev = dict(json.loads(bytes(event_doc)))
        errs = log.step(ev, _stored_target(conn, ev, None if record is None else bytes(record)))
    if log is None or errs:
        raise StoreError(StoreRefusal.CORRUPT, "a stored AHR log does not replay")
    return log


def _append(
    conn: sqlite3.Connection,
    log: AhrLog,
    message_id: str,
    event: str,
    actor: JsonDoc,
    now: float,
    *,
    target: Target | None = None,
    record: bytes | None = None,
    **fields: Any,
) -> JsonDoc:
    """Build, step and append one event: the only statement that adds a log row. Refuses on any violation."""
    at = iso(now)
    ev: JsonDoc = {
        "ahr": AHR_SPEC,
        "type": "event",
        "event_id": uuid.uuid4().hex,
        "handoff_id": log.handoff["handoff_id"],
        "seq": log.count + 1,
        "event": event,
        "actor": actor,
        "at": at,
        "record_digest": log.record_digest,
        **fields,
    }
    if log.prev_event is not None:
        ev["prev_event_digest"] = digest(log.prev_event)
    if event == "accepted":
        ev["effective_at"] = at  # R-LC-6: the store sets it to the stored time
    errs = log.step(ev, target)
    if errs:
        raise _refusal(errs, event)
    conn.execute(
        _INSERT,
        (
            ev["handoff_id"],
            ev["seq"],
            message_id,
            ev["event_id"],
            event,
            actor["id"],
            at,
            log.handoff["subject"],
            digest(ev),
            jcs(ev),
            record,
        ),
    )
    carrier = None
    if event == "accepted":  # FR09: the receiver's span links to the sender's (PRD-CORE-342 carrier)
        stored = conn.execute("SELECT traceparent FROM admissions WHERE message_id=?", (message_id,)).fetchone()
        carrier = None if stored is None else stored[0]
    project_event(event, str(ev["handoff_id"]), log.tier, carrier)
    return ev


def _receiver(log: AhrLog) -> JsonDoc:
    to = log.handoff["to"]
    return {"id": to["id"], "kind": to["kind"]}


def _sender(log: AhrLog) -> JsonDoc:
    sender: JsonDoc = log.handoff["from"]
    return sender


def _logged(conn: sqlite3.Connection, message_id: str) -> AhrLog:
    handoff_id = handoff_of(conn, message_id)
    if handoff_id is None:
        raise AdmissionError("ahr_not_offered")
    return rebuild(conn, handoff_id)


# --- offer (FR02, FR05, FR06) ------------------------------------------------------------------


def read_offer(path: object, *, sender: str, recipient: str) -> JsonDoc:
    """Load, seal-if-unsealed and check one handoff for ``trw_send(handoff=...)``; refuse what this store cannot hold."""
    record = _load_record(path, "handoff")
    if not record.get("integrity"):
        record = seal(record)  # R-INT-1: the store seals an unsealed record
    log, _errs = start(record)  # L1, including X-1 on a stale integrity.digest
    if log is None:
        raise AdmissionError("ahr_invalid")
    if record["to"].get("kind") == "unaddressed":
        raise AdmissionError("ahr_unaddressed_not_supported")
    if record["tier"] == "critical":
        raise AdmissionError("ahr_tier_not_supported")  # S4 (L3) is not built; FR10
    if record["from"]["id"] != sender or record["to"].get("id") != recipient:
        raise AdmissionError("ahr_party_mismatch")
    return record


def offer(conn: sqlite3.Connection, group_id: str, message_id: str, record: JsonDoc, now: float) -> None:
    """Append ``offered`` (and ``superseded`` on each local predecessor) for an admitted carrier message.

    An exact retry (same message, same record) appends nothing; a handoff id already offered
    through another message, or with other bytes, is a conflict.
    """
    carrier = conn.execute("SELECT expires_at FROM admissions WHERE message_id=?", (message_id,)).fetchone()
    expires = record.get("expires_at")
    if expires is not None and carrier is not None and instant(expires).timestamp() > float(carrier[0]):
        raise AdmissionError("ahr_expiry_exceeds_ttl")
    existing = conn.execute(
        "SELECT message_id,event_doc FROM ahr_events WHERE handoff_id=? AND seq=1", (record["handoff_id"],)
    ).fetchone()
    if existing is not None:
        same = existing[0] == message_id and json.loads(bytes(existing[1]))["record_digest"] == digest(record)
        if not same:
            raise AdmissionError("ahr_event_conflict")
        return
    for listed in record.get("supersedes", []):
        predecessor = _local_handoff(conn, group_id, str(listed["handoff_id"]))
        if predecessor is not None:  # one never admitted here changes only the lister's view (R-SUP-1)
            _supersede(conn, rebuild(conn, predecessor), record, str(listed["digest"]), now)
    fresh, _errs = start(record)
    assert fresh is not None  # noqa: S101  # trw:intentional read_offer proved the record valid
    _append(conn, fresh, message_id, "offered", _sender(fresh), now, record=jcs(record))


def _supersede(conn: sqlite3.Connection, log: AhrLog, record: JsonDoc, listed_digest: str, now: float) -> None:
    """R-SUP-1/R-SUP-2 for one listed predecessor admitted here.

    An offered predecessor gets ``superseded`` (the replay checks sender, subject, tier floor and the
    listed digest). Any other predecessor only loses currency, so no event is appended, and the new
    record must come from its current owner (the receiver once accepted or reported; the sender or
    the receiver once terminal), for the same subject, at no lower tier, naming its exact digest.
    """
    if log.state == "offered":
        ref = {"uri": f"trw:ahr/{record['handoff_id']}", "digest": digest(record), "media_type": AHR_MEDIA}
        message = handoff_carrier(conn, str(log.handoff["handoff_id"]))
        _append(conn, log, message, "superseded", _sender(log), now, target=(record, digest(record)), ref=ref)
        return
    owners = {log.receiver} if log.state in ("accepted", "reported") else {log.sender, log.receiver}
    if (
        record["from"]["id"] not in owners
        or record["subject"] != log.handoff["subject"]
        or TIER_RANK[record["tier"]] < TIER_RANK[log.tier]
        or listed_digest != log.record_digest
    ):
        raise AdmissionError("ahr_lifecycle_refused")


def _local_handoff(conn: sqlite3.Connection, group_id: str, handoff_id: str) -> str | None:
    row = conn.execute(
        "SELECT e.handoff_id FROM ahr_events e JOIN admissions a ON a.message_id=e.message_id "
        "WHERE e.handoff_id=? AND e.seq=1 AND a.group_id=?",
        (handoff_id, group_id),
    ).fetchone()
    return None if row is None else str(row[0])


def handoff_carrier(conn: sqlite3.Connection, handoff_id: str) -> str:
    return str(
        conn.execute("SELECT message_id FROM ahr_events WHERE handoff_id=? AND seq=1", (handoff_id,)).fetchone()[0]
    )


def sweep_expired(conn: sqlite3.Connection, group_id: str, now: float) -> None:
    """R-TIME-2: append ``expired`` to every offered record in the group at or after its ``expires_at``.

    Runs at the start of every comms operation, like carrier expiry, so ``expired`` precedes any
    other event on a stale offer. Runtime caller: ``comms._operation``.
    """
    offered = conn.execute(
        "SELECT e.handoff_id,e.message_id,json_extract(CAST(e.record AS TEXT),'$.expires_at') "
        "FROM ahr_events e JOIN admissions a ON a.message_id=e.message_id "
        "WHERE a.group_id=? AND e.seq=1 AND json_extract(CAST(e.record AS TEXT),'$.expires_at') IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM ahr_events t WHERE t.handoff_id=e.handoff_id "
        "AND t.event IN ('accepted','declined','withdrawn','superseded','expired'))",
        (group_id,),
    ).fetchall()
    for handoff_id, message_id, expires_text in offered:
        if instant(str(expires_text)) > datetime.fromtimestamp(now, UTC):
            continue  # not due: only a due record pays for a replay
        log = rebuild(conn, str(handoff_id))
        expires = log.expires_at()
        if log.state == "offered" and expires is not None and datetime.fromtimestamp(now, UTC) >= expires:
            _append(conn, log, str(message_id), "expired", STORE_PRINCIPAL, now)


# --- receiver and sender actions (FR03, FR04) --------------------------------------------------


def read_back(conn: sqlite3.Connection, message_id: str, path: object, now: float) -> None:
    """Store the receiver's read-back (X-7..X-14, X-18 against the stored handoff); a retry is a no-op."""
    log = _logged(conn, message_id)
    readback = _load_record(path, "readback")
    found = digest(readback)
    actor = _receiver(log)
    if log.latest_rb.get(actor["id"], ("",))[0] == found:
        return  # exact retry of the author's latest read-back
    uri = f"trw:ahr/{log.handoff['handoff_id']}/readback/{readback.get('readback_id', 'unknown')}"
    ref = {"uri": uri, "digest": found, "media_type": AHR_MEDIA}
    _append(conn, log, message_id, "read_back", actor, now, target=(readback, found), record=jcs(readback), ref=ref)


def accept(conn: sqlite3.Connection, message_id: str, now: float) -> None:
    """R-LC-11 / R-RB-4 / R-TIME-2 gate, then ``accepted``; an accept retry by the receiver is a no-op."""
    log = _logged(conn, message_id)
    actor = _receiver(log)
    if log.state in ("accepted", "reported", "completed") and log.receiver == actor["id"]:
        return
    _append(conn, log, message_id, "accepted", actor, now)


def decline(conn: sqlite3.Connection, message_id: str, reason: str, now: float) -> None:
    log = _logged(conn, message_id)
    if log.state == "declined":
        return
    _append(conn, log, message_id, "declined", _receiver(log), now, reason=reason)


def withdraw(conn: sqlite3.Connection, message_id: str, reason: str, now: float) -> None:
    log = _logged(conn, message_id)
    if log.state == "withdrawn":
        return
    _append(conn, log, message_id, "withdrawn", _sender(log), now, reason=reason)


def answer(conn: sqlite3.Connection, message_id: str, path: object, now: float) -> None:
    """The sender's digest-bound reply to a read-back's questions (informational, R-LC-8)."""
    log = _logged(conn, message_id)
    relative, target = _hashed(path)
    ref = {"uri": f"file:{relative}", "digest": target[1]}
    _append(conn, log, message_id, "answered", _sender(log), now, target=target, ref=ref)


def report(conn: sqlite3.Connection, message_id: str, next_read: str, outcome: str, now: float) -> None:
    """``reported`` with *outcome* and the §14 ``<path>#sha256:<hex>`` ref, verified against the local file."""
    log = _logged(conn, message_id)
    if log.state in ("reported", "completed"):
        return  # the CORE-322 report (run first) refused a different pointer, so this is the exact retry
    bound = _BOUND.match(next_read)
    if bound is None:
        raise AdmissionError("ahr_ref_unverified")
    relative, target = _hashed(bound["path"])
    if target[1] != bound["digest"]:
        raise AdmissionError("ahr_ref_unverified")
    ref = {"uri": f"file:{relative}", "digest": target[1]}
    _append(conn, log, message_id, "reported", _receiver(log), now, target=target, ref=ref, outcome=outcome)


def complete(conn: sqlite3.Connection, message_id: str, now: float) -> None:
    log = _logged(conn, message_id)
    if log.state == "completed":
        return
    _append(conn, log, message_id, "completed", _sender(log), now)


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


# --- the body-free view (FR07) -----------------------------------------------------------------


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
    peers = conn.execute(
        "SELECT DISTINCT e.handoff_id FROM ahr_events e JOIN admissions a ON a.message_id=e.message_id "
        "WHERE e.subject=? AND a.group_id=? AND e.handoff_id!=? LIMIT 16",
        (log.handoff["subject"], group_id, handoff_id),
    ).fetchall()
    if log.state not in _ENDED and any(rebuild(conn, str(peer[0])).state not in _ENDED for peer in peers):
        block["fork"] = True  # R-SUP-3: two current records for one subject are shown, never resolved silently
    return block
