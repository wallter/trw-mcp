"""Canonical binding and compatibility with legacy alias-bearing endpoint rows."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env, write_pin  # noqa: F401
from tests.comms.conftest import core, enable_comms, joined_member
from trw_mcp.comms import _identity
from trw_mcp.comms._store import database_path


@pytest.mark.parametrize("alias_kind", ["lexical", "symlink"])
@pytest.mark.parametrize("legacy_endpoint", [False, True])
def test_alias_binding_routes_without_accepting_a_different_run(
    formation_env: FormationFixture,
    comms_server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    alias_kind: str,
    legacy_endpoint: bool,
) -> None:
    enable_comms(monkeypatch)
    sender = joined_member(formation_env, "impl-1", "pin-a")
    receiver = joined_member(formation_env, "impl-2", "pin-b")
    alias = receiver / ".." / receiver.name
    if alias_kind == "symlink":
        alias = receiver.parent / "receiver-alias"
        alias.symlink_to(receiver, target_is_directory=True)
    # Today's native writer canonicalizes; this fixture represents retained
    # legacy/raw pins which the public read/binding path still accepts.
    write_pin(formation_env, "pin-b", alias)
    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    binding = _identity.resolve_snapshot(
        None, trw_dir=formation_env.trw_dir, project_root=formation_env.project_root
    ).binding
    assert binding.run_path == receiver.resolve()

    def call(name: str, **arguments: Any) -> dict[str, Any]:
        result = asyncio.run(comms_server.call_tool(name, arguments)).structured_content
        assert isinstance(result, dict)
        return result

    assert call("trw_inbox", action="enroll")["status"] == "ok"
    db = database_path(formation_env.manifest_path())
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT run_path FROM endpoints").fetchone()[0] == str(receiver.resolve())
        if legacy_endpoint:
            with conn:
                conn.execute("UPDATE endpoints SET run_path=?", (str(alias),))
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    sent = call("trw_send", recipient_member_id="impl-2", request_key="alias", body="hello")
    assert sent["status"] == "ok", sent
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT charge FROM groups").fetchone()[0] == 1
        expected = str(alias) if legacy_endpoint else str(receiver.resolve())
        assert conn.execute("SELECT run_path FROM endpoints").fetchone()[0] == expected
        with conn:
            conn.execute("UPDATE endpoints SET run_path=?", (str(sender),))
    # PRD-CORE-274 FR13: a DIRECT send is member-addressed; the endpoint neither
    # authorizes nor refuses it. CORE-276 scoped notify still validates the endpoint.
    stored = call("trw_send", recipient_member_id="impl-2", request_key="different-run", body="hello")
    assert stored["status"] == "ok", stored
    refused = call("trw_send", scope="src/beta/x.py", request_key="different-run-notify", body="hello")
    # CORE-276 aggregates an all-skipped fan-out as recipient_unavailable (the binding check skipped it).
    assert refused["reason"] == "recipient_unavailable", refused
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT charge FROM groups").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0] == 2
    # A previously resolvable alias can become a loop; refuse instead of
    # leaking a Path.resolve exception or charging an undeliverable message.
    loop = receiver.parent / "looped-endpoint"
    loop.symlink_to(loop)
    with closing(sqlite3.connect(db)) as conn:
        with conn:
            conn.execute("UPDATE endpoints SET run_path=?", (str(loop),))
    assert call("trw_send", recipient_member_id="impl-2", request_key="loop", body="hello")["status"] == "ok"
    refused = call("trw_send", scope="src/beta/x.py", request_key="loop-notify", body="hello")
    assert refused["reason"] == "recipient_unavailable", refused
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT charge FROM groups").fetchone()[0] == 3


@pytest.mark.parametrize("error_type", [OSError, RuntimeError])
def test_endpoint_resolution_failure_refuses_without_charge(
    formation_env: FormationFixture,
    comms_server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    """Exercise each catch branch independently of platform symlink semantics."""
    enable_comms(monkeypatch)
    joined_member(formation_env, "impl-1", "pin-a")
    receiver = joined_member(formation_env, "impl-2", "pin-b")

    def call(name: str, **arguments: Any) -> dict[str, Any]:
        result = asyncio.run(comms_server.call_tool(name, arguments)).structured_content
        assert isinstance(result, dict)
        return result

    monkeypatch.setenv("TRW_SESSION_ID", "pin-b")
    assert call("trw_inbox", action="enroll")["status"] == "ok"
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")
    sent = call("trw_send", recipient_member_id="impl-2", request_key="seed", body="hello")
    assert sent["status"] == "ok", sent
    db = database_path(formation_env.manifest_path())
    # Only this retained endpoint string faults. Native pin/run/manifest paths
    # remain real and resolvable; this is not an identity authorization stub.
    endpoint_alias = receiver.parent / "endpoint-only-resolution-fault"
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute("UPDATE endpoints SET run_path=?", (str(endpoint_alias),))
    original_resolve = Path.resolve
    attempted: list[Path] = []

    def resolve(path: Path, strict: bool = False) -> Path:
        if path == endpoint_alias:
            attempted.append(path)
            raise error_type("endpoint-only injected filesystem fault")
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    # The endpoint path is resolved only on the live-delivery (CORE-276 notify) path.
    refused = call("trw_send", scope="src/beta/f.py", request_key="fault", body="hello")
    assert attempted == [endpoint_alias]
    assert refused["status"] == "refused"
    assert refused["reason"] == "recipient_unavailable"  # the fault was caught and the peer skipped
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT charge FROM groups").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0] == 1
        assert conn.execute("SELECT run_path FROM endpoints").fetchone()[0] == str(endpoint_alias)
    assert core(call("trw_send", recipient_member_id="impl-2", request_key="seed", body="hello")) == core(sent)
