"""PRD-QUAL-147 FR10 (B71-73): the comms store's schema check runs under a deadline.

``_schema.verify`` runs ``PRAGMA integrity_check`` plus full-table scans over
.trw/comms.sqlite3 -- a file a checkout can pre-seed -- reached from
``trw_send``/``trw_inbox`` via ``_store.connect``. A crafted or
merely large store must fail closed by ``VERIFY_DEADLINE_S`` rather than hang
or scan to completion (the code_index pattern, commit 9cbb148a8).

Behavioural failing-first proof (r8/qual147-fr10/base_proof.py, a 1.2M-row
``groups`` table, VERIFY_DEADLINE_S=5.0): the pre-fix archive of 9102337d5
ran ``verify()`` to completion in 11.96s with no interruption -- it never
checks a deadline. The fixed branch (commit 38c1c3998) on the same store
raised at 5.00s: ``ValueError('schema check exceeded the 5.0s
comms_verify_deadline_seconds')``.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path

import pytest

from trw_mcp.comms import _schema, _store
from trw_mcp.comms._refusals import REFUSALS
from trw_mcp.comms._store import StoreError, StoreRefusal

#: Comfortably past the 10,000-VM-instruction progress-handler interval so the
#: interrupt is guaranteed to fire during the scan, not merely under it.
_LARGE_GROUP_COUNT = 60_000


def _group_id(i: int) -> str:
    return hashlib.sha256(str(i).encode()).hexdigest()[:32]


def _seed_store(path: Path, *, n_groups: int) -> None:
    conn = sqlite3.connect(path)
    try:
        for statement in _schema.ddl_statements(_schema.SCHEMA_VERSION):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_meta(key,value) VALUES ('schema_version',?)", (str(_schema.SCHEMA_VERSION),))
        now = time.time()
        conn.executemany(
            "INSERT INTO groups(group_id,formation_id,manifest_path,created_at,group_time,closed,"
            "group_limit,body_limit,outstanding_limit,rate_limit,charge,body_budget) "
            "VALUES (?,?,?,?,?,0,4096,65536,256,256,0,65536)",
            [(_group_id(i), "formation", str(path.parent / "m.yaml"), now, now) for i in range(n_groups)],
        )
        conn.commit()
    finally:
        conn.close()


def test_healthy_store_still_verifies_with_no_behavior_change(tmp_path: Path) -> None:
    path = tmp_path / "comms.sqlite3"
    _seed_store(path, n_groups=2)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        assert _schema.verify(conn) is None  # raises on any regression
        # No behavior change: verify reads, it never rewrites the store.
        assert conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0] == 2
        assert _schema.stored_version(conn) == str(_schema.SCHEMA_VERSION)
        # Control: the minimally different store (an unsupported recorded version) is refused.
        conn.execute("UPDATE schema_meta SET value='999'")
        with pytest.raises(_schema.SchemaVersionError, match="unsupported schema version"):
            _schema.verify(conn)
    finally:
        conn.close()


def test_verify_interrupts_past_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "comms.sqlite3"
    _seed_store(path, n_groups=_LARGE_GROUP_COUNT)
    # Monkeypatched short: already-expired, so the first progress-handler tick interrupts.
    monkeypatch.setattr(_schema, "VERIFY_DEADLINE_S", -1.0)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        started = time.monotonic()
        with pytest.raises(ValueError, match="comms_verify_deadline_seconds"):
            _schema.verify(conn)
        elapsed = time.monotonic() - started
    finally:
        conn.close()
    assert elapsed < 2.0, f"verify() did not stop at the deadline: {elapsed}s"


def test_deadline_through_store_adapter_is_timeout_not_corrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A merely slow/large mailbox must not be reported as CORRUPT (sol r1 P2): a user could delete good evidence."""
    path = tmp_path / "comms.sqlite3"
    _seed_store(path, n_groups=_LARGE_GROUP_COUNT)
    monkeypatch.setattr(_schema, "VERIFY_DEADLINE_S", -1.0)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(StoreError, match="comms_verify_deadline_seconds") as excinfo:
            _store._verify_schema(conn)
    finally:
        conn.close()
    assert excinfo.value.refusal is StoreRefusal.TIMEOUT
    assert "not judged corrupt" in str(excinfo.value)
    assert "retry later, or ask the operator if it persists" in str(excinfo.value)
    assert "compact" not in str(excinfo.value)
    assert "reset" not in str(excinfo.value)


def test_timeout_refusal_remedy_names_only_a_real_action() -> None:
    """No compact/reset verb exists, and deleting mailbox rows breaks the never-delete rule (_hint.py)."""
    text = REFUSALS["storage_verify_timeout"].next_action
    assert "compact" not in text
    assert "reset" not in text
    assert "retry later, or ask the operator if it persists" in text


def test_genuine_schema_corruption_through_store_adapter_still_is_corrupt(tmp_path: Path) -> None:
    """The pre-existing corrupt-store contract is unchanged: real inconsistency still maps to CORRUPT."""
    path = tmp_path / "comms.sqlite3"
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT INTO schema_meta(key,value) VALUES ('schema_version',?)", (str(_schema.SCHEMA_VERSION),))
        conn.commit()
    finally:
        conn.close()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        with pytest.raises(StoreError) as excinfo:
            _store._verify_schema(conn)
    finally:
        conn.close()
    assert excinfo.value.refusal is StoreRefusal.CORRUPT
