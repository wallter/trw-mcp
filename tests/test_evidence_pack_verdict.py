"""PRD-CORE-323 slice S2 -- verdict section (FR04), event caps (NFR02) and the event budget (NFR01).

The behavioural tests drive ``trw-mcp run evidence-pack`` through the real dispatcher on a
fixture built by the production writers (``_evidence_pack_s2_fixture``) and assert on the
exit code and the pack. The FR02 and FR04 failing-first tests live in
``test_evidence_pack.py``; this module holds the remaining acceptance criteria. Nothing
imports ``trw_mcp.evidence_pack`` at module scope.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from tests._checkout_access_state import reset_pinned_reads
from tests._evidence_pack_fixture import build_fixture, clone_build_receipts, export, load_pack
from tests._evidence_pack_s2_fixture import (
    RUN_IDENTITY,
    checkpoint,
    compact_journal,
    journal_operation,
    log_event,
    override_record,
    section,
)
from tests._timing import assert_budget

pytestmark = pytest.mark.integration


def _verdict(tmp_path: Path, fx: object) -> dict[str, object]:
    out = tmp_path / "pack.json"
    assert export(fx, out) == 0  # type: ignore[arg-type]
    return section(load_pack(out), "verdict")


# ---------------------------------------------------------------------------
# FR04 parts 1-2: deliver events and override records
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _room_in_the_pinned_read_cache() -> object:
    """The journal classifier reads a file header through ``_checkout_access.read_at``, whose process-global
    cache holds at most 64 pinned inodes. A worker already at the cap turned a legacy-WAL journal into
    ``corrupt_store`` (one flake in ~22k on the final gate); every test here starts with room."""
    reset_pinned_reads()
    yield
    reset_pinned_reads()


def test_cli_pack_lists_deliver_events_and_the_run_override(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    log_event(fx, "delivery_gate_overridden", {"gate_type": "build_gate", "block": "no build"})
    log_event(fx, "trw_deliver_complete", {"critical_steps_completed": 7, "errors": 0})
    override_record(fx, failed_command="pytest tests -q", residual_risk="one flaky test")

    verdict = _verdict(tmp_path, fx)

    deliver = verdict["deliver_events"]
    assert [entry["event"] for entry in deliver["entries"]] == ["delivery_gate_overridden", "trw_deliver_complete"]  # type: ignore[index]
    (record,) = verdict["override_records"]["entries"]  # type: ignore[index]
    # The recorded run_path is absolute; the chokepoint rewrites it, so the entry is labelled redacted.
    assert (record["label"], record["run_path"], record["run_id"]) == ("redacted", "<project>/" + RUN_IDENTITY, "run-1")
    assert str(tmp_path) not in json.dumps(verdict)
    assert (record["failed_command"], record["residual_risk"]) == ("pytest tests -q", "one flaky test")
    assert (record["owner"], record["expiry_iso"], record["gate_type"]) == ("fixture-owner", "2099-01-01", "build_gate")
    assert record["timestamp"]
    assert record["source"]["path"].startswith(".trw/overrides/")
    assert verdict["delivery_record"]["delivery_record"] == "recorded"  # type: ignore[index]


def test_cli_pack_binds_override_records_by_ledger_id_and_run_path(tmp_path: Path) -> None:
    """Same directory name in another task: listed unknown. A longer run id containing ours: not listed."""
    fx = build_fixture(tmp_path)
    twin = tmp_path / ".trw" / "runs" / "other-task" / "run-1"
    longer = tmp_path / ".trw" / "runs" / "task" / "x-run-1"
    override_record(fx, failed_command="twin run command", residual_risk="twin", run=twin)
    override_record(fx, failed_command="longer run command", residual_risk="longer", run=longer)

    verdict = _verdict(tmp_path, fx)

    block = verdict["override_records"]
    assert (block["total"], block["kept"], block["other_run_records"]) == (2, 2, 1)  # type: ignore[index]
    (entry,) = block["entries"]  # type: ignore[index]
    assert (entry["label"], entry["reason"]) == ("unknown", "override_binding_unverified")
    assert entry["failed_command"] == "twin run command"
    assert "longer run command" not in json.dumps(verdict)
    # PRD-CORE-323 FR04: an override whose binding is unverified must never, on its own,
    # produce a definitive (non-unknown) delivery record.
    record = verdict["delivery_record"]
    assert (record["delivery_record"], record["reason"]) == ("unknown", "override_binding_unverified")  # type: ignore[index]


def test_cli_pack_override_missing_run_path_is_unverified_not_recorded(tmp_path: Path) -> None:
    """An override record naming this run's ledger id but with no run_path at all is unverified."""
    from tests._evidence_pack_s2_fixture import RUN_IDENTITY
    from trw_mcp.models._acceptable_failure import AcceptableFailureRecord
    from trw_mcp.tools._acceptable_failure_validation import ledger_run_id

    fx = build_fixture(tmp_path)
    overrides_dir = tmp_path / ".trw" / "overrides"
    overrides_dir.mkdir(parents=True, exist_ok=True)
    record = AcceptableFailureRecord(failed_command="c", residual_risk="r", owner="o", expiry_iso="2099-01-01")
    from trw_mcp.tools._acceptable_failure_validation import write_override_ledger

    ok, error = write_override_ledger(
        tmp_path / ".trw", ledger_run_id(fx.run), record, gate_type="build_gate", run_path=""
    )
    assert ok, error

    verdict = _verdict(tmp_path, fx)

    (entry,) = verdict["override_records"]["entries"]  # type: ignore[index]
    assert (entry["label"], entry["reason"]) == ("unknown", "override_binding_unverified")
    record = verdict["delivery_record"]
    assert (record["delivery_record"], record["reason"]) == ("unknown", "override_binding_unverified")  # type: ignore[index]
    assert RUN_IDENTITY  # sanity: fixture constant imported and available for future assertions


