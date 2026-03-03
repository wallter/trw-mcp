"""Extracted helpers for trw_review tool — finding validation, mode handlers.

Keeps the tool closure in review.py focused on dispatch while
business logic lives in testable pure-ish functions.

All references to review.py functions use lazy imports to preserve
test patchability (patching review._get_git_diff must affect these helpers).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger()


def validate_manual_findings(
    raw_findings: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Validate and normalize a list of manually-provided findings.

    Runs each finding through ReviewFinding model validation,
    normalizing severity levels to the canonical set.
    """
    from trw_mcp.models.run import ReviewFinding
    from trw_mcp.tools.review import _normalize_severity

    validated: list[dict[str, str]] = []
    for f in raw_findings:
        try:
            ReviewFinding(**f)  # type: ignore[arg-type]  # dict[str,str] coerced by Pydantic
            validated.append(f)
            if f.get("severity") not in ("critical", "warning", "info"):
                validated[-1] = {**f, "severity": "info"}
        except Exception:
            validated.append({
                **f,
                "severity": _normalize_severity(f.get("severity", "info")),
            })
    return validated


def count_by_severity(
    findings: list[dict[str, str]],
) -> tuple[int, int, int]:
    """Count findings by severity level.

    Returns:
        Tuple of (critical_count, warning_count, info_count).
    """
    critical = sum(1 for f in findings if f.get("severity") == "critical")
    warning = sum(1 for f in findings if f.get("severity") == "warning")
    info = sum(1 for f in findings if f.get("severity") == "info")
    return critical, warning, info


def handle_manual_mode(
    raw_findings: list[dict[str, str]],
    resolved_run: Path | None,
    review_id: str,
    ts: str,
) -> dict[str, object]:
    """Handle the manual review mode — validate findings, compute verdict, persist."""
    from trw_mcp.tools.review import _compute_verdict, _persist_review

    validated = validate_manual_findings(raw_findings)
    critical_count, warning_count, info_count = count_by_severity(validated)
    verdict = _compute_verdict(validated)

    result: dict[str, object] = {
        "review_id": review_id,
        "verdict": verdict,
        "total_findings": len(validated),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "run_path": str(resolved_run) if resolved_run else None,
    }

    result["review_yaml"] = _persist_review(
        resolved_run,
        {
            "review_id": review_id,
            "timestamp": ts,
            "verdict": verdict,
            "critical_count": critical_count,
            "warning_count": warning_count,
            "info_count": info_count,
            "findings": validated,
        },
        {
            "review_id": review_id,
            "verdict": verdict,
            "critical_count": critical_count,
            "warning_count": warning_count,
        },
    )
    return result


def handle_cross_model_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
) -> dict[str, object]:
    """Handle the cross-model review mode — get diff, invoke provider, persist."""
    from trw_mcp.tools.review import (
        _compute_verdict,
        _get_git_diff,
        _invoke_cross_model_review,
        _normalize_severity,
        _persist_review,
    )

    diff = _get_git_diff()
    cross_model_skipped = False
    cross_model_findings: list[dict[str, str]] = []

    if not config.cross_model_review_enabled:
        cross_model_skipped = True
        logger.info("cross_model_review_disabled")
    elif not diff:
        cross_model_skipped = True
        logger.info("cross_model_review_no_diff")
    else:
        raw_findings = _invoke_cross_model_review(diff, config)
        if not raw_findings:
            cross_model_skipped = True
        else:
            for rf in raw_findings:
                cross_model_findings.append({
                    "category": rf.get("category", "general"),
                    "severity": _normalize_severity(rf.get("severity", "info")),
                    "description": rf.get("description", ""),
                    "source": "cross_model",
                    "provider": config.cross_model_provider,
                })

    verdict = _compute_verdict(cross_model_findings)

    result: dict[str, object] = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "cross_model",
        "cross_model_skipped": cross_model_skipped,
        "cross_model_provider": config.cross_model_provider,
        "total_findings": len(cross_model_findings),
        "run_path": str(resolved_run) if resolved_run else None,
    }

    result["review_yaml"] = _persist_review(
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
        },
    )
    return result


