"""A member's delivery self-report cannot overwrite a terminal verdict (PRD-CORE-265-FR11).

FR11: ``abandoned`` and ``reassigned`` are written only by an FR05-authenticated
orchestrator revision. ``mark_member_delivered`` stamped ``delivered`` whatever
the recorded status was, so a member the orchestrator had retired could replace
that verdict with its own completion claim, and every repeat bumped the revision.
"""

from __future__ import annotations

import pytest

from tests._formation_test_support import FormationFixture, formation_env  # noqa: F401


def _joined(formation_env: FormationFixture) -> None:
    from trw_mcp.formation import create, join

    create(formation_env.orchestrator_run, formation_env.payload(), prds_dir=None)
    join("release-train", "impl-1", formation_env.member_runs["impl-1"], pin_key="pin-impl-1")


def _status_and_revision(formation_env: FormationFixture) -> tuple[str, int]:
    from trw_mcp.formation import load

    context = load(formation_env.orchestrator_run)
    assert context is not None
    return str(context.manifest.member("impl-1").status), context.manifest.revision


@pytest.mark.parametrize("terminal", ["abandoned", "reassigned"])
def test_retired_member_cannot_self_report_delivery(formation_env: FormationFixture, terminal: str) -> None:
    from trw_mcp.formation import FormationError, mark_member_delivered, revise

    _joined(formation_env)
    revise("release-train", formation_env.orchestrator_run, {"impl-1": {"status": terminal}})
    before = _status_and_revision(formation_env)

    with pytest.raises(FormationError, match=terminal):
        mark_member_delivered(formation_env.member_runs["impl-1"])

    assert _status_and_revision(formation_env) == before, "the orchestrator's verdict and revision are unchanged"


def test_repeated_delivery_is_idempotent(formation_env: FormationFixture) -> None:
    from trw_mcp.formation import mark_member_delivered

    _joined(formation_env)
    first = mark_member_delivered(formation_env.member_runs["impl-1"])
    assert first is not None
    second = mark_member_delivered(formation_env.member_runs["impl-1"])

    assert second is not None
    assert second.revision == first.revision, "a repeat self-report must not bump the revision"
    assert _status_and_revision(formation_env) == ("delivered", first.revision)


@pytest.mark.parametrize("recorded", ["delivered", "abandoned"])
def test_foreign_run_is_refused_before_the_status_shortcuts(formation_env: FormationFixture, recorded: str) -> None:
    """Authentication runs first: neither the no-op nor the terminal refusal answers a foreign run."""
    from trw_mcp.formation import FormationError, join, mark_member_delivered, revise
    from trw_mcp.formation._join import mark_member_delivered as mark_for

    _joined(formation_env)
    join("release-train", "impl-2", formation_env.member_runs["impl-2"], pin_key="pin-impl-2")
    if recorded == "delivered":
        mark_member_delivered(formation_env.member_runs["impl-1"])
    else:
        revise("release-train", formation_env.orchestrator_run, {"impl-1": {"status": recorded}})
    before = _status_and_revision(formation_env)

    with pytest.raises(FormationError, match="only from its joined run"):
        mark_for(
            trw_dir=formation_env.trw_dir,
            formation_id="release-train",
            member_id="impl-1",
            run_path=formation_env.member_runs["impl-2"],
            lock_timeout_seconds=5.0,
        )
    assert _status_and_revision(formation_env) == before
