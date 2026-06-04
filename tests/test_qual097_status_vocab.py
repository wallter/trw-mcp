"""PRD-QUAL-097: PRD status-vocabulary canonicalization.

Behavioral tests for:
- FR01: canonical status set + alias map + ``normalize_status`` helper.
- FR02: non-canonical status emits a WARNING (not a blocking failure) from
  ``run_prd_integrity_checks``, and never sets ``valid=False``.
- FR03: the existing FPI #7 functionality_level HARD check fires ONLY for the
  pre-QUAL-097 trigger set (raw ``implemented`` + ``partial``/``stub`` sentinels);
  the implemented-family aliases (``done``/``delivered``/``complete``) produce a
  WARNING (not a ValidationFailure) so they never flip ``valid`` — no corpus
  regression. Canonical ``implemented`` retains the hard gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trw_mcp.state.validation.prd_integrity import (
    _STATUS_ALIASES,
    CANONICAL_STATUSES,
    _check_functionality_level_matches_status,
    _check_implemented_alias_functionality,
    normalize_status,
    run_prd_integrity_checks,
)

_PRDS_REL = "docs/requirements-aare-f/prds"


def _rules(failures: list[object]) -> set[str]:
    return {getattr(f, "rule", "") for f in failures}


# ---------------------------------------------------------------------------
# FR01: canonical set + alias map + normalize_status
# ---------------------------------------------------------------------------


class TestNormalizeAliases:
    """FR01: alias normalization table."""

    def test_canonical_set_is_the_five_template_statuses(self) -> None:
        assert CANONICAL_STATUSES == {
            "draft",
            "review",
            "approved",
            "implemented",
            "deprecated",
        }

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("done", "implemented"),
            ("delivered", "implemented"),
            ("complete", "implemented"),
            ("in-progress", "draft"),
            ("wip", "draft"),
            ("ready", "review"),
        ],
    )
    def test_known_aliases_map_to_canonical(self, raw: str, expected: str) -> None:
        canonical, is_canonical = normalize_status(raw)
        assert canonical == expected
        assert is_canonical is True

    @pytest.mark.parametrize("raw", ["draft", "review", "approved", "implemented", "deprecated"])
    def test_canonical_values_normalize_to_themselves(self, raw: str) -> None:
        canonical, is_canonical = normalize_status(raw)
        assert canonical == raw
        assert is_canonical is True

    def test_unknown_status_returns_itself_not_canonical(self) -> None:
        canonical, is_canonical = normalize_status("active")
        # "active" has no alias entry in the chosen map -> echoed back, flagged.
        assert canonical == "active"
        assert is_canonical is False

    def test_normalize_is_case_and_whitespace_insensitive(self) -> None:
        assert normalize_status("  DONE ") == ("implemented", True)
        assert normalize_status("In-Progress") == ("draft", True)

    def test_empty_status_is_not_canonical(self) -> None:
        canonical, is_canonical = normalize_status("")
        assert canonical == ""
        assert is_canonical is False

    def test_alias_targets_are_all_canonical(self) -> None:
        # Every alias must point at a real canonical value (no drift).
        for target in _STATUS_ALIASES.values():
            assert target in CANONICAL_STATUSES


# ---------------------------------------------------------------------------
# FR02: non-canonical status -> warning, never a blocking failure
# ---------------------------------------------------------------------------


class TestNonCanonicalWarnsNotBlocks:
    """FR02: non-canonical status emits a warning and does NOT set valid=False."""

    def _fm(self, status: str) -> dict[str, object]:
        return {"prd": {"id": "PRD-CORE-001"}, "status": status, "functionality_level": "live"}

    def test_aliased_status_emits_warning_naming_canonical(self, tmp_path: Path) -> None:
        failures, warnings = run_prd_integrity_checks(
            "# PRD body",
            self._fm("done"),
            project_root=tmp_path,
            prds_relative_path=_PRDS_REL,
        )
        assert any("done" in w and "implemented" in w for w in warnings)

    def test_unknown_status_emits_warning_without_alias_suggestion(self, tmp_path: Path) -> None:
        failures, warnings = run_prd_integrity_checks(
            "# PRD body",
            self._fm("active"),
            project_root=tmp_path,
            prds_relative_path=_PRDS_REL,
        )
        assert any("active" in w for w in warnings)

    def test_noncanonical_status_does_not_produce_failure(self, tmp_path: Path) -> None:
        failures, _warnings = run_prd_integrity_checks(
            "# PRD body",
            self._fm("ready"),
            project_root=tmp_path,
            prds_relative_path=_PRDS_REL,
        )
        # No status-vocabulary rule among the blocking failures.
        assert "aaref_status_canonical" not in _rules(failures)
        assert not any("status" in getattr(f, "rule", "") for f in failures)

    def test_canonical_status_emits_no_status_warning(self, tmp_path: Path) -> None:
        _failures, warnings = run_prd_integrity_checks(
            "# PRD body",
            self._fm("approved"),
            project_root=tmp_path,
            prds_relative_path=_PRDS_REL,
        )
        assert not any("approved" in w and "canonical" in w.lower() for w in warnings)

    def test_missing_status_emits_no_status_warning(self, tmp_path: Path) -> None:
        _failures, warnings = run_prd_integrity_checks(
            "# PRD body",
            {"prd": {"id": "PRD-CORE-001"}},
            project_root=tmp_path,
            prds_relative_path=_PRDS_REL,
        )
        assert not any("canonical" in w.lower() for w in warnings)

    def test_warning_path_via_v2_does_not_fail_valid(self, tmp_path: Path) -> None:
        """NFR01 brownfield-safe: a non-canonical status does NOT flip valid=False."""
        from trw_mcp.models.config import get_config
        from trw_mcp.state.validation import validate_prd_quality_v2

        prd = (
            "---\n"
            "prd:\n"
            "  id: PRD-CORE-555\n"
            '  title: "Active status PRD"\n'
            '  version: "1.0"\n'
            "  status: active\n"
            "  priority: P1\n"
            "  category: CORE\n"
            "functionality_level: live\n"
            "stubs: []\n"
            "confidence:\n"
            "  implementation_feasibility: 0.8\n"
            "  requirement_clarity: 0.8\n"
            "  estimate_confidence: 0.7\n"
            "traceability:\n"
            "  implements: [KE-001]\n"
            "---\n\n"
            "# PRD-CORE-555: body\n"
        )
        result = validate_prd_quality_v2(prd, get_config(), project_root=str(tmp_path))
        # The non-canonical status must not appear as a blocking failure rule.
        assert "aaref_status_canonical" not in {f.rule for f in result.failures}


# ---------------------------------------------------------------------------
# FR03: FPI #7 alias consistency (no behavior regression)
# ---------------------------------------------------------------------------


class TestFpi7HardTriggerScope:
    """FR03 (corrected): the HARD FPI #7 failures fire ONLY for the pre-QUAL-097 set
    (raw ``implemented`` + ``partial``/``stub`` sentinels). Implemented-family ALIASES
    (done/delivered/complete) NEVER produce a hard failure here — they warn instead."""

    def test_canonical_implemented_unset_still_hard_fails(self) -> None:
        """status=implemented + no functionality_level STILL emits the hard required failure."""
        failures = _check_functionality_level_matches_status({"status": "implemented"})
        assert "aaref_functionality_level_required" in _rules(failures)

    def test_done_unset_level_does_not_hard_fail(self) -> None:
        """FR03 corrected: status=done + functionality_level unset is NOT a hard failure."""
        failures = _check_functionality_level_matches_status({"status": "done"})
        assert failures == []

    @pytest.mark.parametrize("status", ["delivered", "complete"])
    def test_other_implemented_family_aliases_do_not_hard_fail(self, status: str) -> None:
        failures = _check_functionality_level_matches_status({"status": status})
        assert failures == []

    def test_done_with_nonlive_level_does_not_hard_fail(self) -> None:
        """status=done is not a hard trigger -> requires-live failure does NOT fire."""
        failures = _check_functionality_level_matches_status(
            {"status": "done", "functionality_level": "partial", "stubs": [{"id": "x"}]}
        )
        assert failures == []

    def test_partial_status_sentinel_still_triggers(self) -> None:
        """The pre-existing 'partial' status sentinel must keep triggering (no regression)."""
        failures = _check_functionality_level_matches_status({"status": "partial"})
        assert "aaref_functionality_level_required" in _rules(failures)

    def test_stub_status_sentinel_still_triggers(self) -> None:
        failures = _check_functionality_level_matches_status({"status": "stub"})
        assert "aaref_functionality_level_required" in _rules(failures)

    def test_draft_status_does_not_trigger(self) -> None:
        """A draft PRD is not past-implementation -> no functionality_level requirement."""
        assert _check_functionality_level_matches_status({"status": "draft"}) == []

    def test_review_status_does_not_trigger(self) -> None:
        assert _check_functionality_level_matches_status({"status": "review"}) == []

    def test_ready_alias_maps_to_review_and_does_not_trigger(self) -> None:
        """'ready' -> 'review' (not past-implementation) -> no FPI #7 trigger."""
        assert _check_functionality_level_matches_status({"status": "ready"}) == []

    def test_implemented_live_with_empty_stubs_passes(self) -> None:
        """The happy path: implemented + live + no stubs -> no failures (unchanged)."""
        failures = _check_functionality_level_matches_status(
            {"status": "implemented", "functionality_level": "live", "stubs": []}
        )
        assert failures == []


