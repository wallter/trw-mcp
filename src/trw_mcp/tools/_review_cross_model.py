# Parent facade: tools/_review_helpers.py
"""Cross-model (cross-family) review mode and the review-coverage vocabulary.

Extracted from ``_review_auto.py`` to keep that module under the 350
effective-LOC gate. All public names are re-exported from ``_review_auto.py``
(and, through it, from the ``_review_helpers.py`` facade) so existing import
paths are preserved.

This module owns the *coverage* concept — whether a verdict was reached with a
second model family in the loop, and, when it was not, the closed-set reason
token that says why. ``_review_auto.py`` imports that vocabulary from here
rather than the other way around, so there is no import cycle.

Note: shared helpers are accessed via ``_helpers.<name>`` (module reference)
rather than direct name imports so that
``patch("trw_mcp.tools._review_helpers._get_git_diff", ...)`` in tests
correctly intercepts calls from this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import CrossModelReviewResult
from trw_mcp.tools import _review_helpers as _helpers
from trw_mcp.tools._review_validation import normalize_review_findings

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._review_provenance import RunIdentity

logger = structlog.get_logger(__name__)

# PRD-QUAL-108-FR01/FR02: review family-coverage stamp + closed-set reason tokens.
COVERAGE_CROSS_FAMILY = "cross_family"
COVERAGE_SINGLE_FAMILY = "single_family"

# FR02 degradation reason tokens (closed set). The caveat string is built from a
# fixed template (reason token + provider NAME only) — never free interpolation
# of a provider response body or credentials (NFR03).
REASON_CROSS_MODEL_DISABLED = "cross_model_disabled"
REASON_PROVIDER_UNREACHABLE = "provider_unreachable"
REASON_PROVIDER_RETURNED_EMPTY = "provider_returned_empty"
# The provider is configured but TRW has no transport wired to the integration
# seam, so nothing was ever contacted. Distinct from PROVIDER_RETURNED_EMPTY,
# which blamed a provider that was never called.
REASON_PROVIDER_INTEGRATION_ABSENT = "provider_integration_absent"
REASON_NO_DIFF = "no_diff"
EMPTY_SAME_FAMILY_FALLBACK_LIMITED_REASON = (
    "same-family fallback contained no schema-valid findings and no typed independent-review receipt"
)


def _build_single_family_caveat(reason_token: str, provider: str) -> str:
    """Build the single-family caveat from a closed-set token + provider name.

    Fixed template only — never embeds provider response bodies, API keys, or
    raw error text (NFR03 security invariant).
    """
    provider_label = provider or "unset"
    return (
        f"single-family coverage ({reason_token}): cross-family review was not "
        f"realized for provider '{provider_label}'; verdict reflects same-family "
        f"multi-seed + honeypot findings only."
    )


def _honeypots_in_findings(findings: list[dict[str, object]]) -> bool:
    """True iff any same-family finding is flagged as a honeypot (FR03).

    Records *presence* only; authoring a honeypot corpus is out of scope (NG3).
    A finding is a honeypot if it carries a truthy ``honeypot`` flag.
    """
    return any(isinstance(f, dict) and bool(f.get("honeypot")) for f in findings)


def _same_family_fallback(
    diff: str,
    config: TRWConfig,
) -> tuple[list[dict[str, str]], bool, bool, str]:
    """Run the QUAL-027 same-family multi-reviewer path as the fallback substrate.

    Returns ``(verdict_findings, honeypots_present, analysis_limited,
    limited_reason)``. ``verdict_findings`` is the severity-only list consumed by
    ``_compute_verdict``. The honesty labels travel with the findings so a
    degraded marker scan cannot satisfy the substantive REVIEW gate. This NEVER
    raises: the multi-reviewer path is the already-tested QUAL-027 entry point.
    """
    # The lazy ``__getattr__`` re-export in _review_helpers.py (see its
    # _REEXPORT_MAP / module docstring) types this re-exported callable as
    # ``object``, so mypy flags the call; the runtime target is the real function.
    analysis = _helpers._run_multi_reviewer_analysis(diff, config)  # type: ignore[operator]
    raw_findings = analysis.get("findings", [])
    if not isinstance(raw_findings, list):
        raw_findings = []
    validated_findings, _rejections = normalize_review_findings(raw_findings, default_confidence=0.0)
    verdict_findings: list[dict[str, str]] = [{"severity": str(finding["severity"])} for finding in validated_findings]
    analysis_limited = bool(analysis.get("auto_analysis_limited", False))
    limited_reason = str(analysis.get("limited_reason", "")) if analysis_limited else ""
    if not analysis_limited and not validated_findings:
        analysis_limited = True
        limited_reason = EMPTY_SAME_FAMILY_FALLBACK_LIMITED_REASON
    return verdict_findings, _honeypots_in_findings(raw_findings), analysis_limited, limited_reason


def handle_cross_model_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    prd_ids: list[str] | None = None,
    *,
    verified_reviewer_identity: RunIdentity | None = None,
) -> CrossModelReviewResult:
    """Handle the cross-model review mode -- get diff, invoke provider, persist.

    PRD-QUAL-108: never hard-requires cross-family availability. When cross-family
    is unavailable (disabled / no diff / unreachable provider / empty result) the
    review degrades to the same-family multi-seed + honeypot path, computes a
    verdict from those findings, and stamps the verdict ``single_family`` with a
    closed-set caveat. The coverage stamp reflects REALIZED findings, never
    configuration intent (NFR02).
    """
    diff = _helpers._get_git_diff()
    cross_model_skipped = False
    cross_model_findings: list[dict[str, str]] = []
    # Determine the degradation reason (None => cross-family realized).
    reason_token: str | None = None
    cross_model_rejections: list[dict[str, object]] = []
    rejected_count = 0

    if not _helpers._cross_family_available(config):
        # Config-only unavailability (disabled or no provider configured).
        reason_token = REASON_CROSS_MODEL_DISABLED
        cross_model_skipped = True
        logger.info("cross_model_review_disabled")
    elif not diff:
        reason_token = REASON_NO_DIFF
        cross_model_skipped = True
        logger.info("cross_model_review_no_diff")
    else:
        try:
            raw_findings = _helpers._invoke_cross_model_review(diff, config)
        except Exception:  # trw:intentional fail-toward-single-family-coverage
            # FR03/NFR02: ANY provider error degrades to single-family rather than
            # raising or emitting an ``error`` verdict. The raw exception text is
            # deliberately NOT surfaced (NFR03) — only the reason token + provider.
            logger.info("cross_model_review_provider_unreachable", exc_info=True)
            raw_findings = []
            reason_token = REASON_PROVIDER_UNREACHABLE
            cross_model_skipped = True
        else:
            if raw_findings is None:
                # No transport was attempted — do not blame the provider.
                raw_findings = []
                reason_token = REASON_PROVIDER_INTEGRATION_ABSENT
            validated_cross_model_findings, cross_model_rejections = normalize_review_findings(raw_findings)
            rejected_count = len(raw_findings) - len(validated_cross_model_findings)
            if not validated_cross_model_findings:
                reason_token = reason_token or REASON_PROVIDER_RETURNED_EMPTY
                cross_model_skipped = True
            else:
                cross_model_findings.extend(
                    {
                        "category": str(finding["category"]),
                        "severity": str(finding["severity"]),
                        "description": str(finding["description"]),
                        "source": "cross_model",
                        "provider": config.cross_model_provider,
                    }
                    for finding in validated_cross_model_findings
                )

    # Coverage is cross_family ONLY when realized cross-family findings exist
    # (NFR02 truthfulness invariant). Otherwise fall back to same-family.
    cross_family_realized = reason_token is None and bool(cross_model_findings)
    honeypots_present = False
    # Realized same-family findings on the degraded path. ``total_findings`` below
    # counts only cross-family findings (0 when degraded), so this keeps the
    # verdict-driving evidence count visible (P2-QUAL-108-03).
    same_family_findings_count = 0
    auto_analysis_limited = False
    limited_reason = ""

    if cross_family_realized:
        review_family_coverage = COVERAGE_CROSS_FAMILY
        single_family_caveat = ""
        verdict_findings: list[dict[str, str]] = cross_model_findings
    else:
        # FR03 graceful degradation: compute the verdict from same-family
        # multi-seed + honeypot findings. Never raises, never blocks on missing
        # cross-family access.
        review_family_coverage = COVERAGE_SINGLE_FAMILY
        single_family_caveat = _build_single_family_caveat(
            reason_token or REASON_CROSS_MODEL_DISABLED, config.cross_model_provider
        )
        fallback_findings, honeypots_present, auto_analysis_limited, limited_reason = _same_family_fallback(
            diff, config
        )
        same_family_findings_count = len(fallback_findings)
        verdict_findings = fallback_findings

    # ONE list drives both the verdict and the critical count, so they can never
    # disagree. Reporting the count is load-bearing, not cosmetic: the delivery
    # gate reads ``critical_count`` off review.yaml and only blocks on
    # ``verdict == "block" AND critical_count > 0``. This mode never emitted the
    # field, so it defaulted to 0 and a substantive cross-model 'block' with real
    # critical findings passed the delivery gate and reported p0_count=0 to the
    # ceremony state — suppressing the "P0 findings detected" remediation nudge.
    verdict = _helpers._compute_verdict(verdict_findings)
    critical_count = sum(1 for f in verdict_findings if f.get("severity") == "critical")

    substantive = not auto_analysis_limited

    result: CrossModelReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "cross_model",
        "cross_model_skipped": cross_model_skipped,
        "cross_model_provider": config.cross_model_provider,
        "total_findings": len(cross_model_findings),
        "same_family_findings_count": same_family_findings_count,
        "critical_count": critical_count,
        "run_path": str(resolved_run) if resolved_run else None,
        "review_family_coverage": review_family_coverage,
        "single_family_caveat": single_family_caveat,
        "honeypots_present": honeypots_present,
        "auto_analysis_limited": auto_analysis_limited,
        "limited_reason": limited_reason,
        "substantive": substantive,
    }
    if rejected_count:
        logger.warning("cross_model_review_findings_rejected", review_id=review_id, rejected=rejected_count)
        result["rejected_findings_count"] = rejected_count
        result["rejected_findings"] = cross_model_rejections

    result["review_yaml"] = _helpers._persist_review_artifact(
        resolved_run,
        {
            "review_id": review_id,
            "timestamp": ts,
            "verdict": verdict,
            "mode": "cross_model",
            "cross_model_skipped": cross_model_skipped,
            "cross_model_provider": config.cross_model_provider,
            "cross_model_findings": cross_model_findings,
            # Persisted for the delivery gate, which reads critical_count off
            # review.yaml. Manual and auto mode have always written it; this mode
            # did not, so its 'block' verdicts were unenforceable.
            "critical_count": critical_count,
            # PRD-QUAL-108: coverage + caveat surfaced in the persisted artifact (US3).
            "review_family_coverage": review_family_coverage,
            "single_family_caveat": single_family_caveat,
            "honeypots_present": honeypots_present,
            "same_family_findings_count": same_family_findings_count,
            "auto_analysis_limited": auto_analysis_limited,
            "limited_reason": limited_reason,
            "substantive": substantive,
        },
        {
            "review_id": review_id,
            "verdict": verdict,
            "mode": "cross_model",
            "cross_model_skipped": cross_model_skipped,
            "review_family_coverage": review_family_coverage,
            "critical_count": critical_count,
            "auto_analysis_limited": auto_analysis_limited,
            "substantive": substantive,
            "prd_ids": list(prd_ids) if prd_ids else [],
        },
        cast("dict[str, object]", result),
        verified_reviewer_identity=verified_reviewer_identity,
    )
    return result
