"""trw_cross_repo_ordering MCP tool (PRD-DIST-1995, cycle 748).

Fifth cross-package wire. Surfaces the c745 CrossRepoOrderingAggregate
to any MCP client.

Reads ``cross-repo-aggregate-<sha>.json`` (c745 artifact). Note: the
SHA is derived from ``blake2b(sorted_repo_names)`` per c745, NOT git
HEAD — so this tool diverges from the single-SHA pattern of c746/c747:
the operator must supply the sidecar path explicitly (or accept the
default ``<sidecar_dir>/cross-repo-aggregate-*.json`` glob latest).

Uses c747 DRY substrate where applicable. NO trw_distill imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.tools._learnings_collector import (
    LearningSummary,
    collect_learnings,
)
from trw_mcp.tools._sidecar_substrate import (
    SCHEMA_VERSION_ACCEPTED,
    check_tier_for_feature,
    load_envelope,
    resolve_repo_root,
)
from trw_mcp.tools.ordering_compare import RiskOrderingComparisonPayload

_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"


class PerRepoResultPayload(BaseModel):
    """Mirror of trw-distill PerRepoResult (c745)."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    repo_label: str
    comparison: RiskOrderingComparisonPayload


class CrossRepoOrderingAggregatePayload(BaseModel):
    """Mirror of trw-distill CrossRepoOrderingAggregate (c745)."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    n_repos: int
    per_repo: list[PerRepoResultPayload] = Field(default_factory=list)
    mean_jaccard: float | None = None
    median_jaccard: float | None = None
    stdev_jaccard: float | None = None
    mean_tau_b: float | None = None
    median_tau_b: float | None = None
    stdev_tau_b: float | None = None
    n_tau_defined: int = 0
    overlap_status_counts: dict[str, int] = Field(default_factory=dict)
    summary_verdict: Literal[
        "consistent_overlap", "mixed", "mostly_disjoint", "insufficient",
    ]


class CrossRepoOrderingResult(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    aggregate: CrossRepoOrderingAggregatePayload | None = None
    distill_status: Literal[
        "hint_available", "tier_required", "sidecar_missing",
        "sidecar_malformed", "schema_mismatch", "no_repo_root",
        "sidecar_path_required",
    ] = "sidecar_missing"
    distill_action: str | None = None
    distill_sidecar_path: str | None = None
    learnings: list[LearningSummary] = Field(default_factory=list)
    """PRD-DIST-2001 (c749): aggregate-level learnings (verdict + per-repo labels)."""
    learnings_count: int = 0


def _find_latest_sidecar(sidecar_dir: Path) -> Path | None:
    """Find the most-recent cross-repo-aggregate-*.json by mtime."""
    if not sidecar_dir.is_dir():
        return None
    candidates = list(sidecar_dir.glob("cross-repo-aggregate-*.json"))
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def compute_cross_repo_ordering(
    *,
    repo_root: str | None = None,
    sidecar_path: str | None = None,
    sidecar_dir: str | None = None,
) -> CrossRepoOrderingResult:
    """Resolve sidecar path → load envelope → mirror payload.

    Sidecar resolution order:
    1. Explicit ``sidecar_path`` arg
    2. Latest ``cross-repo-aggregate-*.json`` in ``sidecar_dir``
    3. Latest in ``<repo>/.trw/distill/cross-repo-aggregates/``
       (operator-recommended path; falls back to map-cache for compat)
    """
    resolved_repo_root = resolve_repo_root(repo_root)

    # Resolve sidecar path
    if sidecar_path is not None:
        resolved_sidecar_path = Path(sidecar_path)
    else:
        if sidecar_dir is not None:
            search_dir = Path(sidecar_dir)
        elif resolved_repo_root is not None:
            search_dir = resolved_repo_root / ".trw" / "distill" / "map-cache"
        else:
            return CrossRepoOrderingResult(
                tier="free",
                distill_status="sidecar_path_required",
                distill_action=(
                    "Pass sidecar_path or sidecar_dir, or run from inside a "
                    "git checkout with a .trw/distill/map-cache directory"
                ),
            )
        latest = _find_latest_sidecar(search_dir)
        if latest is None:
            return CrossRepoOrderingResult(
                tier="free" if resolved_repo_root is None
                else check_tier_for_feature(resolved_repo_root, _TIER_FEATURE).tier,
                distill_status="sidecar_missing",
                distill_action=(
                    f"No cross-repo-aggregate-*.json in {search_dir} — run: "
                    f"trw-distill self-improve ordering-cross-repo "
                    f"--repo R1 --repo R2 --persist-sidecar --sidecar-dir <DIR>"
                ),
            )
        resolved_sidecar_path = latest

    # Tier gating uses the repo_root for entitlement lookup; sidecar_dir is irrelevant
    gate = check_tier_for_feature(resolved_repo_root, _TIER_FEATURE)
    if not gate.allowed:
        return CrossRepoOrderingResult(
            tier=gate.tier,
            distill_status="tier_required",
            distill_action=(
                "Acquire team/pro/enterprise tier to enable trw-distill "
                "sidecar consumption (see https://trwframework.com/tier)"
            ),
            distill_sidecar_path=str(resolved_sidecar_path),
        )

    envelope = load_envelope(resolved_sidecar_path)
    if envelope is None:
        return CrossRepoOrderingResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                f"Sidecar {resolved_sidecar_path} is missing or malformed; "
                f"re-run with --persist-sidecar"
            ),
            distill_sidecar_path=str(resolved_sidecar_path),
        )

    if envelope.get("schema_version") != SCHEMA_VERSION_ACCEPTED:
        return CrossRepoOrderingResult(
            tier=gate.tier,
            distill_status="schema_mismatch",
            distill_action=(
                f"Sidecar schema_version={envelope.get('schema_version')!r}; "
                f"expected {SCHEMA_VERSION_ACCEPTED!r}"
            ),
            distill_sidecar_path=str(resolved_sidecar_path),
        )

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return CrossRepoOrderingResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action="Sidecar payload is not a dict",
            distill_sidecar_path=str(resolved_sidecar_path),
        )

    try:
        aggregate = CrossRepoOrderingAggregatePayload.model_validate(payload)
    except Exception:
        return CrossRepoOrderingResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                "Payload does not match CrossRepoOrderingAggregatePayload schema; "
                "check trw-distill version compatibility"
            ),
            distill_sidecar_path=str(resolved_sidecar_path),
        )

    queries: list[str] = [
        f"cross-repo ordering {aggregate.summary_verdict}",
        "risk ordering compare",
    ]
    queries.extend(pr.repo_label for pr in aggregate.per_repo)
    learnings = collect_learnings(queries)

    return CrossRepoOrderingResult(
        tier=gate.tier,
        aggregate=aggregate,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=str(resolved_sidecar_path),
        learnings=learnings,
        learnings_count=len(learnings),
    )


def register_cross_repo_ordering_tools(server: FastMCP) -> None:
    @server.tool()
    def trw_cross_repo_ordering(
        repo_root: str | None = None,
        sidecar_path: str | None = None,
        sidecar_dir: str | None = None,
    ) -> dict[str, Any]:
        """Return the latest c745 CrossRepoOrderingAggregate.

        Use when comparing structural-risk ordering consistency across
        multiple repositories from a persisted aggregate sidecar.

        Sidecar SHA derived from sorted-repo-names (NOT git HEAD), so
        operator passes sidecar_path/sidecar_dir or the tool searches
        the repo-default location for the most-recent aggregate.
        Tier-gated. NEVER raises.
        """
        result = compute_cross_repo_ordering(
            repo_root=repo_root,
            sidecar_path=sidecar_path,
            sidecar_dir=sidecar_dir,
        )
        return result.model_dump()


__all__ = [
    "CrossRepoOrderingAggregatePayload",
    "CrossRepoOrderingResult",
    "PerRepoResultPayload",
    "compute_cross_repo_ordering",
    "register_cross_repo_ordering_tools",
]
