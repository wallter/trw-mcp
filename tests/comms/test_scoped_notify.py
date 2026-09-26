"""PRD-CORE-276 scoped notify, driven through the real registered tools.

Three members with disjoint declared ownership, because the property under test
is *selection*: a notify must reach the member that declared the ground and no
one else. A two-member formation cannot distinguish "reached the right peer"
from "reached the only peer".
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests._formation_test_support import (  # noqa: F401
    FormationFixture,
    formation_env,
    make_run_dir,
    open_slot,
    write_pin,
)
from tests.comms.conftest import call_peers, enable_comms

COMMS = "trw-mcp/src/trw_mcp/comms"
PINS = {"impl-1": "pin-a", "impl-2": "pin-b", "impl-3": "pin-c"}


@dataclass
class ScopeScene:
    formation: FormationFixture
    server: FastMCP
    config: Any
    monkeypatch: pytest.MonkeyPatch

    def actor(self, member: str) -> None:
        self.monkeypatch.setenv("TRW_SESSION_ID", PINS[member])

    def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = asyncio.run(self.server.call_tool(tool, arguments))
        assert isinstance(result.structured_content, dict)
        return result.structured_content

    def notify(self, scope: str = f"{COMMS}/_store.py", key: str = "k1", body: str = "heads up") -> dict[str, Any]:
        return self.call("trw_send", {"scope": scope, "request_key": key, "body": body})

    def inbox_of(self, member: str) -> list[str]:
        """The bodies a member can actually read. Fetching is non-consuming."""
        self.actor(member)
        return [item["body"] for item in self.call("trw_inbox", {"action": "fetch"})["items"]]

    def redeclare(self, member: str, owned_paths: list[str]) -> None:
        """Rewrite one member's declared ownership in the manifest the snapshot reads.

        Declared ownership is the only thing that decides who a scope matches, so
        this is the honest way to move a member into or out of a match between two
        calls. The replacement globs must stay non-overlapping: the manifest model
        re-validates that on every load, so an overlapping edit would fail the read
        rather than exercise the retry path.
        """
        from ruamel.yaml import YAML

        yaml = YAML()
        path = self.formation.manifest_path()
        with path.open(encoding="utf-8") as handle:
            data = yaml.load(handle)
        for entry in data["members"]:
            if entry["member_id"] == member:
                entry["owned_paths"] = list(owned_paths)
        with path.open("w", encoding="utf-8") as handle:
            yaml.dump(data, handle)

    def set_lease(self, member: str, *, live: bool) -> None:
        """Expire or restore a member's endpoint lease without re-enrolling it."""
        offset = 86400.0 if live else 0.0
        self.rows(
            "UPDATE endpoints SET lease_expires_at=last_seen_at+? WHERE member_id=?",
            (offset, member),
        )

    def rows(self, sql: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        from trw_mcp.comms import _store

        conn = _store.sqlite3.connect(_store.database_path(self.formation.manifest_path()))
        try:
            result = conn.execute(sql, parameters).fetchall()
            conn.commit()
            return result
        finally:
            conn.close()


@pytest.fixture
def scene(
    formation_env: FormationFixture,
    comms_server: FastMCP,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> ScopeScene:
    from trw_mcp.formation import create, join

    config = enable_comms(monkeypatch, **getattr(request, "param", {}))
    runs_root = formation_env.trw_dir / "runs"
    runs: dict[str, Path] = dict(formation_env.member_runs)
    runs["impl-3"] = make_run_dir(runs_root, "impl-3")
    payload = formation_env.payload(
        members=[
            open_slot("impl-1", "claude-code", role="implementer", owned_paths=["src/alpha/**"]),
            open_slot("impl-2", role="implementer", owned_paths=[f"{COMMS}/**"]),
            open_slot("impl-3", "antigravity-cli", role="reviewer", owned_paths=["trw-mcp/docs/**"]),
        ]
    )
    formation_id = create(formation_env.orchestrator_run, payload, trw_dir=formation_env.trw_dir).formation_id
    for member, pin in PINS.items():
        join(formation_id, member, runs[member], pin_key=pin, trw_dir=formation_env.trw_dir)
        write_pin(formation_env, pin, runs[member])
    instance = ScopeScene(formation_env, comms_server, config, monkeypatch)
    for member in ("impl-2", "impl-3"):
        instance.actor(member)
        assert call_peers(comms_server, "enroll")["status"] == "ok"
    instance.actor("impl-1")
    assert call_peers(comms_server, "enroll")["status"] == "ok"
    return instance


def test_scope_reaches_the_declared_owner_and_nobody_else(scene: ScopeScene) -> None:
    """FR01. The non-owner's inbox is the oracle, not the sender's response."""
    result = scene.notify()
    assert result["status"] == "ok", result
    assert list(result["recipients"]) == ["impl-2"]
    assert result["skipped"] == {}

    scene.actor("impl-2")
    owner_inbox = scene.call("trw_inbox", {"action": "fetch"})
    assert [item["body"] for item in owner_inbox["items"]] == ["heads up"]
    scene.actor("impl-3")
    assert scene.call("trw_inbox", {"action": "fetch"})["items"] == []


def test_a_scope_matching_no_declaration_refuses_rather_than_succeeding_silently(scene: ScopeScene) -> None:
    """FR03. Reaching nobody is a refusal; a cheerful empty success is a lie."""
    result = scene.notify(scope="svc/app/main.py")
    assert result["reason"] == "scope_matches_no_peer"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


@pytest.mark.parametrize(
    "scope",
    ["", "/etc/passwd", "../outside", "a/../../b", "C:/windows", "src\\alpha", "*", "**", f"{COMMS}/*.py", "x\ty"],
)
def test_every_malformed_scope_is_refused_by_shape(scene: ScopeScene, scope: str) -> None:
    """FR02. A wildcard is refused in the parser, not bounded after resolution."""
    assert scene.notify(scope=scope)["reason"] == "invalid_scope"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


def test_addressing_is_exclusive(scene: ScopeScene) -> None:
    """FR02. Both or neither is ambiguous; picking a winner hides the other."""
    both = scene.call(
        "trw_send",
        {"scope": f"{COMMS}/_store.py", "recipient_member_id": "impl-2", "request_key": "k", "body": "b"},
    )
    assert both["reason"] == "ambiguous_addressing"
    neither = scene.call("trw_send", {"request_key": "k", "body": "b"})
    assert neither["reason"] == "ambiguous_addressing"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


@pytest.mark.parametrize("scene", [{"comms_scope_max_recipients": 1}], indirect=True)
def test_the_ceiling_refuses_before_a_single_row_is_written(scene: ScopeScene) -> None:
    """FR03 and NFR02. The bound is configured, and it is checked before inserting."""
    # impl-2 and impl-3 both declare ground under "trw-mcp"; the ceiling is one.
    assert scene.notify(scope="trw-mcp", key="k4")["reason"] == "scope_too_broad"
    # The same scope narrowed to one owner is admitted, so the refusal above is
    # the ceiling talking and not the scope failing to resolve.
    assert scene.notify(key="k5")["status"] == "ok"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]


def test_exact_retry_returns_the_same_receipts_and_writes_nothing(scene: ScopeScene) -> None:
    """FR04 and FR08. Idempotency is a property of the whole fan-out, not of one row.

    The unchanged case is where ``not_delivered_to`` must be EMPTY and still
    present: a sender that reads the key only when something is wrong cannot
    tell "nothing withheld" from "this build does not report withholding".
    """
    first = scene.notify()
    again = scene.notify()
    assert again["recipients"] == first["recipients"]
    assert again["not_delivered_to"] == {}
    assert first["not_delivered_to"] == {}
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]
    assert scene.rows("SELECT charge FROM groups") == [(1,)]
    assert scene.inbox_of("impl-2") == ["heads up"], "a retry must not deliver a second copy"


