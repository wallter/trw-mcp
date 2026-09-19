"""WP4 A1/A2 plumbing proof: two REAL stdio servers in one formation, no model.

WHAT IS UNDER PROOF. Two ``trw-mcp`` server processes, each with its own
``TRW_SESSION_ID`` and its own working directory (standing in for a leased
worktree), both pointed at ONE disposable coordination project root, can
pre-declare-join a single formation through the MCP tool surface and exchange
messages in both directions -- and the coordination root ends with exactly two
pins, one per session id.

WHY IT IS BUILT FROM TWO EXISTING TEMPLATES RATHER THAN A THIRD HARNESS.
``tests/comms/test_crash_window.py`` drives real child processes but builds its
MCP server IN-PROCESS inside each child, so it never exercises spawn, the stdio
framing, or the environment contract a real client hands a server.
``tests/test_stdio_n_server_handshake.py`` spawns real stdio servers but never
gives them distinct identities that mean anything to the formation. The WP4
scenario is the intersection, and until this module existed it was asserted
nowhere: the closest thing was an assertion about the ENVIRONMENT an adapter
recorded, not about what a server did with it.

WHAT "NO MODEL" COSTS AND WHY IT IS STILL THE RIGHT PROOF. The live WP4 trial
adds two things this file cannot: a vendor CLI deciding to call ``trw_init``,
and that CLI passing ``TRW_SESSION_ID``/``TRW_PROJECT_ROOT`` through to the
server it spawns (recorded as open in the execution plan). Everything BELOW that
seam -- the join, the membership binding, the mailbox, the pin store -- is
server-side and is exactly what this file pins. A regression here would make the
live trial fail for a reason nobody could localise.

THE MUTATIONS ARE TESTS, NOT NOTES. Two ways the identity plumbing could be
fake are exercised as their own cases: the same session id in both children
(the two members collapse onto ONE pin and ONE identity), and a child whose
``TRW_PROJECT_ROOT`` follows its worktree instead of the coordination root (it
cannot see the formation at all). Both fail loudly if the distinctness this
module claims is decorative.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests._stdio_harness import ServerProcess, stdio_import_skip_reason
from tests.comms._two_member_support import (
    Coordination,
    MemberHarness,
    build_coordination,
    error_text_of,
    payload_of,
    write_project_root,
)

_SKIP_REASON = stdio_import_skip_reason()

pytestmark = [
    pytest.mark.integration,
    # Three interpreter spawns plus a full ``trw_init`` scaffold each; the
    # default 120 s budget is a boot-time cliff on a loaded host, not a bound
    # this test is trying to enforce.
    pytest.mark.timeout(300),
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
]

_SESSION_A = "wp4-session-alpha"
_SESSION_B = "wp4-session-beta"
_SESSION_C = "wp4-session-gamma"
_SHARED_SESSION = "wp4-session-shared"

_MEMBER_A = "worker-a"
_MEMBER_B = "worker-b"

_BODY_A_TO_B = "worker-a: src/worker-a is green, seam is yours"
_BODY_B_TO_A = "worker-b: acknowledged, integration check queued"


@dataclass(frozen=True)
class Bench:
    """One coordination project, one harness, one worktree per worker."""

    harness: MemberHarness
    coord: Coordination
    worktrees: dict[str, Path]


@pytest.fixture
def bench(tmp_path: Path) -> Iterator[Bench]:
    """A disposable coordination root, a two-member formation, three worktrees.

    Everything lives under ``tmp_path``: the coordination project, the pin
    store, the mailbox database and every worker run directory. Worker runs
    accumulate under ``<coordination root>/.trw/runs`` with no cleanup path in
    production, which is precisely why a live trial needs a disposable project
    and why this test builds one per case.
    """
    coord_root = tmp_path / "coordination"
    coord_root.mkdir()
    coord = build_coordination(coord_root, [(_MEMBER_A, "claude-code"), (_MEMBER_B, "codex")])
    worktrees = {}
    for name in (_MEMBER_A, _MEMBER_B, "worker-c"):
        path = tmp_path / "worktrees" / name
        path.mkdir(parents=True)
        worktrees[name] = path
    harness = MemberHarness(coord_root, tmp_path / "user", tmp_path / "stderr")
    try:
        yield Bench(harness, coord, worktrees)
    finally:
        harness.teardown()


def _join_args(coord: Coordination, member_id: str, task_name: str) -> dict[str, object]:
    return {
        "task_name": task_name,
        "advanced": {"join_formation": {"formation_id": coord.formation_id, "member_id": member_id}},
    }


def test_two_stdio_members_join_one_formation_and_exchange_messages(bench: Bench) -> None:
    """The whole A1/A2 plumbing path, end to end, across two real processes."""
    harness, coord = bench.harness, bench.coord

    alpha = harness.ready_member(
        "alpha", session_id=_SESSION_A, project_root=coord.root, cwd=bench.worktrees[_MEMBER_A]
    )
    beta = harness.ready_member("beta", session_id=_SESSION_B, project_root=coord.root, cwd=bench.worktrees[_MEMBER_B])

    # (1) Two pre-declared members join ONE formation through the tool surface.
    init_a = harness.call_ok(alpha, "trw_init", _join_args(coord, _MEMBER_A, "worker_a_task"))
    init_b = harness.call_ok(beta, "trw_init", _join_args(coord, _MEMBER_B, "worker_b_task"))
    assert init_a["formation_member_id"] == _MEMBER_A
    assert init_b["formation_member_id"] == _MEMBER_B
    assert init_a["formation_id"] == init_b["formation_id"] == coord.formation_id

    run_a, run_b = Path(init_a["run_path"]), Path(init_b["run_path"])
    assert run_a != run_b
    # The RUN lives in the coordination project even though the process's cwd is
    # its worktree -- the property the first WP3 adapter cut got wrong.
    for run, worktree in ((run_a, bench.worktrees[_MEMBER_A]), (run_b, bench.worktrees[_MEMBER_B])):
        assert coord.root in run.parents, f"{run} is not inside the coordination root"
        assert worktree not in run.parents, f"{run} leaked into the worker's own worktree"

    members = coord.manifest_members()
    assert members[_MEMBER_A]["status"] == "joined"
    assert members[_MEMBER_B]["status"] == "joined"
    assert Path(members[_MEMBER_A]["run_path"]) == run_a
    assert Path(members[_MEMBER_B]["run_path"]) == run_b
    # The manifest records the SESSION id as the pin key -- this is the binding
    # the mailbox re-checks on every later call.
    assert members[_MEMBER_A]["pin_key"] == _SESSION_A
    assert members[_MEMBER_B]["pin_key"] == _SESSION_B

    # (2) Both enroll. Each server reports its OWN member id, unprompted: no
    # tool argument names a member, so this is the server's binding talking.
    enroll_a = harness.call_ok(alpha, "trw_peers", {"action": "enroll"})
    enroll_b = harness.call_ok(beta, "trw_peers", {"action": "enroll"})
    assert enroll_a["status"] == "ok" and enroll_a["member_id"] == _MEMBER_A
    assert enroll_b["status"] == "ok" and enroll_b["member_id"] == _MEMBER_B

    # (3) A -> B, then B -> A, each fetched and ACKed by the other process.
    receipt_ab = _send(harness, alpha, recipient=_MEMBER_B, request_key="a-to-b", body=_BODY_A_TO_B)
    fetched_b = harness.call_ok(beta, "trw_inbox", {"action": "fetch"})
    assert [item["message_id"] for item in fetched_b["items"]] == [receipt_ab["message_id"]]
    assert fetched_b["items"][0]["body"] == _BODY_A_TO_B
    assert fetched_b["items"][0]["sender_member_id"] == _MEMBER_A
    acked_b = harness.call_ok(beta, "trw_inbox", {"action": "ack", "message_ids": [receipt_ab["message_id"]]})
    assert acked_b["acknowledged_ids"] == [receipt_ab["message_id"]]

    receipt_ba = _send(harness, beta, recipient=_MEMBER_A, request_key="b-to-a", body=_BODY_B_TO_A)
    fetched_a = harness.call_ok(alpha, "trw_inbox", {"action": "fetch"})
    assert [item["message_id"] for item in fetched_a["items"]] == [receipt_ba["message_id"]]
    assert fetched_a["items"][0]["body"] == _BODY_B_TO_A
    assert fetched_a["items"][0]["sender_member_id"] == _MEMBER_B
    acked_a = harness.call_ok(alpha, "trw_inbox", {"action": "ack", "message_ids": [receipt_ba["message_id"]]})
    assert acked_a["acknowledged_ids"] == [receipt_ba["message_id"]]

    # Each side's mailbox is now empty: a fetch is not a peek, and the two
    # directions did not cross-deliver.
    assert harness.call_ok(alpha, "trw_inbox", {"action": "fetch"})["items"] == []
    assert harness.call_ok(beta, "trw_inbox", {"action": "fetch"})["items"] == []

    # (4) Exactly two pins, keyed on the two session ids, pointing at the two
    # runs, each written by its OWN server process.
    pins = coord.pins()
    assert set(pins) == {_SESSION_A, _SESSION_B}
    assert Path(pins[_SESSION_A]["run_path"]) == run_a
    assert Path(pins[_SESSION_B]["run_path"]) == run_b
    assert pins[_SESSION_A]["pid"] == alpha.pid
    assert pins[_SESSION_B]["pid"] == beta.pid

    # The mailbox is a real file next to the manifest, not in-memory state.
    from trw_mcp.comms._store import DATABASE_FILENAME

    assert (coord.orchestrator_run / DATABASE_FILENAME).is_file()


def _send(
    harness: MemberHarness, server: ServerProcess, *, recipient: str, request_key: str, body: str
) -> dict[str, Any]:
    result = harness.call_ok(
        server, "trw_send", {"recipient_member_id": recipient, "request_key": request_key, "body": body}
    )
    assert result["status"] == "ok", result
    receipt = result["receipt"]
    assert isinstance(receipt, dict)
    return receipt


@pytest.mark.skipif(not Path("/proc").is_dir(), reason="cwd-per-child is verified through /proc/<pid>/cwd")
def test_each_child_runs_in_its_own_worktree(bench: Bench) -> None:
    """Per-child cwd is real at the OS level, not merely passed to Popen.

    The shared harness pins every child to ``cwd=self.project_root``; the whole
    point of the WP4 environment contract is that a worker's cwd is its leased
    worktree while its TRW state resolves through ``TRW_PROJECT_ROOT``. Asking
    the kernel is the only way to prove the two really are different.
    """
    harness, coord = bench.harness, bench.coord
    alpha = harness.ready_member(
        "alpha", session_id=_SESSION_A, project_root=coord.root, cwd=bench.worktrees[_MEMBER_A]
    )
    beta = harness.ready_member("beta", session_id=_SESSION_B, project_root=coord.root, cwd=bench.worktrees[_MEMBER_B])

    assert Path(os.readlink(f"/proc/{alpha.pid}/cwd")) == bench.worktrees[_MEMBER_A].resolve()
    assert Path(os.readlink(f"/proc/{beta.pid}/cwd")) == bench.worktrees[_MEMBER_B].resolve()

    # ...and the run each one creates still lands in the coordination project.
    init_a = harness.call_ok(alpha, "trw_init", _join_args(coord, _MEMBER_A, "worker_a_task"))
    assert coord.root in Path(init_a["run_path"]).parents


def test_one_session_id_shared_by_two_children_collapses_to_one_identity(bench: Bench) -> None:
    """MUTATION: drop the distinct session ids and the isolation disappears.

    Both children join their OWN declared member, but both write the SAME pin
    key, so the second join overwrites the first pin and the mailbox binds BOTH
    processes to the member the surviving pin points at. One pin, one identity,
    two processes -- the exact failure a shared ``TRW_SESSION_ID`` would cause
    in a live run, and the reason the adapter must override it per worker.
    """
    harness, coord = bench.harness, bench.coord
    alpha = harness.ready_member(
        "alpha", session_id=_SHARED_SESSION, project_root=coord.root, cwd=bench.worktrees[_MEMBER_A]
    )
    beta = harness.ready_member(
        "beta", session_id=_SHARED_SESSION, project_root=coord.root, cwd=bench.worktrees[_MEMBER_B]
    )

    harness.call_ok(alpha, "trw_init", _join_args(coord, _MEMBER_A, "worker_a_task"))
    init_b = harness.call_ok(beta, "trw_init", _join_args(coord, _MEMBER_B, "worker_b_task"))

    pins = coord.pins()
    assert set(pins) == {_SHARED_SESSION}, "two sessions sharing one id must not produce two pins"
    assert Path(pins[_SHARED_SESSION]["run_path"]) == Path(init_b["run_path"])

    # Alpha has lost its own identity: it now answers as the member the
    # surviving pin points at, which is the one beta joined.
    assert harness.call_ok(alpha, "trw_peers", {"action": "enroll"})["member_id"] == _MEMBER_B

    # And the collapse is not benign. Both processes resolve to ONE member, so
    # the second enrolment collides with the first process's live endpoint
    # instead of standing up a second peer.
    assert harness.call_ok(beta, "trw_peers", {"action": "enroll"}) == {
        "status": "refused",
        "reason": "live_endpoint_held_by_other_incarnation",
        "detail": "Peer operation refused.",
        "delivery": "pull_only",
    }


def test_member_pointed_at_its_own_worktree_cannot_see_the_formation(bench: Bench) -> None:
    """MUTATION: point ``TRW_PROJECT_ROOT`` at the worktree instead of the root.

    This is the bug the first WP3 adapter cut shipped. The worktree is made a
    fully valid TRW project with comms ENABLED, so the refusal cannot be blamed
    on a missing config: the caller is refused because membership binds to the
    coordination project root, and this process resolved a different one.
    """
    harness, coord = bench.harness, bench.coord
    worktree = bench.worktrees[_MEMBER_B]
    write_project_root(worktree)

    alpha = harness.ready_member(
        "alpha", session_id=_SESSION_A, project_root=coord.root, cwd=bench.worktrees[_MEMBER_A]
    )
    harness.call_ok(alpha, "trw_init", _join_args(coord, _MEMBER_A, "worker_a_task"))

    # Beta's project root follows its cwd -- the mutation.
    beta = harness.ready_member("beta", session_id=_SESSION_B, project_root=worktree, cwd=worktree)
    refused_init = harness.call_result(beta, "trw_init", _join_args(coord, _MEMBER_B, "worker_b_task"))
    assert refused_init.get("isError"), "a worktree-rooted worker must not be able to join"
    assert coord.formation_id in error_text_of(refused_init)

    # Its comms calls refuse for the formation reason, NOT "comms_disabled".
    for tool, args in (("trw_peers", {"action": "enroll"}), ("trw_inbox", {"action": "fetch"})):
        payload = payload_of(harness.call_result(beta, tool, args))
        assert payload["status"] == "refused"
        assert payload["reason"] == "no_formation"

    # The coordination root saw exactly one worker, and the misdirected one
    # wrote its pin into its own worktree instead.
    assert set(coord.pins()) == {_SESSION_A}
    assert set(json.loads((worktree / ".trw" / "runtime" / "pins.json").read_text(encoding="utf-8"))) == {_SESSION_B}
    assert coord.manifest_members()[_MEMBER_B]["status"] == "pending"


def test_undeclared_third_session_cannot_join_or_use_the_mailbox(bench: Bench) -> None:
    """A third server with a third session id is not a member and cannot become one.

    The formation payload, not the caller, bounds who may claim a slot (WP4-D1):
    the third process is correctly rooted, correctly configured, and still
    refused, because ``worker-c`` was never declared.
    """
    harness, coord = bench.harness, bench.coord
    alpha = harness.ready_member(
        "alpha", session_id=_SESSION_A, project_root=coord.root, cwd=bench.worktrees[_MEMBER_A]
    )
    harness.call_ok(alpha, "trw_init", _join_args(coord, _MEMBER_A, "worker_a_task"))

    gamma = harness.ready_member(
        "gamma", session_id=_SESSION_C, project_root=coord.root, cwd=bench.worktrees["worker-c"]
    )

    # Claiming an UNDECLARED slot is refused, and the refusal names what exists.
    undeclared = harness.call_result(gamma, "trw_init", _join_args(coord, "worker-c", "worker_c_task"))
    assert undeclared.get("isError"), "an undeclared member id must not join"
    text = error_text_of(undeclared)
    assert "worker-c" in text
    assert _MEMBER_A in text and _MEMBER_B in text, "the refusal must list the declared members"

    # Claiming a DECLARED slot from an undeclared session is refused too: the
    # join records this session's pin against a member another session owns.
    stolen = harness.call_result(gamma, "trw_init", _join_args(coord, _MEMBER_A, "worker_c_steal"))
    assert stolen.get("isError"), "a third session must not rebind an already-joined member"

    for tool, args in (("trw_peers", {"action": "enroll"}), ("trw_inbox", {"action": "fetch"})):
        payload = payload_of(harness.call_result(gamma, tool, args))
        assert payload["status"] == "refused"
        assert payload["reason"] == "no_formation"

    # The declared member is untouched and still owned by its own session.
    members = coord.manifest_members()
    assert members[_MEMBER_A]["pin_key"] == _SESSION_A
    assert members[_MEMBER_B]["status"] == "pending"


def test_bounded_wait_on_one_real_process_observes_a_message_sent_by_another(bench: Bench) -> None:
    """PRD-CORE-274 Amendment 01 (FR11), T12: B calls trw_inbox(wait_seconds=10)
    on its OWN real stdio server; ~2s later A sends on ITS OWN real stdio
    server. B's page must arrive before its 10s deadline and carry the message
    — proof that the bounded wait genuinely spans two independent processes,
    not an in-process simulation. The two child processes are already distinct
    OS processes with their own stdio pipes (the harness's own isolation), so
    driving B's blocking call from a background THREAD in this test process
    touches no shared identity — each thread only ever reads/writes the one
    child's own pipe.
    """
    harness, coord = bench.harness, bench.coord
    alpha = harness.ready_member(
        "alpha", session_id=_SESSION_A, project_root=coord.root, cwd=bench.worktrees[_MEMBER_A]
    )
    beta = harness.ready_member("beta", session_id=_SESSION_B, project_root=coord.root, cwd=bench.worktrees[_MEMBER_B])
    harness.call_ok(alpha, "trw_init", _join_args(coord, _MEMBER_A, "worker_a_task"))
    harness.call_ok(beta, "trw_init", _join_args(coord, _MEMBER_B, "worker_b_task"))
    harness.call_ok(alpha, "trw_peers", {"action": "enroll"})
    harness.call_ok(beta, "trw_peers", {"action": "enroll"})

    outcome: dict[str, Any] = {}

    def wait_on_beta() -> None:
        outcome["payload"] = harness.call_ok(beta, "trw_inbox", {"action": "fetch", "wait_seconds": 10})

    started = time.monotonic()
    waiter = threading.Thread(target=wait_on_beta)
    waiter.start()
    time.sleep(2.0)
    receipt = _send(harness, alpha, recipient=_MEMBER_B, request_key="waited", body="arrived-during-wait")
    waiter.join(timeout=15)
    elapsed = time.monotonic() - started

    assert not waiter.is_alive(), "B's wait must have returned, not hung past its own join timeout"
    assert elapsed < 8, "the page must arrive well before B's 10s deadline, not by exhausting it"
    payload = outcome["payload"]
    assert payload["status"] == "ok"
    assert [item["message_id"] for item in payload["items"]] == [receipt["message_id"]]
    assert payload["items"][0]["body"] == "arrived-during-wait"
    # Wait-free payload shape: identical keys to an ordinary zero-wait fetch page.
    assert set(payload) == {"status", "delivery", "items", "next_cursor"}