# ---------------------------------------------------------------------------
# FR04 part 3 + retention: the journal and the delivery record
# ---------------------------------------------------------------------------


def test_cli_pack_no_recorded_delivery_never_creates_the_journal(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)

    verdict = _verdict(tmp_path, fx)

    record = verdict["delivery_record"]
    assert (record["label"], record["delivery_record"]) == ("observed", "no_recorded_delivery")  # type: ignore[index]
    (journal,) = verdict["journal"]["entries"]  # type: ignore[index]
    assert (journal["label"], journal["reason"]) == ("unknown", "no_journal")
    assert not (tmp_path / ".trw" / "delivery").exists()


@pytest.mark.parametrize("compacted_before_run", [False, True], ids=["after-run-start", "before-run-start"])
def test_cli_pack_compacted_rows_are_never_no_recorded_delivery(tmp_path: Path, compacted_before_run: bool) -> None:
    """A tombstone created after the run started blocks no_recorded_delivery; an older one does not."""
    if compacted_before_run:
        journal_operation(tmp_path)
        compact_journal(tmp_path)
        time.sleep(0.01)  # the run's first event is recorded strictly after the tombstoned operation
        fx = build_fixture(tmp_path)
    else:
        fx = build_fixture(tmp_path)
        journal_operation(tmp_path)
        compact_journal(tmp_path)

    verdict = _verdict(tmp_path, fx)

    journal = verdict["journal"]
    assert (journal["state"], journal["total"]) == ("ok", 0)  # type: ignore[index]
    record = verdict["delivery_record"]
    if compacted_before_run:
        assert (journal["tombstones_since_run_start"], record["delivery_record"]) == (0, "no_recorded_delivery")  # type: ignore[index]
    else:
        assert journal["tombstones_since_run_start"] == 1  # type: ignore[index]
        assert (record["label"], record["reason"]) == ("unknown", "unattributable_compacted_rows_exist")  # type: ignore[index]