def handle_auto_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    reviewer_findings: list[dict[str, object]] | None,
) -> dict[str, object]:
    """Handle the auto review mode — multi-reviewer analysis, filter, persist."""
    from trw_mcp.state.persistence import FileStateWriter
    from trw_mcp.tools.review import (
        REVIEWER_ROLES,
        _compute_verdict,
        _get_git_diff,
        _persist_review,
        _run_multi_reviewer_analysis,
    )

    diff = _get_git_diff()

    if reviewer_findings is not None:
        analysis: dict[str, object] = {
            "reviewer_roles_run": list(REVIEWER_ROLES),
            "reviewer_errors": [],
            "findings": reviewer_findings,
        }
    else:
        analysis = _run_multi_reviewer_analysis(diff, config)

    all_auto_findings = analysis.get("findings", [])
    if not isinstance(all_auto_findings, list):
        all_auto_findings = []

    # Multi-agent review confidence threshold (QUAL-027): 0-100 scale
    confidence_threshold = config.review_confidence_threshold

    # Filter findings by confidence threshold
    surfaced: list[dict[str, object]] = []
    for f in all_auto_findings:
        if not isinstance(f, dict):
            continue
        confidence = f.get("confidence", 0)
        if isinstance(confidence, (int, float)) and confidence >= confidence_threshold:
            surfaced.append(f)

    # Compute verdict from surfaced findings only
    surfaced_for_verdict: list[dict[str, str]] = [
        {"severity": str(f.get("severity", "info"))} for f in surfaced
    ]
    verdict = _compute_verdict(surfaced_for_verdict)

    result: dict[str, object] = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "auto",
        "reviewer_roles_run": analysis.get("reviewer_roles_run", []),
        "reviewer_errors": analysis.get("reviewer_errors", []),
        "surfaced_findings_count": len(surfaced),
        "total_findings_count": len(all_auto_findings),
        "confidence_threshold": confidence_threshold,
        "total_findings": len(surfaced),
        "run_path": str(resolved_run) if resolved_run else None,
    }

    # SOC 2 fields (INFRA-027-FR04) — compute from available context
    import hashlib
    from datetime import datetime, timedelta

    diff_hash = hashlib.sha256((diff or "").encode()).hexdigest() if diff else ""
    roles_run = analysis.get("reviewer_roles_run", [])
    reviewer_role_str = ", ".join(str(r) for r in roles_run) if isinstance(roles_run, list) else ""
    try:
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        retention_dt = ts_dt + timedelta(days=config.compliance_review_retention_days)
        retention_expires = retention_dt.isoformat()
    except (ValueError, AttributeError):
        retention_expires = ""

    result["review_yaml"] = _persist_review(
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
        },
    )

    # Write supplementary auto-mode artifacts when a run is active
    if resolved_run is not None:
        writer = FileStateWriter()

        # review-all.yaml — ALL findings unfiltered
        review_all_path = resolved_run / "meta" / "review-all.yaml"
        review_all_data: dict[str, object] = {
            "review_id": review_id,
            "timestamp": ts,
            "mode": "auto",
            "total_findings_count": len(all_auto_findings),
            "findings": all_auto_findings,
        }
        writer.write_yaml(review_all_path, review_all_data)

        # integration-review.yaml — integration findings only (INFRA-027-FR03)
        integration_findings = [
            f for f in all_auto_findings
            if isinstance(f, dict) and f.get("reviewer_role") == "integration"
        ]
        if integration_findings:
            # Compute verdict from integration findings
            int_critical = sum(
                1 for f in integration_findings
                if isinstance(f, dict) and f.get("severity") == "critical"
            )
            int_verdict = "block" if int_critical > 0 else ("warn" if integration_findings else "pass")

            integration_path = resolved_run / "meta" / "integration-review.yaml"
            integration_data: dict[str, object] = {
                "review_id": review_id,
                "timestamp": ts,
                "mode": "auto",
                "run_id": "",  # Populated by caller
                "reviewer_id": "",
                "reviewer_role": "integration",
                "git_diff_hash": "",
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
