"""PRD-CORE-274-FR18 compact guidance: taught once per change, never repeated (lead board 709)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests._formation_test_support import formation_env  # noqa: F401
from tests.comms.test_policy import SendScene, scene  # noqa: F401
from trw_mcp.comms import _guidance


def _call(scene: SendScene, tool: str, **args: Any) -> dict[str, Any]:
    import asyncio

    result = asyncio.run(scene.server.call_tool(tool, args))
    assert isinstance(result.structured_content, dict)
    return result.structured_content


def test_repeated_ordinary_calls_do_not_repeat_instructions(scene: SendScene) -> None:
    _guidance._reset_for_test()  # the scene's own enroll already taught; start as a fresh process
    scene.actor("impl-2")
    first = _call(scene, "trw_inbox")
    assert first["state"] == "enrolled" and "guidance" in first, "a new process teaches the protocol once"
    later = [_call(scene, "trw_inbox") for _ in range(3)]
    assert all(page == later[0] for page in later)
    assert not {"state", "guidance_version", "guidance"} & set(later[0]), later[0]
    assert later[0]["status"] == "ok" and later[0]["items"] == []


def test_a_reconnect_teaches_exactly_once_more(scene: SendScene) -> None:
    _guidance._reset_for_test()
    scene.actor("impl-2")
    _call(scene, "trw_inbox")
    assert "guidance" not in _call(scene, "trw_inbox")
    _guidance._reset_for_test()  # a new server process has no memory of what it taught
    assert "guidance" in _call(scene, "trw_inbox")
    assert "guidance" not in _call(scene, "trw_inbox")


def test_a_state_change_teaches_the_new_state(scene: SendScene) -> None:
    from trw_mcp import formation

    scene.actor("impl-2")
    _call(scene, "trw_inbox")
    formation.revise(
        "release-train",
        scene.formation.orchestrator_run,
        {"impl-2": {"status": "abandoned"}},
        trw_dir=scene.formation.trw_dir,
    )
    terminal = _call(scene, "trw_inbox")
    assert (terminal["state"], terminal["reason"]) == ("terminal", "member_not_eligible")
    assert "guidance" in terminal and "membership has ended" in terminal["guidance"]


@pytest.mark.parametrize("scene", [{"comms_wait_max_seconds": 7}], indirect=True)
def test_every_refusal_names_its_next_action_and_the_bound_it_enforced(scene: SendScene) -> None:
    scene.actor("impl-2")
    refused = _call(scene, "trw_inbox", wait_seconds=99)
    assert (refused["status"], refused["reason"]) == ("refused", "invalid_wait_seconds")
    assert refused["detail"] == "use a wait within the bound (bound comms_wait_max_seconds=7)"
    assert refused["state"] == "enrolled" and refused["guidance_version"] == _guidance.GUIDANCE_VERSION


def test_the_refusal_vocabulary_is_closed_over_next_actions() -> None:
    from trw_mcp.comms._endpoints import EndpointRefusal
    from trw_mcp.comms._identity import IdentityRefusal
    from trw_mcp.comms._policy import REFUSALS
    from trw_mcp.comms._store import StoreRefusal
    from trw_mcp.models.config import TRWConfig

    reasons = set(REFUSALS) | {r.value for r in (*IdentityRefusal, *StoreRefusal, *EndpointRefusal)}
    reasons |= {"admission_revoked", "candidate_registry_full", "context_isolation_disabled"}
    reasons -= {"endpoint_replaced_by_newer_incarnation"}  # carries its own recovery text upstream
    generic = {r for r in reasons if _guidance.refusal_detail(r, TRWConfig()) == "Peer operation refused."}
    assert not generic, f"refusals without a next action: {sorted(generic)}"


def test_guidance_fits_its_byte_budget_for_every_state() -> None:
    from trw_mcp.models.config import TRWConfig

    config = TRWConfig(comms_wait_max_seconds=300, comms_fetch_max_items=64)
    for state in _guidance.STATES:
        assert len(_guidance.guidance_text(state, config).encode()) <= _guidance.GUIDANCE_MAX_BYTES, state
    sizes = {state: len(json.dumps(_guidance.guidance_text(state, config))) for state in _guidance.STATES}
    assert max(sizes.values()) <= _guidance.GUIDANCE_MAX_BYTES + 8


def test_state_reports_what_the_call_established_not_what_its_status_implies(scene: SendScene) -> None:
    """C review M5: a joined member may send without enrolling; it must not be told it is enrolled."""
    _guidance._reset_for_test()
    scene.actor("impl-1")  # joined by the scene, never enrolled
    sent = scene.send("k", "hello")
    assert sent["status"] == "ok"
    assert sent["state"] == "joined" and "enroll" in sent["guidance"]
    enrolled = _call(scene, "trw_inbox", action="enroll")
    assert enrolled["state"] == "enrolled" and "guidance" in enrolled


def test_no_session_key_means_no_steady_state_decoration(scene: SendScene, monkeypatch: pytest.MonkeyPatch) -> None:
    """C review S2: without a pin key there is no memory; ordinary successes stay undecorated."""
    from trw_mcp.comms import _guidance as guidance

    config = scene.config
    for _ in range(2):
        plain = guidance.finish(
            {"status": "ok", "items": []}, key=None, action="fetch", config=config, observed="enrolled"
        )
        assert plain == {"status": "ok", "items": []}
    refused = guidance.finish(
        {"status": "refused", "reason": "invalid_cursor", "detail": "x"}, key=None, action="fetch", config=config
    )
    assert refused["detail"] == "drop the cursor and fetch or list afresh" and "guidance" not in refused


def test_the_refusal_registry_is_the_one_source_for_buckets_and_identity() -> None:
    """Ledger N13: every stored refusal bucket and the identity set come from one registry."""
    from trw_mcp.comms import _refusals
    from trw_mcp.comms._policy import REFUSALS as STORED

    buckets = {spec.persisted_as for spec in _refusals.REFUSALS.values() if spec.persisted_as}
    assert buckets <= STORED, "a persisted bucket must be in the stored refusal vocabulary"
    assert _refusals.persisted_bucket("invalid_wait_seconds") == "invalid_inbox_arguments"
    assert _refusals.persisted_bucket("sender_rate_limit") == "sender_rate_limit"
    assert _refusals.IDENTITY_REASONS == {"no_formation", "no_matching_member", "worktree_record_unbound"}
    assert _refusals.detail("never-heard-of-it") == _refusals.GENERIC_DETAIL


def test_the_guidance_memory_is_bounded_and_evicts_oldest_first(config: Any) -> None:
    """Ledger N10: an unbounded cache keyed by caller-supplied identity is a leak.

    Eviction costs the evicted caller one repeated guidance block and nothing
    else, which is why oldest-first is safe for a dedup hint.
    """
    from trw_mcp.comms import _guidance

    _guidance._reset_for_test()
    for index in range(_guidance._LAST_MAX_KEYS + 10):
        _guidance.finish({"status": "ok"}, key=f"pin-{index}", action="list", config=config, observed="enrolled")
    assert len(_guidance._LAST) == _guidance._LAST_MAX_KEYS
    assert "pin-0" not in _guidance._LAST, "the oldest key is evicted first"
    assert f"pin-{_guidance._LAST_MAX_KEYS + 9}" in _guidance._LAST

    # The evicted caller is taught again; the retained one is not.
    revisit = _guidance.finish({"status": "ok"}, key="pin-0", action="list", config=config, observed="enrolled")
    assert "guidance" in revisit
    retained = _guidance.finish(
        {"status": "ok"}, key=f"pin-{_guidance._LAST_MAX_KEYS + 9}", action="list", config=config, observed="enrolled"
    )
    assert "guidance" not in retained and "state" not in retained


def test_a_busy_key_is_not_evicted_by_newer_quiet_ones(config: Any) -> None:
    """move_to_end on write: recency, not insertion order, decides who survives."""
    from trw_mcp.comms import _guidance

    _guidance._reset_for_test()
    _guidance.finish({"status": "ok"}, key="busy", action="list", config=config, observed="enrolled")
    for index in range(_guidance._LAST_MAX_KEYS):
        # The busy caller changes state each round, so it keeps writing.
        state = "joined" if index % 2 else "enrolled"
        _guidance.finish({"status": "ok"}, key="busy", action="list", config=config, observed=state)
        _guidance.finish({"status": "ok"}, key=f"quiet-{index}", action="list", config=config, observed="enrolled")
    assert "busy" in _guidance._LAST