class TestImplementedAliasWarning:
    """FR03 (corrected): implemented aliases surface as WARNINGS, never failures."""

    def test_done_unset_level_warns(self) -> None:
        warnings = _check_implemented_alias_functionality({"status": "done"})
        assert len(warnings) == 1
        assert "done" in warnings[0] and "implemented" in warnings[0]

    @pytest.mark.parametrize("status", ["delivered", "complete"])
    def test_other_aliases_unset_level_warn(self, status: str) -> None:
        warnings = _check_implemented_alias_functionality({"status": status})
        assert len(warnings) == 1
        assert status in warnings[0]

    def test_done_nonlive_level_warns(self) -> None:
        warnings = _check_implemented_alias_functionality(
            {"status": "done", "functionality_level": "partial", "stubs": [{"id": "x"}]}
        )
        assert len(warnings) == 1

    def test_done_live_with_stubs_warns(self) -> None:
        warnings = _check_implemented_alias_functionality(
            {"status": "done", "functionality_level": "live", "stubs": [{"id": "x"}]}
        )
        assert len(warnings) == 1

    def test_done_clean_live_no_warning(self) -> None:
        """status=done + live + empty stubs is audit-clean -> no warning."""
        warnings = _check_implemented_alias_functionality(
            {"status": "done", "functionality_level": "live", "stubs": []}
        )
        assert warnings == []

    def test_non_alias_status_no_warning(self) -> None:
        assert _check_implemented_alias_functionality({"status": "implemented"}) == []
        assert _check_implemented_alias_functionality({"status": "draft"}) == []


