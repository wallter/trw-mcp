"""Verdict section of the evidence pack (PRD-CORE-323 FR04, NFR02 event caps).

Four parts, every entry sealed by the shared redacting writer (FR06):

1. deliver events (``trw_deliver_complete``, ``delivery_gate_overridden``) from the same
   single event source as the decisions section (``_run_log``);
2. acceptable-failure override records bound to the run (``_overrides``);
3. delivery-journal operations whose ``run_identity`` is the run's project-relative path,
   from the clock-free ``tools._delivery_status.pack_operation_projection`` on a
   read-only connection that never creates the database;
4. the DeliverResultDict keys ``trw_deliver`` never persists, as ``unknown``.

Journal retention. Maintenance compacts terminal operations after 30 days and
unresolved ones after 90 into tombstones that carry no run identity. So the
``delivery_record`` entry says ``no_recorded_delivery`` only when the run has no deliver
event, no override record, no matching operation and the journal holds no tombstone
created at or after the run's first recorded event; otherwise it is ``recorded`` or
``unknown`` with a named reason (``unattributable_compacted_rows_exist``, the journal's
unreadable state, or ``no_legacy_event_log``).

Soundness scope: proves which deliver events, override records and journal operations
exist for the run at export, and their recorded state. It does not prove the delivery
was correct, does not reconstruct gate outcomes ``trw_deliver`` never persisted, and
cannot see operations compacted past retention except as
``unattributable_compacted_rows_exist``.
"""

from __future__ import annotations

from pathlib import Path

from trw_mcp.evidence_pack._decisions import MAX_DECISION_EVENTS
from trw_mcp.evidence_pack._redaction import EntryWriter, JsonValue
from trw_mcp.evidence_pack._run_log import MAX_EVENT_READ_LINES, RunLog, row_utc_ms, select_events
from trw_mcp.evidence_pack._wording import AS_OF_RUN_RECORD

DELIVER_EVENT_CLASSES: frozenset[str] = frozenset({"trw_deliver_complete", "delivery_gate_overridden"})
#: NFR02: journal operations kept per run, applied before any step row is read.
MAX_JOURNAL_OPERATIONS = 1000

#: FR04 part 4: gate-bearing DeliverResultDict keys trw_deliver returns but never persists (not exhaustive).
NOT_PERSISTED_KEYS: tuple[str, ...] = (
    "success",
    "delivery_blocked",
    "missing_gate",
    "build_gate_block",
    "build_gate_warning",
    "review_block",
    "review_warning",
    "review_scope_block",
    "integration_review_block",
    "integration_review_warning",
    "integration_isolated_warning",
    "capability_integration",
    "safety_critical_adversarial_block",
    "safety_critical_adversarial_advisory",
    "acceptable_failure_error",
)
#: The two override keys: their text is gone, but the override record and event are parts 1 and 2.
OVERRIDE_POINTER_KEYS: tuple[str, ...] = ("build_gate_override", "truthfulness_gate_bypassed")
OVERRIDE_POINTER_REASON = "result text not persisted; see override record + event"


def _unpersisted_keys(run_source: str, writer: EntryWriter) -> dict[str, JsonValue]:
    source = {"path": f"{run_source}/meta"}
    entries: list[JsonValue] = [
        writer.seal(
            source=source, as_of=AS_OF_RUN_RECORD, fields={"result_key": key}, reason="not_persisted_by_deliver"
        )
        for key in NOT_PERSISTED_KEYS
    ]
    entries.extend(
        writer.seal(
            source=source,
            as_of=AS_OF_RUN_RECORD,
            fields={"result_key": key, "see": ["deliver_events", "override_records"]},
            reason=OVERRIDE_POINTER_REASON,
        )
        for key in OVERRIDE_POINTER_KEYS
    )
    return {"list_complete": False, "entries": entries}


def _count(value: object) -> int:
    return value if isinstance(value, int) else 0


def _journal(
    project_root: Path, trw_dir: Path, run_source: str, run_start_ms: int | None, writer: EntryWriter
) -> tuple[dict[str, JsonValue], str, int]:
    """The journal block, its state (``ok`` or a named reason) and the tombstone count."""
    from trw_mcp.tools._delivery_journal_store import JournalStore
    from trw_mcp.tools._delivery_status import pack_operation_projection

    db_path = trw_dir / "delivery" / "operations.sqlite3"
    try:
        source = db_path.relative_to(project_root).as_posix()
    except ValueError:  # trw-fail-silent-allow: a trw_dir outside the project is cited by its conventional name
        source = ".trw/delivery/operations.sqlite3"
    projection = pack_operation_projection(
        JournalStore(db_path), run_source, max_operations=MAX_JOURNAL_OPERATIONS, since_utc_ms=run_start_ms
    )
    state = str(projection["result"])
    if state != "ok":
        fields = {key: value for key, value in projection.items() if key != "result"}
        entry = writer.seal(source={"path": source}, as_of=AS_OF_RUN_RECORD, fields=fields, reason=state)
        return {"state": state, "total": 0, "kept": 0, "entries": [entry]}, state, 0
    operations = projection.get("operations")
    entries: list[JsonValue] = [
        writer.seal(source={"path": source, "table": "operations"}, as_of=AS_OF_RUN_RECORD, fields=operation)
        for operation in (operations if isinstance(operations, list) else [])
        if isinstance(operation, dict)
    ]
    tombstones = _count(projection.get("tombstones_since_run_start"))
    block: dict[str, JsonValue] = {
        "state": state,
        "total": _count(projection.get("total")),
        "kept": len(entries),
        "entries": entries,
        "tombstones_since_run_start": tombstones,
    }
    return block, state, tombstones


