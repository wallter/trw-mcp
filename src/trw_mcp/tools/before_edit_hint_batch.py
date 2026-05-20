"""trw_before_edit_hint_batch MCP tool (PRD-DIST-1989, cycle 747).

Batch sibling of c746's ``trw_before_edit_hint``. Reads the c735+c743
``before-edit-batch-<sha>.json`` artifact and returns per-file hints
for refactor-time multi-file workflows.

Uses the c747 DRY substrate (``_sidecar_substrate``) for repo-root,
SHA, envelope, and tier resolution — no duplicated code with the
single-file tool.

IP boundary: trw-mcp PUBLIC; trw-distill PROPRIETARY. NO
``from trw_distill`` imports. Cross-package contract = sidecar
envelope ``risk-report-sidecar/v0``.
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
from trw_mcp.tools.before_edit_hint import BeforeYouEditHintPayload

_ARTIFACT_NAME_BATCH: str = "before-edit-batch"
_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"


class BeforeYouEditBatchPayload(BaseModel):
    """Field-by-field mirror of trw-distill BeforeYouEditBatch.

    Maintained by hand in trw-mcp (no trw_distill import).
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    total_files: int
    files_in_map: int
    total_hotspot_warnings: int
    hints: list[BeforeYouEditHintPayload] = Field(default_factory=list)


class BeforeEditHintBatchResult(BaseModel):
    """Top-level result for the batch tool."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    distill_batch: BeforeYouEditBatchPayload | None = None
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
    """PRD-DIST-2001 (c749): per-hint trw_recall aggregate, deduped."""
    learnings_count: int = 0


def compute_before_edit_hint_batch(
    *,
    repo_root: str | None = None,
    cache_dir: str | None = None,
) -> BeforeEditHintBatchResult:
    """Pure-Python entry point used by the MCP tool registrar + tests."""
    resolved_repo_root = resolve_repo_root(repo_root)

    if resolved_repo_root is None:
        return BeforeEditHintBatchResult(
            tier="free",
            distill_status="no_repo_root",
            distill_action="Pass --repo or run from inside a git checkout",
        )

    gate = check_tier_for_feature(resolved_repo_root, _TIER_FEATURE)
    if not gate.allowed:
        return BeforeEditHintBatchResult(
            tier=gate.tier,
            distill_status="tier_required",
            distill_action=(
                "Acquire team/pro/enterprise tier to enable trw-distill "
                "sidecar consumption (see https://trwframework.com/tier)"
            ),
        )

    git_sha = resolve_git_sha(resolved_repo_root)
    if git_sha is None:
        return BeforeEditHintBatchResult(
            tier=gate.tier,
            distill_status="no_git_sha",
            distill_action="Could not run `git rev-parse HEAD` — verify .git/ present",
        )

    resolved_cache_dir = Path(cache_dir) if cache_dir is not None else resolved_repo_root / DEFAULT_CACHE_DIR_REL
    sidecar_path = resolved_cache_dir / f"{_ARTIFACT_NAME_BATCH}-{git_sha}.json"

    load = load_sidecar_with_sha_check(
        sidecar_path,
        expected_sha=git_sha,
        file_path_hint="<batch>",
        cli_remediation=("trw-distill self-improve before-edit --repo . --files-from <changed.txt> --persist-sidecar"),
    )
    if load.status != "ok" or load.payload is None:
        return BeforeEditHintBatchResult(
            tier=gate.tier,
            distill_status=load.status,  # type: ignore[arg-type]
            distill_action=load.action,
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    try:
        batch = BeforeYouEditBatchPayload.model_validate(load.payload)
    except Exception:
        return BeforeEditHintBatchResult(
            tier=gate.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                "Batch sidecar payload does not match BeforeYouEditBatchPayload "
                "schema; check trw-distill version compatibility"
            ),
            distill_sidecar_path=load.sidecar_path,
            distill_sidecar_sha=load.sidecar_sha,
        )

    queries: list[str] = []
    for h in batch.hints:
        queries.extend(build_file_queries(h.target_path))
    learnings = collect_learnings(queries)

    return BeforeEditHintBatchResult(
        tier=gate.tier,
        distill_batch=batch,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=load.sidecar_path,
        distill_sidecar_sha=load.sidecar_sha,
        learnings=learnings,
        learnings_count=len(learnings),
    )


def register_before_edit_hint_batch_tools(server: FastMCP) -> None:
    """Register trw_before_edit_hint_batch on the MCP server."""

    @server.tool()
    def trw_before_edit_hint_batch(
        repo_root: str | None = None,
        cache_dir: str | None = None,
    ) -> dict[str, Any]:
        """Return c735+c743 BeforeYouEditBatch for the current SHA.

        Use when an agent is planning a multi-file edit and needs batched
        before-edit hints from a persisted trw-distill sidecar.

        Tier-gated (paid tiers only — see trw_before_edit_hint for the
        free-tier learnings counterpart). Returns
        ``BeforeEditHintBatchResult.model_dump()``. NEVER raises.
        """
        result = compute_before_edit_hint_batch(
            repo_root=repo_root,
            cache_dir=cache_dir,
        )
        return result.model_dump()


__all__ = [
    "BeforeEditHintBatchResult",
    "BeforeYouEditBatchPayload",
    "compute_before_edit_hint_batch",
    "register_before_edit_hint_batch_tools",
]
