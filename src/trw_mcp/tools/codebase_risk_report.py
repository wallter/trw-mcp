"""Codebase risk-report engine, read by the ``trw-mcp code risk`` CLI
command (PRD-DIST-1990, cycle 747; PRD-CORE-300-FR06 slice S4 moved this from
an MCP tool to the CLI).

Second cross-package wire (after c746 trw_code's hint mode).
Surfaces the c737/c739 ranked composite-risk report to any caller.

Reads the c742 ``risk-report-<sha>.json`` sidecar (written by
``trw-distill self-improve risk-report --persist-sidecar``); returns
``FileRiskScore[]`` ordered by composite_score DESC.

Uses the c747 DRY substrate (``_sidecar_substrate``). NO
``from trw_distill`` imports (IP boundary).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trw_mcp.state._entitlements import DISTILL_SIDECAR_FEATURE
from trw_mcp.tools._learnings_collector import (
    LearningSummary,
    build_file_queries,
    collect_learnings,
)
from trw_mcp.tools._sidecar_substrate import CurrentSidecarStatus, resolve_current_sidecar

_ARTIFACT_NAME_RISK_REPORT: str = "risk-report"
_TIER_FEATURE: str = DISTILL_SIDECAR_FEATURE


class FileRiskScorePayload(BaseModel):
    """Field-by-field mirror of trw-distill FileRiskScore.

    Maintained by hand in trw-mcp (no trw_distill import). If
    trw-distill bumps the field set, returns ``sidecar_malformed``
    until the mirror is updated.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    # Constraints + field set mirror the trw-distill source; parity-checked by
    # scripts/check-schema-mirror-parity.py (PRD-INFRA-134 FR-05). test_signal_confidence
    # was added to the source by PRD-DIST-2095 FR03 — without it here, extra="forbid"
    # rejected every current risk-report sidecar as `sidecar_malformed`. Counts are required
    # to match the source (a source-produced sidecar always carries them).
    target_path: str = Field(min_length=1)
    target_exists_in_map: bool
    composite_score: float = Field(ge=0.0, le=1.0)
    fanin_score: float = Field(ge=0.0, le=1.0)
    fanout_score: float = Field(ge=0.0, le=1.0)
    untested_score: float = Field(ge=0.0, le=1.0)
    undocumented_score: float = Field(ge=0.0, le=1.0)
    size_score: float = Field(ge=0.0, le=1.0)
    churn_score: float = Field(default=0.0, ge=0.0, le=1.0)
    fanin_count: int = Field(ge=0)
    fanout_count: int = Field(ge=0)
    test_edge_count: int = Field(ge=0)
    doc_edge_count: int = Field(ge=0)
    line_count: int = Field(ge=0)
    test_signal_confidence: Literal["high", "medium", "low"] = "low"


class CodebaseRiskReportResult(BaseModel):
    """Top-level result for the risk-report tool."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    risk_report: list[FileRiskScorePayload] = Field(default_factory=list)
    distill_status: CurrentSidecarStatus = "sidecar_missing"
    distill_action: str | None = None
    distill_sidecar_path: str | None = None
    distill_sidecar_sha: str | None = None
    n_scores: int = 0
    learnings: list[LearningSummary] = Field(default_factory=list)
    """PRD-DIST-2001 (c749): learnings for top-N risk paths."""
    learnings_count: int = 0


def compute_codebase_risk_report(
    *,
    repo_root: str | None = None,
    cache_dir: str | None = None,
    top_n: int = 0,
) -> CodebaseRiskReportResult:
    """Pure-Python entry point used by the MCP tool registrar + tests.

    ``top_n=0`` returns all scores; ``top_n>0`` truncates to top-N by
    DESC composite_score (sidecar producer already sorts; we just slice).
    """
    sidecar = resolve_current_sidecar(
        repo_root=repo_root,
        cache_dir=cache_dir,
        feature=_TIER_FEATURE,
        artifact_name=_ARTIFACT_NAME_RISK_REPORT,
        cli_remediation=("trw-distill self-improve risk-report --repo . --persist-sidecar"),
    )
    if sidecar.status != "hint_available" or sidecar.payload is None:
        return CodebaseRiskReportResult(
            tier=sidecar.tier,
            distill_status=sidecar.status,
            distill_action=sidecar.action,
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    # Payload is FileRiskScore[] (array) — c742 writes the list directly
    if not isinstance(sidecar.payload, list):
        return CodebaseRiskReportResult(
            tier=sidecar.tier,
            distill_status="sidecar_malformed",
            distill_action=("risk-report sidecar payload is not an array; re-run with --persist-sidecar"),
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    scores: list[FileRiskScorePayload] = []
    for entry in sidecar.payload:
        if not isinstance(entry, dict):
            continue
        try:
            scores.append(FileRiskScorePayload.model_validate(entry))
        except ValidationError:
            # Skip rows the mirror doesn't accept — log via status if total fails
            continue

    if not scores:
        return CodebaseRiskReportResult(
            tier=sidecar.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                "No FileRiskScore entries parsed from sidecar; check schema "
                "compatibility between trw-distill and trw-mcp"
            ),
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    if top_n > 0:
        scores = scores[:top_n]

    queries: list[str] = []
    for s in scores:
        queries.extend(build_file_queries(s.target_path))
    learnings = collect_learnings(queries)

    return CodebaseRiskReportResult(
        tier=sidecar.tier,
        risk_report=scores,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=sidecar.sidecar_path,
        distill_sidecar_sha=sidecar.sidecar_sha,
        n_scores=len(scores),
        learnings=learnings,
        learnings_count=len(learnings),
    )


__all__ = [
    "CodebaseRiskReportResult",
    "FileRiskScorePayload",
    "compute_codebase_risk_report",
]
