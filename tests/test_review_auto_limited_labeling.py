"""F5 truthfulness defect: honest labeling of limited auto-review.

`trw_review(mode='auto')` without pre-collected reviewer_findings (and with
cross-model review disabled / returning []) performs ONLY a TODO/FIXME/HACK/XXX
marker scan of the diff. The resulting artifact previously reported
``verdict=pass`` / ``critical_count=0`` with no signal that no substantive
code-quality analysis ran — a constant-true verification stand-in (VISION
Principle #3 violation).

These tests drive the REAL auto-review path (no mocking of the review unit)
and assert the explicit ``auto_analysis_limited`` / ``limited_reason`` honesty
labels: True for a pattern-scan-only run, False when real reviewer findings are
supplied.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from tests._review_helpers_support import _make_config
from trw_mcp.tools._review_helpers import handle_auto_mode
from trw_mcp.tools._review_multi import (
    PATTERN_SCAN_LIMITED_REASON,
    _run_multi_reviewer_analysis,
)

from ._review_helpers_support import run_dir  # noqa: F401

# A diff that contains a TODO marker on an added line plus a benign added line.
_DIFF_WITH_TODO = (
    "diff --git a/mod.py b/mod.py\n"
    "--- a/mod.py\n"
    "+++ b/mod.py\n"
    "@@ -1,2 +1,4 @@\n"
    "+def handler():\n"
    "+    # TODO: validate the input before dispatch\n"
    "+    return dispatch()\n"
    " existing_line\n"
)

_DIFF_NO_MARKERS = (
    "diff --git a/mod.py b/mod.py\n--- a/mod.py\n+++ b/mod.py\n@@ -1,1 +1,2 @@\n+def clean():\n existing_line\n"
)


class TestRunMultiReviewerAnalysisLimited:
    """_run_multi_reviewer_analysis is the pattern-scan-only unit."""

    def test_pattern_scan_flags_limited_with_reason(self) -> None:
        """A real diff scanned for markers is always flagged limited."""
        config = _make_config()
        result = _run_multi_reviewer_analysis(_DIFF_WITH_TODO, config)
        assert result["auto_analysis_limited"] is True
        assert result["limited_reason"] == PATTERN_SCAN_LIMITED_REASON
        assert result["limited_reason"]  # non-empty

    def test_pattern_scan_still_detects_todo_markers(self) -> None:
        """TODO/FIXME detection still works and findings remain severity=info."""
        config = _make_config()
        result = _run_multi_reviewer_analysis(_DIFF_WITH_TODO, config)
        findings = result["findings"]
        assert len(findings) >= 1
        assert all(f["severity"] == "info" for f in findings)
        descriptions = " ".join(str(f["description"]) for f in findings)
        assert "TODO" in descriptions.upper()

    def test_empty_diff_is_still_flagged_limited(self) -> None:
        """Even with no findings, the empty-diff path is honestly limited."""
        config = _make_config()
        result = _run_multi_reviewer_analysis("", config)
        assert result["findings"] == []
        assert result["auto_analysis_limited"] is True
        assert result["limited_reason"]

    def test_clean_diff_no_findings_but_still_limited(self) -> None:
        """No markers -> no findings, but analysis is still pattern-scan-only."""
        config = _make_config()
        result = _run_multi_reviewer_analysis(_DIFF_NO_MARKERS, config)
        assert result["findings"] == []
        assert result["auto_analysis_limited"] is True


class TestHandleAutoModeLabeling:
    """handle_auto_mode propagates the honest-labeling flag into the result."""

    def test_pattern_scan_only_sets_limited_true(self, run_dir: Path) -> None:
        """No reviewer_findings + cross-model off -> auto_analysis_limited=True."""
        config = _make_config(cross_model_enabled=False)
        with patch(
            "trw_mcp.tools._review_helpers._get_git_diff",
            return_value=_DIFF_WITH_TODO,
        ):
            result = handle_auto_mode(
                config,
                run_dir,
                "review-limited",
                "2026-06-04T00:00:00Z",
                None,  # no pre-collected reviewer findings -> pattern-scan path
            )
        assert result["auto_analysis_limited"] is True
        assert result["substantive"] is False
        assert result["limited_reason"] == PATTERN_SCAN_LIMITED_REASON
        assert result["limited_reason"]
        # The defect signature: verdict=pass on a limited scan, now honestly labeled.
        assert result["verdict"] == "pass"
        assert result["critical_count"] == 0

    def test_pattern_scan_todo_findings_preserved(self, run_dir: Path) -> None:
        """The TODO finding is still counted even though the review is limited."""
        config = _make_config(cross_model_enabled=False)
        with patch(
            "trw_mcp.tools._review_helpers._get_git_diff",
            return_value=_DIFF_WITH_TODO,
        ):
            result = handle_auto_mode(
                config,
                run_dir,
                "review-limited-todo",
                "2026-06-04T00:00:00Z",
                None,
            )
        assert result["total_findings_count"] >= 1
        assert result["auto_analysis_limited"] is True

    def test_real_reviewer_findings_set_limited_false(self, run_dir: Path) -> None:
        """Pre-collected reviewer findings -> a substantive review, NOT limited."""
        config = _make_config(confidence_threshold=50)
        reviewer_findings = [
            {
                "reviewer_role": "correctness",
                "confidence": 90,
                "category": "logic",
                "severity": "critical",
                "description": "Null deref on the error path",
            },
        ]
        with patch(
            "trw_mcp.tools._review_helpers._get_git_diff",
            return_value=_DIFF_WITH_TODO,
        ):
            result = handle_auto_mode(
                config,
                run_dir,
                "review-substantive",
                "2026-06-04T00:00:00Z",
                reviewer_findings,
            )
        assert result["auto_analysis_limited"] is False
        assert result["limited_reason"] == ""
        assert result["substantive"] is True
        # Real critical finding actually blocks — proves substantive analysis.
        assert result["verdict"] == "block"
        assert result["critical_count"] == 1

    def test_empty_reviewer_findings_list_is_non_substantive(self, run_dir: Path) -> None:
        """An empty list has no independently verifiable receipt and fails closed."""
        config = _make_config()
        with patch(
            "trw_mcp.tools._review_helpers._get_git_diff",
            return_value=_DIFF_WITH_TODO,
        ):
            result = handle_auto_mode(
                config,
                run_dir,
                "review-empty-unverified",
                "2026-06-04T00:00:00Z",
                [],
            )
        assert result["auto_analysis_limited"] is True
        assert "no schema-valid findings" in result["limited_reason"]
        assert result["substantive"] is False
        assert result["total_findings_count"] == 0

    def test_placeholder_reviewer_finding_is_non_substantive(self, run_dir: Path) -> None:
        """A non-empty list is not evidence when its only mapping is invalid."""
        config = _make_config()
        with patch("trw_mcp.tools._review_helpers._get_git_diff", return_value=_DIFF_WITH_TODO):
            result = handle_auto_mode(
                config,
                run_dir,
                "review-placeholder-unverified",
                "2026-06-04T00:00:00Z",
                [{}],
            )
        assert result["auto_analysis_limited"] is True
        assert result["substantive"] is False
        assert result["total_findings_count"] == 0

    def test_limited_label_persisted_to_review_yaml(self, run_dir: Path) -> None:
        """The honest label is written into review.yaml for downstream readers."""
        import yaml

        config = _make_config(cross_model_enabled=False)
        with patch(
            "trw_mcp.tools._review_helpers._get_git_diff",
            return_value=_DIFF_WITH_TODO,
        ):
            result = handle_auto_mode(
                config,
                run_dir,
                "review-persist",
                "2026-06-04T00:00:00Z",
                None,
            )
        review_yaml_path = Path(result["review_yaml"])
        assert review_yaml_path.exists()
        data = yaml.safe_load(review_yaml_path.read_text(encoding="utf-8"))
        assert data["auto_analysis_limited"] is True
        assert data["substantive"] is False
        assert data["limited_reason"]
        assert data["review_kind"] == "pattern-scan (limited)"
