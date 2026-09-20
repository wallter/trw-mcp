"""PRD-INFRA-189 FR08 adoption, extended by PRD-CORE-274 FR14 (authenticated continuation).

A formation member's recorded ``pin_key`` is part of its comms identity (run_path
AND pin_key must both match). A restart under a CHANGED client key adopts the run
(INFRA-189), and FR14 lets that SAME process rebind the member's pin because it
performed the adoption itself (client lineage, held in memory). A new key WITHOUT
that in-process lineage -- or lineage from a different pin -- never rebinds, and
comms fails closed. An UNCHANGED key matches exactly as before.
"""

from __future__ import annotations

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import enable_comms, joined_member
from trw_mcp.comms._identity import IdentityError, IdentityRefusal, resolve_snapshot
from trw_mcp.formation import join, load
from trw_mcp.state import _paths_pin_mgmt
from trw_mcp.state._paths import get_pinned_run
from trw_mcp.state._pin_store import upsert_pin_entry


def _restart_under(monkeypatch: pytest.MonkeyPatch, client_key: str) -> None:
    """A fresh server process of the same client, launched with *client_key*."""
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", client_key)
    monkeypatch.setattr(_paths_pin_mgmt, "_sibling_adoption_done", False)
    monkeypatch.setattr(_paths_pin_mgmt, "_ADOPTED_FROM", {})  # a new process remembers no lineage


@pytest.fixture(autouse=True)
def _fresh_lineage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lineage is per process; each test starts as a process that adopted nothing."""
    monkeypatch.setattr(_paths_pin_mgmt, "_ADOPTED_FROM", {})


def _member(env: FormationFixture) -> tuple[str, str | None]:
    loaded = load(env.orchestrator_run, trw_dir=env.trw_dir)
    assert loaded is not None
    member = loaded.manifest.member("impl-1")
    return loaded.manifest.formation_id, member.pin_key


def test_lineage_from_a_different_pin_does_not_rebind(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR14 negative control: adoption proves continuation of the ADOPTED pin only. A process
    that adopted some other pin cannot rebind a member recorded under key-A."""
    enable_comms(monkeypatch)
    run = joined_member(formation_env, "impl-1", "key-A")
    upsert_pin_entry("key-A", run)
    _restart_under(monkeypatch, "key-B")
    assert get_pinned_run(session_id="key-B") == run, "run resolution still adopts (INFRA-189)"
    monkeypatch.setattr(_paths_pin_mgmt, "_ADOPTED_FROM", {"key-B": "key-Z"})  # lineage of another pin

    formation_id, _ = _member(formation_env)
    with pytest.raises(Exception, match="rebind_not_authorized"):
        join(formation_id, "impl-1", run, pin_key="key-B", trw_dir=formation_env.trw_dir)
    assert _member(formation_env)[1] == "key-A"
    with pytest.raises(IdentityError) as refused:
        resolve_snapshot(None, trw_dir=formation_env.trw_dir, project_root=formation_env.project_root)
    assert refused.value.refusal is IdentityRefusal.NO_MATCH


def test_unchanged_key_binds_as_before(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_comms(monkeypatch)
    run = joined_member(formation_env, "impl-1", "key-A")
    upsert_pin_entry("key-A", run)

    _restart_under(monkeypatch, "key-A")
    assert get_pinned_run(session_id="key-A") == run

    binding = resolve_snapshot(None, trw_dir=formation_env.trw_dir, project_root=formation_env.project_root).binding
    assert (binding.member_id, binding.session_id) == ("impl-1", "key-A")


# PRD-CORE-274 FR14 (Amendment 02, prospective): authenticated member continuation.
# Extends PRD-INFRA-189 FR08, whose scope claims no comms recovery. The first test is RED
# on the pre-amendment tree by design (slice A0); slice A4 turns it green and rewrites
# test_changed_key_adopts_the_run_but_not_the_comms_identity above.


def test_fr14_client_lineage_rejoin_rebinds_comms_identity(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_comms(monkeypatch)
    run = joined_member(formation_env, "impl-1", "key-A")
    upsert_pin_entry("key-A", run)

    _restart_under(monkeypatch, "key-B")
    assert get_pinned_run(session_id="key-B") == run, "this process adopted key-A's pin (client lineage)"

    formation_id, _ = _member(formation_env)
    join(formation_id, "impl-1", run, pin_key="key-B", trw_dir=formation_env.trw_dir)
    assert _member(formation_env)[1] == "key-B", "lineage-proven rejoin rebinds the recorded pin"

    binding = resolve_snapshot(None, trw_dir=formation_env.trw_dir, project_root=formation_env.project_root).binding
    assert (binding.member_id, binding.session_id) == ("impl-1", "key-B")


def test_fr14_changed_key_without_lineage_never_rebinds(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Negative control: a new key that did not adopt the member's pin in-process cannot rebind."""
    enable_comms(monkeypatch)
    run = joined_member(formation_env, "impl-1", "key-A")
    monkeypatch.setenv("TRW_SESSION_ID", "key-B")  # operator-forced key: no client-sibling adoption

    formation_id, _ = _member(formation_env)
    try:
        join(formation_id, "impl-1", run, pin_key="key-B", trw_dir=formation_env.trw_dir)
    except Exception as exc:  # FR14 names this refusal; pre-amendment join is a silent no-op
        assert "rebind_not_authorized" in str(exc)
    assert _member(formation_env)[1] == "key-A"
