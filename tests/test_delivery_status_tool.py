"""PRD-CORE-208 FR05: mechanically read-only status projection."""

from __future__ import annotations

import sqlite3
import stat
import threading
import time
from pathlib import Path

import pytest

from tests._delivery_support import (
    make_coordinator,
    make_uuid7,
    project_metadata_snapshot,
    strong_capability,
)
from tests.conftest import extract_tool_fn
from trw_mcp.tools._delivery_models import OperationState, StepState
from trw_mcp.tools._delivery_status import build_status_projection


def test_public_status_is_read_only_and_recovers_after_timeout(tmp_path) -> None:
    """FR05: a timed-out explicit-ID operation is readable after a fresh coordinator.

    Simulates the lost-response case: the claim + partial work is durable; a
    brand-new coordinator (restarted process) reads the same operation/revision
    without invoking delivery.
    """
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity="task/run-1", owner="w", pid=1)
    coord.begin_step(did, "S01", owner="w", pid=1)
    coord.finalize_step(did, "S01", state=StepState.SUCCEEDED, proof_digest="d1")

    trw_dir = tmp_path / ".trw" if (tmp_path / ".trw").exists() else tmp_path
    # Snapshot the whole project tree, run status, and assert nothing changed.
    before = project_metadata_snapshot(tmp_path)
    restarted = make_coordinator(tmp_path)  # fresh "process"
    status = restarted.project_status(did)
    after = project_metadata_snapshot(tmp_path)

    assert status["result"] == "ok"
    assert status["operation_id"] == did
    assert status["revision"] >= 2  # claim + step transitions durable
    assert before == after  # zero project mutation from status
    assert "trw_dir" not in status  # no absolute path leakage
    assert str(trw_dir) not in repr(status)


