"""trw_ordering_compare MCP tool (PRD-DIST-1994, cycle 748).

Fourth cross-package wire (after c746 + 2× c747). Surfaces the c741
risk-ordering-compare result to any MCP client.

Reads ``ordering-compare-<sha>.json`` (c742 artifact). Uses c747 DRY
substrate. NO trw_distill imports.
"""

from __future__ import annotations

from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.tools._learnings_collector import (
    LearningSummary,
    build_file_queries,
    collect_learnings,
)
from trw_mcp.tools._sidecar_substrate import CurrentSidecarStatus, resolve_current_sidecar

_ARTIFACT_NAME: str = "ordering-compare"
_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"


class RiskOrderingComparisonPayload(BaseModel):
    """Field-by-field mirror of trw-distill RiskOrderingComparison (c741).

    The wire contract is parity-checked against the trw_distill source by
    ``scripts/check-schema-mirror-parity.py`` (PRD-INFRA-134 FR-05); the validation
    constraints below MUST match the source model's. ``only_in_*`` stay ``list[str]``
    (the source uses ``tuple[str, ...]``): both render identically in JSON Schema, and
    ``list`` is required here because this payload is loaded from JSON via
    ``model_validate`` under ``strict=True`` — strict mode will NOT coerce a JSON array
    into a ``tuple``, so changing these to ``tuple`` would break sidecar loading.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    label_a: str = Field(min_length=1)
    label_b: str = Field(min_length=1)
    n_a: int = Field(ge=0)
    n_b: int = Field(ge=0)
    n_intersection: int = Field(ge=0)
    n_union: int = Field(ge=0)
    jaccard: float = Field(ge=0.0, le=1.0)
    kendall_tau_b: float | None = Field(default=None, ge=-1.0, le=1.0)
    only_in_a: list[str] = Field(default_factory=list)
    only_in_b: list[str] = Field(default_factory=list)
    overlap_status: Literal["identical", "disjoint", "overlap", "insufficient"] = "overlap"


class OrderingCompareResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    comparison: RiskOrderingComparisonPayload | None = None
    distill_status: CurrentSidecarStatus = "sidecar_missing"
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
    sidecar = resolve_current_sidecar(
        repo_root=repo_root,
        cache_dir=cache_dir,
        feature=_TIER_FEATURE,
        artifact_name=_ARTIFACT_NAME,
        cli_remediation=("trw-distill self-improve risk-ordering-compare --repo . --persist-sidecar"),
    )
    if sidecar.status != "hint_available" or sidecar.payload is None:
        return OrderingCompareResult(
            tier=sidecar.tier,
            distill_status=sidecar.status,
            distill_action=sidecar.action,
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    if not isinstance(sidecar.payload, dict):
        return OrderingCompareResult(
            tier=sidecar.tier,
            distill_status="sidecar_malformed",
            distill_action=("ordering-compare payload is not a dict; re-run with --persist-sidecar"),
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    try:
        comparison = RiskOrderingComparisonPayload.model_validate(sidecar.payload)
    except Exception:
        return OrderingCompareResult(
            tier=sidecar.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                "ordering-compare payload does not match RiskOrderingComparisonPayload "
                "schema; check trw-distill version compatibility"
            ),
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    queries: list[str] = []
    for path in list(comparison.only_in_a) + list(comparison.only_in_b):
        queries.extend(build_file_queries(path))
    learnings = collect_learnings(queries)

    return OrderingCompareResult(
        tier=sidecar.tier,
        comparison=comparison,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=sidecar.sidecar_path,
        distill_sidecar_sha=sidecar.sidecar_sha,
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
