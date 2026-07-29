# Parent facade: tools/_review_helpers.py
"""Auto-mode review handler.

Extracted from ``_review_helpers.py`` to keep the facade under the
500-line threshold.  All public names are re-exported from
``_review_helpers.py`` so existing import paths are preserved.

The cross-model mode and the review-coverage vocabulary it owns now live in
``_review_cross_model.py``; both are re-exported here unchanged so
``from trw_mcp.tools._review_auto import handle_cross_model_mode`` keeps
working.

Note: shared helpers are accessed via ``_helpers.<name>`` (module reference)
rather than direct name imports so that ``patch("trw_mcp.tools._review_helpers._get_git_diff", ...)``
in tests correctly intercepts calls from this module.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

import structlog

from trw_mcp.models.run import IntegrationReviewArtifact
from trw_mcp.models.typed_dicts import (
    AutoReviewResult,
    MultiReviewerAnalysisResult,
)
from trw_mcp.state.persistence import FileStateWriter
from trw_mcp.tools import _review_helpers as _helpers

# Re-exported for back-compat: these moved to _review_cross_model.py but callers
# and tests import them from here. ``X as X`` is required — a plain re-export is
# deleted by ruff --fix and rejected by mypy --strict.
from trw_mcp.tools._review_cross_model import (
    COVERAGE_CROSS_FAMILY as COVERAGE_CROSS_FAMILY,
)
from trw_mcp.tools._review_cross_model import (
    COVERAGE_SINGLE_FAMILY as COVERAGE_SINGLE_FAMILY,
)
from trw_mcp.tools._review_cross_model import (
    EMPTY_SAME_FAMILY_FALLBACK_LIMITED_REASON as EMPTY_SAME_FAMILY_FALLBACK_LIMITED_REASON,
)
from trw_mcp.tools._review_cross_model import (
    REASON_CROSS_MODEL_DISABLED as REASON_CROSS_MODEL_DISABLED,
)
from trw_mcp.tools._review_cross_model import (
    REASON_NO_DIFF as REASON_NO_DIFF,
)
from trw_mcp.tools._review_cross_model import (
    REASON_PROVIDER_INTEGRATION_ABSENT as REASON_PROVIDER_INTEGRATION_ABSENT,
)
from trw_mcp.tools._review_cross_model import (
    REASON_PROVIDER_RETURNED_EMPTY as REASON_PROVIDER_RETURNED_EMPTY,
)
from trw_mcp.tools._review_cross_model import (
    REASON_PROVIDER_UNREACHABLE as REASON_PROVIDER_UNREACHABLE,
)
from trw_mcp.tools._review_cross_model import (
    _build_single_family_caveat as _build_single_family_caveat,
)
from trw_mcp.tools._review_cross_model import (
    _honeypots_in_findings as _honeypots_in_findings,
)
from trw_mcp.tools._review_cross_model import (
    _same_family_fallback as _same_family_fallback,
)
from trw_mcp.tools._review_cross_model import (
    handle_cross_model_mode as handle_cross_model_mode,
)
from trw_mcp.tools._review_validation import apply_confidence_gate, normalize_review_findings

if TYPE_CHECKING:
    from trw_mcp.models.config import TRWConfig
    from trw_mcp.tools._review_provenance import RunIdentity

logger = structlog.get_logger(__name__)

EMPTY_REVIEWER_FINDINGS_LIMITED_REASON = (
    "pre-collected reviewer_findings contained no schema-valid findings and "
    "no typed independent-review receipt was supplied"
)


def _roles_attested_by(findings: list[dict[str, object]]) -> list[str]:
    """Distinct ``reviewer_role`` values actually present in *findings*.

    The canonical roles come first in :data:`_helpers.REVIEWER_ROLES` order so the
    stamp is deterministic; any additional caller-named role is appended. An
    empty list is the honest answer when no finding names a role — that shortfall
    is what makes a one-role payload fail a six-role review plan instead of
    silently satisfying it.
    """
    named = {str(f.get("reviewer_role", "")).strip() for f in findings if isinstance(f, dict)}
    named.discard("")
    ordered = [role for role in _helpers.REVIEWER_ROLES if role in named]
    return ordered + sorted(named.difference(ordered))


def handle_auto_mode(
    config: TRWConfig,
    resolved_run: Path | None,
    review_id: str,
    ts: str,
    reviewer_findings: list[dict[str, object]] | None,
    prd_ids: list[str] | None = None,
    *,
    verified_reviewer_identity: RunIdentity | None = None,
) -> AutoReviewResult:
    """Handle the auto review mode -- multi-reviewer analysis, filter, persist.

    ``verified_reviewer_identity`` (OQ-001) must come from
    ``resolve_verified_reviewer_identity``; it stamps the framework-verified
    reviewer identity onto the persisted provenance block.
    """
    diff = _helpers._get_git_diff()

    rejections: list[dict[str, object]] = []
    rejected_count = 0
    if reviewer_findings is not None:
        # No default_confidence: an omitted ``confidence`` means the caller stated
        # no score, NOT that they have zero confidence. Injecting 0.0 here put
        # every unscored finding below the (80) threshold, so an audit handoff of
        # real P0/P1 findings came back verdict='pass', substantive=True,
        # critical_count=0 — the accept-list incident one layer down. Falling
        # through to ReviewFinding's own 1.0 default makes an unscored finding
        # count, which can only ever make the gate harder to pass.
        validated_reviewer_findings, rejections = normalize_review_findings(reviewer_findings)
        rejected_count = len(reviewer_findings) - len(validated_reviewer_findings)
        # Real pre-collected findings from client-side multi-agent review:
        # only schema-valid evidence is substantive. An empty/placeholder list
        # has no typed independent-review receipt, so it fails closed.
        analysis: MultiReviewerAnalysisResult = {
            # Honest roles: the roles the SUPPLIED findings actually attest to,
            # never the full role list inferred from "the caller sent something".
            # The receipt's realized_reviewer_roles is derived from this, so
            # claiming all six here would let one reviewer's findings satisfy a
            # six-role plan by assertion (VISION principle 3).
            "reviewer_roles_run": _roles_attested_by(validated_reviewer_findings),
            "reviewer_errors": [],
            "findings": validated_reviewer_findings,
            "auto_analysis_limited": not validated_reviewer_findings,
            "limited_reason": "" if validated_reviewer_findings else EMPTY_REVIEWER_FINDINGS_LIMITED_REASON,
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
    substantive = not auto_analysis_limited

    all_auto_findings = analysis.get("findings", [])
    if not isinstance(all_auto_findings, list):
        all_auto_findings = []

    # Multi-agent review confidence threshold (QUAL-027): 0-100 scale
    confidence_threshold = config.review_confidence_threshold

    # Filter findings by confidence threshold. Every removal is recorded with an
    # index + reason so a suppressed finding is visible to the caller that sent
    # it, instead of only showing up as a gap between the two counts.
    surfaced, suppressed, suppressed_count = apply_confidence_gate(all_auto_findings, confidence_threshold)

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
        # Downstream gates must not accept a limited pattern scan or an
        # empty/invalid pre-collected payload as a code-quality signal.
        "auto_analysis_limited": auto_analysis_limited,
        "limited_reason": limited_reason,
        "substantive": substantive,
        # PRD-QUAL-108-FR01: auto mode is same-family today (OQ1), so coverage is
        # always single_family; a caveat names the same-family-only limitation.
        "review_family_coverage": COVERAGE_SINGLE_FAMILY,
        "single_family_caveat": _build_single_family_caveat(REASON_CROSS_MODEL_DISABLED, "auto-mode (same-family)"),
    }
    if rejected_count:
        # Caller-supplied findings were dropped: report it, never just log it.
        logger.warning("auto_review_findings_rejected", review_id=review_id, rejected=rejected_count)
        result["rejected_findings_count"] = rejected_count
        result["rejected_findings"] = rejections
    if suppressed_count:
        # Schema-valid findings the confidence gate removed before the verdict.
        # Reported separately from rejections: the fix is different (raise the
        # finding's confidence or lower review_confidence_threshold, vs. correct
        # a malformed payload).
        logger.warning("auto_review_findings_suppressed", review_id=review_id, suppressed=suppressed_count)
        result["suppressed_findings_count"] = suppressed_count
        result["suppressed_findings"] = suppressed
    if auto_analysis_limited:
        logger.info(
            "auto_review_analysis_limited",
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

    # Prevalidate the optional integration artifact before persisting review
    # completion state. A schema failure must not leave review.yaml, a typed
    # receipt, or review_complete telemetry claiming partial success.
    integration_data: dict[str, object] | None = None
    if resolved_run is not None:
        integration_findings = [
            f for f in all_auto_findings if isinstance(f, dict) and f.get("reviewer_role") == "integration"
        ]
        if integration_findings:
            int_verdict = "block" if any(f.get("severity") == "critical" for f in integration_findings) else "warn"
            artifact = IntegrationReviewArtifact.model_validate(
                {
                    "run_id": resolved_run.name,
                    "reviewer_id": f"trw-auto-{review_id}",
                    "reviewer_role": "integration",
                    "timestamp": ts,
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
            )
            integration_data = artifact.model_dump(mode="json")
            # Operational envelope fields are intentionally outside the SOC 2
            # artifact schema but remain part of the persisted file contract.
            integration_data.update({"review_id": review_id, "mode": "auto"})

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
            # Persisted alongside the surfaced findings so a later reader of
            # review.yaml can tell "nothing was found" from "findings were
            # filtered out below the confidence threshold".
            "suppressed_findings_count": suppressed_count,
            "suppressed_findings": suppressed,
            # Honest labeling persisted into the artifact so any reader of
            # review.yaml can tell a limited pattern-scan from a real review.
            "auto_analysis_limited": auto_analysis_limited,
            "limited_reason": limited_reason,
            "substantive": substantive,
            # PRD-QUAL-108: coverage stamp surfaced in the persisted artifact (US3).
            "review_family_coverage": COVERAGE_SINGLE_FAMILY,
            "single_family_caveat": _build_single_family_caveat(REASON_CROSS_MODEL_DISABLED, "auto-mode (same-family)"),
            "review_kind": (
                "empty/invalid reviewer findings (limited)"
                if reviewer_findings is not None and auto_analysis_limited
                else "pattern-scan (limited)"
                if auto_analysis_limited
                else "multi-reviewer"
            ),
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
            "substantive": substantive,
            "prd_ids": list(prd_ids) if prd_ids else [],
        },
        cast("dict[str, object]", result),
        verified_reviewer_identity=verified_reviewer_identity,
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
        if integration_data is not None:
            integration_path = resolved_run / "meta" / "integration-review.yaml"
            writer.write_yaml(integration_path, integration_data)

    return result