def test_status_reads_latest_commit_without_mutating_store(tmp_path) -> None:
    """FR05: status observes the latest committed revision without side effects."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability())
    writer = coord.store.connect()
    try:
        current = coord.store.get_operation(writer, did)
        assert current is not None
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        committed_revision = current.revision + 1
        writer.execute("BEGIN IMMEDIATE")
        writer.execute(
            "UPDATE operations SET revision=?, updated_utc_ms=updated_utc_ms+1 WHERE operation_id=?",
            (committed_revision, did),
        )
        writer.commit()

        before = project_metadata_snapshot(tmp_path)
        status = coord.project_status(did)
        after = project_metadata_snapshot(tmp_path)

        assert status["result"] == "ok"
        assert status["revision"] == committed_revision
        assert before == after
    finally:
        writer.close()


def test_legacy_wal_connection_can_drain_during_bounded_migration(tmp_path) -> None:
    from trw_mcp.tools._delivery_journal_store import JournalStore

    db_path = tmp_path / "delivery" / "operations.sqlite3"
    db_path.parent.mkdir(parents=True)
    legacy = sqlite3.connect(db_path, check_same_thread=False)
    legacy.execute("PRAGMA journal_mode=WAL")
    legacy.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    legacy.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    legacy.execute("CREATE TABLE legacy_probe (value TEXT NOT NULL)")
    legacy.execute("INSERT INTO legacy_probe(value) VALUES ('committed')")
    legacy.commit()
    legacy.execute("BEGIN")
    legacy.execute("SELECT * FROM legacy_probe").fetchall()

    def _close_legacy() -> None:
        time.sleep(0.1)
        legacy.rollback()
        legacy.close()

    closer = threading.Thread(target=_close_legacy)
    closer.start()
    store = JournalStore(db_path, busy_timeout_ms=1_000)
    conn = store.connect()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("SELECT value FROM legacy_probe").fetchone()[0] == "committed"
    finally:
        conn.close()
        closer.join(timeout=2)
    assert not closer.is_alive()


def test_status_first_legacy_wal_store_fails_closed_without_mutation(tmp_path) -> None:
    coord = make_coordinator(tmp_path)
    db_path = coord.store.db_path
    db_path.parent.mkdir(parents=True)
    legacy = sqlite3.connect(db_path)
    legacy.execute("PRAGMA journal_mode=WAL")
    legacy.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    legacy.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    legacy.commit()
    legacy.close()

    before = project_metadata_snapshot(tmp_path)
    status = coord.project_status(make_uuid7())
    after = project_metadata_snapshot(tmp_path)

    assert status == {"result": "legacy_wal_migration_required", "schema_version": 1}
    assert before == after


def test_status_does_not_touch_active_legacy_wal_sidecars(tmp_path) -> None:
    coord = make_coordinator(tmp_path)
    db_path = coord.store.db_path
    db_path.parent.mkdir(parents=True)
    legacy = sqlite3.connect(db_path)
    try:
        legacy.execute("PRAGMA journal_mode=WAL")
        legacy.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        legacy.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
        legacy.commit()
        wal_path = Path(f"{db_path}-wal")
        assert wal_path.stat().st_size > 0

        before = project_metadata_snapshot(tmp_path)
        status = coord.project_status(make_uuid7())
        after = project_metadata_snapshot(tmp_path)

        assert status["result"] == "legacy_wal_migration_required"
        assert before == after
    finally:
        legacy.close()


@pytest.mark.parametrize(
    ("stored_version", "expected_result"),
    [
        ("999", "unsupported_schema"),
        ("not-an-int", "corrupt_store"),
        ("9" * 5_000, "corrupt_store"),
        (None, "corrupt_store"),
    ],
)
def test_status_rejects_unknown_or_malformed_schema_without_mutation(
    tmp_path: Path, stored_version: str | None, expected_result: str
) -> None:
    from trw_mcp.tools._delivery_journal_store import JournalStore

    db_path = tmp_path / "delivery" / "operations.sqlite3"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if stored_version is not None:
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (stored_version,))
    conn.commit()
    conn.close()
    store = JournalStore(db_path)

    before = project_metadata_snapshot(tmp_path)
    status = build_status_projection(store, make_uuid7(), now_ms=int(time.time() * 1000))
    after = project_metadata_snapshot(tmp_path)

    assert status["result"] == expected_result
    if expected_result == "unsupported_schema":
        assert status["store_schema_version"] == 999
        assert status["supported_schema_version"] == 1
    assert before == after


@pytest.mark.parametrize("stored_version", ["999", "not-an-int", "9" * 5_000, None])
def test_writer_rejects_unknown_or_malformed_schema_before_ddl(tmp_path: Path, stored_version: str | None) -> None:
    from trw_mcp.tools._delivery_journal_store import (
        CorruptDeliveryJournalSchema,
        JournalStore,
        UnsupportedDeliveryJournalSchema,
    )

    db_path = tmp_path / "delivery" / "operations.sqlite3"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    if stored_version is not None:
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', ?)", (stored_version,))
    conn.commit()
    conn.close()
    store = JournalStore(db_path)
    expected_error = UnsupportedDeliveryJournalSchema if stored_version == "999" else CorruptDeliveryJournalSchema

    with pytest.raises(expected_error):
        store.connect()

    probe = sqlite3.connect(db_path)
    try:
        tables = {str(row[0]) for row in probe.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        probe.close()
    assert tables == {"meta"}


def test_concurrent_first_connects_share_one_schema_initialization(tmp_path: Path) -> None:
    from trw_mcp.tools._delivery_journal_store import JournalStore

    store = JournalStore(tmp_path / "delivery" / "operations.sqlite3")
    start = threading.Barrier(3)
    versions: list[int] = []

    def _connect() -> None:
        start.wait()
        conn = store.connect()
        try:
            versions.append(store.read_schema_version(conn))
        finally:
            conn.close()

    threads = [threading.Thread(target=_connect) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert versions == [1, 1]


def test_rollback_journal_is_private_and_counted(tmp_path) -> None:
    from trw_mcp.tools._delivery_journal_store import JournalStore

    store = JournalStore(tmp_path / "delivery" / "operations.sqlite3")
    conn = store.connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('held', '1')")
        journal_path = Path(f"{store.db_path}-journal")
        assert journal_path.exists()
        assert stat.S_IMODE(journal_path.stat().st_mode) == 0o600
        assert store.store_bytes() == store.db_path.stat().st_size + journal_path.stat().st_size
        conn.rollback()
    finally:
        conn.close()


def test_missing_store_returns_not_found_without_creating_files(tmp_path) -> None:
    """FR05: a missing store returns not_found_store and creates nothing."""
    coord = make_coordinator(tmp_path)  # constructing does not create the DB
    db_path = tmp_path / ".trw" / "delivery" / "operations.sqlite3"
    assert not db_path.exists()
    before = project_metadata_snapshot(tmp_path)

    status = coord.project_status(make_uuid7())

    assert status["result"] == "not_found_store"
    assert not db_path.exists()  # status did not create the database
    assert project_metadata_snapshot(tmp_path) == before


def test_status_reports_critical_complete_while_deferred_pending(tmp_path) -> None:
    """FR05: critical_complete is reported without aggregate success."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w", pid=1)
    coord.mark_operation_state(did, OperationState.CRITICAL_COMPLETE)

    status = coord.project_status(did)
    assert status["critical_complete"] is True
    assert status["aggregate_success"] is False
    assert status["state"] == "critical_complete"


