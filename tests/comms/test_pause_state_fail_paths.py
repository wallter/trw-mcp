"""Fail paths of the pause gate: an unreadable pause record must never read as "not paused".

Send fails CLOSED (refused ``formation_unavailable``, nothing admitted), member state is an
advisory that fails OPEN (``None`` plus a log event), and ack refuses with a closed reason.
All are driven through the real ``trw_send`` / ``trw_inbox`` dispatch on a formation whose
pause record is corrupt on disk.
"""

from __future__ import annotations

import sqlite3

import pytest
from structlog.testing import capture_logs

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_formation_pause import PauseScene, ps  # noqa: F401
from trw_mcp.comms import _pause_state
from trw_mcp.comms._store import database_path
from trw_mcp.formation._pause import pause_path


def _admissions(scene: PauseScene) -> int:
    with sqlite3.connect(database_path(scene.formation.manifest_path())) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM admissions").fetchone()[0])


def _corrupt_pause(scene: PauseScene) -> None:
    pause_path(scene.formation.manifest_path()).write_text("- not\n- a mapping\n", encoding="utf-8")


def test_send_fails_closed_when_the_pause_record_is_unreadable(ps: PauseScene) -> None:
    ps.pause()
    assert ps.send("lead", "before", kind="status")["status"] == "ok"  # creates the mailbox
    assert _admissions(ps) == 1
    _corrupt_pause(ps)
    for to, kind in [("impl-2", "request"), ("lead", "status"), ("lead", "reply")]:
        refused = ps.send(to, f"after-{to}-{kind}", kind=kind)
        assert (refused["status"], refused["reason"]) == ("refused", "formation_unavailable")
    assert _admissions(ps) == 1, "a refused send must admit nothing"


def test_send_refusal_is_none_only_when_no_pause_file_exists(ps: PauseScene) -> None:
    assert ps.send("impl-2", "unpaused")["status"] == "ok"  # no pause file: proceeds
    _corrupt_pause(ps)  # the same formation, now with an unreadable pause
    assert ps.send("impl-2", "unreadable")["reason"] == "formation_unavailable"


def test_member_state_fails_open_and_logs_when_the_pause_record_is_unreadable(ps: PauseScene) -> None:
    from trw_mcp.comms import _CALL_BINDING

    ps.call("trw_inbox", action="list")  # enrolled: resolves a binding
    _corrupt_pause(ps)
    seen: list[object] = []
    real = _pause_state.member_state
    ps.monkeypatch.setattr(_pause_state, "member_state", lambda b: (seen.append(real(b)), real(b))[1])
    with capture_logs() as logs:
        listed = ps.call("trw_inbox", action="list")
    assert listed["status"] == "ok" and listed.get("state") != "paused", "advisory state fails open"
    assert seen == [None]
    assert any(e["event"] == "comms_pause_record_unreadable" for e in logs)
    assert _CALL_BINDING.get() is None  # scoped: nothing leaked past the call


@pytest.mark.parametrize("pause_id", [None, ""])
def test_ack_without_a_pause_id_is_refused_as_a_mismatch(ps: PauseScene, pause_id: str | None) -> None:
    ps.pause()
    args = {} if pause_id is None else {"pause_id": pause_id}
    refused = ps.call("trw_inbox", action="ack_pause", **args)
    assert (refused["status"], refused["reason"]) == ("refused", "pause_id_mismatch")


def test_ack_surfaces_the_pause_error_reason(ps: PauseScene) -> None:
    ps.pause()
    refused = ps.call("trw_inbox", action="ack_pause", pause_id="0" * 16)
    assert (refused["status"], refused["reason"]) == ("refused", "pause_id_mismatch")
    ps.resume()
    late = ps.call("trw_inbox", action="ack_pause", pause_id="0" * 16)
    assert (late["status"], late["reason"]) == ("refused", "not_paused")


def test_ack_refuses_formation_unavailable_when_the_pause_record_is_unreadable(ps: PauseScene) -> None:
    record = ps.pause()
    _corrupt_pause(ps)
    refused = ps.call("trw_inbox", action="ack_pause", pause_id=record.pause_id)
    assert (refused["status"], refused["reason"]) == ("refused", "formation_unavailable")