def test_the_same_key_with_a_changed_body_refuses(scene: ScopeScene) -> None:
    """FR04. A key names a message; reusing it for another one is a conflict."""
    assert scene.notify()["status"] == "ok"
    assert scene.notify(body="something else")["reason"] == "idempotency_conflict"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]


def test_a_retry_whose_scope_no_longer_covers_the_original_recipient_refuses(scene: ScopeScene) -> None:
    """FR04. Narrowing the scope under an existing key is a different message."""
    assert scene.notify()["status"] == "ok"
    narrowed = scene.notify(scope="trw-mcp/docs/README.md")
    assert narrowed["reason"] == "idempotency_conflict"


@pytest.mark.parametrize("scene", [{"comms_group_row_limit": 1}], indirect=True)
def test_one_saturated_recipient_rolls_the_whole_notify_back(scene: ScopeScene) -> None:
    """FR05. Partial fan-out is never committed.

    The scope MUST reach two members for this to mean anything. An external
    review caught the earlier version of this test addressing a single
    recipient, where the refusal happened before any insert and atomicity across
    a fan-out was never exercised at all -- it would have passed with the
    rollback deleted. Here impl-2 is admitted first and impl-3 trips the group
    lifetime cap, so the assertion that ZERO rows survive is a real claim about
    the first admission being undone.
    """
    result = scene.notify(scope="trw-mcp")
    assert result["reason"] == "group_admission_limit", result
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]
    assert scene.rows("SELECT charge FROM groups") == [(0,)]


