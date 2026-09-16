"""Unresolvable manifest paths refuse before storage; missing peers stay scoped."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

import pytest
import yaml
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import enable_comms, joined_member


@pytest.mark.parametrize("broken", ["loop", "nul", "missing"])
def test_manifest_path_resolution_stays_typed_and_nonmutating(
    formation_env: FormationFixture,
    comms_server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    broken: str,
) -> None:
    enable_comms(monkeypatch)
    run = joined_member(formation_env, "impl-1", "pin-a")
    joined_member(formation_env, "impl-2", "pin-b")
    monkeypatch.setenv("TRW_SESSION_ID", "pin-a")

    def call(tool: str, **arguments: Any) -> dict[str, Any]:
        payload = asyncio.run(comms_server.call_tool(tool, arguments)).structured_content
        assert isinstance(payload, dict)
        return payload

    assert call("trw_peers", action="enroll")["status"] == "ok"
    target = run.parent / "unavailable-peer"
    if broken == "loop":
        target.symlink_to(target)
    value = str(target) + ("\x00invalid" if broken == "nul" else "")
    manifest = formation_env.manifest_path()
    original = manifest.read_bytes()
    data = yaml.safe_load(original)
    next(member for member in data["members"] if member["member_id"] == "impl-2")["run_path"] = value
    manifest.write_text(yaml.safe_dump(data))
    files = [manifest, formation_env.trw_dir / "runtime" / "pins.json", *manifest.parent.glob("comms.sqlite3*")]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()}
    if broken == "missing":
        # Canonical identity is not a new existence requirement for EVERY peer.
        assert call("trw_peers", action="list")["status"] == "ok"
        manifest.write_bytes(original)
        assert call("trw_peers", action="list")["status"] == "ok"
        return
    for tool, arguments in (
        ("trw_peers", {"action": "list"}),
        ("trw_send", {"recipient_member_id": "impl-2", "request_key": "bad-path", "body": "hello"}),
        ("trw_inbox", {"action": "status"}),
    ):
        result = call(tool, **arguments)
        assert result["status"] == "refused"
        assert result["reason"] == "formation_unavailable"
        assert result["detail"] == "Peer operation refused."
        assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.is_file()} == before
    manifest.write_bytes(original)
    assert call("trw_peers", action="list")["status"] == "ok"
