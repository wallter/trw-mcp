"""trw_codebase_risk_report MCP tool (PRD-DIST-1990, cycle 747).

Second cross-package wire (after c746 trw_before_edit_hint).
Surfaces the c737/c739 ranked composite-risk report to any MCP client.

Reads the c742 ``risk-report-<sha>.json`` sidecar (written by
``trw-distill self-improve risk-report --persist-sidecar``); returns
``FileRiskScore[]`` ordered by composite_score DESC.

Uses the c747 DRY substrate (``_sidecar_substrate``). NO
``from trw_distill`` imports (IP boundary).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastmcp import Context, FastMCP
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from trw_mcp.tools._client_detection import resolve_client_profile, resolve_tier_for_client
from trw_mcp.tools._learnings_collector import (
    LearningSummary,
    build_file_queries,
    collect_learnings,
)
from trw_mcp.tools._sidecar_substrate import (
    DEFAULT_CACHE_DIR_REL,
    check_tier_for_feature,
    load_sidecar_with_sha_check,
    resolve_git_sha,
    resolve_repo_root,
)

_ARTIFACT_NAME_RISK_REPORT: str = "risk-report"
_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"


class FileRiskScorePayload(BaseModel):
    """Field-by-field mirror of trw-distill FileRiskScore.

    Maintained by hand in trw-mcp (no trw_distill import). If
    trw-distill bumps the field set, returns ``sidecar_malformed``
    until the mirror is updated.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    target_path: str
    target_exists_in_map: bool
    composite_score: float
    fanin_score: float
    fanout_score: float
    untested_score: float
    undocumented_score: float
    size_score: float
    churn_score: float = 0.0
    fanin_count: int = 0
    fanout_count: int = 0
    test_edge_count: int = 0
    doc_edge_count: int = 0
    line_count: int = 0


class CodebaseRiskReportResult(BaseModel):
    """Top-level result for the risk-report tool."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    risk_report: list[FileRiskScorePayload] = Field(default_factory=list)
    distill_status: Literal[
        "hint_available",
        "tier_required",
        "sidecar_missing",
        "sidecar_malformed",
        "schema_mismatch",
        "stale_sha",
        "no_repo_root",
        "no_git_sha",
    ] = "sidecar_missing"
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
    resolved_repo_root = resolve_repo_root(repo_root)

    if resolved_repo_root is None:
        return CodebaseRiskReportResult(
            tier="free",
            distill_status="no_repo_root",
            distill_action="Pass --repo or run from inside a git checkout",
        )

    gate = check_tier_for_feature(resolved_repo_root, _TIER_FEATURE)
    if not gate.allowed:
        return CodebaseRiskReportResult(
            tier=gate.tier,
            distill_status="tier_required",
            distill_action=(
                "Acquire team/pro/enterprise tier to enable trw-distill "
                "sidecar consumption (see https://trwframework.com/tier)"
            ),
        )

    git_sha = resolve_git_sha(resolved_repo_root)
    if git_sha is None:
        return CodebaseRiskReportResult(
            tier=gate.tier,
            distill_status="no_git_sha",
            distill_action="Could not run `git rev-parse HEAD` — verify .git/ present",
        )

    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else resolved_repo_root / DEFAULT_CACHE_DIR_REL
    sidecar_path = resolved_cache_dir / f"{_ARTIFACT_NAME_RISK_REPORT}-{git_sha}.json"

    load = load_sidecar_with_sha_check(
        sidecar_path,
        expected_sha=git_sha,
        file_path_hint="<risk-report>",
        cli_remediation=("trw-distill self-improve risk-report --repo . --persist-sidecar"),
    )
    if load.status != "ok" or load.payload is None:
        return CodebaseRiskReportResult(
            tier=gate.tier,
            distill_status=load.status,  # type: ignore[arg-type]
            distill_action=load.action,
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    # Payload is FileRiskScore[] (array) — c742 writes the list directly
    if not isinstance(load.payload, list):
        return CodebaseRiskReportResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action=("risk-report sidecar payload is not an array; re-run with --persist-sidecar"),
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    scores: list[FileRiskScorePayload] = []
    for entry in load.payload:
        if not isinstance(entry, dict):
            continue
        try:
            scores.append(FileRiskScorePayload.model_validate(entry))
        except ValidationError:
            # Skip rows the mirror doesn't accept — log via status if total fails
            continue

    if not scores:
        return CodebaseRiskReportResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                "No FileRiskScore entries parsed from sidecar; check schema "
                "compatibility between trw-distill and trw-mcp"
            ),
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    if top_n > 0:
        scores = scores[:top_n]

    queries: list[str] = []
    for s in scores:
        queries.extend(build_file_queries(s.target_path))
    learnings = collect_learnings(queries)

    return CodebaseRiskReportResult(
        tier=gate.tier,
        risk_report=scores,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=load.sidecar_path,
        distill_sidecar_sha=load.sidecar_sha,
        n_scores=len(scores),
        learnings=learnings,
        learnings_count=len(learnings),
    )


def register_codebase_risk_report_tools(server: FastMCP) -> None:
    """Register trw_codebase_risk_report on the MCP server."""

    @server.tool()
    def trw_codebase_risk_report(
        repo_root: str | None = None,
        cache_dir: str | None = None,
        top_n: int = 20,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return c737/c739 ranked composite-risk report for the current SHA.

        Use when a reviewer needs file-level structural risk ordering from
        a persisted trw-distill sidecar before prioritizing review effort.

        Tier-gated. ``top_n=0`` returns all entries; default 20.
        Returns ``CodebaseRiskReportResult.model_dump()`` enriched by client
        tier. NEVER raises.
        """
        result = compute_codebase_risk_report(
            repo_root=repo_root,
            cache_dir=cache_dir,
            top_n=top_n,
        )
        # --- telemetry (fail-open) ---
        try:
            from trw_mcp.channels._distill_telemetry import emit_tool_call

            sidecar_sha = result.distill_sidecar_sha or ""
            record_ids = [f"risk-report@{sidecar_sha[:8]}"] if sidecar_sha else []
            emit_tool_call(
                tool_name="trw_codebase_risk_report",
                tier=result.tier,
                record_ids=record_ids,
            )
        except Exception:  # justified: BLE001 — fail-open telemetry, never break the tool
            pass
        # --- tier-aware response enrichment (fail-open) ---
        base: dict[str, Any] = result.model_dump()
        try:
            from trw_mcp.channels._tool_return_tiers import enrich_response

            client = resolve_client_profile(ctx=ctx)
            client_tier = resolve_tier_for_client(client)
            return enrich_response(base, client_tier=client_tier)
        except Exception:  # justified: BLE001 — enrichment never breaks the base response
            pass
        return base


__all__ = [
    "CodebaseRiskReportResult",
    "FileRiskScorePayload",
    "compute_codebase_risk_report",
    "register_codebase_risk_report_tools",
]
