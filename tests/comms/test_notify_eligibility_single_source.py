"""Eligibility is decided in admit() only; the fan-out re-catches, never re-decides (ledger RC-009).

``_notify.candidates`` filters on DECLARED ownership and the sender, and reads
nothing about a peer's status; ``admit`` is the one place ``peer.eligible`` is
consulted, and the fan-out merely classifies admit's own refusal as skippable.
The ledger asked for this to be confirmed and made explicit rather than left to
a reader, because two eligibility decisions could silently diverge.

The structural test is the load-bearing one: it fails the moment ``_notify``
starts reading eligibility for itself, which is the shape of the divergence.
"""

from __future__ import annotations

import ast
from pathlib import Path

from tests._formation_test_support import formation_env, make_run_dir, write_pin  # noqa: F401
from tests.comms.test_scoped_notify import COMMS, ScopeScene, scene  # noqa: F401
from trw_mcp.comms import _admission, _notify


def test_only_admission_reads_peer_eligibility() -> None:
    """A second reader of .eligible in the fan-out is the divergence this pins against."""

    def reads_eligible(module: object) -> list[int]:
        source = Path(str(module.__file__)).read_text(encoding="utf-8")
        return [
            node.lineno
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Attribute) and node.attr == "eligible"
        ]

    assert reads_eligible(_admission), "admit() is the authoritative eligibility check"
    assert reads_eligible(_notify) == [], "the fan-out must inherit the decision, not repeat it"


def test_a_scope_that_reaches_an_ineligible_member_skips_it_with_admissions_own_reason(scene: ScopeScene) -> None:
    """The fan-out reports exactly the refusal a direct send to that member would raise."""
    scene.redeclare("impl-3", [f"{COMMS}/_notify.py"])
    _end_membership(scene, "impl-3")

    scene.actor("impl-1")
    result = scene.notify(scope=f"{COMMS}/_notify.py", key="k-mixed")
    assert result["status"] == "ok", result
    assert list(result["recipients"]) == ["impl-2"], "the eligible owner is still served"
    assert result["skipped"] == {"impl-3": "recipient_not_eligible"}

    direct = scene.call("trw_send", {"recipient_member_id": "impl-3", "request_key": "k-direct", "body": "x"})
    assert (direct["status"], direct["reason"]) == ("refused", "recipient_not_eligible"), (
        "one decision procedure, so the direct path and the fan-out must name the same reason"
    )


def test_a_scope_reaching_only_ineligible_members_refuses_rather_than_reporting_success(scene: ScopeScene) -> None:
    scene.redeclare("impl-3", ["trw-mcp/docs/**"])
    _end_membership(scene, "impl-3")
    scene.actor("impl-1")
    refused = scene.notify(scope="trw-mcp/docs/guide.md", key="k-none")
    assert (refused["status"], refused["reason"]) == ("refused", "recipient_unavailable")


def _end_membership(scene: ScopeScene, member: str) -> None:
    """Terminal status through the orchestrator, the only supported way in."""
    from trw_mcp.formation import revise

    revise(
        "release-train",
        scene.formation.orchestrator_run,
        {member: {"status": "abandoned"}},
        trw_dir=scene.formation.trw_dir,
    )
