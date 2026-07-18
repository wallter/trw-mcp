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

from typing import Any

from fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.tools._learnings_collector import (
    LearningSummary,
    build_file_queries,
    collect_learnings,
)
from trw_mcp.tools._sidecar_substrate import CurrentSidecarStatus, resolve_current_sidecar
from trw_mcp.tools.before_edit_hint import BeforeYouEditHintPayload

_ARTIFACT_NAME_BATCH: str = "before-edit-batch"
_TIER_FEATURE: str = "trw_before_edit_hint:distill_sidecar"


class BeforeYouEditBatchPayload(BaseModel):
    """Field-by-field mirror of trw-distill BeforeYouEditBatch.

    Maintained by hand in trw-mcp (no trw_distill import).
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    # Constraints mirror the trw-distill source; parity-checked by
    # scripts/check-schema-mirror-parity.py (PRD-INFRA-134 FR-05).
    total_files: int = Field(ge=0)
    files_in_map: int = Field(ge=0)
    total_hotspot_warnings: int = Field(ge=0)
    hints: list[BeforeYouEditHintPayload] = Field(default_factory=list)


class BeforeEditHintBatchResult(BaseModel):
    """Top-level result for the batch tool."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    tier: str
    distill_batch: BeforeYouEditBatchPayload | None = None
    distill_status: CurrentSidecarStatus = "sidecar_missing"
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
    sidecar = resolve_current_sidecar(
        repo_root=repo_root,
        cache_dir=cache_dir,
        feature=_TIER_FEATURE,
        artifact_name=_ARTIFACT_NAME_BATCH,
        cli_remediation=("trw-distill self-improve before-edit --repo . --files-from <changed.txt> --persist-sidecar"),
    )
    if sidecar.status != "hint_available" or sidecar.payload is None:
        return BeforeEditHintBatchResult(
            tier=sidecar.tier,
            distill_status=sidecar.status,
            distill_action=sidecar.action,
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    try:
        batch = BeforeYouEditBatchPayload.model_validate(sidecar.payload)
    except Exception:
        return BeforeEditHintBatchResult(
            tier=sidecar.tier,
            distill_status="sidecar_malformed",
            distill_action=(
                "Batch sidecar payload does not match BeforeYouEditBatchPayload "
                "schema; check trw-distill version compatibility"
            ),
            distill_sidecar_path=sidecar.sidecar_path,
            distill_sidecar_sha=sidecar.sidecar_sha,
        )

    queries: list[str] = []
    for h in batch.hints:
        queries.extend(build_file_queries(h.target_path))
    learnings = collect_learnings(queries)

    return BeforeEditHintBatchResult(
        tier=sidecar.tier,
        distill_batch=batch,
        distill_status="hint_available",
        distill_action=None,
        distill_sidecar_path=sidecar.sidecar_path,
        distill_sidecar_sha=sidecar.sidecar_sha,
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
