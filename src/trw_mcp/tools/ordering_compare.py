"""trw_ordering_compare MCP tool (PRD-DIST-1994, cycle 748).

Fourth cross-package wire (after c746 + 2× c747). Surfaces the c741
risk-ordering-compare result to any MCP client.

Reads ``ordering-compare-<sha>.json`` (c742 artifact). Uses c747 DRY
substrate. NO trw_distill imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

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

_ARTIFACT_NAME: str = "ordering-compare"
_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"


class RiskOrderingComparisonPayload(BaseModel):
    """Field-by-field mirror of trw-distill RiskOrderingComparison (c741)."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    label_a: str
    label_b: str
    n_a: int
    n_b: int
    n_intersection: int
    n_union: int
    jaccard: float
    kendall_tau_b: float | None = None
    only_in_a: list[str] = Field(default_factory=list)
    only_in_b: list[str] = Field(default_factory=list)
    overlap_status: Literal["identical", "disjoint", "overlap", "insufficient"]


class OrderingCompareResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    comparison: RiskOrderingComparisonPayload | None = None
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
    learnings: list[LearningSummary] = Field(default_factory=list)
    """PRD-DIST-2001 (c749): learnings for divergent paths (only_in_a + only_in_b)."""
    learnings_count: int = 0


def compute_ordering_compare(
    *,
    repo_root: str | None = None,
    cache_dir: str | None = None,
) -> OrderingCompareResult:
    resolved_repo_root = resolve_repo_root(repo_root)
    if resolved_repo_root is None:
        return OrderingCompareResult(
            tier="free",
            distill_status="no_repo_root",
            distill_action="Pass --repo or run from inside a git checkout",
        )

    gate = check_tier_for_feature(resolved_repo_root, _TIER_FEATURE)
    if not gate.allowed:
        return OrderingCompareResult(
            tier=gate.tier,
            distill_status="tier_required",
            distill_action=(
                "Acquire team/pro/enterprise tier to enable trw-distill "
                "sidecar consumption (see https://trwframework.com/tier)"
            ),
        )

    git_sha = resolve_git_sha(resolved_repo_root)
    if git_sha is None:
        return OrderingCompareResult(
            tier=gate.tier,
            distill_status="no_git_sha",
            distill_action="Could not run `git rev-parse HEAD` — verify .git/ present",
        )

    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else resolved_repo_root / DEFAULT_CACHE_DIR_REL
    sidecar_path = resolved_cache_dir / f"{_ARTIFACT_NAME}-{git_sha}.json"

    load = load_sidecar_with_sha_check(
        sidecar_path,
        expected_sha=git_sha,
        file_path_hint="<ordering-compare>",
        cli_remediation=("trw-distill self-improve risk-ordering-compare --repo . --persist-sidecar"),
    )
    if load.status != "ok" or load.payload is None:
        return OrderingCompareResult(
            tier=gate.tier,
            distill_status=load.status,  # type: ignore[arg-type]
            distill_action=load.action,
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    if not isinstance(load.payload, dict):
        return OrderingCompareResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action=("ordering-compare payload is not a dict; re-run with --persist-sidecar"),
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    try:
        comparison = RiskOrderingComparisonPayload.model_validate(load.payload)
    except Exception:
        return OrderingCompareResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                "ordering-compare payload does not match RiskOrderingComparisonPayload "
                "schema; check trw-distill version compatibility"
            ),
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    queries: list[str] = []
    for path in list(comparison.only_in_a) + list(comparison.only_in_b):
        queries.extend(build_file_queries(path))
    learnings = collect_learnings(queries)

    return OrderingCompareResult(
        tier=gate.tier,
        comparison=comparison,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=load.sidecar_path,
        distill_sidecar_sha=load.sidecar_sha,
        learnings=learnings,
        learnings_count=len(learnings),
    )


def register_ordering_compare_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_ordering_compare(
        repo_root: str | None = None,
        cache_dir: str | None = None,
    ) -> dict[str, Any]:
        """Return c741 RiskOrderingComparison for the current SHA.

        Use when comparing two persisted risk-ordering sidecars for overlap
        and rank-correlation drift.

        Tier-gated. NEVER raises.
        """
        result = compute_ordering_compare(
            repo_root=repo_root,
            cache_dir=cache_dir,
        )
        return result.model_dump()


__all__ = [
    "OrderingCompareResult",
    "RiskOrderingComparisonPayload",
    "compute_ordering_compare",
    "register_ordering_compare_tools",
]
