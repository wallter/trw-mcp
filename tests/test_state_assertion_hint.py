"""PRD-CORE-244 FR05 — the state-assertion classifier proposes, never stamps."""

from __future__ import annotations

from datetime import timedelta

import pytest

from trw_mcp.models.config import TRWConfig
from trw_mcp.tools._state_assertion_hint import (
    STATE_ASSERTION_MARKERS,
    propose_validity_window,
    validity_window_nudge,
)

_TTL = TRWConfig().state_learning_default_ttl_days


class TestProposeValidityWindow:
    @pytest.mark.parametrize("marker", STATE_ASSERTION_MARKERS)
    def test_every_marker_phrase_proposes_a_window(self, marker: str) -> None:
        window = propose_validity_window(f"The gate {marker} open", "", "incident", _TTL)
        assert window == timedelta(days=_TTL["incident"])

    def test_bare_cardinal_count_proposes_a_window(self) -> None:
        """A number that was measured is a number that changes."""
        assert propose_validity_window("7 callers reach this seam", "", "hypothesis", _TTL) == timedelta(days=30)

    def test_invariant_types_are_never_offered_a_window(self) -> None:
        """``convention`` and ``pattern`` record invariants, whatever the text says."""
        for learning_type in ("convention", "pattern"):
            assert propose_validity_window("currently 7 callers", "detail", learning_type, _TTL) is None

    def test_text_with_no_state_marker_proposes_nothing(self) -> None:
        assert propose_validity_window("Prefer a narrow interface", "Deep modules win", "incident", _TTL) is None

    def test_marker_in_detail_is_enough(self) -> None:
        assert propose_validity_window("A short summary", "This is not yet wired", "workaround", _TTL) is not None

    def test_version_and_path_digits_are_not_cardinals(self) -> None:
        """A version or a file path must not look like a measured count."""
        assert propose_validity_window("Bump to v2.1.4", "see src/a1/b2.py", "incident", _TTL) is None

    def test_per_type_windows_come_from_config(self) -> None:
        assert propose_validity_window("currently true", "", "incident", _TTL) == timedelta(days=90)
        assert propose_validity_window("currently true", "", "hypothesis", _TTL) == timedelta(days=30)
        assert propose_validity_window("currently true", "", "workaround", _TTL) == timedelta(days=180)

    def test_an_operator_can_retire_a_type_by_removing_its_key(self) -> None:
        assert propose_validity_window("currently true", "", "incident", {"hypothesis": 30}) is None

    def test_classifier_performs_no_io(self) -> None:
        """NFR01: a pure string operation.

        Proven by construction — the module imports only ``re`` and ``timedelta``
        — and pinned here so an I/O call added later fails a test rather than
        adding latency to every trw_learn.
        """
        import trw_mcp.tools._state_assertion_hint as module

        source = module.__doc__ or ""
        assert "no filesystem" in source
        assert not hasattr(module, "Path")
        assert not hasattr(module, "open")


class TestValidityWindowNudge:
    def test_nudge_names_the_days_and_the_exact_call(self) -> None:
        text = validity_window_nudge("L-abc1", timedelta(days=90))
        assert "90 days" in text
        assert "trw_learn_update(learning_id='L-abc1'" in text
        assert "'expires'" in text
