"""Ledger RC-006: one vocabulary per lifecycle, and illegal moves refused; RC-001 codec pinned directly."""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms import _paging
from trw_mcp.comms._envelope import MESSAGE_STATES, MILESTONE_FACTS, TERMINAL_MESSAGE_STATES, MessageState
from trw_mcp.formation import CandidateState, FormationError, announce_candidate, set_candidate_state


def test_message_states_are_one_closed_vocabulary() -> None:
    assert MESSAGE_STATES == {"pending", "acked", "expired"}
    assert TERMINAL_MESSAGE_STATES == MESSAGE_STATES - {MessageState.PENDING.value}
    assert set(MILESTONE_FACTS) >= TERMINAL_MESSAGE_STATES, "every terminal state has its milestone fact"


def _inbox(scene: SendScene, **arguments: Any) -> dict[str, Any]:
    result = asyncio.run(scene.server.call_tool("trw_inbox", arguments))
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_a_message_leaves_pending_exactly_once_and_never_moves_between_terminal_states(scene: SendScene) -> None:
    """pending -> acked and pending -> expired are the only moves. Expiry skips an acked
    row, and an expired row cannot be acknowledged: the whole batch is refused unchanged."""
    acked = scene.send("to-ack")["receipt"]["message_id"]
    lapsing = scene.send("to-expire")["receipt"]["message_id"]
    scene.actor("impl-2")
    assert _inbox(scene, action="ack", message_ids=[acked])["status"] == "ok"
    scene.rows("UPDATE groups SET group_time = group_time + 10000000")

    assert _inbox(scene, action="status")["status"] == "ok"  # any operation runs expiry
    states = dict(scene.rows("SELECT message_id, state FROM admissions"))
    assert states == {acked: MessageState.ACKED.value, lapsing: MessageState.EXPIRED.value}

    refused = _inbox(scene, action="ack", message_ids=[lapsing])
    assert refused["reason"] == "ack_not_authorized", refused
    assert dict(scene.rows("SELECT message_id, state FROM admissions")) == states
    facts = scene.rows("SELECT message_id, fact FROM milestones WHERE fact IN ('acked', 'expired') ORDER BY fact")
    assert facts == [(acked, "acked"), (lapsing, "expired")], "one terminal fact per message"


def _candidate(tmp_path: Path) -> str:
    return announce_candidate(
        tmp_path, pin_key="p", run_path=tmp_path, worktree=None, client="codex", ttl_seconds=600
    ).candidate_id


@pytest.mark.parametrize(
    ("path", "illegal"),
    [
        ([CandidateState.REVOKED], CandidateState.ACTIVE),
        ([CandidateState.WITHDRAWN], CandidateState.ADMITTED),
        ([CandidateState.ADMITTED, CandidateState.JOINING, CandidateState.PICKED_UP], CandidateState.JOINING),
        ([], CandidateState.PICKED_UP),
        ([], CandidateState.JOINING),
    ],
)
def test_illegal_candidate_transitions_are_refused(
    tmp_path: Path, path: list[CandidateState], illegal: CandidateState
) -> None:
    handle = _candidate(tmp_path)
    for state in path:
        set_candidate_state(tmp_path, handle, state)
    with pytest.raises(FormationError, match="cannot move"):
        set_candidate_state(tmp_path, handle, illegal)


def test_legal_candidate_lifecycle_and_idempotent_reentry(tmp_path: Path) -> None:
    handle = _candidate(tmp_path)
    for state in (
        CandidateState.ADMITTED,
        CandidateState.ACTIVE,  # released before pick-up
        CandidateState.ADMITTED,
        CandidateState.JOINING,
        CandidateState.JOINING,  # a resumed stage 1
        CandidateState.PICKED_UP,
        CandidateState.PICKED_UP,  # a repeated completion
    ):
        found = set_candidate_state(tmp_path, handle, state)
        assert found is not None and found.state == state.value
    with pytest.raises(ValueError):
        set_candidate_state(tmp_path, handle, "resurrected")


def test_the_shared_cursor_codec_accepts_only_its_canonical_spelling() -> None:
    cursor = _paging.encode_cursor("scope", 7)
    assert _paging.decode_cursor(cursor, max_chars=256, arity=2) == ["scope", 7]
    raw = base64.urlsafe_b64decode(cursor)
    respelled = base64.urlsafe_b64encode(raw.replace(b",", b", ")).decode("ascii")  # same JSON, other spelling
    assert _paging.decode_cursor(respelled, max_chars=256, arity=2) is None
    assert _paging.decode_cursor(cursor + "=", max_chars=256, arity=2) is None, "re-padding is not canonical"
    assert _paging.decode_cursor(cursor, max_chars=256, arity=3) is None, "wrong arity"
    assert _paging.decode_cursor(cursor, max_chars=len(cursor) - 1, arity=2) is None, "over length"
    wrong_version = base64.urlsafe_b64encode(b'[2,"scope",7]').decode("ascii")
    assert _paging.decode_cursor(wrong_version, max_chars=256, arity=2) is None
