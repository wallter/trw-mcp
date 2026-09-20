"""PRD-CORE-274-FR01/FR07/FR10 endpoint and closure regressions.

Reuse the native formation/pin/public FastMCP fixture. Each database is
synthetic. Transaction-entry interception is process-local and advances only
that synthetic persisted clock, modelling a writer that won the lock first.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
import yaml

from tests.comms import test_identity_boundary as contract

Scene = contract.Scene
scene = contract.scene


def test_formation_id_persisted_not_member_id(scene: Scene) -> None:
    from trw_mcp.comms import _store

    assert scene.call("enroll")["status"] == "ok"
    path = next(scene.root.rglob(_store.DATABASE_FILENAME))
    conn = _store.sqlite3.connect(path)
    try:
        assert conn.execute("SELECT formation_id FROM groups").fetchall() == [("diagnostic",)]
    finally:
        conn.close()


@pytest.mark.parametrize("pre_enroll", [False, True])
def test_invalid_terminal_binding_cannot_create_or_close(scene: Scene, pre_enroll: bool) -> None:
    from trw_mcp import formation
    from trw_mcp.comms import _store

    if pre_enroll:
        assert scene.call("enroll")["status"] == "ok"
    formation.revise("diagnostic", scene.owner, {"lead": {"status": "abandoned"}}, trw_dir=scene.root / ".trw")
    run_file = scene.owner / "meta" / "run.yaml"
    raw = yaml.safe_load(run_file.read_text())
    raw["member_id"] = "wrong-stamped-member"
    run_file.write_text(yaml.safe_dump(raw))
    refused = scene.call("list")
    assert refused["reason"] == "stamped_identity_mismatch"
    paths = list(scene.root.rglob(_store.DATABASE_FILENAME))
    if pre_enroll:
        conn = _store.sqlite3.connect(paths[0])
        try:
            assert conn.execute("SELECT closed FROM groups").fetchall() == [(0,)]
        finally:
            conn.close()
    else:
        assert paths == []
    # Positive control: restore valid stamp while all-terminal. Closure must
    # persist even when this is the first-ever mailbox operation.
    raw["member_id"] = "lead"
    run_file.write_text(yaml.safe_dump(raw))
    assert scene.call("list")["reason"] == "group_closed"
    conn = _store.sqlite3.connect(next(scene.root.rglob(_store.DATABASE_FILENAME)))
    try:
        assert conn.execute("SELECT closed FROM groups").fetchall() == [(1,)]
        if not pre_enroll:
            assert conn.execute("SELECT COUNT(*) FROM endpoints").fetchone()[0] == 0
    finally:
        conn.close()


def test_operation_clock_sampled_after_transaction_entry(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    import trw_mcp.comms as comms
    from trw_mcp.comms import _store

    assert scene.call("enroll")["status"] == "ok"
    real_immediate = comms.immediate
    real_clock = comms.effective_time
    observations: list[bool] = []
    future = 9_000_000_000.0

    @contextmanager
    def predecessor(conn: _store.sqlite3.Connection) -> Iterator[_store.sqlite3.Connection]:
        with real_immediate(conn):
            conn.execute("UPDATE groups SET group_time = ?", (future,))
            yield conn

    def observed_clock(conn: _store.sqlite3.Connection, group_id: str) -> float:
        observations.append(conn.in_transaction)
        return real_clock(conn, group_id)

    monkeypatch.setattr(comms, "immediate", predecessor)
    monkeypatch.setattr(comms, "effective_time", observed_clock)
    assert scene.call("heartbeat")["status"] == "ok"
    assert observations == [True]
    conn = _store.sqlite3.connect(next(scene.root.rglob(_store.DATABASE_FILENAME)))
    try:
        assert conn.execute("SELECT last_seen_at, lease_expires_at FROM endpoints").fetchone() == (
            future,
            future + scene.config.comms_lease_ttl_seconds,
        )
    finally:
        conn.close()


def test_numeric_incarnation_roundtrips_without_affinity_loss(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    from trw_mcp.comms import _endpoints, _store

    token = "12345678901234567890123456789012"  # Synthetic numeric-affinity control.
    monkeypatch.setattr(_endpoints.secrets, "token_hex", lambda size: token)
    assert scene.call("enroll")["status"] == "ok"
    conn = _store.sqlite3.connect(next(scene.root.rglob(_store.DATABASE_FILENAME)))
    try:
        assert conn.execute("SELECT incarnation, typeof(incarnation) FROM endpoints").fetchone() == (token, "text")
    finally:
        conn.close()
    assert scene.call("heartbeat")["status"] == "ok"


def test_persisted_group_identity_mismatch_refuses_without_repair(scene: Scene) -> None:
    from trw_mcp.comms import _store

    assert scene.call("enroll")["status"] == "ok"
    path = next(scene.root.rglob(_store.DATABASE_FILENAME))
    conn = _store.sqlite3.connect(path)
    try:
        conn.execute("UPDATE groups SET formation_id = 'foreign'")
        conn.commit()
    finally:
        conn.close()
    assert scene.call("heartbeat")["reason"] == "storage_corrupt"
    conn = _store.sqlite3.connect(path)
    try:
        assert conn.execute("SELECT formation_id FROM groups").fetchone() == ("foreign",)
    finally:
        conn.close()


def test_every_endpoint_decision_refuses_a_connection_outside_the_write_lock(
    scene: Scene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ownership guard is uniform across reads and writes, not just writes.

    ``receiver_incarnation`` only reads, which is why it lacked this guard while
    ``enroll``/``heartbeat`` had it. The incarnation it returns authorizes the
    fetch that follows, so reading it outside the write lock would let another
    process displace the endpoint between the check and the use. Deleting the
    guard from ``_endpoints.receiver_incarnation`` makes this test fail.
    """
    import trw_mcp.comms as comms
    from trw_mcp.comms import _endpoints, _identity, _store

    captured: list[_identity.CallerBinding] = []
    real = comms.receiver_incarnation

    def capture(conn: _store.sqlite3.Connection, binding: _identity.CallerBinding, now: float, **kwargs: Any) -> str:
        captured.append(binding)
        return real(conn, binding, now, **kwargs)

    monkeypatch.setattr(comms, "receiver_incarnation", capture)
    assert scene.call("enroll")["status"] == "ok"
    fetched = asyncio.run(scene.server.call_tool("trw_inbox", {"action": "fetch"})).structured_content
    assert isinstance(fetched, dict) and fetched["status"] == "ok", fetched
    assert captured, "fixture never reached the receiver check"

    binding = captured[0]
    conn = _store.sqlite3.connect(next(scene.root.rglob(_store.DATABASE_FILENAME)))
    conn.row_factory = _store.sqlite3.Row
    try:
        assert not conn.in_transaction
        for call in (
            lambda: _endpoints.receiver_incarnation(conn, binding, 0.0, lease_ttl_seconds=60),
            lambda: _endpoints.touch(conn, binding, 0.0, lease_ttl_seconds=60),
            lambda: _endpoints.heartbeat(conn, binding, now=0.0, lease_ttl_seconds=60),
            lambda: _endpoints.enroll(conn, binding, now=0.0, lease_ttl_seconds=60),
        ):
            with pytest.raises(RuntimeError, match="facade transaction"):
                call()
    finally:
        conn.close()