def _legacy_wal(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.commit()
    conn.close()


def _schema(db: Path, value: str) -> None:
    from tests._delivery_support import make_coordinator

    make_coordinator(db.parent.parent).store.connect().close()
    conn = sqlite3.connect(db)
    conn.execute("UPDATE meta SET value=? WHERE key='schema_version'", (value,))
    conn.commit()
    conn.close()


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (_legacy_wal, "journal_migration_required"),
        (lambda db: _schema(db, "99"), "unsupported_schema"),
        (lambda db: _schema(db, "not-a-number"), "corrupt_store"),
        (lambda db: db.write_bytes(b"this is not a database at all" * 10), "corrupt_store"),
    ],
    ids=["legacy-wal", "unsupported-schema", "malformed-schema", "garbage-file"],
)
def test_cli_pack_unreadable_journal_is_a_named_unknown(tmp_path: Path, make: object, reason: str) -> None:
    fx = build_fixture(tmp_path)
    db = tmp_path / ".trw" / "delivery" / "operations.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    make(db)  # type: ignore[operator]

    verdict = _verdict(tmp_path, fx)  # exit 0 asserted inside

    (journal,) = verdict["journal"]["entries"]  # type: ignore[index]
    assert (journal["label"], journal["reason"]) == ("unknown", reason)
    record = verdict["delivery_record"]
    assert (record["label"], record["reason"]) == ("unknown", reason)  # type: ignore[index]


def test_cli_pack_lists_each_unpersisted_key_with_its_reason(tmp_path: Path) -> None:
    from trw_mcp.evidence_pack._verdict import NOT_PERSISTED_KEYS, OVERRIDE_POINTER_KEYS
    from trw_mcp.models.typed_dicts._tools import DeliverResultDict

    verdict = _verdict(tmp_path, build_fixture(tmp_path))

    block = verdict["unpersisted_result_keys"]
    assert block["list_complete"] is False  # type: ignore[index]
    reasons = {entry["result_key"]: (entry["label"], entry["reason"]) for entry in block["entries"]}  # type: ignore[index]
    assert set(reasons) == {*NOT_PERSISTED_KEYS, *OVERRIDE_POINTER_KEYS}
    assert set(reasons) <= set(DeliverResultDict.__annotations__)  # every listed key is a real result key
    for key in NOT_PERSISTED_KEYS:
        assert reasons[key] == ("unknown", "not_persisted_by_deliver")
    for key in OVERRIDE_POINTER_KEYS:
        assert reasons[key] == ("unknown", "result text not persisted; see override record + event")


# ---------------------------------------------------------------------------
# NFR02 event caps and the NFR01 event budget
# ---------------------------------------------------------------------------


def _clone_last_line(path: Path, count: int) -> None:
    last = path.read_text(encoding="utf-8").splitlines()[-1]
    with path.open("a", encoding="utf-8") as handle:
        handle.write((last + "\n") * count)


def test_cli_pack_caps_checkpoints_events_and_overrides(tmp_path: Path) -> None:
    fx = build_fixture(tmp_path)
    meta = fx.run / "meta"
    checkpoint(fx, "cap probe")
    _clone_last_line(meta / "checkpoints.jsonl", 2004)  # 2,005 checkpoints
    log_event(fx, "phase_enter", {"phase": "implement"})
    _clone_last_line(meta / "events.jsonl", 5002)  # 5,003 decision-class events
    log_event(fx, "trw_deliver_complete", {"errors": 0})
    _clone_last_line(meta / "events.jsonl", 5001)  # 5,002 deliver events
    override_record(fx, failed_command="cap", residual_risk="cap")
    (ledger,) = (tmp_path / ".trw" / "overrides").iterdir()
    for index in range(501):
        (ledger.parent / ledger.name.replace("-run-1-", f"-run-1-{index:04d}-")).write_bytes(ledger.read_bytes())
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    pack = load_pack(out)
    decisions, verdict = section(pack, "decisions"), section(pack, "verdict")
    for block, expected in (
        (decisions["checkpoints"], (2005, 2000)),
        (decisions["events"], (5003, 5000)),
        (verdict["deliver_events"], (5002, 5000)),
        (verdict["override_records"], (502, 500)),
    ):
        assert (block["total"], block["kept"]) == expected  # type: ignore[index]
        assert len(block["entries"]) == expected[1]  # type: ignore[index]


