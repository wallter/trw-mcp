# Parent facade: tools/_review_helpers.py
"""Auto-mode and cross-model review handlers.

Extracted from ``_review_helpers.py`` to keep the facade under the
500-line threshold.  All public names are re-exported from
``_review_helpers.py`` so existing import paths are preserved.

Note: shared helpers are accessed via ``_helpers.<name>`` (module reference)
rather than direct name imports so that ``patch("trw_mcp.tools._review_helpers._get_git_diff", ...)``
in tests correctly intercepts calls from this module.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from trw_mcp.models.typed_dicts import (
    AutoReviewResult,
    CrossModelReviewResult,
    MultiReviewerAnalysisResult,
)
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools import _review_helpers as _helpers

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

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
REASON_NO_DIFF = "no_diff"


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
) -> tuple[list[dict[str, str]], bool]:
    """Run the QUAL-027 same-family multi-reviewer path as the fallback substrate.

    Returns ``(verdict_findings, honeypots_present)``. ``verdict_findings`` is the
    severity-only list consumed by ``_compute_verdict``. This NEVER raises: the
    multi-reviewer path is the already-tested QUAL-027 entry point.
    """
    # The lazy ``__getattr__`` re-export in _review_helpers.py (see its
    # _REEXPORT_MAP / module docstring) types this re-exported callable as
    # ``object``, so mypy flags the call; the runtime target is the real function.
    analysis = _helpers._run_multi_reviewer_analysis(diff, config)  # type: ignore[operator]
    raw_findings = analysis.get("findings", [])
    if not isinstance(raw_findings, list):
        raw_findings = []
    verdict_findings: list[dict[str, str]] = [
        {"severity": str(f.get("severity", "info"))} for f in raw_findings if isinstance(f, dict)
    ]
    return verdict_findings, _honeypots_in_findings(raw_findings)


def handle_auto_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    reviewer_findings: list[dict[str, object]] | None,
    prd_ids: list[str] | None = None,
) -> AutoReviewResult:
    """Handle the auto review mode -- multi-reviewer analysis, filter, persist."""
    diff = _helpers._get_git_diff()

    if reviewer_findings is not None:
        # Real pre-collected findings from client-side multi-agent review:
        # this is a substantive review, NOT the limited pattern-scan path.
        analysis: MultiReviewerAnalysisResult = {
            "reviewer_roles_run": list(_helpers.REVIEWER_ROLES),
            "reviewer_errors": [],
            "findings": reviewer_findings,
            "auto_analysis_limited": False,
            "limited_reason": "",
        }
    else:
        # Pattern-scan-only fallback: _run_multi_reviewer_analysis flags this
        # as auto_analysis_limited=True so the artifact cannot pose as a
        # substantive review (see _review_multi.PATTERN_SCAN_LIMITED_REASON).
        # The lazy ``__getattr__`` re-export in _review_helpers.py (see its
        # _REEXPORT_MAP / module docstring) types this re-exported callable as
        # ``object``, so mypy flags the call; the runtime target is the real function.
        analysis = _helpers._run_multi_reviewer_analysis(diff, config)  # type: ignore[operator]

    auto_analysis_limited = bool(analysis.get("auto_analysis_limited", False))
    limited_reason = str(analysis.get("limited_reason", "")) if auto_analysis_limited else ""

    all_auto_findings = analysis.get("findings", [])
    if not isinstance(all_auto_findings, list):
        all_auto_findings = []

    # Multi-agent review confidence threshold (QUAL-027): 0-100 scale
    confidence_threshold = config.review_confidence_threshold

    # Filter findings by confidence threshold
    # ReviewFinding.confidence is 0.0-1.0 float; config threshold is 0-100 int.
    # Normalize both to 0-100 for comparison.
    surfaced: list[dict[str, object]] = []
    for f in all_auto_findings:
        if not isinstance(f, dict):
            continue
        confidence = f.get("confidence", 0)
        if isinstance(confidence, (int, float)):
            # Normalize: values <= 1.0 are 0-1 scale, multiply to 0-100
            confidence_pct = confidence * 100 if confidence <= 1.0 else confidence
            if confidence_pct >= confidence_threshold:
                surfaced.append(f)

    # Compute verdict from surfaced findings only
    surfaced_for_verdict: list[dict[str, str]] = [{"severity": str(f.get("severity", "info"))} for f in surfaced]
    verdict = _helpers._compute_verdict(surfaced_for_verdict)

    # Count critical findings among surfaced for downstream ceremony tracking
    critical_count = sum(
        1 for f in surfaced if isinstance(f, dict) and str(f.get("severity", "")).lower() == "critical"
    )

    result: AutoReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "auto",
        "reviewer_roles_run": analysis.get("reviewer_roles_run", []),
        "reviewer_errors": analysis.get("reviewer_errors", []),
        "surfaced_findings_count": len(surfaced),
        "total_findings_count": len(all_auto_findings),
        "confidence_threshold": confidence_threshold,
        "critical_count": critical_count,
        "run_path": str(resolved_run) if resolved_run else None,
        # Honest labeling: True iff only the pattern-scan ran (no substantive
        # multi-reviewer / cross-model findings). Downstream gates must not
        # accept a limited auto-review as a code-quality signal.
        "auto_analysis_limited": auto_analysis_limited,
        "limited_reason": limited_reason,
        # PRD-QUAL-108-FR01: auto mode is same-family today (OQ1), so coverage is
        # always single_family; a caveat names the same-family-only limitation.
        "review_family_coverage": COVERAGE_SINGLE_FAMILY,
        "single_family_caveat": _build_single_family_caveat(REASON_CROSS_MODEL_DISABLED, "auto-mode (same-family)"),
    }
    if auto_analysis_limited:
        logger.info(
            "auto_review_pattern_scan_limited",
            review_id=review_id,
            verdict=verdict,
            reason=limited_reason,
        )

    # SOC 2 fields (INFRA-027-FR04) -- compute from available context
    diff_hash = hashlib.sha256((diff or "").encode()).hexdigest() if diff else ""
    roles_run = analysis.get("reviewer_roles_run", [])
    reviewer_role_str = ", ".join(str(r) for r in roles_run) if isinstance(roles_run, list) else ""
    try:
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        retention_dt = ts_dt + timedelta(days=config.compliance_review_retention_days)
        retention_expires = retention_dt.isoformat()
    except (ValueError, AttributeError):
        retention_expires = ""

    result["review_yaml"] = _helpers._persist_review_artifact(
        resolved_run,
        {
            "review_id": review_id,
            "timestamp": ts,
            "verdict": verdict,
            "mode": "auto",
            "reviewer_roles_run": roles_run,
            "reviewer_errors": analysis.get("reviewer_errors", []),
            "surfaced_findings_count": len(surfaced),
            "total_findings_count": len(all_auto_findings),
            "confidence_threshold": confidence_threshold,
            "findings": surfaced,
            # Honest labeling persisted into the artifact so any reader of
            # review.yaml can tell a limited pattern-scan from a real review.
            "auto_analysis_limited": auto_analysis_limited,
            "limited_reason": limited_reason,
            # PRD-QUAL-108: coverage stamp surfaced in the persisted artifact (US3).
            "review_family_coverage": COVERAGE_SINGLE_FAMILY,
            "single_family_caveat": _build_single_family_caveat(REASON_CROSS_MODEL_DISABLED, "auto-mode (same-family)"),
            "review_kind": "pattern-scan (limited)" if auto_analysis_limited else "multi-reviewer",
            # SOC 2 fields (INFRA-027-FR04)
            "reviewer_id": f"trw-auto-{review_id}",
            "reviewer_role": reviewer_role_str,
            "git_diff_hash": diff_hash,
            "human_escalation_path": "Escalate to team lead via GitHub PR comment",
            "retention_expires": retention_expires,
        },
        {
            "review_id": review_id,
            "verdict": verdict,
            "mode": "auto",
            "surfaced_findings": len(surfaced),
            "total_findings": len(all_auto_findings),
            "auto_analysis_limited": auto_analysis_limited,
            "prd_ids": list(prd_ids) if prd_ids else [],
        },
    )

    # Write supplementary auto-mode artifacts when a run is active
    if resolved_run is not None:
        writer = FileStateWriter()

        # review-all.yaml -- ALL findings unfiltered
        review_all_path = resolved_run / "meta" / "review-all.yaml"
        review_all_data: dict[str, object] = {
            "review_id": review_id,
            "timestamp": ts,
            "mode": "auto",
            "total_findings_count": len(all_auto_findings),
            "findings": all_auto_findings,
        }
        writer.write_yaml(review_all_path, review_all_data)

        # integration-review.yaml -- integration findings only (INFRA-027-FR03)
        integration_findings = [
            f for f in all_auto_findings if isinstance(f, dict) and f.get("reviewer_role") == "integration"
        ]
        if integration_findings:
            # Compute verdict from integration findings
            int_critical = sum(
                1 for f in integration_findings if isinstance(f, dict) and f.get("severity") == "critical"
            )
            int_verdict = "block" if int_critical > 0 else ("warn" if integration_findings else "pass")

            integration_path = resolved_run / "meta" / "integration-review.yaml"
            integration_data: dict[str, object] = {
                "review_id": review_id,
                "timestamp": ts,
                "mode": "auto",
                "run_id": resolved_run.name if resolved_run else "",
                "reviewer_id": f"trw-auto-{review_id}",
                "reviewer_role": "integration",
                "git_diff_hash": diff_hash,
                "shards_reviewed": [],
                "checks_performed": [
                    "duplicate_functions",
                    "inconsistent_types",
                    "unresolved_imports",
                    "api_contract_mismatch",
                ],
                "findings": integration_findings,
                "verdict": int_verdict,
                "human_escalation_path": "Escalate to team lead via GitHub PR comment",
            }
            writer.write_yaml(integration_path, integration_data)

    return result


def handle_cross_model_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    prd_ids: list[str] | None = None,
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
            if not raw_findings:
                reason_token = REASON_PROVIDER_RETURNED_EMPTY
                cross_model_skipped = True
            else:
                cross_model_findings.extend(
                    {
                        "category": rf.get("category", "general"),
                        "severity": _helpers._normalize_severity(rf.get("severity", "info")),
                        "description": rf.get("description", ""),
                        "source": "cross_model",
                        "provider": config.cross_model_provider,
                    }
                    for rf in raw_findings
                )

    # Coverage is cross_family ONLY when realized cross-family findings exist
    # (NFR02 truthfulness invariant). Otherwise fall back to same-family.
    cross_family_realized = reason_token is None and bool(cross_model_findings)
    honeypots_present = False
    # Realized same-family findings on the degraded path. ``total_findings`` below
    # counts only cross-family findings (0 when degraded), so this keeps the
    # verdict-driving evidence count visible (P2-QUAL-108-03).
    same_family_findings_count = 0

    if cross_family_realized:
        review_family_coverage = COVERAGE_CROSS_FAMILY
        single_family_caveat = ""
        verdict = _helpers._compute_verdict(cross_model_findings)
    else:
        # FR03 graceful degradation: compute the verdict from same-family
        # multi-seed + honeypot findings. Never raises, never blocks on missing
        # cross-family access.
        review_family_coverage = COVERAGE_SINGLE_FAMILY
        single_family_caveat = _build_single_family_caveat(
            reason_token or REASON_CROSS_MODEL_DISABLED, config.cross_model_provider
        )
        fallback_findings, honeypots_present = _same_family_fallback(diff, config)
        same_family_findings_count = len(fallback_findings)
        verdict = _helpers._compute_verdict(fallback_findings)

    result: CrossModelReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "cross_model",
        "cross_model_skipped": cross_model_skipped,
        "cross_model_provider": config.cross_model_provider,
        "total_findings": len(cross_model_findings),
        "same_family_findings_count": same_family_findings_count,
        "run_path": str(resolved_run) if resolved_run else None,
        "review_family_coverage": review_family_coverage,
        "single_family_caveat": single_family_caveat,
        "honeypots_present": honeypots_present,
    }

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
            # PRD-QUAL-108: coverage + caveat surfaced in the persisted artifact (US3).
            "review_family_coverage": review_family_coverage,
            "single_family_caveat": single_family_caveat,
            "honeypots_present": honeypots_present,
            "same_family_findings_count": same_family_findings_count,
        },
        {
            "review_id": review_id,
            "verdict": verdict,
            "mode": "cross_model",
            "cross_model_skipped": cross_model_skipped,
            "review_family_coverage": review_family_coverage,
            "prd_ids": list(prd_ids) if prd_ids else [],
        },
    )
    return result
