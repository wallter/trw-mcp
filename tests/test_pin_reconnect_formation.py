"""PRD-INFRA-189 FR08 scope: adoption restores run resolution, never comms identity.

A formation member's stamped ``pin_key`` is part of its comms identity (run_path
AND pin_key must both match). A restart under a CHANGED client key adopts the run
for run resolution only: a repeated join does not re-stamp the member, and comms
fails closed. An UNCHANGED key matches exactly as before.
"""

from __future__ import annotations

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests.comms.conftest import enable_comms, joined_member
from trw_mcp.comms._identity import IdentityError, IdentityRefusal, resolve_snapshot
from trw_mcp.formation import join, load
from trw_mcp.state import _paths_pin_mgmt
from trw_mcp.state._paths import get_pinned_run, resolve_pin_key
from trw_mcp.state._pin_store import upsert_pin_entry


def _restart_under(monkeypatch: pytest.MonkeyPatch, client_key: str) -> None:
    """A fresh server process of the same client, launched with *client_key*."""
    monkeypatch.delenv("TRW_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", client_key)
    monkeypatch.setattr(_paths_pin_mgmt, "_sibling_adoption_done", False)


def _member(env: FormationFixture) -> tuple[str, str | None]:
    loaded = load(env.orchestrator_run, trw_dir=env.trw_dir)
    assert loaded is not None
    member = loaded.manifest.member("impl-1")
    return loaded.manifest.formation_id, member.pin_key


def test_changed_key_adopts_the_run_but_not_the_comms_identity(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_comms(monkeypatch)
    run = joined_member(formation_env, "impl-1", "key-A")
    upsert_pin_entry("key-A", run)  # the first server's pin, with its client_pid

    _restart_under(monkeypatch, "key-B")
    assert resolve_pin_key(None) == "key-B"
    assert get_pinned_run(session_id="key-B") == run, "run resolution survives the reconnect"

    formation_id, _ = _member(formation_env)
    join(formation_id, "impl-1", run, pin_key="key-B", trw_dir=formation_env.trw_dir)
    assert _member(formation_env)[1] == "key-A", "a repeated join returns the manifest unchanged"

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