def test_cli_pack_bounds_the_read_on_a_run_ten_times_the_event_cap(tmp_path: Path) -> None:
    """PRD-CORE-323 NFR02: the caps bound what is read, not just what is kept.

    10x MAX_DECISION_EVENTS (50,000) decision-class lines: the read stops at
    MAX_EVENT_READ_LINES (well below 50,000), so the section still gets its full
    MAX_DECISION_EVENTS entries but reports the read bound was reached rather than
    silently under-counting the true total.
    """
    from trw_mcp.evidence_pack._decisions import MAX_DECISION_EVENTS
    from trw_mcp.evidence_pack._run_log import MAX_EVENT_READ_LINES

    fx = build_fixture(tmp_path)
    log_event(fx, "phase_enter", {"phase": "implement"})
    _clone_last_line(fx.run / "meta" / "events.jsonl", MAX_DECISION_EVENTS * 10)
    events_path = fx.run / "meta" / "events.jsonl"
    true_line_count = len(events_path.read_text(encoding="utf-8").splitlines())
    assert true_line_count > MAX_EVENT_READ_LINES
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    decisions = section(load_pack(out), "decisions")
    events_block = decisions["events"]
    assert events_block["kept"] == MAX_DECISION_EVENTS  # type: ignore[index]
    # The read is bounded to MAX_EVENT_READ_LINES: the reported total can never exceed it,
    # even though the true, unread number of matching lines in the file is far larger.
    assert events_block["total"] <= MAX_EVENT_READ_LINES < true_line_count  # type: ignore[index]
    assert events_block["read_bound_reached"] is True  # type: ignore[index]


def test_cli_pack_caps_journal_operations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.evidence_pack._verdict as verdict_module

    fx = build_fixture(tmp_path)
    first = journal_operation(tmp_path)
    journal_operation(tmp_path)
    monkeypatch.setattr(verdict_module, "MAX_JOURNAL_OPERATIONS", 1)

    journal = _verdict(tmp_path, fx)["journal"]

    assert (journal["total"], journal["kept"]) == (2, 1)  # type: ignore[index]
    assert journal["entries"][0]["operation_id"] == first  # type: ignore[index]


def _build_20k_event_fixture(tmp_path: Path):
    fx = build_fixture(tmp_path)
    clone_build_receipts(fx, 196)
    events = fx.run / "meta" / "events.jsonl"
    log_event(fx, "phase_enter", {"phase": "implement", "detail": "budget fixture"})
    _clone_last_line(events, 4999)
    log_event(fx, "tool_call", {"tool_name": "trw_learn", "success": True})
    _clone_last_line(events, 14998)
    assert len(events.read_text(encoding="utf-8").splitlines()) == 20_000
    return fx


def test_cli_pack_events_keeps_the_expected_count(tmp_path: Path) -> None:
    """Correctness half (unmarked, gating): the speed half below only measures time."""
    fx = _build_20k_event_fixture(tmp_path)
    out = tmp_path / "pack.json"
    assert export(fx, out) == 0
    assert section(load_pack(out), "decisions")["events"]["kept"] == 5000  # type: ignore[index]


@pytest.mark.requires_local_timing
def test_cli_pack_events_within_budget(tmp_path: Path) -> None:
    """NFR01 event half: 20,000 events and 200 receipts export within 10 s (best of 3)."""
    fx = _build_20k_event_fixture(tmp_path)
    out = tmp_path / "pack.json"
    timings = []
    for _ in range(3):
        started = time.perf_counter()
        if export(fx, out) != 0:
            pytest.fail("export() returned nonzero mid-timing loop")
        timings.append(time.perf_counter() - started)
    assert_budget("evidence_pack_export_20000_events", min(timings), 10.0, "s")


def test_cli_pack_a_delivery_past_the_read_bound_is_never_a_definitive_absence(tmp_path: Path) -> None:
    """core323-s2 r2: a trw_deliver_complete beyond MAX_EVENT_READ_LINES is unread, so the pack says unknown.

    Before the fix the verdict ignored read_bound_reached and reported no_recorded_delivery,
    a definitive false absence for a run that did deliver.
    """
    from trw_mcp.evidence_pack._run_log import MAX_EVENT_READ_LINES

    fx = build_fixture(tmp_path)
    log_event(fx, "phase_enter", {"phase": "implement"})
    _clone_last_line(fx.run / "meta" / "events.jsonl", MAX_EVENT_READ_LINES + 10)
    log_event(fx, "trw_deliver_complete", {"errors": 0})
    out = tmp_path / "pack.json"

    assert export(fx, out) == 0

    record = section(load_pack(out), "verdict")["delivery_record"]
    assert record["delivery_record"] == "unknown"  # type: ignore[index]
    assert record["reason"] == "event_read_bound_reached"  # type: ignore[index]
