"""Formation pause and resume with per-member ack (PAUSE-RESUME-DESIGN rev 2, lane C READY).

The formation here has three joined members: impl-1 and impl-2, plus ``lead``, whose
run IS the orchestrator run, so the orchestrator is a member the paused ones may
still send status and replies to.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import FormationFixture, formation_env, open_slot, write_pin  # noqa: F401
from tests.comms.conftest import call_peers, enable_comms
from trw_mcp.formation import (
    PauseError,
    ack_pause,
    create,
    join,
    pause,
    pause_roll_call,
    read_manifest,
    read_pause,
    resume,
    revise,
)
from trw_mcp.formation._pause import PauseRecord, pause_path

_PINS = {"impl-1": "pin-a", "impl-2": "pin-b", "lead": "pin-l"}


class PauseScene:
    def __init__(self, formation: FormationFixture, server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> None:
        self.formation, self.server, self.monkeypatch = formation, server, monkeypatch

    def actor(self, member: str) -> None:
        self.monkeypatch.setenv("TRW_SESSION_ID", _PINS[member])

    def call(self, tool: str, **args: Any) -> dict[str, Any]:
        result = asyncio.run(self.server.call_tool(tool, args))
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    def send(self, to: str, key: str, kind: str = "request") -> dict[str, Any]:
        return self.call("trw_send", recipient_member_id=to, request_key=key, body="b", kind=kind)

    def pause(self, reason: str = "release cut", until: str | None = None) -> PauseRecord:
        f = self.formation
        return pause("release-train", f.orchestrator_run, reason, until_utc=until, trw_dir=f.trw_dir)

    def resume(self) -> str:
        return resume("release-train", self.formation.orchestrator_run, trw_dir=self.formation.trw_dir)

    def events(self, run: Path) -> list[dict[str, Any]]:
        path = run / "meta" / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


@pytest.fixture
def ps(formation_env: FormationFixture, comms_server: FastMCP, monkeypatch: pytest.MonkeyPatch) -> PauseScene:
    enable_comms(monkeypatch)
    payload = formation_env.payload()
    payload["members"].append(open_slot("lead", "claude-code", role="lead"))
    create(formation_env.orchestrator_run, payload, trw_dir=formation_env.trw_dir)
    runs = {**formation_env.member_runs, "lead": formation_env.orchestrator_run}
    for member, pin in _PINS.items():
        join("release-train", member, runs[member], pin_key=pin, trw_dir=formation_env.trw_dir)
        write_pin(formation_env, pin, runs[member])
    scene = PauseScene(formation_env, comms_server, monkeypatch)
    for member in _PINS:
        scene.actor(member)
        assert call_peers(comms_server, "enroll")["status"] == "ok"
    scene.actor("impl-1")
    return scene


def test_pause_is_a_sibling_file_the_manifest_never_changes(ps: PauseScene) -> None:
    manifest = ps.formation.manifest_path()
    before = manifest.read_bytes()
    record = ps.pause(until="2099-01-01T00:00:00Z")
    assert manifest.read_bytes() == before, "pause must not touch formation.yaml (old servers read it)"
    read_manifest(manifest)  # still loadable by the pre-pause model
    assert stat.S_IMODE(os.stat(pause_path(manifest)).st_mode) == 0o600
    assert read_pause(manifest) == record
    ps.resume()
    assert manifest.read_bytes() == before and not pause_path(manifest).exists()


def test_pause_and_resume_are_orchestrator_only_and_single(ps: PauseScene) -> None:
    member_run = ps.formation.member_runs["impl-1"]
    with pytest.raises(PauseError) as refused:
        pause("release-train", member_run, "x", trw_dir=ps.formation.trw_dir)
    assert refused.value.reason == "not_orchestrator"
    with pytest.raises(PauseError) as not_paused:
        ps.resume()
    assert not_paused.value.reason == "not_paused"
    ps.pause()
    with pytest.raises(PauseError) as twice:
        ps.pause()
    assert twice.value.reason == "already_paused"
    with pytest.raises(PauseError) as member_resume:
        resume("release-train", member_run, trw_dir=ps.formation.trw_dir)
    assert member_resume.value.reason == "not_orchestrator"


def test_a_member_is_taught_the_pause_once_then_acks_it(ps: PauseScene) -> None:
    ps.call("trw_inbox", action="list")  # settle the steady state before the pause
    record = ps.pause()
    first = ps.call("trw_inbox", action="list")
    assert first["state"] == "paused" and first["pause"] == {"pause_id": record.pause_id, "reason": "release cut"}
    assert "ack_pause" in first["guidance"]
    steady = ps.call("trw_inbox", action="list")
    assert "state" not in steady and "guidance" not in steady, "taught once per state change"

    stale = ps.call("trw_inbox", action="ack_pause", pause_id="0" * 16)
    assert (stale["status"], stale["reason"]) == ("refused", "pause_id_mismatch")
    acked = ps.call("trw_inbox", action="ack_pause", pause_id=record.pause_id)
    assert acked["status"] == "ok" and acked["already"] is False and acked["state"] == "paused_acked"
    again = ps.call("trw_inbox", action="ack_pause", pause_id=record.pause_id)
    assert again["status"] == "ok" and again["already"] is True
    assert pause_roll_call(ps.formation.manifest_path()) == {
        "pause_id": record.pause_id,
        "reason": "release cut",
        "since_utc": record.since_utc,
        "acked": ["impl-1"],
        "not_acked": ["impl-2"],  # the orchestrator's own member is not in the roll call
    }


def test_an_ack_writes_only_the_callers_own_member(ps: PauseScene) -> None:
    record = ps.pause()
    ps.actor("impl-2")
    assert ps.call("trw_inbox", action="ack_pause", pause_id=record.pause_id)["status"] == "ok"
    assert set((read_pause(ps.formation.manifest_path()) or record).acks) == {"impl-2"}


def test_a_paused_member_may_only_status_or_reply_to_the_orchestrator(ps: PauseScene) -> None:
    ps.pause()
    admitted_before = ps.send("lead", "pre-check", kind="status")
    assert admitted_before["status"] == "ok"
    for to, kind in [("impl-2", "request"), ("impl-2", "status"), ("lead", "request")]:
        refused = ps.send(to, f"{to}-{kind}", kind=kind)
        assert (refused["status"], refused["reason"], refused["state"]) == ("refused", "formation_paused", "paused")
        assert "ack_pause" in refused["detail"]
    assert ps.send("lead", "a-reply", kind="reply")["status"] == "ok"
    ps.actor("lead")
    assert ps.send("impl-2", "lead-note")["status"] == "ok", "the orchestrator is never paused"
    ps.actor("impl-2")
    assert ps.call("trw_inbox", action="fetch")["items"], "a paused member still drains its inbox"


def test_resume_restores_sends_and_says_resumed(ps: PauseScene) -> None:
    ps.pause()
    assert ps.call("trw_inbox", action="list")["state"] == "paused"
    ps.resume()
    back = ps.call("trw_inbox", action="list")
    assert back["state"] == "enrolled" and back["guidance"].startswith("resumed. ")
    assert ps.send("impl-2", "after")["status"] == "ok"


def test_events_land_in_the_orchestrator_run_and_the_ackers_own_run(ps: PauseScene) -> None:
    record = ps.pause()
    assert ps.call("trw_inbox", action="ack_pause", pause_id=record.pause_id)["status"] == "ok"
    ps.resume()
    lead_types = [e.get("event") for e in ps.events(ps.formation.orchestrator_run)]
    assert "formation_paused" in lead_types and "formation_resumed" in lead_types
    assert "formation_pause_acked" not in lead_types, "no cross-run writes"
    member_types = [e.get("event") for e in ps.events(ps.formation.member_runs["impl-1"])]
    assert "formation_pause_acked" in member_types
    assert "formation_paused" not in member_types


def test_a_late_ack_racing_a_resume_refuses_and_never_recreates_the_file(ps: PauseScene) -> None:
    record = ps.pause()
    ps.resume()
    manifest = ps.formation.manifest_path()
    with pytest.raises(PauseError) as late:
        ack_pause(manifest, "impl-1", ps.formation.member_runs["impl-1"], record.pause_id)
    assert late.value.reason == "not_paused"
    assert not pause_path(manifest).exists()
    refused = ps.call("trw_inbox", action="ack_pause", pause_id=record.pause_id)
    assert (refused["status"], refused["reason"]) == ("refused", "not_paused")


def test_a_terminal_member_cannot_ack(ps: PauseScene) -> None:
    record = ps.pause()
    revise(
        "release-train",
        ps.formation.orchestrator_run,
        {"impl-1": {"status": "abandoned"}},
        trw_dir=ps.formation.trw_dir,
    )
    refused = ps.call("trw_inbox", action="ack_pause", pause_id=record.pause_id)
    assert (refused["status"], refused["reason"]) == ("refused", "member_not_eligible")
    assert (read_pause(ps.formation.manifest_path()) or record).acks == {}


def test_overdue_is_reported_after_until(ps: PauseScene) -> None:
    ps.pause(until="2000-01-01T00:00:00Z")
    block = pause_roll_call(ps.formation.manifest_path())
    assert block is not None and block["overdue"] is True
    assert (read_pause(ps.formation.manifest_path()) or PauseRecord("x", "", "")).overdue()


def test_an_invalid_until_is_refused(ps: PauseScene) -> None:
    with pytest.raises(PauseError) as refused:
        ps.pause(until="tomorrow")
    assert refused.value.reason == "invalid_until"
    assert not pause_path(ps.formation.manifest_path()).exists()


def test_the_cli_pauses_reports_the_roll_call_and_resumes(
    ps: PauseScene, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import argparse

    from tests._formation_test_support import pin_session
    from trw_mcp.tools._formation_cli import run_formation

    pin_session(monkeypatch, ps.formation.orchestrator_run)

    def cli(command: str, **extra: Any) -> tuple[int, str]:
        args = argparse.Namespace(formation_command=command, run_path=str(ps.formation.orchestrator_run), **extra)
        with pytest.raises(SystemExit) as done:
            run_formation(args)
        return int(done.value.code or 0), capsys.readouterr().out

    code, out = cli("pause", reason="release cut", until="2000-01-01T00:00:00Z")
    record = read_pause(ps.formation.manifest_path())
    assert code == 0 and record is not None and f"pause_id={record.pause_id}" in out
    code, out = cli("status", as_json=False)
    assert code == 0 and f"PAUSED {record.pause_id}" in out and "OVERDUE" in out
    assert "acked 0/2; not acked: impl-1, impl-2" in out
    code, out = cli("status", as_json=True)
    assert json.loads(out)["pause"]["not_acked"] == ["impl-1", "impl-2"]
    code, out = cli("resume")
    assert code == 0 and "resumed" in out and read_pause(ps.formation.manifest_path()) is None
    assert cli("resume")[0] == 1, "resume when not paused refuses"


def test_the_orchestrator_is_the_manifest_field_not_the_manifest_location(
    ps: PauseScene, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C review SF1: were the manifest relocated, only orchestrator_run_path would still be right."""
    from trw_mcp.comms import _pause_state

    ps.pause()
    real = read_manifest(ps.formation.manifest_path())
    moved = real.model_copy(update={"orchestrator_run_path": str(ps.formation.member_runs["impl-2"])})
    monkeypatch.setattr(_pause_state, "read_manifest", lambda _path: moved)
    assert ps.send("impl-2", "to-the-field", kind="status")["status"] == "ok"
    refused = ps.send("lead", "to-the-location", kind="status")
    assert refused["reason"] == "formation_paused"


def test_a_schema_version_is_read_but_never_written(ps: PauseScene) -> None:
    """Ledger N4: 5.0.0 ships the READER; writing the key would break older peers.

    FormationManifest is extra="forbid", so the first manifest carrying
    schema_version is unreadable to every server built before it. Shipping the
    reader one release early lets the release that starts writing assume its peers
    already understand the key.
    """
    import yaml as _yaml

    from trw_mcp.formation import validate
    from trw_mcp.formation._store import render_manifest

    manifest = read_manifest(ps.formation.manifest_path())
    assert manifest.schema_version is None, "written before schema versions existed, not version 0"
    # Parsed, not substring-matched: the rendered YAML embeds the run path, which
    # in a tmp_path carries this test's own name.
    assert "schema_version" not in _yaml.safe_load(render_manifest(manifest)), "a reader-only release writes no key"

    declared = validate({**manifest.model_dump(mode="json"), "schema_version": 2})
    assert declared.schema_version == 2, "a manifest that HAS the key round-trips it"
    assert _yaml.safe_load(render_manifest(declared))["schema_version"] == 2

    with pytest.raises(Exception):
        validate({**manifest.model_dump(mode="json"), "schema_version": 0})
