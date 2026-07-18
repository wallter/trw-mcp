"""PRD-QUAL-108: cross-family review graceful degradation tests.

Covers the additive ``review_family_coverage`` enum stamp, the single-family
caveat (closed reason tokens + provider name), the same-family + honeypot
fallback that never raises/blocks/errors, the single
``_cross_family_available`` predicate carrying the SEAM(PRD-DIST-2444) marker,
and the NFR02 truthfulness invariant (coverage reflects REALIZED cross-family
findings, never configuration intent).

Unit-only — no live provider; the provider dispatch is patched/injected.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests._review_helpers_support import _make_config
from trw_mcp.state.persistence import FileStateReader
from trw_mcp.tools._review_auto import handle_auto_mode, handle_cross_model_mode
from trw_mcp.tools._review_helpers import _cross_family_available

from ._review_helpers_support import run_dir  # noqa: F401

_HELPERS = "trw_mcp.tools._review_helpers"
_SEAM_FILE = Path(__file__).resolve().parents[1] / "src" / "trw_mcp" / "tools" / "_review_helpers.py"


# --------------------------------------------------------------------------
# FR04 — single availability predicate + discovery seam
# --------------------------------------------------------------------------


class TestCrossFamilyPredicate:
    def test_cross_family_predicate_truth_table(self) -> None:
        disabled = _make_config(cross_model_enabled=False, cross_model_provider="gemini-2.5-pro")
        assert _cross_family_available(disabled) is False

        enabled = _make_config(cross_model_enabled=True, cross_model_provider="gemini-2.5-pro")
        assert _cross_family_available(enabled) is True

        no_provider = _make_config(cross_model_enabled=True, cross_model_provider="")
        assert _cross_family_available(no_provider) is False

    def test_seam_marker_present(self) -> None:
        text = _SEAM_FILE.read_text(encoding="utf-8")
        assert text.count("SEAM(PRD-DIST-2444)") == 1
        assert "def _cross_family_available" in text


# --------------------------------------------------------------------------
# FR01 — verdict family-coverage field on every verdict
# --------------------------------------------------------------------------


class TestEveryVerdictHasCoverage:
    @pytest.mark.parametrize(
        ("findings", "expected_verdict"),
        [
            ([{"category": "c", "severity": "critical", "description": "x"}], "block"),
            ([{"category": "c", "severity": "warning", "description": "x"}], "warn"),
            ([], "pass"),
        ],
    )
    def test_cross_model_verdicts_carry_coverage(
        self, run_dir: Path, findings: list[dict[str, str]], expected_verdict: str
    ) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=findings),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-cov", "2026-03-01T00:00:00Z")
        assert "review_family_coverage" in result
        assert result["review_family_coverage"] in {"cross_family", "single_family"}
        assert result["verdict"] == expected_verdict

    def test_auto_mode_verdict_carries_coverage(self, run_dir: Path) -> None:
        config = _make_config()
        reviewer_findings = [
            {"reviewer_role": "security", "confidence": 90, "severity": "critical", "description": "x"},
        ]
        with patch(f"{_HELPERS}._get_git_diff", return_value="diff content"):
            result = handle_auto_mode(config, run_dir, "rev-auto", "2026-03-01T00:00:00Z", reviewer_findings)
        assert result["review_family_coverage"] == "single_family"

    def test_cross_family_when_findings_returned(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        findings = [{"category": "c", "severity": "warning", "description": "x"}]
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=findings),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-cf", "2026-03-01T00:00:00Z")
        assert result["review_family_coverage"] == "cross_family"


# --------------------------------------------------------------------------
# FR02 — single-family caveat stamp (reason token + provider)
# --------------------------------------------------------------------------


class TestSingleFamilyCaveat:
    def test_single_family_caveat_reason_token(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False, cross_model_provider="gemini-2.5-pro")
        with patch(f"{_HELPERS}._get_git_diff", return_value="diff content"):
            result = handle_cross_model_mode(config, run_dir, "rev-caveat", "2026-03-01T00:00:00Z")
        assert result["review_family_coverage"] == "single_family"
        caveat = result["single_family_caveat"]
        assert "cross_model_disabled" in caveat
        assert "gemini-2.5-pro" in caveat
        # Persisted into the surfaced report fields (US3).
        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert "cross_model_disabled" in data["single_family_caveat"]
        assert "gemini-2.5-pro" in data["single_family_caveat"]

    def test_no_diff_reason_token(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        with patch(f"{_HELPERS}._get_git_diff", return_value=""):
            result = handle_cross_model_mode(config, run_dir, "rev-nodiff", "2026-03-01T00:00:00Z")
        assert "no_diff" in result["single_family_caveat"]
        # FR03: degradation never emits an ``error`` verdict.
        assert result["verdict"] in {"block", "warn", "pass"}
        # FR01: no-diff is a config-realized single-family degradation.
        assert result["review_family_coverage"] == "single_family"

    def test_provider_returned_empty_reason_token(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=[]),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-empty", "2026-03-01T00:00:00Z")
        assert result["review_family_coverage"] == "single_family"
        assert "provider_returned_empty" in result["single_family_caveat"]

    def test_provider_placeholder_finding_degrades_to_limited_fallback(self, run_dir: Path) -> None:
        """A non-empty provider payload is not realized evidence unless it validates."""
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=[{}]),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-invalid", "2026-03-01T00:00:00Z")

        assert result["cross_model_skipped"] is True
        assert result["review_family_coverage"] == "single_family"
        assert result["auto_analysis_limited"] is True
        assert result["substantive"] is False
        artifact = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert artifact["cross_model_findings"] == []
        assert artifact["substantive"] is False

    def test_cross_family_no_caveat(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        findings = [{"category": "c", "severity": "info", "description": "x"}]
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=findings),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-cf2", "2026-03-01T00:00:00Z")
        assert result["review_family_coverage"] == "cross_family"
        assert not result.get("single_family_caveat")


# --------------------------------------------------------------------------
# FR03 — same-family + honeypot fallback (no hard-require)
# --------------------------------------------------------------------------


class TestFallbackNoRaise:
    def test_unreachable_provider_falls_back_no_raise(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")

        def _boom(diff: str, cfg: object) -> list[dict[str, str]]:
            raise ConnectionError("provider unreachable")

        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", side_effect=_boom),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-unreach", "2026-03-01T00:00:00Z")
        assert result["verdict"] in {"block", "warn", "pass"}
        assert result["verdict"] != "error"
        assert result["review_family_coverage"] == "single_family"
        assert "provider_unreachable" in result["single_family_caveat"]

    def test_fallback_uses_same_family_findings(self, run_dir: Path) -> None:
        """Unreachable provider falls back to same-family multi-reviewer findings."""
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        fallback_analysis = {
            "reviewer_roles_run": ["security"],
            "reviewer_errors": [],
            "findings": [
                {
                    "reviewer_role": "security",
                    "confidence": 95,
                    "category": "security",
                    "severity": "critical",
                    "description": "x",
                },
            ],
            "auto_analysis_limited": False,
            "limited_reason": "",
        }
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", side_effect=ConnectionError("x")),
            patch(f"{_HELPERS}._run_multi_reviewer_analysis", return_value=fallback_analysis),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-fb", "2026-03-01T00:00:00Z")
        # Verdict computed from the same-family critical finding.
        assert result["verdict"] == "block"
        assert result["review_family_coverage"] == "single_family"
        assert result["auto_analysis_limited"] is False
        assert result["limited_reason"] == ""
        assert result["substantive"] is True
        artifact = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert artifact["auto_analysis_limited"] is False
        assert artifact["substantive"] is True

    def test_honeypots_present_flag(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False, cross_model_provider="gpt-4o")
        honeypot_analysis = {
            "reviewer_roles_run": ["security"],
            "reviewer_errors": [],
            "findings": [
                {
                    "reviewer_role": "security",
                    "confidence": 90,
                    "severity": "info",
                    "description": "h",
                    "honeypot": True,
                },
            ],
            "auto_analysis_limited": False,
            "limited_reason": "",
        }
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._run_multi_reviewer_analysis", return_value=honeypot_analysis),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-hp", "2026-03-01T00:00:00Z")
        assert result["honeypots_present"] is True

    def test_honeypots_absent_flag_default_false(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=False, cross_model_provider="gpt-4o")
        with patch(f"{_HELPERS}._get_git_diff", return_value="diff content"):
            result = handle_cross_model_mode(config, run_dir, "rev-nohp", "2026-03-01T00:00:00Z")
        assert result["honeypots_present"] is False


# --------------------------------------------------------------------------
# NFR02 — truthfulness invariant: realized evidence, not config intent
# --------------------------------------------------------------------------


class TestTruthfulnessInvariant:
    def test_configured_but_empty_findings_is_single_family(self, run_dir: Path) -> None:
        """Provider configured + reachable but returns nothing => single_family."""
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=[]),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-inv", "2026-03-01T00:00:00Z")
        assert result["review_family_coverage"] == "single_family"


# --------------------------------------------------------------------------
# NFR03 — caveat hygiene: no provider response body / secrets
# --------------------------------------------------------------------------


class TestCaveatHygiene:
    def test_caveat_contains_no_provider_response_body(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        secret_body = "API_KEY=sk-supersecret-token-leak raw provider response body"

        def _boom(diff: str, cfg: object) -> list[dict[str, str]]:
            raise RuntimeError(secret_body)

        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", side_effect=_boom),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-sec", "2026-03-01T00:00:00Z")
        caveat = result["single_family_caveat"]
        assert "sk-supersecret-token-leak" not in caveat
        assert "raw provider response body" not in caveat
        # Only the reason token + provider name are present.
        assert "provider_unreachable" in caveat
        assert "gpt-4o" in caveat


# --------------------------------------------------------------------------
# P2-QUAL-108-03 — degraded-path findings count is not hidden behind
# total_findings (which counts only cross-family findings = 0 when degraded)
# --------------------------------------------------------------------------


class TestSameFamilyFindingsCount:
    def test_degraded_path_surfaces_same_family_count_not_just_total(self, run_dir: Path) -> None:
        """A block verdict on the degraded path must not read as total_findings=0.

        ``total_findings`` counts cross-family findings only (0 here, since the
        provider returned empty). The verdict is computed from same-family
        fallback findings, so ``same_family_findings_count`` must carry that
        count in both the return value and the persisted artifact.
        """
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        fallback_analysis = {
            "reviewer_roles_run": ["security", "correctness"],
            "reviewer_errors": [],
            "findings": [
                {
                    "reviewer_role": "security",
                    "confidence": 95,
                    "category": "security",
                    "severity": "critical",
                    "description": "a",
                },
                {
                    "reviewer_role": "correctness",
                    "confidence": 80,
                    "category": "correctness",
                    "severity": "warning",
                    "description": "b",
                },
            ],
            "auto_analysis_limited": False,
            "limited_reason": "",
        }
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=[]),
            patch(f"{_HELPERS}._run_multi_reviewer_analysis", return_value=fallback_analysis),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-sfc", "2026-03-01T00:00:00Z")

        # Degraded => coverage single_family, verdict driven by the critical finding.
        assert result["review_family_coverage"] == "single_family"
        assert result["verdict"] == "block"
        # The trap this guards against: total_findings=0 next to verdict=block.
        assert result["total_findings"] == 0
        assert result["same_family_findings_count"] == 2
        # Persisted artifact carries the same-family count too (US3 surfacing).
        data = FileStateReader().read_yaml(run_dir / "meta" / "review.yaml")
        assert data["same_family_findings_count"] == 2

    def test_cross_family_path_same_family_count_is_zero(self, run_dir: Path) -> None:
        config = _make_config(cross_model_enabled=True, cross_model_provider="gpt-4o")
        findings = [{"category": "c", "severity": "info", "description": "x"}]
        with (
            patch(f"{_HELPERS}._get_git_diff", return_value="diff content"),
            patch(f"{_HELPERS}._invoke_cross_model_review", return_value=findings),
        ):
            result = handle_cross_model_mode(config, run_dir, "rev-cfc", "2026-03-01T00:00:00Z")
        assert result["review_family_coverage"] == "cross_family"
        assert result["same_family_findings_count"] == 0
        assert result["total_findings"] == 1
