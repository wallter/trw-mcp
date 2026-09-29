"""PRD-CORE-323 slice S2 -- direct tests, one per changed function and arm (charter Q7(25)).

The CLI-level behaviour is pinned in ``test_evidence_pack.py`` and
``test_evidence_pack_verdict.py``; this module calls each new function directly so every
arm is exercised by name: ``pack_operation_projection`` (``tools/_delivery_status.py``),
``_run_log``, ``_decisions``, ``_overrides`` and ``_verdict.delivery_record_status``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests._delivery_support import make_coordinator
from tests._evidence_pack_s2_fixture import RUN_IDENTITY, compact_journal, journal_operation, journal_proof_ref


def _writer(root: Path):  # type: ignore[no-untyped-def]
    from trw_mcp.evidence_pack._redaction import EntryWriter

    return EntryWriter(root)


def _project(root: Path, **kwargs: object) -> dict[str, object]:
    from trw_mcp.tools._delivery_journal_store import JournalStore
    from trw_mcp.tools._delivery_status import pack_operation_projection

    store = JournalStore(root / ".trw" / "delivery" / "operations.sqlite3")
    options: dict[str, object] = {"max_operations": 1000, "since_utc_ms": None, **kwargs}
    return pack_operation_projection(store, RUN_IDENTITY, **options)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# tools/_delivery_status.py::pack_operation_projection
# ---------------------------------------------------------------------------


def test_projection_no_journal_creates_nothing(tmp_path: Path) -> None:
    assert _project(tmp_path) == {"result": "no_journal"}
    assert not (tmp_path / ".trw").exists()


def _db(root: Path) -> Path:
    make_coordinator(root / ".trw").store.connect().close()
    return root / ".trw" / "delivery" / "operations.sqlite3"


def _execute(db: Path, sql: str) -> None:
    conn = sqlite3.connect(db)
    conn.execute(sql)
    conn.commit()
    conn.close()


def _legacy(root: Path) -> None:
    db = root / ".trw" / "delivery" / "operations.sqlite3"
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    conn.close()


def _directory(root: Path) -> None:
    (root / ".trw" / "delivery" / "operations.sqlite3").mkdir(parents=True)


def _bad_row(root: Path) -> None:
    journal_operation(root)
    _execute(_db(root), "UPDATE operations SET state='not-a-state'")


def _missing_column(root: Path) -> None:
    journal_operation(root)
    _execute(_db(root), "ALTER TABLE operations DROP COLUMN request_digest")


def _wrong_column_type(root: Path) -> None:
    journal_operation(root)
    _execute(_db(root), "UPDATE operations SET revision='not-an-int'")


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (_legacy, {"result": "journal_migration_required"}),
        (
            lambda root: _execute(_db(root), "UPDATE meta SET value='99' WHERE key='schema_version'"),
            {"result": "unsupported_schema", "store_schema_version": 99},
        ),
        (lambda root: _execute(_db(root), "DELETE FROM meta WHERE key='schema_version'"), {"result": "corrupt_store"}),
        (_directory, {"result": "corrupt_store"}),
        (_bad_row, {"result": "corrupt_store"}),
        (_missing_column, {"result": "corrupt_store"}),
        (_wrong_column_type, {"result": "corrupt_store"}),
    ],
    ids=[
        "legacy-wal",
        "unsupported-schema",
        "schema-missing",
        "unopenable",
        "unreadable-row",
        "missing-column",
        "wrong-column-type",
    ],
)
def test_projection_unreadable_states_are_named(tmp_path: Path, make: object, expected: dict[str, object]) -> None:
    make(tmp_path)  # type: ignore[operator]
    assert _project(tmp_path) == expected


def test_projection_ok_is_run_scoped_capped_and_clock_free(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    first = journal_operation(tmp_path)
    journal_operation(tmp_path)
    journal_operation(tmp_path, run_identity=".trw/runs/task/other-run")
    proof = journal_proof_ref(tmp_path, "ticket-42")

    def _no_clock() -> float:
        raise AssertionError("the pack projection must not read the clock")

    monkeypatch.setattr(time, "time", _no_clock)
    capped = _project(tmp_path, max_operations=1)
    full = _project(tmp_path)

    assert (capped["result"], capped["total"], capped["kept"]) == ("ok", 3, 1)
    assert [op["operation_id"] for op in capped["operations"]] == [first]  # type: ignore[attr-defined]
    assert full["kept"] == 3 and full["tombstones_since_run_start"] == 0
    (reconciled,) = [op for op in full["operations"] if op["operation_id"] == proof]  # type: ignore[attr-defined]
    (step,) = reconciled["steps"]
    assert (step["effect_id"], step["proof_ref"], step["finding_code"]) == ("D16", "ticket-42", "confirmed_applied")
    assert set(reconciled) == {
        "operation_id",
        "state",
        "revision",
        "created_utc_ms",
        "updated_utc_ms",
        "terminal_utc_ms",
        "steps",
    }
    text = json.dumps(full)
    for absent in ("lease", "recovery_eligible", "capability", "request_digest", "proof_digest", str(tmp_path)):
        assert absent not in text, absent


@pytest.mark.parametrize(("since", "expected"), [(None, 2), (0, 2), (10**15, 0)])
def test_projection_counts_tombstones_since_run_start(tmp_path: Path, since: int | None, expected: int) -> None:
    journal_operation(tmp_path)
    journal_operation(tmp_path, run_identity=".trw/runs/task/other-run")
    compact_journal(tmp_path)

    projection = _project(tmp_path, since_utc_ms=since)

    assert (projection["total"], projection["tombstones_since_run_start"]) == (0, expected)


# ---------------------------------------------------------------------------
# evidence_pack/_run_log.py
# ---------------------------------------------------------------------------


def test_read_run_log_keeps_file_line_numbers_and_flags_bad_lines(tmp_path: Path) -> None:
    from trw_mcp.evidence_pack._run_log import read_run_log

    (tmp_path / "meta").mkdir()
    (tmp_path / "meta" / "events.jsonl").write_text(
        '{"event":"a"}\n\n{not json\n[1, 2]\n{"event":"b"}\n', encoding="utf-8"
    )

    log = read_run_log(tmp_path, "r", "events.jsonl")

    assert (log.present, log.source) == (True, "r/meta/events.jsonl")
    assert log.rows() == [(1, {"event": "a"}), (3, None), (4, None), (5, {"event": "b"})]
    assert log.rows(limit=1) == [(1, {"event": "a"})]
    absent = read_run_log(tmp_path, "r", "checkpoints.jsonl")
    assert (absent.present, absent.rows()) == (False, [])


def test_rows_treats_an_oversized_line_as_unparsable_without_calling_json_loads(tmp_path: Path) -> None:
    """PRD-CORE-323 NFR02: one adversarially huge line must not be handed to ``json.loads``."""
    import json
    from unittest import mock

    from trw_mcp.evidence_pack._run_log import MAX_LINE_CHARS, RunLog

    huge_line = json.dumps({"event": "phase_enter", "huge": "x" * (MAX_LINE_CHARS + 1)})
    assert len(huge_line) > MAX_LINE_CHARS
    log = RunLog(present=True, source="r/meta/events.jsonl", lines=((1, huge_line),))

    with mock.patch("trw_mcp.evidence_pack._run_log.json.loads") as loads:
        rows = log.rows()

    loads.assert_not_called()
    assert rows == [(1, None)]


def test_rows_limit_bounds_json_loads_calls_below_a_file_ten_times_the_event_cap(tmp_path: Path) -> None:
    """PRD-CORE-323 NFR02: the read is bounded, not just the entries kept.

    A run directory built with 10x MAX_DECISION_EVENTS (50,000) lines must not force
    ``json.loads`` to run 50,000 times; the read stops at MAX_EVENT_READ_LINES.
    """
    import json
    from unittest import mock

    from trw_mcp.evidence_pack._decisions import MAX_DECISION_EVENTS
    from trw_mcp.evidence_pack._run_log import MAX_EVENT_READ_LINES, RunLog

    line_count = MAX_DECISION_EVENTS * 10
    lines = tuple((i, json.dumps({"event": "phase_enter", "n": i})) for i in range(1, line_count + 1))
    log = RunLog(present=True, source="r/meta/events.jsonl", lines=lines)
    calls = {"n": 0}
    real_loads = json.loads

    def counting_loads(text: str) -> object:
        calls["n"] += 1
        return real_loads(text)

    with mock.patch("trw_mcp.evidence_pack._run_log.json.loads", side_effect=counting_loads):
        rows = log.rows(MAX_EVENT_READ_LINES)

    assert calls["n"] == MAX_EVENT_READ_LINES < line_count
    assert len(rows) == MAX_EVENT_READ_LINES


@pytest.mark.parametrize(
    ("row", "name"),
    [({"event": "run_init"}, "run_init"), ({"event_type": "phase_enter"}, "phase_enter"), ({"event": 3}, ""), ({}, "")],
)
def test_event_name(row: dict[str, object], name: str) -> None:
    from trw_mcp.evidence_pack._run_log import event_name

    assert event_name(row) == name


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2026-09-26T00:00:00+00:00", 1_790_380_800_000),
        ("2026-09-26T00:00:00Z", 1_790_380_800_000),
        ("2026-09-26T00:00:00", None),
        ("yesterday", None),
        (12, None),
    ],
    ids=["offset", "zulu", "naive", "garbage", "not-a-string"],
)
def test_row_utc_ms(stamp: object, expected: int | None) -> None:
    from trw_mcp.evidence_pack._run_log import row_utc_ms

    assert row_utc_ms({"ts": stamp}) == expected


def test_select_events_caps_in_source_order() -> None:
    from trw_mcp.evidence_pack._run_log import select_events

    rows: list[tuple[int, dict[str, object] | None]] = [
        (1, {"event": "a"}),
        (2, None),
        (3, {"event": "b"}),
        (4, {"event": "a"}),
        (5, {"event": "a"}),
    ]
    assert select_events(rows, {"a"}, 2) == ([(1, {"event": "a"}), (4, {"event": "a"})], 3)
    assert select_events(rows, {"zzz"}, 2) == ([], 0)


# ---------------------------------------------------------------------------
# evidence_pack/_decisions.py
# ---------------------------------------------------------------------------


def test_decisions_section_without_an_event_log(tmp_path: Path) -> None:
    from trw_mcp.evidence_pack._decisions import decisions_section
    from trw_mcp.evidence_pack._run_log import RunLog

    events = RunLog(present=False, source="r/meta/events.jsonl", lines=())
    checkpoints = RunLog(
        present=True, source="r/meta/checkpoints.jsonl", lines=((1, "{bad"), (2, '{"message":"m","state":{"x":1}}'))
    )

    body = decisions_section(events, [], checkpoints, [], tmp_path, _writer(tmp_path))

    (missing,) = body["events"]["entries"]  # type: ignore[index]
    assert (missing["label"], missing["reason"]) == ("unknown", "no_legacy_event_log")
    tools = body["tool_calls"]
    assert (tools["reason"], tools["learning_linkage"]) == ("no_legacy_event_log", "not_recorded")  # type: ignore[index]
    bad, good = body["checkpoints"]["entries"]  # type: ignore[index]
    assert (bad["reason"], bad["source"]["line"]) == ("line_unparsable", 1)
    assert (good["message"], "state" in good) == ("m", False)


def test_tool_summary_names_unnamed_and_legacy_tool_rows(tmp_path: Path) -> None:
    from trw_mcp.evidence_pack._decisions import decisions_section
    from trw_mcp.evidence_pack._run_log import RunLog

    rows: list[tuple[int, dict[str, object] | None]] = [
        (1, {"event": "tool_call", "tool_name": "trw_learn"}),
        (2, {"event": "tool_invocation", "tool": "trw_learn"}),
        (3, {"event": "tool_call"}),
        (4, {"event": "file_modified"}),
        (5, {"ts": "x"}),
        (6, None),
    ]
    events = RunLog(present=True, source="r/meta/events.jsonl", lines=())
    empty = RunLog(present=False, source="r/meta/checkpoints.jsonl", lines=())

    tools = decisions_section(events, rows, empty, [], tmp_path, _writer(tmp_path))["tool_calls"]

    assert tools["tool_call_counts"] == {"<unnamed>": 1, "trw_learn": 2}  # type: ignore[index]
    assert tools["trw_learn_calls"] == 2  # type: ignore[index]
    assert tools["not_copied_event_counts"] == {"<unnamed>": 1, "file_modified": 1}  # type: ignore[index]
    assert tools["unparsable_lines"] == 1  # type: ignore[index]


def test_prd_decision_sections(tmp_path: Path) -> None:
    from trw_mcp.evidence_pack._decisions import prd_decision_sections

    prd = tmp_path / "PRD.md"
    prd.write_text(
        "# T\n\n## Decisions\n\nD1\n\n```\n## Decision inside a fence\n```\n\n## Other\n\nx\n\n## Late Decision log\nD2\n",
        encoding="utf-8",
    )

    found = prd_decision_sections(prd, tmp_path, _writer(tmp_path))

    assert [(e["heading"], e["source"]["line"]) for e in found] == [("Decisions", 3), ("Late Decision log", 15)]  # type: ignore[index]
    assert found[0]["text"] == "D1\n\n```\n## Decision inside a fence\n```"  # type: ignore[index]
    assert found[1]["text"] == "D2"  # type: ignore[index]
    missing = prd_decision_sections(tmp_path / "gone.md", tmp_path, _writer(tmp_path))
    assert [(e["label"], e["reason"]) for e in missing] == [("unknown", "prd_unreadable")]  # type: ignore[index]


# ---------------------------------------------------------------------------
# evidence_pack/_overrides.py
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "matches"),
    [
        ("2026-09-26-run-1-1790000000-abcdef12.yaml", True),
        ("2026-09-26-run-1.yaml", True),
        ("2026-09-26-run-10-1790000000-abcdef12.yaml", False),
        ("2026-09-26-run-1-1790000000-abcdef12.json", False),
    ],
)
def test_names_run(name: str, matches: bool) -> None:
    from trw_mcp.evidence_pack._overrides import names_run

    assert names_run(name, "run-1") is matches


def _ledger(root: Path, name: str, body: str) -> None:
    overrides = root / ".trw" / "overrides"
    overrides.mkdir(parents=True, exist_ok=True)
    (overrides / name).write_text(body, encoding="utf-8")


def test_override_records_arms(tmp_path: Path) -> None:
    from trw_mcp.evidence_pack._overrides import override_records

    run = tmp_path / ".trw" / "runs" / "task" / "run-1"
    common = "failed_command: c\nresidual_risk: r\nowner: o\nexpiry_iso: '2099-01-01'\ngate_type: g\ntimestamp: t\n"
    _ledger(tmp_path, "d-run-1-1-aaaaaaaa.yaml", common + f"run_id: run-1\nrun_path: {run}\n")
    _ledger(tmp_path, "d-run-1-2-bbbbbbbb.yaml", common + f"run_id: run-1\nrun_path: {RUN_IDENTITY}\n")
    _ledger(tmp_path, "d-run-1-3-cccccccc.yaml", common + "run_path: /elsewhere/run-1\n")
    _ledger(tmp_path, "d-run-1-4-dddddddd.yaml", common + "run_id: x-run-1\n")
    _ledger(tmp_path, "d-run-1-5-eeeeeeee.yaml", "[not: a mapping")
    _ledger(tmp_path, "d-run-1-6-ffffffff.yaml", common + "run_id: run-1\nrun_path: /elsewhere/run-1\n")
    _ledger(tmp_path, "d-run-1-7-gggggggg.yaml", common + "run_id: run-1\n")
    trw_dir = tmp_path / ".trw"

    block = override_records(run, RUN_IDENTITY, tmp_path, trw_dir, _writer(tmp_path))

    assert (block["total"], block["kept"], block["other_run_records"], block["confirmed_bound"]) == (7, 7, 1, 2)
    labels = [(e.get("reason"), e["source"]["path"].rsplit("-", 1)[-1]) for e in block["entries"]]  # type: ignore[union-attr, index]
    assert labels == [
        (None, "aaaaaaaa.yaml"),  # absolute run_path inside the project
        (None, "bbbbbbbb.yaml"),  # already project-relative
        ("override_binding_unverified", "cccccccc.yaml"),  # no run_id, run_path outside the project
        ("override_unreadable", "eeeeeeee.yaml"),
        ("override_binding_unverified", "ffffffff.yaml"),  # right run_id, run_path outside the project
        ("override_binding_unverified", "gggggggg.yaml"),  # right run_id, no run_path
    ]
    # A trw dir outside the project and without an overrides directory: no records, no crash.
    outside = override_records(run, RUN_IDENTITY, tmp_path / "proj", tmp_path / "outside", _writer(tmp_path))
    assert outside == {"total": 0, "kept": 0, "other_run_records": 0, "confirmed_bound": 0, "entries": []}


def test_journal_block_outside_trw_dir_cites_the_conventional_path(tmp_path: Path) -> None:
    from trw_mcp.evidence_pack._verdict import _journal

    block, state, tombstones = _journal(tmp_path / "proj", tmp_path / "outside", RUN_IDENTITY, None, _writer(tmp_path))

    assert (state, tombstones) == ("no_journal", 0)
    (entry,) = block["entries"]  # type: ignore[misc]
    assert entry["source"] == {"path": ".trw/delivery/operations.sqlite3"}  # type: ignore[index]
    assert not (tmp_path / "outside").exists()


# ---------------------------------------------------------------------------
# evidence_pack/_verdict.py::delivery_record_status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"deliver_events": 1}, ("recorded", None)),
        ({"overrides": 1}, ("recorded", None)),
        ({"operations": 1, "journal_state": "ok"}, ("recorded", None)),
        ({"events_present": False}, ("unknown", "no_legacy_event_log")),
        ({"journal_state": "corrupt_store"}, ("unknown", "corrupt_store")),
        ({"tombstones": 1}, ("unknown", "unattributable_compacted_rows_exist")),
        ({"journal_state": "no_journal"}, ("no_recorded_delivery", None)),
        ({}, ("no_recorded_delivery", None)),
        ({"unverified_overrides": 1}, ("unknown", "override_binding_unverified")),
        (
            {"unverified_overrides": 1, "overrides": 1},
            ("recorded", None),
        ),  # a confirmed override still wins even with an unrelated unverified one present
        # core323-s2 r2: a truncated read cannot prove absence; a deliver event may lie past the bound.
        ({"read_bound_reached": True}, ("unknown", "event_read_bound_reached")),
        ({"read_bound_reached": True, "deliver_events": 1}, ("recorded", None)),
    ],
    ids=[
        "event",
        "override",
        "operation",
        "no-event-log",
        "journal-unreadable",
        "tombstones",
        "no-journal",
        "empty",
        "unverified-override-alone-is-unknown-not-recorded",
        "confirmed-override-outranks-unverified",
        "truncated-read-without-evidence-is-unknown",
        "truncated-read-with-a-deliver-event-is-recorded",
    ],
)
def test_delivery_record_status(kwargs: dict[str, object], expected: tuple[str, str | None]) -> None:
    from trw_mcp.evidence_pack._verdict import delivery_record_status

    base: dict[str, object] = {
        "events_present": True,
        "deliver_events": 0,
        "overrides": 0,
        "operations": 0,
        "journal_state": "ok",
        "tombstones": 0,
    }
    assert delivery_record_status(**{**base, **kwargs}) == expected  # type: ignore[arg-type]
