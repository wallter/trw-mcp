"""Tests for review helper behavior split from test_review_modes.py."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import pytest

from tests._review_modes_support import _make_config
from trw_mcp.tools._review_helpers import (
    REVIEWER_ROLES,
    CrossModelIncomplete,
    CrossModelUnavailable,
    _compute_verdict,
    _get_git_diff,
    _invoke_cross_model_review,
    _normalize_severity,
    _run_multi_reviewer_analysis,
)
from trw_mcp.tools._review_cross_model import _reviewer_is_same_family


@dataclass
class _DispatchResultStub:
    """Minimal stand-in for DispatchResult — only what the seam reads."""

    timed_out: bool
    exit_code: int | None
    text: str
    structured: dict[str, object] | None = None


class TestNormalizeSeverity:
    """_normalize_severity maps external labels to internal levels."""

    def test_error_maps_to_critical(self) -> None:
        assert _normalize_severity("error") == "critical"

    def test_critical_maps_to_critical(self) -> None:
        assert _normalize_severity("critical") == "critical"

    def test_high_maps_to_critical(self) -> None:
        assert _normalize_severity("high") == "critical"

    def test_warning_maps_to_warning(self) -> None:
        assert _normalize_severity("warning") == "warning"

    def test_medium_maps_to_warning(self) -> None:
        assert _normalize_severity("medium") == "warning"

    def test_info_maps_to_info(self) -> None:
        assert _normalize_severity("info") == "info"

    def test_unknown_maps_to_info(self) -> None:
        assert _normalize_severity("unknown") == "info"

    def test_empty_string_maps_to_info(self) -> None:
        assert _normalize_severity("") == "info"

    def test_case_insensitive_error(self) -> None:
        assert _normalize_severity("ERROR") == "critical"

    def test_case_insensitive_warning(self) -> None:
        assert _normalize_severity("WARNING") == "warning"

    def test_strips_whitespace(self) -> None:
        assert _normalize_severity("  high  ") == "critical"


class TestInvokeCrossModelReview:
    """_invoke_cross_model_review dispatches another coding-agent CLI (PRD-CORE-270)."""

    @pytest.mark.parametrize("diff", ["", "+ some diff content\n- removed line"])
    def test_no_reviewer_configured_returns_none_not_empty_list(self, diff: str) -> None:
        """None means "nothing was contacted" — distinct from "provider said nothing".

        Returning ``[]`` here made handle_cross_model_mode report
        ``provider_returned_empty``, blaming a provider TRW never called. The
        caller maps None to ``provider_integration_absent``. The meaning of None
        narrowed when the transport landed -- it was "no transport exists", it is
        now "no reviewer configured" -- but the honesty rule it protects is the
        same, so the distinction is still asserted here.
        """
        config = _make_config()
        config.cross_model_provider = ""
        assert _invoke_cross_model_review(diff, config) is None

    def test_unsupported_client_is_unavailable_not_empty(self) -> None:
        """A value that names no dispatch client must not read as a clean review.

        The field used to hold a bare model name (``gemini-2.5-pro``). Such a
        value cannot address the dispatch subsystem, and reporting it as "ran,
        found nothing" would turn a misconfiguration into an apparently passing
        review (FR03 outcome (c)).
        """
        config = _make_config()
        config.cross_model_provider = "gemini-2.5-pro"
        with pytest.raises(CrossModelUnavailable, match="not a dispatch client"):
            _invoke_cross_model_review("+ diff", config)

    def test_timeout_is_incomplete_not_empty(self) -> None:
        """A reviewer that ran out of time did not find nothing (FR03 outcome (d))."""
        config = _make_config()
        config.cross_model_provider = "codex"
        result = _DispatchResultStub(timed_out=True, exit_code=None, text="")
        with patch("trw_mcp.tools._review_helpers.dispatch", return_value=result):
            with pytest.raises(CrossModelIncomplete):
                _invoke_cross_model_review("+ diff", config)

    def test_nonzero_exit_is_unavailable(self) -> None:
        """A CLI that refused to run is unavailable, not empty (FR03 outcome (c))."""
        config = _make_config()
        config.cross_model_provider = "codex"
        result = _DispatchResultStub(timed_out=False, exit_code=1, text="")
        with patch("trw_mcp.tools._review_helpers.dispatch", return_value=result):
            with pytest.raises(CrossModelUnavailable, match="exited 1"):
                _invoke_cross_model_review("+ diff", config)

    def test_unparseable_output_is_incomplete_not_empty(self) -> None:
        """Empty text from a clean exit yields incomplete, never [] (FR05)."""
        config = _make_config()
        config.cross_model_provider = "codex"
        result = _DispatchResultStub(timed_out=False, exit_code=0, text="   ")
        with patch("trw_mcp.tools._review_helpers.dispatch", return_value=result):
            with pytest.raises(CrossModelIncomplete):
                _invoke_cross_model_review("+ diff", config)

    def test_structured_empty_findings_is_a_real_empty_review(self) -> None:
        """An explicit empty findings list IS "ran, found nothing" (FR03 outcome (b))."""
        config = _make_config()
        config.cross_model_provider = "codex"
        result = _DispatchResultStub(timed_out=False, exit_code=0, text="none", structured={"findings": []})
        with patch("trw_mcp.tools._review_helpers.dispatch", return_value=result):
            assert _invoke_cross_model_review("+ diff", config) == []

    def test_dispatch_request_is_read_only_and_review_roled(self) -> None:
        """The child runs read-only and carries the review preamble (NFR02)."""
        config = _make_config()
        config.cross_model_provider = "codex"
        captured: dict[str, object] = {}

        def _capture(request: object) -> object:
            captured["request"] = request
            return _DispatchResultStub(timed_out=False, exit_code=0, text="ok", structured={"findings": []})

        with patch("trw_mcp.tools._review_helpers.dispatch", side_effect=_capture):
            _invoke_cross_model_review("+ diff", config)

        request = captured["request"]
        assert request.read_only is True
        assert request.client == "codex"
        assert "second-opinion reviewer" in request.prompt
        assert "+ diff" in request.prompt


class TestRunMultiReviewerAnalysis:
    """_run_multi_reviewer_analysis performs basic structural diff analysis."""

    def test_empty_diff_returns_empty_findings(self) -> None:
        config = _make_config()
        result = _run_multi_reviewer_analysis("", config)
        assert result["findings"] == []

    def test_empty_diff_claims_no_reviewer_roles(self) -> None:
        """A scan that ran nothing must not claim the full role list.

        This previously reported all six roles for a TODO/FIXME grep, and that
        claim was persisted verbatim as the receipt's realized_reviewer_roles.
        """
        result = _run_multi_reviewer_analysis("", _make_config())
        assert result["reviewer_roles_run"] == []

    def test_marker_scan_claims_only_the_role_it_actually_ran(self) -> None:
        diff = "+++ b/foo.py\n+ # TODO: fix this later\n"
        result = _run_multi_reviewer_analysis(diff, _make_config())
        assert result["reviewer_roles_run"] == ["style"]
        assert set(result["reviewer_roles_run"]).issubset(set(REVIEWER_ROLES))

    def test_empty_diff_has_no_errors(self) -> None:
        config = _make_config()
        result = _run_multi_reviewer_analysis("", config)
        assert result["reviewer_errors"] == []

    def test_detects_todo_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/foo.py\n+ # TODO: fix this later\n- removed line\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1
        descriptions = [str(f["description"]) for f in findings]
        assert any("TODO" in description or "todo" in description.lower() for description in descriptions)

    def test_detects_fixme_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/bar.py\n+ # FIXME: this is broken\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1

    def test_detects_hack_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/baz.py\n+ # HACK: workaround for issue #42\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1

    def test_detects_xxx_in_added_lines(self) -> None:
        config = _make_config()
        diff = "+++ b/qux.py\n+ # XXX: needs attention\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1

    def test_no_findings_for_clean_diff(self) -> None:
        config = _make_config()
        diff = "+++ b/clean.py\n+ def add(a, b):\n+     return a + b\n- def old_add(a, b):\n"
        result = _run_multi_reviewer_analysis(diff, config)
        assert result["findings"] == []

    def test_ignores_diff_header_lines(self) -> None:
        """Lines starting with +++ (file headers) are not treated as added lines."""
        config = _make_config()
        diff = "+++ b/TODO.py\n"
        result = _run_multi_reviewer_analysis(diff, config)
        assert result["findings"] == []

    def test_finding_has_correct_structure(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert len(findings) >= 1
        finding = findings[0]
        assert "reviewer_role" in finding
        assert "confidence" in finding
        assert "category" in finding
        assert "severity" in finding
        assert "description" in finding

    def test_finding_severity_is_info(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert all(finding["severity"] == "info" for finding in findings)

    def test_finding_reviewer_role_is_style(self) -> None:
        config = _make_config()
        diff = "+++ b/work.py\n+ # TODO: finish implementation\n"
        result = _run_multi_reviewer_analysis(diff, config)
        findings = result["findings"]
        assert all(finding["reviewer_role"] == "style" for finding in findings)

    def test_result_carries_every_key_handle_auto_mode_reads(self) -> None:
        """The five keys the auto-mode handler consumes must all be present.

        Replaces an ``isinstance(result, dict)`` check, which an empty ``{}``
        would satisfy — and an empty dict is exactly the failure that matters
        here: ``handle_auto_mode`` reads ``auto_analysis_limited`` with a
        ``False`` default, so a result missing that key would be treated as a
        SUBSTANTIVE review instead of a limited pattern scan.
        """
        config = _make_config()

        result = _run_multi_reviewer_analysis("some diff", config)

        assert set(result) >= {
            "reviewer_roles_run",
            "reviewer_errors",
            "findings",
            "auto_analysis_limited",
            "limited_reason",
        }
        # The pattern scan is never substantive, and it says so in the payload.
        assert result["auto_analysis_limited"] is True
        assert result["limited_reason"]


class TestComputeVerdict:
    """_compute_verdict derives pass/warn/block from finding severities."""

    def test_empty_findings_returns_pass(self) -> None:
        assert _compute_verdict([]) == "pass"

    def test_info_only_returns_pass(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "info"}]
        assert _compute_verdict(findings) == "pass"

    def test_warning_returns_warn(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "warning"}]
        assert _compute_verdict(findings) == "warn"

    def test_critical_returns_block(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "critical"}]
        assert _compute_verdict(findings) == "block"

    def test_critical_with_warning_returns_block(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "critical"}, {"severity": "warning"}]
        assert _compute_verdict(findings) == "block"

    def test_multiple_warnings_no_critical_returns_warn(self) -> None:
        findings: list[dict[str, str]] = [
            {"severity": "warning"},
            {"severity": "info"},
            {"severity": "warning"},
        ]
        assert _compute_verdict(findings) == "warn"

    def test_multiple_info_returns_pass(self) -> None:
        findings: list[dict[str, str]] = [{"severity": "info"}, {"severity": "info"}]
        assert _compute_verdict(findings) == "pass"

    def test_missing_severity_key_returns_pass(self) -> None:
        findings: list[dict[str, str]] = [{"category": "style"}]
        assert _compute_verdict(findings) == "pass"


class TestGetGitDiff:
    """_get_git_diff returns diff text or empty string on any error."""

    @patch("trw_mcp.tools._review_helpers.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run: Any) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="diff --git a/foo.py b/foo.py\n+new line\n",
            stderr="",
        )
        result = _get_git_diff()
        assert "diff --git" in result
        assert "+new line" in result

    @patch(
        "trw_mcp.tools._review_helpers.subprocess.run",
        side_effect=FileNotFoundError("git: command not found"),
    )
    def test_returns_empty_string_on_file_not_found(self, mock_run: Any) -> None:
        result = _get_git_diff()
        assert result == ""

    @patch(
        "trw_mcp.tools._review_helpers.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30),
    )
    def test_returns_empty_string_on_timeout(self, mock_run: Any) -> None:
        result = _get_git_diff()
        assert result == ""

    @patch(
        "trw_mcp.tools._review_helpers.subprocess.run",
        side_effect=OSError("permission denied"),
    )
    def test_returns_empty_string_on_oserror(self, mock_run: Any) -> None:
        result = _get_git_diff()
        assert result == ""

    @patch("trw_mcp.tools._review_helpers.subprocess.run")
    def test_returns_empty_string_on_empty_diff(self, mock_run: Any) -> None:
        mock_run.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        result = _get_git_diff()
        assert result == ""

    @patch("trw_mcp.tools._review_helpers.subprocess.run")
    def test_returns_stdout_regardless_of_returncode(self, mock_run: Any) -> None:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="some diff output\n",
            stderr="",
        )
        result = _get_git_diff()
        assert result == "some diff output\n"


class TestNormalizeSeverityEdgeCases:
    """Additional edge cases for _normalize_severity."""

    def test_low_maps_to_info(self) -> None:
        assert _normalize_severity("low") == "info"

    def test_none_like_string_maps_to_info(self) -> None:
        assert _normalize_severity("none") == "info"

    def test_mixed_case_medium_maps_to_warning(self) -> None:
        assert _normalize_severity("Medium") == "warning"

    def test_padded_critical_maps_to_critical(self) -> None:
        assert _normalize_severity("  critical  ") == "critical"

    def test_tab_padded_high_maps_to_critical(self) -> None:
        assert _normalize_severity("\thigh\t") == "critical"

    def test_very_long_unknown_string_maps_to_info(self) -> None:
        assert _normalize_severity("x" * 10_000) == "info"


class TestReviewerRolesConstant:
    """REVIEWER_ROLES constant is correctly defined."""

    def test_reviewer_roles_is_tuple(self) -> None:
        assert isinstance(REVIEWER_ROLES, tuple)

    def test_reviewer_roles_has_six_entries(self) -> None:
        assert len(REVIEWER_ROLES) == 6

    def test_reviewer_roles_contains_expected_roles(self) -> None:
        expected = {"correctness", "security", "test-quality", "performance", "style", "spec-compliance"}
        assert set(REVIEWER_ROLES) == expected


class TestCrossModelAnswerRejection:
    """Only a findings DOCUMENT counts as a review (PRD-CORE-270-FR05).

    Every case here was verified by an independent codex review on 2026-09-11 to
    have previously produced ``verdict=pass, critical_count=0,
    review_family_coverage=cross_family, substantive=true``.
    """

    @staticmethod
    def _invoke(text: str, structured: dict[str, object] | None = None) -> list[dict[str, str]] | None:
        config = _make_config()
        config.cross_model_provider = "codex"
        result = _DispatchResultStub(timed_out=False, exit_code=0, text=text, structured=structured)
        with patch("trw_mcp.tools._review_helpers.dispatch", return_value=result):
            return _invoke_cross_model_review("+ diff", config)

    @pytest.mark.parametrize(
        "answer",
        [
            "P0: arbitrary code execution. Overall verdict: BLOCK.",
            "{not valid json",
            "I could not review this diff because authentication is required.",
            "LGTM",
        ],
        ids=["blocking-prose", "malformed-json", "auth-failure", "approval-prose"],
    )
    def test_prose_is_incomplete_never_a_passing_review(self, answer: str) -> None:
        with pytest.raises(CrossModelIncomplete, match="did not return a findings document"):
            self._invoke(answer)

    def test_incomplete_message_carries_the_reviewers_words(self) -> None:
        """The answer survives as diagnostics — just not as a verdict input."""
        with pytest.raises(CrossModelIncomplete, match="Overall verdict: BLOCK"):
            self._invoke("P0: rce. Overall verdict: BLOCK.")

    def test_non_object_finding_elements_are_rejected_not_dropped(self) -> None:
        """Dropping unreadable elements reported a P0 list as "found nothing"."""
        with pytest.raises(CrossModelIncomplete):
            self._invoke("", structured={"findings": ["P0: arbitrary code execution"]})

    def test_explicit_error_envelope_is_not_an_empty_review(self) -> None:
        with pytest.raises(CrossModelIncomplete):
            self._invoke("", structured={"is_error": True, "findings": [], "result": "Review unavailable"})

    def test_findings_document_in_text_is_decoded(self) -> None:
        """Claude's normalizer puts the ANSWER in text, the envelope in structured."""
        findings = self._invoke(
            '```json\n{"findings": [{"category": "security", "severity": "critical", "description": "rce"}]}\n```',
            structured={"type": "result", "subtype": "success"},
        )
        assert findings == [{"category": "security", "severity": "critical", "description": "rce"}]


class TestReviewerSameFamily:
    """A reviewer provably in the host's family is not cross-family (FR04)."""

    def test_same_family_is_detected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")
        assert _reviewer_is_same_family("codex") is True

    def test_different_family_is_not_suppressed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_CLIENT_PROFILE", "claude-code")
        assert _reviewer_is_same_family("codex") is False

    @pytest.mark.parametrize("reviewer", ["cursor-cli", "opencode", "copilot"])
    def test_unknown_reviewer_family_cannot_prove_sameness(
        self, reviewer: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Model-agnostic front-ends leave existing behaviour untouched."""
        monkeypatch.setenv("TRW_CLIENT_PROFILE", "codex")
        assert _reviewer_is_same_family(reviewer) is False

    def test_unknown_host_cannot_prove_sameness(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TRW_CLIENT_PROFILE", "unknown")
        assert _reviewer_is_same_family("codex") is False
