"""FR01 public refusal matrix: malformed authority cannot mutate persisted peers."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from tests.comms import test_identity_boundary as contract
from trw_mcp import formation

Scene = contract.Scene
scene = contract.scene


#: The exact refusal each malformed-authority case must produce, OBSERVED from the
#: implementation rather than predicted. Asserting only "refused" let the documented
#: distinction between an unreadable manifest and an absent formation go untested, so
#: collapsing those two branches together would have stayed green.
#:
#: `corrupt` and `missing` share `formation_unavailable` legitimately — an unparseable
#: manifest and one whose declared manifest is absent are the same operator-actionable
#: cause — but they are listed per case, not merged, so a future change that made them
#: diverge has to update this map deliberately.
EXPECTED_REASON = {
    "pending": "member_not_eligible",
    "terminal_with_active_peer": "member_not_eligible",
    "empty": "no_matching_member",
    "corrupt": "formation_unavailable",
    "missing": "formation_unavailable",
}


def _storage(root: Path) -> dict[str, str]:
    return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("comms.sqlite3*")}


@pytest.mark.parametrize("pre_enroll", [False, True])
@pytest.mark.parametrize("case", ["pending", "terminal_with_active_peer", "empty", "corrupt", "missing"])
def test_invalid_authority_refuses_without_mutation(scene: Scene, case: str, pre_enroll: bool) -> None:
    if pre_enroll:
        assert scene.call("enroll")["status"] == "ok"
    path = formation.manifest_path_for_run(scene.owner)
    original = path.read_bytes()
    raw = yaml.safe_load(original)
    if case == "pending":
        raw["members"][0]["status"] = "pending"
    elif case == "terminal_with_active_peer":
        raw["members"][0]["status"] = "abandoned"
        other = dict(raw["members"][0])
        other.update(member_id="other", status="active", run_path=str(scene.owner.parent / "other"), pin_key="other")
        raw["members"].append(other)
    elif case == "empty":
        raw["members"] = []
    if case == "corrupt":
        path.write_text("[unterminated")
    elif case == "missing":
        path.unlink()
    else:
        path.write_text(yaml.safe_dump(raw))
    if case in {"pending", "terminal_with_active_peer"}:
        loaded = formation.load(scene.owner, trw_dir=scene.root / ".trw")
        assert loaded is not None
        assert loaded.manifest.members[0].status == raw["members"][0]["status"]
    authority_before = path.read_bytes() if path.exists() else None
    storage_before = _storage(scene.root)
    for action in ("enroll", "list", "heartbeat"):
        result = scene.call(action)
        assert result["status"] == "refused", (case, action, result)
        assert result["reason"] == EXPECTED_REASON[case], (case, action, result)
        assert _storage(scene.root) == storage_before
        assert (path.read_bytes() if path.exists() else None) == authority_before
    # Restoring only fixture authority makes the same public path usable.
    path.write_bytes(original)
    assert scene.call("enroll")["status"] == "ok"


def test_refusal_reasons_do_not_collapse_to_one_cause(scene: Scene, monkeypatch: pytest.MonkeyPatch) -> None:
    """NEGATIVE CONTROL for the reason map: distinct causes must stay distinct.

    Forcing every authority read to fail the same way makes the per-case map
    above unsatisfiable. Without this the map could be satisfied by an
    implementation that reported one reason for everything, which is precisely
    the collapse the map exists to prevent.

    Process-local monkeypatch of the consumer's own binding — never an edit to a
    shared production file, which would disarm the check for every other agent
    working in this checkout.
    """
    from trw_mcp.comms import _identity

    distinct = {EXPECTED_REASON[case] for case in EXPECTED_REASON}
    assert len(distinct) > 1  # positive control: the map is not trivially uniform

    def always_unreadable(*_args: object, **_kwargs: object) -> object:
        raise formation.FormationError("forced unreadable manifest")

    original = _identity.load
    monkeypatch.setattr(_identity, "load", always_unreadable)

    reason = scene.call("enroll")["reason"]

    assert reason == "formation_unavailable"
    # Restore ONLY this attribute. monkeypatch.undo() would also revert the
    # fixture's own patches — including the one enabling comms — and the call
    # below would come back "disabled", which looks like a pass for the wrong
    # reason.
    monkeypatch.setattr(_identity, "load", original)
    assert scene.call("enroll")["status"] == "ok"