def test_verbose_status_projects_all_registry_effects_and_replay_classes(tmp_path) -> None:
    """FR05: verbose projects per-step state + replay class for every registered effect."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability())
    status = coord.project_status(did, verbose=True)
    steps = status["steps"]
    assert isinstance(steps, dict)
    # Unstarted effects default to not_started with their declared replay class.
    assert steps["D16"]["state"] == "not_started"
    assert steps["D16"]["replay_class"] == "non_replayable"
    assert steps["S01"]["replay_class"] == "postcondition_provable"
    assert len(steps) == 46


def test_compact_status_omits_not_started_and_replay_class(tmp_path) -> None:
    """Wave 8: the default compact response enumerates only run steps and drops
    the static per-step replay_class, plus a steps_total/started/succeeded summary.

    The full 46-entry census (with replay_class) stays available via verbose=True;
    only the MCP response shape is compacted — journal/DB truth is unaffected.
    """
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), owner="w", pid=1)
    coord.begin_step(did, "S01", owner="w", pid=1)
    coord.finalize_step(did, "S01", state=StepState.SUCCEEDED, proof_digest="d1")

    status = coord.project_status(did)
    steps = status["steps"]
    assert isinstance(steps, dict)
    # Only the started/succeeded step is enumerated; the 45 not_started effects
    # (e.g. D16) are omitted from the compact response.
    assert set(steps) == {"S01"}
    assert steps["S01"]["state"] == "succeeded"
    assert "replay_class" not in steps["S01"]  # static registry metadata dropped
    # Summary counts describe the full census without enumerating it.
    assert status["steps_total"] == 46
    assert status["steps_started"] == 1
    assert status["steps_succeeded"] == 1

    # verbose=True still returns the full census with replay_class.
    verbose = coord.project_status(did, verbose=True)
    assert len(verbose["steps"]) == 46
    assert verbose["steps"]["S01"]["replay_class"] == "postcondition_provable"
    assert verbose["steps"]["S01"]["state"] == "succeeded"
    assert verbose["steps_total"] == 46
    assert verbose["steps_started"] == 1


def test_status_invalid_and_unknown_id_are_distinct_results(tmp_path) -> None:
    """FR05: malformed id, unknown id, and missing store are distinct stable results."""
    coord = make_coordinator(tmp_path)
    coord.claim(delivery_id=make_uuid7(), capability_token=strong_capability())  # create the store

    assert coord.project_status("not-a-uuid")["result"] == "invalid_id"
    assert coord.project_status(make_uuid7())["result"] == "not_found_id"


def test_status_only_shows_request_digest_prefix(tmp_path) -> None:
    """FR05: status exposes a short request-digest prefix, never the full digest."""
    coord = make_coordinator(tmp_path)
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=strong_capability(), run_identity="task/run-1")
    conn = coord.store.connect()
    op = coord.store.get_operation(conn, did)
    conn.close()
    assert op is not None
    status = coord.project_status(did)
    assert status["request_digest_prefix"] == op.request_digest[:12]
    assert op.request_digest not in repr(status)  # full digest never surfaces


# --- PRD-CORE-215 FR05: CORE-208 delivery adapter and status routing ---


def test_prd_core_215_fr05(tmp_path, monkeypatch) -> None:
    """FR05: every CORE-208 state/reason code projects losslessly into the common
    envelope, exactly one delivery effect occurs on replay, the existing status
    tool remains authoritative (envelope added alongside), and status routed to a
    non-owner is refused typed.
    """
    from trw_mcp.models.tool_result import CeremonyExecutionClass, Outcome
    from trw_mcp.tools import delivery_ops
    from trw_mcp.tools._delivery_models import OperationState
    from trw_mcp.tools._operation_owner_adapter import (
        _STATE_OUTCOME,
        route_status_query,
        status_envelope,
    )

    coord = make_coordinator(tmp_path)
    cap = strong_capability()

    # Lossless: EVERY operation state keeps its exact CORE-208 label in the
    # projected envelope — no two states collapse into an indistinguishable one.
    seen_states: set[str] = set()
    for state in OperationState:
        synthetic = {"result": "ok", "state": state.value, "operation_id": "op-1", "revision": 3}
        env = status_envelope(synthetic)
        assert env.diagnostics["operation_state"] == state.value  # original preserved
        assert env.execution_class is CeremonyExecutionClass.OPERATION_BACKED
        assert env.outcome is _STATE_OUTCOME[state][0]
        seen_states.add(env.diagnostics["operation_state"])
    assert seen_states == {s.value for s in OperationState}  # no state lost

    # Non-ok reason codes also round-trip losslessly through diagnostics.
    for result_code in ("not_found_store", "invalid_id", "corrupt_store", "unsupported_schema"):
        env = status_envelope({"result": result_code})
        assert env.diagnostics["result"] == result_code

    # Exactly one delivery effect occurs on replay: a second identical claim
    # follows the original operation without re-running the effect.
    did = make_uuid7()
    coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1", owner="w", pid=1)
    coord.begin_step(did, "S01", owner="w", pid=1)
    coord.finalize_step(did, "S01", state=StepState.SUCCEEDED, proof_digest="d1")
    replay = coord.claim(delivery_id=did, capability_token=cap, run_identity="task/run-1")
    assert replay.effect_calls == 0  # replay attaches, never re-runs the effect
    status = coord.project_status(did)
    assert status["steps_succeeded"] == 1  # the single effect ran exactly once

    # The existing trw_delivery_status tool stays authoritative and now carries the
    # typed envelope alongside its legacy shape (FR02 wiring / FR05 projection).
    monkeypatch.setattr(delivery_ops, "_coordinator", lambda: coord)
    status_fn = extract_tool_fn(_delivery_server(), "trw_delivery_status")
    tool_out = status_fn(delivery_id=did)
    assert tool_out["result"] == "ok"  # legacy authority preserved
    assert tool_out["envelope"]["outcome"] in {o.value for o in Outcome}
    assert tool_out["envelope"]["operation_id"] == did

    # Status routed to a non-owner tool is refused (typed); the declared owner serves it.
    refused = route_status_query("trw_status", delivery_id=did)
    assert refused["result"] == "owner_routing_refused"
    assert refused["reason_code"] == "not_delivery_owner"
    routed = route_status_query("trw_delivery_status", delivery_id=did, coordinator_factory=lambda: coord)
    assert routed["result"] == "ok"
    assert "envelope" in routed


def _delivery_server():  # type: ignore[no-untyped-def]
    from fastmcp import FastMCP

    from trw_mcp.tools.delivery_ops import register_delivery_tools

    server = FastMCP("test")
    register_delivery_tools(server)
    return server
