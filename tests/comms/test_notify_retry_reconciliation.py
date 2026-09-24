"""Retry semantics for a scoped notify, tested without delivering anything (ledger RC-007).

``reconcile_retry`` answers one question — is this key a fresh fan-out, an exact
retry, or a different message wearing the same name — and was inlined among the
reachability and fan-out code. These tests call it directly, so each of the three
conflicts is pinned independently of whether a message is admitted afterwards.
"""

from __future__ import annotations

import pytest

from tests._formation_test_support import formation_env, make_run_dir, write_pin  # noqa: F401
from tests.comms.test_scoped_notify import COMMS, ScopeScene, scene  # noqa: F401
from trw_mcp.comms._envelope import AdmissionError
from trw_mcp.comms._notify import candidates, reconcile_retry

_SCOPE = f"{COMMS}/_store.py"


def _reconcile(scene: ScopeScene, *, key: str = "k1", scope: str = _SCOPE) -> set[str]:
    """Run the helper under the caller's own binding and a read-only connection."""
    from trw_mcp.comms._identity import resolve_authority_snapshot
    from trw_mcp.comms._store import connect

    snapshot = resolve_authority_snapshot(
        None, trw_dir=scene.formation.trw_dir, project_root=scene.formation.project_root
    )
    with connect(scene.formation.manifest_path(), busy_timeout_ms=2000) as conn:
        return reconcile_retry(conn, snapshot.binding, key, scope, candidates(snapshot, scope))


def test_a_first_send_is_not_a_retry(scene: ScopeScene) -> None:
    scene.actor("impl-1")
    assert _reconcile(scene) == set()


def test_an_exact_retry_returns_the_members_the_first_call_reached(scene: ScopeScene) -> None:
    scene.actor("impl-1")
    assert list(scene.notify(key="k1")["recipients"]) == ["impl-2"]
    assert _reconcile(scene, key="k1") == {"impl-2"}


def test_a_key_already_used_by_a_direct_send_conflicts(scene: ScopeScene) -> None:
    scene.actor("impl-1")
    direct = scene.call("trw_send", {"recipient_member_id": "impl-2", "request_key": "k1", "body": "x"})
    assert direct["status"] == "ok"
    with pytest.raises(AdmissionError) as refused:
        _reconcile(scene, key="k1")
    assert refused.value.reason == "idempotency_conflict"


def test_the_same_key_under_a_different_scope_conflicts(scene: ScopeScene) -> None:
    scene.actor("impl-1")
    assert scene.notify(scope=_SCOPE, key="k1")["status"] == "ok"
    with pytest.raises(AdmissionError) as refused:
        _reconcile(scene, key="k1", scope=f"{COMMS}/_notify.py")
    assert refused.value.reason == "idempotency_conflict"


def test_a_contracted_reach_conflicts_but_a_grown_one_does_not(scene: ScopeScene) -> None:
    """Growth is a retry with a frozen fan-out; losing a previous recipient is a new message."""
    scene.actor("impl-1")
    assert list(scene.notify(scope=_SCOPE, key="k1")["recipients"]) == ["impl-2"]

    scene.redeclare("impl-3", [f"{COMMS}/**"])
    scene.redeclare("impl-2", ["src/beta/**"])
    with pytest.raises(AdmissionError) as refused:
        _reconcile(scene, key="k1")  # impl-2, which was reached, no longer is
    assert refused.value.reason == "idempotency_conflict"

    scene.redeclare("impl-2", [f"{COMMS}/**"])
    scene.redeclare("impl-3", [f"{COMMS}/_notify.py"])
    assert _reconcile(scene, key="k1") == {"impl-2"}, "a grown reach still reconciles as a retry"
