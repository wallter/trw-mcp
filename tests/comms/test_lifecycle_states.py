"""Ledger RC-006: one vocabulary per lifecycle, and illegal moves refused; RC-001 codec pinned directly."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from trw_mcp.comms import _paging
from trw_mcp.comms._envelope import MESSAGE_STATES, MILESTONE_FACTS, TERMINAL_MESSAGE_STATES, MessageState
from trw_mcp.formation import CandidateState, FormationError, announce_candidate, set_candidate_state


def test_message_states_are_one_closed_vocabulary() -> None:
    assert MESSAGE_STATES == {"pending", "acked", "expired"}
    assert TERMINAL_MESSAGE_STATES == MESSAGE_STATES - {MessageState.PENDING.value}
    assert set(MILESTONE_FACTS) >= TERMINAL_MESSAGE_STATES, "every terminal state has its milestone fact"


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
