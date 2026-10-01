"""PRD-CORE-322 FR05: the mailbox version chain to v5 and the v5 handoff verifier rules.

Library-level arms of the explicit upgrade (one commit, row retention, idempotence,
interruption, rollback to ``from_version``) and one planted violation per verifier rule.
The CLI arms, and the S1 failing-first test, live in ``test_formation_cli_comms_upgrade.py``.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.test_formation_cli_comms_upgrade import (
    _dump,
    _execute,
    _open_refusal,
    _source,
    _v4_mailbox,
    _verdict,
    _version,
)
from tests.comms.test_storage_contract import _T0, _v3_mailbox
from trw_mcp.comms import _schema, _store, _upgrade

_REQ = "3" * 32  # the fixture's acked request (fetch_prepared _T0+5, acked _T0+6)
_PENDING = "2" * 32  # a pending, fetch-prepared request


def _record(manifest: Path) -> dict[str, Any]:
    path = _store.database_path(manifest).with_name(_upgrade.RECORD_FILENAME)
    return dict(json.loads(path.read_text(encoding="utf-8")))


_ACCEPTED = ("INSERT INTO milestones VALUES (?,?,?)", (_REQ, "accepted", _T0 + 7))
_REPORTED = ("INSERT INTO milestones VALUES (?,?,?)", (_REQ, "reported", _T0 + 8))
_POINTER = ("INSERT INTO handoff_reports VALUES (?,?,?)", (_REQ, "branch worker-3/x @ abc1234", _T0 + 8))
_COMPLETED = ("INSERT INTO milestones VALUES (?,?,?)", (_REQ, "completed", _T0 + 9))


def _v5_handoff(env: FormationFixture) -> Path:
    """A v4 mailbox upgraded to v5, then one request carried through the full handoff chain."""
    manifest = _v4_mailbox(env)
    assert _upgrade.upgrade(manifest, ttl_seconds=86400)["status"] == "upgraded"
    _execute(manifest, _ACCEPTED, _REPORTED, _POINTER, _COMPLETED)
    return manifest


@pytest.mark.parametrize("source", ["3", "4"])
def test_upgrade_to_v5_is_one_commit_and_keeps_every_row(formation_env: FormationFixture, source: str) -> None:
    manifest = _source(formation_env, source)
    path = _store.database_path(manifest)
    before_rows, before_counter = _dump(manifest), _upgrade.change_counter(path)
    result = _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert (result["status"], result["schema_version"], result["from_version"]) == (
        "upgraded",
        _schema.SCHEMA_VERSION,
        int(source),
    )
    record = _record(manifest)
    assert record["from_version"] == int(source)
    assert record["change_counter"] == before_counter + 1, "the upgrade is exactly one commit"
    assert record["backup"].startswith(f"comms.sqlite3.v{source}-")
    assert _verdict(manifest) is None and _open_refusal(manifest) is None
    if source == "4":  # v4 -> v6 is additive: every row is retained; v6 appends a NULL traceparent to admissions
        after = _dump(manifest)
        assert after["admissions"] == [(*row, None) for row in before_rows["admissions"]]
        assert {k: v for k, v in after.items() if k != "admissions"} == {
            k: v for k, v in before_rows.items() if k != "admissions"
        }
    else:  # v3 also runs the v4 rewrite (proven in test_storage_contract); row identity is retained
        assert [len(rows) for rows in _dump(manifest).values()] == [len(rows) for rows in before_rows.values()]
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM handoff_reports").fetchone()[0] == 0
    finally:
        conn.close()


def test_upgrading_a_v5_mailbox_again_is_a_no_op(formation_env: FormationFixture) -> None:
    manifest = _v4_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    path = _store.database_path(manifest)
    before, record = path.read_bytes(), _record(manifest)
    backups = sorted(path.parent.glob("comms.sqlite3.v*-*.bak"))
    assert _upgrade.upgrade(manifest, ttl_seconds=86400) == {
        "status": "already_current",
        "schema_version": _schema.SCHEMA_VERSION,
    }
    assert path.read_bytes() == before
    assert _record(manifest) == record and sorted(path.parent.glob("comms.sqlite3.v*-*.bak")) == backups


@pytest.mark.parametrize("interruption", ["failing_v5_step", "crash_after_all_steps"])
def test_an_interrupted_v4_upgrade_leaves_the_v4_file_intact(
    formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    manifest = _source(formation_env, "4")
    path = _store.database_path(manifest)
    before = _dump(manifest)
    if interruption == "failing_v5_step":
        monkeypatch.setattr(_upgrade, "V5_STEPS", (*_schema.V5_STEPS, "ALTER TABLE no_such_table ADD COLUMN x INTEGER"))
        expected: type[BaseException] = sqlite3.OperationalError
    else:
        real = _upgrade._apply_chain

        def crash(conn: sqlite3.Connection, from_version: int, ttl_seconds: int) -> None:
            real(conn, from_version, ttl_seconds)
            raise KeyboardInterrupt  # every step applied, nothing committed

        monkeypatch.setattr(_upgrade, "_apply_chain", crash)
        expected = KeyboardInterrupt
    with pytest.raises(expected):
        _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert _version(manifest) == "4" and _verdict(manifest, version=4) is None
    assert _dump(manifest) == before
    assert not path.with_name(_upgrade.RECORD_FILENAME).exists()
    assert not list(path.parent.glob("comms.sqlite3.v*-*.bak")), "a failed attempt leaves no orphan backup"


def test_rollback_is_refused_after_a_handoff_fact_is_written(formation_env: FormationFixture) -> None:
    manifest = _v4_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    _execute(manifest, _ACCEPTED)
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal is _store.StoreRefusal.ROLLBACK_WOULD_DROP_TRAFFIC
    assert _version(manifest) == str(_schema.SCHEMA_VERSION)
    assert (_REQ, "accepted", _T0 + 7) in _dump(manifest)["milestones"]


def test_a_record_without_from_version_rolls_back_to_v3(formation_env: FormationFixture) -> None:
    """A record written by the v3 -> v4 build has no from_version: it means 3."""
    manifest = _v3_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    record_path = _store.database_path(manifest).with_name(_upgrade.RECORD_FILENAME)
    legacy = {key: value for key, value in _record(manifest).items() if key != "from_version"}
    record_path.write_text(json.dumps(legacy), encoding="utf-8")
    assert _upgrade.rollback(manifest)["schema_version"] == 3
    assert _version(manifest) == "3" and _verdict(manifest, version=3) is None


@pytest.mark.parametrize("from_version", [_schema.SCHEMA_VERSION, 2, "4", None, True])
def test_a_record_naming_an_unsupported_from_version_is_refused(
    formation_env: FormationFixture, from_version: object
) -> None:
    manifest = _v4_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    path = _store.database_path(manifest)
    record_path = path.with_name(_upgrade.RECORD_FILENAME)
    record_path.write_text(json.dumps({**_record(manifest), "from_version": from_version}), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal is _store.StoreRefusal.CORRUPT
    assert path.read_bytes() == before


def test_a_v4_restore_that_crashed_before_the_record_rename_is_finished(formation_env: FormationFixture) -> None:
    manifest = _v4_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    record_path = _store.database_path(manifest).with_name(_upgrade.RECORD_FILENAME)
    saved = record_path.read_bytes()
    assert _upgrade.rollback(manifest)["status"] == "rolled_back"
    record_path.write_bytes(saved)
    assert _upgrade.rollback(manifest) == {
        "status": "already_rolled_back",
        "schema_version": 4,
        "backup": json.loads(saved)["backup"],
    }


def test_a_missing_record_refusal_names_a_v4_backup(formation_env: FormationFixture) -> None:
    manifest = _v4_mailbox(formation_env)
    backup = _upgrade.upgrade(manifest, ttl_seconds=86400)["backup"]
    _store.database_path(manifest).with_name(_upgrade.RECORD_FILENAME).unlink()
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.rollback(manifest)
    assert refused.value.refusal is _store.StoreRefusal.UNAVAILABLE
    assert backup.startswith("comms.sqlite3.v4-") and backup in str(refused.value)


@pytest.mark.parametrize(
    "facts",
    [
        pytest.param((_ACCEPTED,), id="accepted-only"),
        pytest.param((_ACCEPTED, _REPORTED, _POINTER), id="reported-not-yet-verified"),
        pytest.param((_ACCEPTED, _REPORTED, _POINTER, _COMPLETED), id="verified"),
    ],
)
def test_the_v5_verifier_accepts_every_ordered_handoff_prefix(
    formation_env: FormationFixture, facts: tuple[tuple[str, tuple[Any, ...]], ...]
) -> None:
    manifest = _v4_mailbox(formation_env)
    _upgrade.upgrade(manifest, ttl_seconds=86400)
    _execute(manifest, *facts)
    assert _verdict(manifest) is None
    assert _open_refusal(manifest) is None


_MS = "UPDATE milestones SET at=? WHERE message_id=? AND fact=?"


@pytest.mark.parametrize(
    ("planted", "rejection"),
    [
        pytest.param(
            [("UPDATE admissions SET kind='reply' WHERE message_id=?", (_REQ,))],
            "handoff fact on a non-request message",
            id="handoff-fact-on-a-reply-row",
        ),
        pytest.param(
            [("UPDATE admissions SET kind='status' WHERE message_id=?", (_REQ,))],
            "handoff fact on a non-request message",
            id="handoff-fact-on-a-status-row",
        ),
        pytest.param(
            [("INSERT INTO milestones VALUES (?,?,?)", (_PENDING, "accepted", _T0 + 9))],
            "accepted fact without acked",
            id="accepted-on-a-pending-row",
        ),
        pytest.param(
            [("DELETE FROM milestones WHERE message_id=? AND fact='accepted'", (_REQ,))],
            "reported fact without accepted",
            id="reported-without-accepted",
        ),
        pytest.param(
            [
                ("DELETE FROM milestones WHERE message_id=? AND fact='reported'", (_REQ,)),
                ("DELETE FROM handoff_reports WHERE message_id=?", (_REQ,)),
            ],
            "completed fact without reported",
            id="verified-completion-without-a-report",
        ),
        pytest.param([(_MS, (_T0 + 5.5, _REQ, "accepted"))], "accepted fact before acked", id="accepted-before-acked"),
        pytest.param(
            [(_MS, (_T0 + 6.5, _REQ, "reported")), ("UPDATE handoff_reports SET reported_at=?", (_T0 + 6.5,))],
            "reported fact before accepted",
            id="reported-before-accepted",
        ),
        pytest.param(
            [(_MS, (_T0 + 7.5, _REQ, "completed"))], "completed fact before reported", id="completed-before-reported"
        ),
        pytest.param(
            [("DELETE FROM handoff_reports", ())],
            "reported fact without its handoff report",
            id="report-without-pointer",
        ),
        pytest.param(
            [("DELETE FROM milestones WHERE message_id=? AND fact IN ('reported','completed')", (_REQ,))],
            "handoff report without a reported fact",
            id="pointer-without-report",
        ),
        pytest.param(
            [("INSERT INTO handoff_reports VALUES (?,?,?)", ("f" * 32, "x", _T0 + 8))],
            "orphan handoff report",
            id="pointer-for-an-unknown-message",
        ),
        pytest.param(
            [("UPDATE handoff_reports SET reported_at=?", (_T0 + 8.5,))],
            "handoff report time differs from its reported fact",
            id="pointer-time-differs-from-fact",
        ),
        pytest.param(
            [("UPDATE handoff_reports SET next_read=?", ("",))], "invalid handoff next_read", id="pointer-empty"
        ),
        pytest.param(
            [("UPDATE handoff_reports SET next_read=?", ("x" * 513,))],
            "invalid handoff next_read",
            id="pointer-513-bytes",
        ),
        pytest.param(
            [("UPDATE handoff_reports SET next_read=?", ("é" * 257,))],
            "invalid handoff next_read",
            id="pointer-514-utf8-bytes",
        ),
        pytest.param(
            [("UPDATE handoff_reports SET next_read=?", ("a\nb",))], "invalid handoff next_read", id="pointer-newline"
        ),
        pytest.param(
            [("UPDATE handoff_reports SET next_read=?", ("abc\u202edef",))],
            "invalid handoff next_read",
            id="pointer-bidi-override",
        ),
        pytest.param(
            [("UPDATE handoff_reports SET next_read=?", (b"docs/prd.md",))],
            "invalid handoff next_read",
            id="pointer-stored-as-blob",
        ),
        pytest.param(
            [("INSERT INTO milestones VALUES (?,?,?)", (_REQ, "declined", _T0 + 9))],
            "unknown milestone",
            id="fact-outside-the-vocabulary",
        ),
        pytest.param(
            [("INSERT INTO milestones VALUES (?,?,?)", ("e" * 32, "accepted", _T0 + 9))],
            "orphan milestone",
            id="handoff-fact-for-an-unknown-message",
        ),
    ],
)
def test_the_v5_verifier_rejects_each_planted_violation(
    formation_env: FormationFixture, planted: list[tuple[str, tuple[Any, ...]]], rejection: str
) -> None:
    manifest = _v5_handoff(formation_env)
    assert _verdict(manifest) is None
    _execute(manifest, *planted)
    verdict = _verdict(manifest)
    assert verdict is not None and rejection in verdict, verdict
    assert _open_refusal(manifest) is _store.StoreRefusal.CORRUPT


def test_the_pointer_bound_is_inclusive_at_512_bytes(formation_env: FormationFixture) -> None:
    manifest = _v5_handoff(formation_env)
    for pointer in ("x", "x" * 512, "é" * 256, "docs/prd.md#L1 — ok"):
        _execute(manifest, ("UPDATE handoff_reports SET next_read=?", (pointer,)))
        assert _verdict(manifest) is None, pointer


def test_a_v4_file_carrying_a_handoff_fact_is_corrupt_and_is_never_upgraded(formation_env: FormationFixture) -> None:
    manifest = _v4_mailbox(formation_env)
    _execute(manifest, _ACCEPTED)
    path = _store.database_path(manifest)
    before = path.read_bytes()
    assert "unknown milestone" in str(_verdict(manifest, version=4))
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert refused.value.refusal is _store.StoreRefusal.CORRUPT
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    ("stamp", "on_open", "on_upgrade"),
    [
        pytest.param("4", _store.StoreRefusal.UPGRADE_REQUIRED, _store.StoreRefusal.CORRUPT, id="v5-file-stamped-4"),
        pytest.param("3", _store.StoreRefusal.UPGRADE_REQUIRED, _store.StoreRefusal.CORRUPT, id="v5-file-stamped-3"),
        pytest.param(
            "5", _store.StoreRefusal.UPGRADE_REQUIRED, _store.StoreRefusal.CORRUPT, id="current-file-stamped-5"
        ),
        pytest.param(
            "6", _store.StoreRefusal.UPGRADE_REQUIRED, _store.StoreRefusal.CORRUPT, id="current-file-stamped-6"
        ),
        pytest.param(
            str(_schema.SCHEMA_VERSION + 1),
            _store.StoreRefusal.SCHEMA_MISMATCH,
            _store.StoreRefusal.SCHEMA_MISMATCH,
            id="future",
        ),
        pytest.param("2", _store.StoreRefusal.SCHEMA_MISMATCH, _store.StoreRefusal.SCHEMA_MISMATCH, id="ancient-2"),
    ],
)
def test_a_spoofed_schema_version_is_refused_and_never_rewritten(
    formation_env: FormationFixture, stamp: str, on_open: _store.StoreRefusal, on_upgrade: _store.StoreRefusal
) -> None:
    manifest = _v5_handoff(formation_env)
    _execute(manifest, ("UPDATE schema_meta SET value=?", (stamp,)))
    path = _store.database_path(manifest)
    before = path.read_bytes()
    assert _open_refusal(manifest) is on_open
    with pytest.raises(_store.StoreError) as refused:
        _upgrade.upgrade(manifest, ttl_seconds=86400)
    assert refused.value.refusal is on_upgrade
    assert path.read_bytes() == before


def test_a_v4_shaped_file_stamped_current_is_corrupt(formation_env: FormationFixture) -> None:
    manifest = _v4_mailbox(formation_env)
    _execute(manifest, ("UPDATE schema_meta SET value=?", (str(_schema.SCHEMA_VERSION),)))
    assert "unexpected or incomplete schema" in str(_verdict(manifest))
    assert _open_refusal(manifest) is _store.StoreRefusal.CORRUPT