def test_an_owner_that_is_not_listening_is_reported_not_hidden(scene: ScopeScene) -> None:
    """FR06. Silence and absence are different facts and must read differently."""
    scene.rows("UPDATE endpoints SET lease_expires_at=last_seen_at WHERE member_id='impl-2'")
    result = scene.notify()
    assert result["reason"] == "recipient_unavailable", result
    assert result["reason"] != "scope_matches_no_peer"


@pytest.mark.parametrize("key", ["a\x00b", "a\x1fb"])
def test_a_direct_request_key_may_not_carry_a_control_character(scene: ScopeScene, key: str) -> None:
    """FR07. Ordinary keys refuse control characters, the derived namespace's separator included."""
    from trw_mcp.comms._scope import is_shard_key

    plain = scene.call("trw_send", {"recipient_member_id": "impl-2", "request_key": key, "body": "b"})
    assert plain["reason"] == "invalid_request_key"
    assert not is_shard_key(key)


def test_a_direct_send_cannot_forge_a_scoped_notify_delivery_to_an_offline_peer(scene: ScopeScene) -> None:
    """Release-verify S3. The derived namespace is refused at the direct boundary, not reserved by convention.

    Before this, a direct send under ``shard_key("k", scope, peer)`` was stored
    for an offline peer (FR13 durability), and a later scoped notify with key
    ``"k"`` found that row, took it for a retry of its own shard, and returned a
    receipt although the peer was not listening.
    """
    from trw_mcp.comms._scope import shard_key

    scope = f"{COMMS}/_store.py"
    scene.set_lease("impl-2", live=False)
    forged = scene.call(
        "trw_send",
        {"recipient_member_id": "impl-2", "request_key": shard_key("k", scope, "impl-2"), "body": "b"},
    )
    assert (forged["status"], forged["reason"]) == ("refused", "invalid_request_key"), forged
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]

    notified = scene.notify(scope=scope, key="k", body="b")
    assert notified["reason"] == "recipient_unavailable", notified
    assert "recipients" not in notified


