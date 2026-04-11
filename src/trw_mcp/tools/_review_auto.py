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
        analysis: MultiReviewerAnalysisResult = {
            "reviewer_roles_run": list(_helpers.REVIEWER_ROLES),
            "reviewer_errors": [],
            "findings": reviewer_findings,
        }
    else:
        analysis = _helpers._run_multi_reviewer_analysis(diff, config)  # type: ignore[operator]

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
    }

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
    """Handle the cross-model review mode -- get diff, invoke provider, persist."""
    diff = _helpers._get_git_diff()
    cross_model_skipped = False
    cross_model_findings: list[dict[str, str]] = []

    if not config.cross_model_review_enabled:
        cross_model_skipped = True
        logger.info("cross_model_review_disabled")
    elif not diff:
        cross_model_skipped = True
        logger.info("cross_model_review_no_diff")
    else:
        raw_findings = _helpers._invoke_cross_model_review(diff, config)
        if not raw_findings:
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

    verdict = _helpers._compute_verdict(cross_model_findings)

    result: CrossModelReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "cross_model",
        "cross_model_skipped": cross_model_skipped,
        "cross_model_provider": config.cross_model_provider,
        "total_findings": len(cross_model_findings),
        "run_path": str(resolved_run) if resolved_run else None,
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
        },
        {
            "review_id": review_id,
            "verdict": verdict,
            "mode": "cross_model",
            "cross_model_skipped": cross_model_skipped,
            "prd_ids": list(prd_ids) if prd_ids else [],
        },
    )
    return result
