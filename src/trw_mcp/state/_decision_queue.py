"""Project-level blocked-decision queue (PRD-CORE-329 Slice A).

Backs ``trw_checkpoint(blocked_decision=...)`` (FR01), the ``trw_status``/
``trw_session_start`` surfacing (FR02/FR03), and the ``trw-mcp decision
resolve`` CLI verb (FR04). The queue is project-level, not per-run:
``.trw/runtime/decisions.jsonl`` (PRD-CORE-329 §OQ-001) — trw-loop, the
enforcing reader in Slice B, does not always know which run it is driving.

Every record is appended through :class:`FileStateWriter.append_jsonl`'s
existing exclusive-locked path, never mutated in place. A "pending" record
and its "resolved" record are two separate lines sharing one ``id``; there is
no read-then-write existence check at creation (ids are ``uuid4``, FR04), so
two concurrent creators never collide.

FR05: fail-closed read. Any unparseable line anywhere in the file — not only
the last — makes the WHOLE file ``unreadable``: a reader must never confuse
"no pending decision" (absence) with "could not tell" (parse failure), the
exact distinction ``_resolve_formation_line`` names in
``tools/checkpoint.py:151``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from trw_mcp.exceptions import StateError
from trw_mcp.state.persistence import FileStateReader, FileStateWriter

#: Project-relative path to the queue file, under the already-TRW-owned,
#: git-ignored ``.trw/runtime/`` directory (NFR03).
DECISIONS_RELATIVE_PATH = Path("runtime") / "decisions.jsonl"

#: FR07: the only markers that exist in code today. ``TRW_DISPATCH_CHILD``'s
#: presence is checked regardless of value (it is an MCP-server-entry marker,
#: not a client-shell one — see ``dispatch/_child_marker.py``); the other two
#: are checked for their documented truthy string.
_LOOP_WORKER_ENV = "TRW_LOOP_WORKER"
_DISPATCH_CHILD_ENV = "TRW_DISPATCH_CHILD"
_SURFACE_ROLE_ENV = "TRW_SURFACE_ROLE"


def unattended_actor_active() -> bool:
    """True when any FR07 unattended signal holds in THIS process's environment.

    Governs FR01's ``actor`` field at write time and FR08's advisory refusal
    at resolve time. Presence, not value, for ``TRW_DISPATCH_CHILD`` — an
    empty or garbled value still marks a child (matches
    ``dispatched_child_active()``'s own convention).
    """
    if os.environ.get(_LOOP_WORKER_ENV, "").strip() == "1":
        return True
    if _DISPATCH_CHILD_ENV in os.environ:
        return True
    return os.environ.get(_SURFACE_ROLE_ENV, "").strip().lower() == "reviewer"


@dataclass(frozen=True)
class DecisionQueueState:
    """Result of reading ``decisions.jsonl`` (FR02/FR03/FR04's shared view).

    ``pending_ids``/``resolved_ids`` preserve queue order (the order records
    first appear in the file), never ``ts`` order — ``ts`` is caller-written
    data (PRD-CORE-329 §Background). ``unreadable=True`` means the file
    exists but could not be fully parsed; every other field is then empty and
    MUST NOT be trusted as "no pending decision" (FR05).
    """

    pending_ids: tuple[str, ...] = ()
    resolved_ids: tuple[str, ...] = ()
    unreadable: bool = False
    #: id -> the full pending record (question/options/why_unreachable/ts/run_path).
    records: dict[str, dict[str, object]] = field(default_factory=dict)
    #: id -> the full accepted-resolution record (FR08 rule 3: actor != unattended).
    resolutions: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def oldest_pending_id(self) -> str | None:
        """The first pending id in queue order, or ``None`` when none is pending."""
        return self.pending_ids[0] if self.pending_ids else None


def _queue_path(trw_dir: Path) -> Path:
    return trw_dir / DECISIONS_RELATIVE_PATH


def record_decision(
    trw_dir: Path,
    *,
    run_path: str,
    question: str,
    options: list[str],
    why_unreachable: str,
) -> str:
    """Append a new pending decision record (FR01) and return its ``uuid4`` id.

    Callers MUST validate ``question``/``why_unreachable`` non-empty BEFORE
    calling this (FR01's refusal happens ahead of any write); this function
    performs no validation of its own and always writes.
    """
    decision_id = str(uuid.uuid4())
    actor = "unattended" if unattended_actor_active() else "attended"
    record: dict[str, object] = {
        "id": decision_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "run_path": run_path,
        "question": question,
        "options": list(options),
        "why_unreachable": why_unreachable,
        "actor": actor,
    }
    FileStateWriter().append_jsonl(_queue_path(trw_dir), record)
    return decision_id


def read_decision_queue(trw_dir: Path) -> DecisionQueueState:
    """Read ``decisions.jsonl`` and derive pending/resolved state (FR02-FR05).

    Fail-closed (FR05): ``strict=True`` on the underlying JSONL read makes
    ANY unparseable line raise, at which point this returns
    ``DecisionQueueState(unreadable=True)`` rather than the records read
    before the bad line — "we never checked" and "we checked and found
    nothing" must not be the same sentence.

    A resolution whose ``actor`` is ``unattended`` never accepts (FR08 rule
    3): the pending record it names stays in ``pending_ids``.
    """
    path = _queue_path(trw_dir)
    if not path.exists():
        return DecisionQueueState()
    try:
        rows = FileStateReader().read_jsonl(path, strict=True)
    except StateError:
        return DecisionQueueState(unreadable=True)

    pending_order: list[str] = []
    records: dict[str, dict[str, object]] = {}
    resolutions: dict[str, dict[str, object]] = {}
    for row in rows:
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            # A record with no usable id can be neither pending nor resolved
            # against anything; skipping it is not the FR05 fail-closed case
            # (the JSON itself parsed fine) so this does not set unreadable.
            continue
        status = row.get("status")
        if status == "pending":
            if row_id not in records:
                pending_order.append(row_id)
            records[row_id] = row
        elif status == "resolved":
            if row.get("actor") != "unattended":
                resolutions[row_id] = row

    pending_ids = tuple(rid for rid in pending_order if rid not in resolutions)
    resolved_ids = tuple(resolutions.keys())
    return DecisionQueueState(
        pending_ids=pending_ids,
        resolved_ids=resolved_ids,
        records=records,
        resolutions=resolutions,
    )


def resolve_decision(
    trw_dir: Path,
    *,
    decision_id: str,
    choice: str,
    actor: str,
) -> dict[str, object] | None:
    """Append a resolution record for ``decision_id`` (FR04); idempotent; refuses an unknown id.

    Returns the (possibly pre-existing) accepted resolution record, or
    ``None`` when ``decision_id`` matches no pending record in the queue
    (NFR03: nothing is appended in that case). When an accepted resolution
    already exists, it is returned unchanged — a retried or duplicated
    resolve call never double-applies (MCP transport-loss retry protocol).
    """
    state = read_decision_queue(trw_dir)
    if state.unreadable:
        return None
    if decision_id in state.resolutions:
        return state.resolutions[decision_id]
    if decision_id not in state.records:
        return None

    record: dict[str, object] = {
        "id": decision_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "resolved",
        "choice": choice,
        "actor": actor,
    }
    FileStateWriter().append_jsonl(_queue_path(trw_dir), record)
    return record


def blocked_decision_block(trw_dir: Path) -> tuple[dict[str, object] | None, int]:
    """Shared FR02/FR03 view: the surfaceable block plus the pending count.

    Returns ``(None, 0)`` when nothing is pending and the queue reads
    cleanly (NFR01: the caller omits the key entirely in that case). An
    unreadable queue (FR05) returns ``({"status": "unreadable"}, 0)`` —
    surfaced, never omitted, regardless of pending count.
    """
    state = read_decision_queue(trw_dir)
    if state.unreadable:
        return {"status": "unreadable"}, 0
    oldest = state.oldest_pending_id
    if oldest is None:
        return None, 0
    record = state.records[oldest]
    block = {
        "id": oldest,
        "question": record.get("question"),
        "options": record.get("options"),
        "why_unreachable": record.get("why_unreachable"),
        "ts": record.get("ts"),
    }
    return block, len(state.pending_ids)


__all__ = [
    "DECISIONS_RELATIVE_PATH",
    "DecisionQueueState",
    "blocked_decision_block",
    "read_decision_queue",
    "record_decision",
    "resolve_decision",
    "unattended_actor_active",
]
