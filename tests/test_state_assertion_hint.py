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

    def test_a_bare_count_alone_proposes_nothing(self) -> None:
        """PRD-FIX-141-FR08: a measured number is not a temporal qualifier.

        "A number that was measured is a number that changes" was the original
        reasoning, and it selected almost exactly the wrong population — see
        ``TestDefectRecordsAreNotStateAssertions`` below.
        """
        assert propose_validity_window("7 callers reach this seam", "", "hypothesis", _TTL) is None

    def test_a_count_with_a_temporal_qualifier_still_proposes(self) -> None:
        """The author saying "now" is the signal; the digits never were."""
        assert propose_validity_window("currently 7 callers reach this seam", "", "hypothesis", _TTL) == timedelta(
            days=30
        )

    def test_invariant_types_are_never_offered_a_window(self) -> None:
        """``convention`` and ``pattern`` record invariants, whatever the text says."""
        for learning_type in ("convention", "pattern"):
            assert propose_validity_window("currently 7 callers", "detail", learning_type, _TTL) is None

    def test_text_with_no_state_marker_proposes_nothing(self) -> None:
        assert propose_validity_window("Prefer a narrow interface", "Deep modules win", "incident", _TTL) is None

    def test_marker_in_detail_is_enough(self) -> None:
        assert propose_validity_window("A short summary", "This is not yet wired", "workaround", _TTL) is not None

    def test_version_and_path_digits_propose_nothing(self) -> None:
        """A version or a file path must not look like a state assertion."""
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
        # PRD-CORE-291 merged trw_learn_update into trw_learn's update mode.
        assert "trw_learn(learning_id='L-abc1'" in text
        assert "'expires'" in text


class TestDefectRecordsAreNotStateAssertions:
    """PRD-FIX-141-FR08 — the corpus that proved the old heuristic backwards.

    These are the seven learnings recorded on 2026-09-16 while probing every TRW
    surface (tag ``probe-2026-09-16``). Each one asserts a DEFECT: a durable
    property of the system that stays true until somebody fixes it. Every one of
    them quantifies that defect, because a useful defect record does — and the
    bare-cardinal trigger fired on all seven while the temporal markers fired on
    none. Stamping a TTL on "the handshake reports the wrong version" would
    schedule the store to forget a bug that is still there.

    The texts are abridged to the phrases that carry the counts; the full
    records live in the project store.
    """

    _PROBE_RECORDS = (
        pytest.param(
            "MCP serverInfo.version reports FastMCP's version (3.4.7), not trw-mcp's (3.0.0)",
            "FastMCP('trw', ...) in server/_app.py passes no version, so every client-side check sees it",
            id="L-vITW-handshake-version",
        ),
        pytest.param(
            "Session advisories point at tools the session cannot call",
            "with progressive disclosure only 15 of 53 tools are exposed",
            id="L-R15h-masked-tools",
        ),
        pytest.param(
            "Team-synced learnings from other projects pollute every project-scoped surface",
            "only 9 of 1343 rows are embedded so ranking is lexical substring overlap",
            id="L-XIhp-synced-pollution",
        ),
        pytest.param(
            "Health and count surfaces contradict each other on the same store",
            "session_start reports 'knowledge graph dead: 0 edges for 1343 memories' at severity error",
            id="L-Rikf-health-contradiction",
        ),
        pytest.param(
            "Shipped Claude Code hook set: self-review.sh has a bash syntax error (line 51)",
            "10 of 26 .claude/hooks/*.sh are referenced by no settings.json matcher",
            id="L-1cHv-dead-hooks",
        ),
        pytest.param(
            "skill discovery strict mode rejects frontmatter fields Claude Code itself defines",
            "on 12 of 31 bundled skills, and its ranking counts stopwords as query matches",
            id="L-ODuU-skill-frontmatter",
        ),
        pytest.param(
            "trw_prd_validate returns grade A and valid=false for the same PRD",
            "three severity=error failures demand keys the template never documents",
            id="L-9GXR-prd-verdict",
        ),
    )

    @pytest.mark.parametrize(("summary", "detail"), _PROBE_RECORDS)
    @pytest.mark.parametrize("learning_type", ["incident", "hypothesis", "workaround"])
    def test_a_defect_record_is_never_offered_a_validity_window(
        self, summary: str, detail: str, learning_type: str
    ) -> None:
        assert propose_validity_window(summary, detail, learning_type, _TTL) is None

    def test_a_genuine_state_claim_in_the_same_shape_still_fires(self) -> None:
        """Non-vacuity: the suppression is the marker rule, not a blanket off switch.

        Same subject matter, same counts — the difference is the author saying
        the claim is about *now*.
        """
        assert (
            propose_validity_window(
                "Embedding coverage is currently 9 of 1343 rows",
                "A backfill is not yet scheduled",
                "incident",
                _TTL,
            )
            is not None
        )