def test_one_key_cannot_name_a_direct_message_and_a_notify_at_once(scene: ScopeScene) -> None:
    """A request key names ONE message from one sender, in either addressing mode.

    Found by an external review: direct sends store the raw key and notifies
    store derived ones, so before this the same key could name two live
    messages, and a retry of either would find only its own.
    """
    assert (
        scene.call("trw_send", {"recipient_member_id": "impl-2", "request_key": "k1", "body": "first"})["status"]
        == "ok"
    )
    assert scene.notify(key="k1", body="second")["reason"] == "idempotency_conflict"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]


def test_the_collision_is_refused_from_the_other_direction_too(scene: ScopeScene) -> None:
    """Notify first, then a direct send under the same key: still one name, one message."""
    assert scene.notify(key="k2", body="first")["status"] == "ok"
    direct = scene.call("trw_send", {"recipient_member_id": "impl-3", "request_key": "k2", "body": "second"})
    assert direct["reason"] == "idempotency_conflict", direct
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]


def test_the_same_key_with_a_different_scope_refuses_even_when_it_reaches_the_same_peer(
    scene: ScopeScene,
) -> None:
    """FR04. A retry is the same message, and the scope is part of what it is.

    Found by an external review: two scopes that resolve to the same recipient
    were treated as a retry, so the second call was handed the first call's
    receipts and reported success for a scope nothing had been sent about.
    """
    first = scene.notify(scope=f"{COMMS}/_store.py", key="k9")
    assert first["status"] == "ok"
    second = scene.notify(scope=f"{COMMS}/_admission.py", key="k9")
    assert second["reason"] == "idempotency_conflict", second
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]


def test_a_notify_never_addresses_its_own_sender(scene: ScopeScene) -> None:
    """FR01. The sender declares ground too, and must not be reached by its own notify.

    ``src/alpha/**`` is impl-1's own declaration and nobody else's, so the only
    way this scope can resolve to anyone is by including the sender. Refusing
    with "nobody" is therefore the evidence: without the exclusion the sender is
    enrolled and live, so the notify would succeed.
    """
    assert scene.notify(scope="src/alpha/thing.py")["reason"] == "scope_matches_no_peer"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(0,)]


def test_a_bare_directory_declaration_still_reaches_its_owner(scene: ScopeScene) -> None:
    """The declaration an author is most likely to write is the one enforcement accepts.

    ``_ownership`` treats ``a/b`` as owning everything beneath it precisely
    because forgetting ``/**`` is the likely authoring mistake. Addressing must
    agree: a plain fnmatch does NOT, and under it this member is silently never
    notified about ground it owns. Deleting the reuse of ``declaration_covers``
    from ``_scope.intersects`` makes this fail.
    """
    from trw_mcp.comms._scope import intersects

    assert intersects(f"{COMMS}/_store.py", COMMS), "bare directory declaration must cover files beneath it"
    assert intersects(f"{COMMS}/_store.py", f"{COMMS}/**")
    assert not intersects("svc/app.py", COMMS)


def test_addressing_and_enforcement_answer_ownership_the_same_way(scene: ScopeScene) -> None:
    """One definition of "does this declaration cover this path", not two.

    A disagreement here is worse than a bug in either one alone: the notify
    would reach a member the commit boundary does not consider the owner, or
    skip the member it does.
    """
    from trw_mcp.comms._scope import intersects
    from trw_mcp.formation import declaration_covers

    cases = [
        (f"{COMMS}/_store.py", COMMS),
        (f"{COMMS}/_store.py", f"{COMMS}/**"),
        ("src/alpha/x.py", "src/alpha"),
        ("src/alphabet/x.py", "src/alpha"),
        ("svc/app.py", COMMS),
    ]
    for path, glob in cases:
        expected = declaration_covers(glob, path) or glob.startswith(f"{path}/")
        assert intersects(path, glob) is expected, (path, glob)


