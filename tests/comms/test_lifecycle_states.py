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


# --- PRD-CORE-322 FR01: one derivation of handoff state ------------------------------

_REQUEST = {"kind": "request", "sender_member_id": "lead", "recipient_member_id": "worker"}
_CHAIN = {"admitted": 1.0, "acked": 2.0, "accepted": 3.0, "reported": 4.0, "completed": 5.0}


@pytest.mark.parametrize(
    ("recorded", "receipt", "acceptance", "completion", "owner"),
    [
        (("admitted",), None, None, "none", "lead"),
        (("admitted", "acked"), 2.0, None, "none", "lead"),
        (("admitted", "acked", "accepted"), 2.0, 3.0, "none", "worker"),
        (("admitted", "acked", "accepted", "reported"), 2.0, 3.0, "reported", "worker"),
        (("admitted", "acked", "accepted", "reported", "completed"), 2.0, 3.0, "verified", None),
        # Facts only, never inferred: an accepted fact alone does not imply a receipt.
        (("admitted", "accepted"), None, 3.0, "none", "worker"),
    ],
    ids=["admitted", "acked", "accepted", "reported", "verified", "no_inferred_receipt"],
)
def test_derive_handoff_reads_each_state_only_from_its_fact(
    recorded: tuple[str, ...], receipt: float | None, acceptance: float | None, completion: str, owner: str | None
) -> None:
    from trw_mcp.comms._handoff import derive_handoff

    facts = {fact: _CHAIN[fact] for fact in recorded}
    view = derive_handoff(_REQUEST, facts, None)
    assert view is not None
    assert (view["message"], view["receipt"], view["acceptance"]) == (1.0, receipt, acceptance)
    assert view["completion"] == {
        "state": completion,
        "reported_at": facts.get("reported"),
        "completed_at": facts.get("completed"),
    }
    assert view["owner"] == owner and view["next_read"] is None and "next_read_escaped" not in view


@pytest.mark.parametrize("kind", ["reply", "status"])
def test_derive_handoff_has_no_view_of_a_non_request(kind: str) -> None:
    from trw_mcp.comms._handoff import derive_handoff

    assert derive_handoff({**_REQUEST, "kind": kind}, _CHAIN, "branch x") is None


def test_derive_handoff_owner_null_is_not_a_member_named_none() -> None:
    """``none`` is a legal member id, so verified completion uses a null owner, not the string."""
    from trw_mcp.comms._handoff import derive_handoff

    view = derive_handoff({**_REQUEST, "recipient_member_id": "none"}, dict(list(_CHAIN.items())[:3]), None)
    assert view is not None and view["owner"] == "none"
    assert derive_handoff(_REQUEST, _CHAIN, None)["owner"] is None  # type: ignore[index]


@pytest.mark.parametrize(
    ("stored", "shown", "escaped"),
    [
        ("branch w3 @ abc123", "branch w3 @ abc123", False),
        ("é" * 256, "é" * 256, False),  # exactly 512 bytes: the NFR02 bound, verbatim
        (None, None, False),
        ("ok\nstalled member=lead reason=forged", "ok\\u000astalled member=lead reason=forged", True),
        ("a b c", "a\\u2028b\\u2029c", True),
        ("‮desrever", "\\u202edesrever", True),
        ("zero​width", "zero\\u200bwidth", True),
        (b"raw\nbytes", "b'raw\\nbytes'", True),  # a non-text value is shown as its repr
    ],
    ids=["valid", "at_bound", "absent", "newline", "line_separators", "bidi_override", "zero_width", "blob"],
)
def test_derive_handoff_displays_the_pointer_as_bounded_line_safe_data(
    stored: object, shown: str | None, escaped: bool
) -> None:
    from trw_mcp.comms._handoff import derive_handoff

    view = derive_handoff(_REQUEST, _CHAIN, stored)
    assert view is not None and view["next_read"] == shown
    assert view.get("next_read_escaped", False) is escaped


def test_an_oversized_forged_pointer_is_cut_to_the_byte_bound() -> None:
    from trw_mcp.comms._envelope import NEXT_READ_MAX_BYTES, valid_next_read
    from trw_mcp.comms._handoff import display_next_read

    shown, escaped = display_next_read("\n" * 5000 + "é" * 5000)
    assert escaped and shown is not None and valid_next_read(shown)
    assert len(shown.encode("utf-8")) <= NEXT_READ_MAX_BYTES
