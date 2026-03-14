"""Extracted helpers for trw_review tool — finding validation, mode handlers.

Keeps the tool closure in review.py focused on dispatch while
business logic lives in testable pure-ish functions.

All references to review.py functions use lazy imports to preserve
test patchability (patching review._get_git_diff must affect these helpers).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.typed_dicts import (
    AutoReviewResult,
    CrossModelReviewResult,
    ManualReviewResult,
    MultiReviewerAnalysisResult,
    ReconcileReviewResult,
    ReviewFindingDict,
    ReviewModeResult,
)

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig

logger = structlog.get_logger()


def validate_manual_findings(
    raw_findings: list[dict[str, str]],
) -> list[ReviewFindingDict]:
    """Validate and normalize a list of manually-provided findings.

    Runs each finding through ReviewFinding model validation,
    normalizing severity levels to the canonical set.
    """
    from trw_mcp.models.run import ReviewFinding
    from trw_mcp.tools.review import _normalize_severity

    validated: list[ReviewFindingDict] = []
    for f in raw_findings:
        normalized = {**f, "severity": _normalize_severity(f.get("severity", "info"))}
        try:
            ReviewFinding(**normalized)  # type: ignore[arg-type]  # dict[str,str] coerced by Pydantic
        except Exception:  # justified: fail-open, include finding even if model validation fails
            pass  # Still include even if other fields fail validation
        validated.append(cast(ReviewFindingDict, normalized))
    return validated


def count_by_severity(
    findings: list[ReviewFindingDict],
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
) -> ManualReviewResult:
    """Handle the manual review mode — validate findings, compute verdict, persist."""
    from trw_mcp.tools.review import _compute_verdict, _persist_review_artifact

    validated = validate_manual_findings(raw_findings)
    critical_count, warning_count, info_count = count_by_severity(validated)
    verdict = _compute_verdict(cast(list[dict[str, str]], validated))

    result: ManualReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "total_findings": len(validated),
        "critical_count": critical_count,
        "warning_count": warning_count,
        "info_count": info_count,
        "run_path": str(resolved_run) if resolved_run else None,
    }

    result["review_yaml"] = _persist_review_artifact(
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
) -> CrossModelReviewResult:
    """Handle the cross-model review mode — get diff, invoke provider, persist."""
    from trw_mcp.tools.review import (
        _compute_verdict,
        _get_git_diff,
        _invoke_cross_model_review,
        _normalize_severity,
        _persist_review_artifact,
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

    result: CrossModelReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "cross_model",
        "cross_model_skipped": cross_model_skipped,
        "cross_model_provider": config.cross_model_provider,
        "total_findings": len(cross_model_findings),
        "run_path": str(resolved_run) if resolved_run else None,
    }

    result["review_yaml"] = _persist_review_artifact(
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


def _extract_section(content: str, section_name: str) -> str:
    """Extract content under a ## section heading."""
    pattern = rf"^##\s+(?:\d+\.\s+)?{re.escape(section_name)}\s*$"
    match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
    if not match:
        return ""
    start = match.end()
    next_heading = re.search(r"^##\s+", content[start:], re.MULTILINE)
    if next_heading:
        return content[start : start + next_heading.start()].strip()
    return content[start:].strip()