class TestDoneUnsetLevelEndToEnd:
    """FR03 corrected acceptance via the public validate_prd_quality_v2 surface."""

    def _done_unset_prd(self) -> str:
        return (
            "---\n"
            "prd:\n"
            "  id: PRD-CORE-777\n"
            '  title: "Done no-level PRD"\n'
            '  version: "1.0"\n'
            "  status: done\n"
            "  priority: P1\n"
            "  category: CORE\n"
            "confidence:\n"
            "  implementation_feasibility: 0.8\n"
            "  requirement_clarity: 0.8\n"
            "  estimate_confidence: 0.7\n"
            "traceability:\n"
            "  implements: [KE-001]\n"
            "---\n\n"
            "# PRD-CORE-777: body\n"
        )

    def test_done_unset_level_does_not_fail_valid(self, tmp_path: Path) -> None:
        """A status=done + no functionality_level PRD has NO required failure; FPI#7 produces
        a WARNING, never flipping ``valid`` to False on this account."""
        from trw_mcp.models.config import get_config
        from trw_mcp.state.validation import validate_prd_quality_v2

        result = validate_prd_quality_v2(self._done_unset_prd(), get_config(), project_root=str(tmp_path))
        # FPI#7 hard failure must NOT be present.
        assert "aaref_functionality_level_required" not in {f.rule for f in result.failures}
        assert "aaref_implemented_requires_live" not in {f.rule for f in result.failures}
        # And the integrity warning IS surfaced through run_prd_integrity_checks.
        _failures, warnings = run_prd_integrity_checks(
            self._done_unset_prd(),
            {"prd": {"id": "PRD-CORE-777"}, "status": "done"},
            project_root=tmp_path,
            prds_relative_path=_PRDS_REL,
        )
        assert any("done" in w and "implemented" in w for w in warnings)

    def test_canonical_implemented_unset_still_hard_fails(self, tmp_path: Path) -> None:
        """status=implemented + no functionality_level STILL emits the hard FPI#7 failure
        through the public surface (pre-097 behavior preserved)."""
        from trw_mcp.models.config import get_config
        from trw_mcp.state.validation import validate_prd_quality_v2

        prd = self._done_unset_prd().replace("  status: done\n", "  status: implemented\n")
        result = validate_prd_quality_v2(prd, get_config(), project_root=str(tmp_path))
        assert "aaref_functionality_level_required" in {f.rule for f in result.failures}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
