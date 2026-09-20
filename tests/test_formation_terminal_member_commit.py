"""A member whose own status is terminal holds no commit authority (PRD-CORE-265-FR09, B1).

The commit precondition compared owners by ``member_id`` only, so a member the
orchestrator had moved to ``abandoned`` or ``reassigned`` could still commit the
paths its globs named, and every unowned path. Terminal status is the fence the
orchestrator already controls through ``revise()``; it must reach the boundary.

Out of scope, deliberately: staleness (a slow member that is not terminal keeps
its authority, FR08), and fencing against a status change racing a commit that
is already past this check. This check is not monotonic fencing.
"""

from __future__ import annotations

import dataclasses

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401
from tests._layout import MONOREPO_ROOT, requires_monorepo


def _gate(formation_env: FormationFixture, monkeypatch: pytest.MonkeyPatch, caller: str):  # type: ignore[no-untyped-def]
    assert MONOREPO_ROOT is not None
    monkeypatch.syspath_prepend(str(MONOREPO_ROOT / "scripts"))
    import check_formation_ownership as gate

    from trw_mcp.formation import create, join

    create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)
    for name in ("impl-1", "impl-2"):
        join("release-train", name, formation_env.member_runs[name], pin_key=f"pin-{name}")
    monkeypatch.setattr(gate, "_resolve_caller_run", lambda: formation_env.member_runs[caller])
    return gate


def _set_status(formation_env: FormationFixture, member_id: str, status: str) -> None:
    from trw_mcp.formation import revise

    revise("release-train", formation_env.orchestrator_run, {member_id: {"status": status}})


@requires_monorepo
@pytest.mark.parametrize("terminal", ["abandoned", "reassigned", "delivered"])
def test_terminal_caller_is_refused_on_own_and_unowned_paths(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    terminal: str,
) -> None:
    gate = _gate(formation_env, monkeypatch, "impl-1")
    assert gate.main(["src/alpha/thing.py"]) == 0, "precondition: a live member commits its own path"

    _set_status(formation_env, "impl-1", terminal)

    assert gate.main(["src/alpha/thing.py"]) == 1, "its former glob must not keep authorizing it"
    assert gate.main(["docs/unclaimed.md"]) == 1, "the unowned-path branch must not re-admit it"
    err = capsys.readouterr().err
    assert "impl-1" in err and terminal in err


@requires_monorepo
def test_terminal_caller_warn_mode_reports_and_allows(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    gate = _gate(formation_env, monkeypatch, "impl-1")
    _set_status(formation_env, "impl-1", "abandoned")
    from trw_mcp import formation

    warn = dataclasses.replace(formation.settings(), ownership_enforcement="warn")
    monkeypatch.setattr(formation, "settings", lambda: warn)
    assert gate.main(["src/alpha/thing.py"]) == 0
    err = capsys.readouterr().err
    assert "abandoned" in err and "warn" in err


@requires_monorepo
def test_live_member_semantics_are_unchanged_when_a_peer_is_terminal(
    formation_env: FormationFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _gate(formation_env, monkeypatch, "impl-1")
    _set_status(formation_env, "impl-2", "abandoned")

    assert gate.main(["src/alpha/thing.py"]) == 0, "a live member keeps its own path"
    assert gate.main(["docs/unclaimed.md"]) == 0, "unowned stays unowned for a live member"
    assert gate.main(["src/beta/thing.py"]) == 1, (
        "a terminal member's globs stay fenced until the orchestrator re-assigns them; "
        "they are not released to whoever commits first"
    )
