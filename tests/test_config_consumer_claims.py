"""An admission may not name the config model as its own consumer. FR03.

PRD-QUAL-131-FR03. The contract test that enforces the ``consumer`` slot is
``assert getattr(record, slot)`` — a non-empty check, which the string
``TRWConfig`` satisfies. 370 of 383 admissions do exactly that, and it is how 115
fields with no reader anywhere kept passing a gate whose whole purpose was to
require one. These tests pin the falsification: the claim fails only when it is
BOTH self-referential AND unbacked, so a real reader with lazy prose warns
instead of breaking the build.
"""

from __future__ import annotations

import pytest


def _admission(name: str, consumer: str) -> object:
    from trw_mcp.models.config._field_admission_registry import ConfigAdmission

    return ConfigAdmission(
        field_name=name,
        owner="fixture-owner",
        consumer=consumer,
        default_rationale="fixture rationale",
        interaction_analysis="fixture interaction analysis",
        deprecation_plan="fixture deprecation plan",
        docs_pointer="fixture docs",
        test_pointer="fixture test",
        budget_decision="admitted",
    )


@pytest.mark.unit
def test_self_referential_consumer_without_reader_is_rejected() -> None:
    """The falsification: a claimed consumer with nothing behind it."""
    from trw_mcp.models.config._field_admission import verify_consumer_claims

    report = verify_consumer_claims(
        {"new_dead_knob": _admission("new_dead_knob", "TRWConfig")},
        unread={"new_dead_knob"},
        grandfathered=set(),
    )

    assert not report.ok
    assert report.rejected == ("new_dead_knob",)
    assert "new_dead_knob" in report.message


@pytest.mark.unit
def test_the_same_claim_with_a_reader_warns_and_passes() -> None:
    """A documentation defect must not fail the build.

    298 live fields carry the self-referential string and DO have readers.
    Rejecting them would fail the gate 298 times on day one, and a gate that does
    that gets suppressed within a week — the reasoning that made the
    config-consumer check a ratchet rather than a zero-tolerance gate.
    """
    from trw_mcp.models.config._field_admission import verify_consumer_claims

    report = verify_consumer_claims(
        {"wired_knob": _admission("wired_knob", "TRWConfig")},
        unread=set(),
        grandfathered=set(),
    )

    assert report.ok
    assert report.warned == ("wired_knob",)
    assert report.rejected == ()


@pytest.mark.unit
def test_a_real_consumer_is_neither_rejected_nor_warned() -> None:
    """Negative case. An admission naming actual code must be silent."""
    from trw_mcp.models.config._field_admission import verify_consumer_claims

    report = verify_consumer_claims(
        {"good_knob": _admission("good_knob", "tools/_ceremony_status_pool.py::compute_pool")},
        unread={"good_knob"},
        grandfathered=set(),
    )

    assert report.ok
    assert report.warned == ()
    assert report.rejected == ()


@pytest.mark.unit
def test_the_recorded_backlog_is_grandfathered_but_a_new_field_is_not() -> None:
    """Both halves of the ratchet, in one assertion pair.

    Failing the 72 already-recorded unread self-referential fields would punish
    them twice for one defect. Letting a NEW one through would leave the door the
    backlog came through wide open.
    """
    from trw_mcp.models.config._field_admission import verify_consumer_claims

    admissions = {
        "old_dead_knob": _admission("old_dead_knob", "TRWConfig"),
        "new_dead_knob": _admission("new_dead_knob", "TRWConfig"),
    }
    report = verify_consumer_claims(
        admissions,
        unread={"old_dead_knob", "new_dead_knob"},
        grandfathered={"old_dead_knob"},
    )

    assert report.rejected == ("new_dead_knob",)
    assert not report.ok


@pytest.mark.unit
def test_the_live_registry_passes_against_the_published_unread_set() -> None:
    """HEAD is clean: nothing unread claims the config model outside the ledger."""
    from trw_mcp.models.config import unread_config_fields
    from trw_mcp.models.config._field_admission import build_field_admissions, verify_consumer_claims

    report = verify_consumer_claims(build_field_admissions(), unread=unread_config_fields())
    assert report.ok, report.message


@pytest.mark.unit
def test_the_self_referential_ratchet_may_only_shrink() -> None:
    """The count of lazy-but-backed consumer claims is a ceiling, never a floor.

    A rise means a new field copied the pattern, which is precisely how the
    115-field backlog accumulated. Lower the constant when the number drops.
    """
    from trw_mcp.models.config import unread_config_fields
    from trw_mcp.models.config._field_admission import (
        SELF_REFERENTIAL_WITH_READER_CEILING,
        build_field_admissions,
        verify_consumer_claims,
    )

    report = verify_consumer_claims(build_field_admissions(), unread=unread_config_fields())
    assert len(report.warned) <= SELF_REFERENTIAL_WITH_READER_CEILING, (
        f"{len(report.warned)} admissions name the config model as their own consumer, above the "
        f"recorded ceiling of {SELF_REFERENTIAL_WITH_READER_CEILING}. A new field copied the pattern."
    )
    # Non-vacuity: a ceiling far above the real number would never catch anything.
    assert len(report.warned) > SELF_REFERENTIAL_WITH_READER_CEILING - 20, (
        "the ceiling has drifted far above the measurement — lower it so it can still bite"
    )
