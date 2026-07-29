"""Auto-mode confidence gate must never discard a caller's finding in silence.

The accept-list incident (every ``P0``/``P1``/``P2`` finding dropped, an empty
review recorded, nothing in the response naming the offending field) had a
second layer underneath it: even once a finding VALIDATES, ``handle_auto_mode``
filters it against ``review_confidence_threshold`` before computing the verdict.
A finding removed there never reached ``surfaced``, never reached ``findings`` in
review.yaml, and was named nowhere in the response — so two P0/P1 findings came
back as ``verdict='pass'``, ``substantive=True``, ``critical_count=0``.

These tests pin the two halves of the fix:
  1. An unscored finding is *unscored*, not zero-confidence — it keeps the
     ``ReviewFinding`` model's own default and drives the verdict.
  2. Any finding the confidence gate does remove is reported back with an index
     and a reason, so a suppressed finding is visible to the agent that sent it.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._review_helpers_support import _make_config
from trw_mcp.tools._review_auto import handle_auto_mode
from trw_mcp.tools._review_validation import (
    SUPPRESSED_BELOW_THRESHOLD,
    SUPPRESSED_UNSCORABLE_CONFIDENCE,
)

from ._review_helpers_support import run_dir  # noqa: F401


def _call(config: object, run_path: Path, findings: list[dict[str, object]] | None) -> dict[str, object]:
    with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=""):
        return dict(
            handle_auto_mode(
                config,  # type: ignore[arg-type]
                run_path,
                "review-auto",
                "2026-03-01T00:00:00Z",
                findings,
            )
        )


class TestUnscoredFindingsDriveTheVerdict:
    """A finding that states no confidence must not be coerced to zero."""

    def test_unscored_critical_finding_blocks(self, run_dir: Path) -> None:
        # The exact shape an audit handoff sends: category/severity/description,
        # no confidence field. Before the fix this returned verdict='pass'.
        result = _call(
            _make_config(confidence_threshold=80),
            run_dir,
            [
                {"category": "security", "severity": "P0", "description": "auth bypass in login handler"},
                {"category": "correctness", "severity": "P1", "description": "off-by-one in pagination"},
            ],
        )
        assert result["verdict"] == "block"
        assert result["critical_count"] == 2
        assert result["surfaced_findings_count"] == 2
        assert result["total_findings_count"] == 2
        # Nothing was suppressed, so the advisory keys stay off the response.
        assert "suppressed_findings_count" not in result

    def test_unscored_finding_reaches_persisted_artifact(self, run_dir: Path) -> None:
        """review.yaml must record the finding, not an empty list."""
        from trw_mcp.state.persistence import FileStateReader

        _call(
            _make_config(confidence_threshold=80),
            run_dir,
            [{"category": "security", "severity": "P0", "description": "auth bypass"}],
        )
        persisted = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert len(persisted["findings"]) == 1
        assert persisted["findings"][0]["category"] == "security"


class TestSuppressedFindingsAreReported:
    """Whatever the confidence gate removes, the response must name."""

    def test_below_threshold_finding_is_reported_with_index_and_reason(self, run_dir: Path) -> None:
        result = _call(
            _make_config(confidence_threshold=80),
            run_dir,
            [
                {
                    "category": "logic",
                    "severity": "critical",
                    "description": "Low confidence critical",
                    "confidence": 20,
                }
            ],
        )
        # Existing policy is preserved: an explicitly low-confidence finding is
        # still filtered and still yields 'pass'. What changes is that the caller
        # is told, instead of being left to infer it from a count mismatch.
        assert result["verdict"] == "pass"
        assert result["surfaced_findings_count"] == 0
        assert result["suppressed_findings_count"] == 1
        assert result["suppressed_findings"] == [
            {"index": 0, "reason": SUPPRESSED_BELOW_THRESHOLD, "value": "20.0"}
        ]

    def test_unscorable_confidence_is_reported_not_dropped(self, run_dir: Path) -> None:
        """A non-numeric confidence must not vanish between the two counts."""
        # ReviewFinding rejects a non-float confidence outright, so this lands in
        # rejected_findings; the point is that SOMETHING names it.
        result = _call(
            _make_config(confidence_threshold=80),
            run_dir,
            [{"category": "logic", "severity": "critical", "description": "x", "confidence": "high"}],
        )
        named = int(result.get("rejected_findings_count", 0)) + int(result.get("suppressed_findings_count", 0))
        assert named == 1, "a discarded finding must be named in exactly one of the two reports"

    def test_suppression_report_is_capped_but_count_is_exact(self, run_dir: Path) -> None:
        from trw_mcp.tools._review_validation import MAX_REPORTED_REJECTIONS

        # 0.2 == 20%, comfortably under the 80 threshold. (Note the dual scale:
        # confidence=1 would mean 100%, not 1%.)
        findings: list[dict[str, object]] = [
            {"category": "c", "severity": "info", "description": f"finding {i}", "confidence": 0.2}
            for i in range(MAX_REPORTED_REJECTIONS + 5)
        ]
        result = _call(_make_config(confidence_threshold=80), run_dir, findings)
        assert result["suppressed_findings_count"] == MAX_REPORTED_REJECTIONS + 5
        assert len(result["suppressed_findings"]) == MAX_REPORTED_REJECTIONS  # type: ignore[arg-type]

    def test_no_suppression_keys_when_everything_surfaces(self, run_dir: Path) -> None:
        result = _call(
            _make_config(confidence_threshold=50),
            run_dir,
            [{"category": "c", "severity": "info", "description": "d", "confidence": 90}],
        )
        assert result["surfaced_findings_count"] == 1
        assert "suppressed_findings_count" not in result
        assert "suppressed_findings" not in result


class TestSuppressionReasonsAreDistinct:
    def test_reason_tokens_are_distinct_strings(self) -> None:
        # Two different causes must not collapse to one label — "you set it too
        # low" and "the value could not be scored" need different fixes.
        assert SUPPRESSED_BELOW_THRESHOLD != SUPPRESSED_UNSCORABLE_CONFIDENCE