def test_a_retry_whose_match_grew_replays_and_names_the_new_owner(scene: ScopeScene) -> None:
    """FR04 and FR08. A frozen fan-out, and the growth it excludes stated out loud.

    impl-3 declares ground outside ``trw-mcp`` for the first call, then declares
    ground inside it. The retry therefore matches a strictly larger set than the
    one it admitted. It replays rather than refusing -- a sender that lost the
    response must still be able to learn whether its first send landed, and a
    peer declaring a matching path in between is not the sender's doing -- and it
    names impl-3 under ``not_delivered_to`` so the sender knows to address it
    under a fresh key. The oracle is impl-3's EMPTY inbox, not the response.
    """
    scene.redeclare("impl-3", ["svc/**"])
    first = scene.notify(scope="trw-mcp", key="grow")
    assert list(first["recipients"]) == ["impl-2"], first
    assert first["not_delivered_to"] == {}

    scene.redeclare("impl-3", ["trw-mcp/docs/**"])
    scene.actor("impl-1")
    again = scene.notify(scope="trw-mcp", key="grow")
    assert again["status"] == "ok", again
    assert again["recipients"] == first["recipients"], "the retained fan-out is replayed verbatim"
    assert again["not_delivered_to"] == {"impl-3": "not_in_retained_notify"}, again

    assert scene.inbox_of("impl-2") == ["heads up"]
    assert scene.inbox_of("impl-3") == [], "a grown match must not widen the fan-out"
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(1,)]
    assert scene.rows("SELECT charge FROM groups") == [(1,)]


def test_a_retry_whose_match_contracted_refuses(scene: ScopeScene) -> None:
    """FR04. Losing a recipient is a different message, and must not replay as one.

    Growth is replayable because everyone who was told is still told. Contraction
    is not: the retained set contains a member the scope no longer reaches, so
    answering with the first call's receipts would report a fan-out the caller
    can no longer reproduce. The scope string is IDENTICAL here, so the refusal
    is the subset rule talking and not the scope-digest guard.
    """
    first = scene.notify(scope="trw-mcp", key="shrink")
    assert sorted(first["recipients"]) == ["impl-2", "impl-3"], first

    scene.redeclare("impl-3", ["svc/**"])
    scene.actor("impl-1")
    again = scene.notify(scope="trw-mcp", key="shrink")
    assert again["reason"] == "idempotency_conflict", again
    assert scene.rows("SELECT COUNT(*) FROM admissions") == [(2,)]
    assert scene.inbox_of("impl-2") == ["heads up"]
    assert scene.inbox_of("impl-3") == ["heads up"]


def test_an_owner_that_was_offline_is_never_delivered_under_that_key(scene: ScopeScene) -> None:
    """FR06 and FR08. The most likely real growth: a peer that was not listening.

    impl-3 owns matching ground throughout, so it is MATCHED on both calls; it
    simply could not be admitted on the first. Once its lease is live the retry
    still withholds -- that message was admitted without it -- and says so. The
    remedy the response implies is a fresh key, and that remedy is asserted to
    work rather than assumed.
    """
    scene.set_lease("impl-3", live=False)
    first = scene.notify(scope="trw-mcp", key="offline")
    assert list(first["recipients"]) == ["impl-2"], first
    assert first["skipped"] == {"impl-3": "recipient_unavailable"}
    assert first["not_delivered_to"] == {}

    scene.set_lease("impl-3", live=True)
    again = scene.notify(scope="trw-mcp", key="offline")
    assert again["status"] == "ok", again
    assert again["not_delivered_to"] == {"impl-3": "not_in_retained_notify"}, again
    assert scene.inbox_of("impl-3") == [], "coming back does not retroactively join a fan-out"

    scene.actor("impl-1")
    fresh = scene.notify(scope="trw-mcp", key="offline-2", body="second try")
    assert sorted(fresh["recipients"]) == ["impl-2", "impl-3"], fresh
    assert fresh["not_delivered_to"] == {}
    assert scene.inbox_of("impl-3") == ["second try"], "a fresh key is the remedy and it works"