def _extract_identifiers(text: str) -> list[str]:
    """Extract code identifiers from FR text."""
    identifiers: list[str] = []
    # Backtick-wrapped identifiers
    identifiers.extend(re.findall(r"`([^`]+)`", text))
    # --flags
    identifiers.extend(re.findall(r"(--[a-zA-Z][\w-]*)", text))
    # PascalCase class names (2+ uppercase letters)
    identifiers.extend(re.findall(r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b", text))
    return list(dict.fromkeys(identifiers))  # deduplicate preserving order


def _added_lines_only(diff: str) -> str:
    """Extract only added (+) lines from a git diff.

    Filters out removed (-) lines to prevent false negatives: an identifier
    that was *deleted* from code should not count as "present in diff".
    """
    return "\n".join(
        line for line in diff.splitlines()
        if not line.startswith("-") or line.startswith("---")
    )


def _extract_fr_mismatches(
    prd_content: str, prd_id: str, diff: str,
) -> list[dict[str, str]]:
    """Compare FR identifiers against diff, return mismatches."""
    mismatches: list[dict[str, str]] = []
    section = _extract_section(prd_content, "Functional Requirements")
    if not section:
        return mismatches

    # Only check added lines — removed lines should not mask drift
    added_diff = _added_lines_only(diff)

    # Split into individual FRs
    fr_pattern = re.compile(
        r"(?:^|\n)(?:###?\s*)?FR(\d+)\s*[:\-\u2013]\s*(.+?)(?=\n(?:###?\s*)?FR\d|\Z)",
        re.DOTALL,
    )
    for m in fr_pattern.finditer(section):
        fr_num = m.group(1)
        fr_text = m.group(2).strip()
        identifiers = _extract_identifiers(fr_text)
        for ident in identifiers:
            if ident not in added_diff:
                mismatches.append({
                    "prd_id": prd_id,
                    "fr": f"FR{fr_num}",
                    "identifier": ident,
                    "recommendation": "update_spec",
                })
    return mismatches


def _count_frs_in_prd(prd_path: Path) -> int:
    """Count FR entries in a PRD file."""
    try:
        content = prd_path.read_text(encoding="utf-8")
    except OSError:
        return 0
    section = _extract_section(content, "Functional Requirements")
    return len(re.findall(r"(?:^|\n)(?:###?\s*)?FR\d+", section))


def handle_reconcile_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    prd_ids: list[str] | None,
) -> ReconcileReviewResult:
    """Handle the reconcile review mode — compare PRD FRs against git diff."""
    from trw_mcp.state._paths import resolve_project_root
    from trw_mcp.state.persistence import FileEventLogger, FileStateWriter
    from trw_mcp.tools.review import _get_git_diff

    # Discover PRDs if not explicitly provided
    effective_prd_ids = list(prd_ids) if prd_ids else []
    if not effective_prd_ids and resolved_run is not None:
        from trw_mcp.state.prd_utils import discover_governing_prds

        effective_prd_ids = discover_governing_prds(resolved_run, config)

    if not effective_prd_ids:
        return {
            "review_id": review_id,
            "verdict": "clean",
            "mismatches": [],
            "message": "No governing PRDs found",
        }

    diff = _get_git_diff()

    project_root = resolve_project_root()
    prds_dir = project_root / config.prds_relative_path

    all_mismatches: list[dict[str, str]] = []
    total_frs = 0

    for prd_id in effective_prd_ids:
        prd_path = prds_dir / f"{prd_id}.md"
        try:
            prd_content = prd_path.read_text(encoding="utf-8")
        except OSError:
            logger.warning("reconcile_prd_not_found", prd_id=prd_id, path=str(prd_path))
            continue
        # Count FRs from already-loaded content (avoids double file read)
        fr_section = _extract_section(prd_content, "Functional Requirements")
        total_frs += len(re.findall(r"(?:^|\n)(?:###?\s*)?FR\d+", fr_section))
        mismatches = _extract_fr_mismatches(prd_content, prd_id, diff)
        all_mismatches.extend(mismatches)

    verdict = "drift_detected" if all_mismatches else "clean"

    result: ReconcileReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mismatches": all_mismatches,
        "prd_count": len(effective_prd_ids),
        "total_frs": total_frs,
        "mismatch_count": len(all_mismatches),
    }

    # Persist reconciliation artifact and log event
    if resolved_run is not None:
        writer = FileStateWriter()
        reconciliation_path = resolved_run / "meta" / "reconciliation.yaml"
        reconciliation_data: dict[str, object] = {
            "review_id": review_id,
            "timestamp": ts,
            "verdict": verdict,
            "prd_ids": effective_prd_ids,
            "prd_count": len(effective_prd_ids),
            "total_frs": total_frs,
            "mismatch_count": len(all_mismatches),
            "mismatches": all_mismatches,
        }
        writer.write_yaml(reconciliation_path, reconciliation_data)

        # Log spec_reconciliation event
        events_path = resolved_run / "meta" / "events.jsonl"
        if events_path.parent.exists():
            event_logger = FileEventLogger(writer)
            event_logger.log_event(events_path, "spec_reconciliation", {
                "review_id": review_id,
                "verdict": verdict,
                "mismatch_count": len(all_mismatches),
                "prd_count": len(effective_prd_ids),
            })

        result["reconciliation_yaml"] = str(reconciliation_path)

    return result


def handle_auto_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    reviewer_findings: list[dict[str, object]] | None,
) -> AutoReviewResult:
    """Handle the auto review mode — multi-reviewer analysis, filter, persist."""
    from trw_mcp.state.persistence import FileStateWriter
    from trw_mcp.tools.review import (
        REVIEWER_ROLES,
        _compute_verdict,
        _get_git_diff,
        _persist_review_artifact,
        _run_multi_reviewer_analysis,
    )

    diff = _get_git_diff()

    if reviewer_findings is not None:
        analysis: MultiReviewerAnalysisResult = {
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
    surfaced_for_verdict: list[dict[str, str]] = [
        {"severity": str(f.get("severity", "info"))} for f in surfaced
    ]
    verdict = _compute_verdict(surfaced_for_verdict)

    result: AutoReviewResult = {
        "review_id": review_id,
        "verdict": verdict,
        "mode": "auto",
        "reviewer_roles_run": analysis.get("reviewer_roles_run", []),
        "reviewer_errors": analysis.get("reviewer_errors", []),
        "surfaced_findings_count": len(surfaced),
        "total_findings_count": len(all_auto_findings),
        "confidence_threshold": confidence_threshold,
        "run_path": str(resolved_run) if resolved_run else None,
    }

    # SOC 2 fields (INFRA-027-FR04) — compute from available context
    diff_hash = hashlib.sha256((diff or "").encode()).hexdigest() if diff else ""
    roles_run = analysis.get("reviewer_roles_run", [])
    reviewer_role_str = ", ".join(str(r) for r in roles_run) if isinstance(roles_run, list) else ""
    try:
        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        retention_dt = ts_dt + timedelta(days=config.compliance_review_retention_days)
        retention_expires = retention_dt.isoformat()
    except (ValueError, AttributeError):
        retention_expires = ""

    result["review_yaml"] = _persist_review_artifact(
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