def delivery_record_status(
    *,
    events_present: bool,
    deliver_events: int,
    overrides: int,
    operations: int,
    journal_state: str,
    tombstones: int,
    unverified_overrides: int = 0,
    read_bound_reached: bool = False,
) -> tuple[str, str | None]:
    """``(status, reason)`` of the run's delivery record under the FR04 retention rule.

    Runtime caller: :func:`verdict_section`. ``recorded`` when any deliver event, a
    *confirmed*-binding override record, or journal operation exists; otherwise
    ``no_recorded_delivery`` only when the event log was read, the journal is absent or
    readable with no tombstone since the run started, and no override record with an
    unverified binding to this run exists; else ``unknown`` with the reason that blocks the
    claim. ``overrides`` must count only confirmed bindings (:func:`_overrides.override_records`
    ``confirmed_bound``); an override whose binding could not be confirmed — read but
    unverified, or unread beyond the record cap — is passed separately as
    *unverified_overrides* and can only ever push the record to ``unknown``, never
    ``recorded`` (PRD-CORE-323 FR04: an unbound override must never produce a definitive
    delivery record). A truncated event read (*read_bound_reached*) cannot prove absence, so
    it yields ``unknown`` / ``event_read_bound_reached`` unless positive evidence exists.
    Soundness scope: absence of evidence in these three stores at export,
    nothing more.
    """
    if deliver_events or overrides or operations:
        return "recorded", None
    if not events_present:
        return "unknown", "no_legacy_event_log"
    if read_bound_reached:
        # Lines past MAX_EVENT_READ_LINES were not parsed, so a deliver event may lie there:
        # a truncated read proves nothing absent (core323-s2 r2).
        return "unknown", "event_read_bound_reached"
    if journal_state not in ("ok", "no_journal"):
        return "unknown", journal_state
    if tombstones:
        return "unknown", "unattributable_compacted_rows_exist"
    if unverified_overrides:
        return "unknown", "override_binding_unverified"
    return "no_recorded_delivery", None


def verdict_section(
    run: Path,
    run_source: str,
    project_root: Path,
    events: RunLog,
    event_rows: list[tuple[int, dict[str, object] | None]],
    writer: EntryWriter,
) -> dict[str, JsonValue]:
    """Deliver events, override records, journal operations, the delivery record and unpersisted keys.

    Runtime caller: ``_pack.build_pack`` (``trw-mcp run evidence-pack`` -> ``run_run`` ->
    ``build_pack`` -> here -> ``pack_operation_projection``). *event_rows* is the one
    parse of events.jsonl shared with the decisions section. The run start that scopes
    tombstones is the first recorded ``ts`` in events.jsonl; with none, every tombstone
    counts, so an unknown start never yields ``no_recorded_delivery``.
    """
    from trw_mcp.evidence_pack._overrides import override_records
    from trw_mcp.state._paths import resolve_trw_dir

    trw_dir = resolve_trw_dir()
    kept, total = select_events(event_rows, DELIVER_EVENT_CLASSES, MAX_DECISION_EVENTS)
    deliver: list[JsonValue] = [
        writer.seal(source={"path": events.source, "line": line}, as_of=AS_OF_RUN_RECORD, fields=row)
        for line, row in kept
    ]
    run_start_ms = next(
        (ms for _line, row in event_rows if row is not None and (ms := row_utc_ms(row)) is not None), None
    )
    overrides = override_records(run, run_source, project_root, trw_dir, writer)
    journal, journal_state, tombstones = _journal(project_root, trw_dir, run_source, run_start_ms, writer)
    # Records beyond the cap were not read: their binding cannot be confirmed either, so
    # they are unverified, not confirmed (they may or may not be this run's).
    unread_overrides = _count(overrides["total"]) - _count(overrides["kept"])
    unverified_overrides = (
        _count(overrides["kept"]) - _count(overrides["other_run_records"]) - _count(overrides["confirmed_bound"])
    ) + unread_overrides
    status, reason = delivery_record_status(
        events_present=events.present,
        deliver_events=total,
        overrides=_count(overrides["confirmed_bound"]),
        operations=_count(journal["total"]),
        journal_state=journal_state,
        tombstones=tombstones,
        unverified_overrides=unverified_overrides,
        read_bound_reached=len(events.lines) > MAX_EVENT_READ_LINES,
    )
    record = writer.seal(
        source={"path": events.source},
        as_of=AS_OF_RUN_RECORD,
        fields={"delivery_record": status, "run_start_utc_ms": run_start_ms},
        reason=reason,
    )
    return {
        "section": "verdict",
        "deliver_events": {
            "log_present": events.present,
            "total": total,
            "kept": len(deliver),
            "entries": deliver,
            "read_bound_reached": len(events.lines) > MAX_EVENT_READ_LINES,
        },
        "override_records": overrides,
        "journal": journal,
        "delivery_record": record,
        "unpersisted_result_keys": _unpersisted_keys(run_source, writer),
    }
