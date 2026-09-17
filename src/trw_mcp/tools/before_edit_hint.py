"""trw_before_edit_hint MCP tool (PRD-DIST-1983 + PRD-DIST-1984, cycle 746).

Surfaces cold-start codebase intelligence to any MCP client without
requiring the client to shell out to the trw-distill CLI.

Two-source composition (PRD-DIST-1984):
- **Primary**: c742/c743 trw-distill sidecar at
  ``<cache_dir>/before-edit-hint-<sha>.json`` (tier-gated;
  ``trw_before_edit_hint:distill_sidecar`` feature flag).
- **Secondary**: trw_recall over existing learnings keyed on the
  file path / basename (always available, no tier gate). Returns top-N
  relevant learnings even when the distill sidecar is absent.

IP boundary (trw-distill is PROPRIETARY; trw-mcp is PUBLIC):
- This module MUST NOT import ``trw_distill``. The cross-package
  contract is the sidecar envelope ``risk-report-sidecar/v0`` —
  field-by-field Pydantic mirror via :class:`BeforeYouEditHintPayload`.

Honest scope per CONSTITUTION §1:
- Reads the single-file ``before-edit-hint-<sha>.json`` artifact (c743) and
  falls back to ``before-edit-batch-<sha>.json`` when that artifact does not
  describe the requested file. The fallback is not a convenience: the
  single-file name has NO per-file discriminator, so every producer writing it
  overwrites the last, and a commit touching N files can serve at most one of
  them from it. The post-commit refresh accordingly emits the batch artifact.
- Stale-SHA detection compares sidecar SHA literal to current git HEAD.
  No version-range / fuzzy-match fallback.
- Learnings half always returns even when distill_sidecar feature is
  ungated — preserves operator value at free tier.

Repo-root / SHA / envelope resolution lives in ``_sidecar_substrate``
(extracted FROM this module at c747). This tool was the last of the five
sidecar consumers still carrying a hand-rolled copy of that logic, and the
copy had diverged: an unresolvable ``git rev-parse HEAD`` was reported as
``stale_sha`` — a status that asserts a sidecar was read and disagreed —
when no sidecar had been consulted at all. The substrate has a distinct
``no_git_sha`` for exactly that case. Consuming the substrate is what keeps
the two from diverging again; do not reintroduce a local resolver here.
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from fastmcp import Context, FastMCP

from trw_mcp.tools._before_edit_hint_core import (
    _SCHEMA_VERSION_ACCEPTED,
    BeforeEditHintResult,
    BeforeEditHintStatus,
    BeforeYouEditHintPayload,
    LearningSummary,
    _select_distill_hint,
    compute_before_edit_hint,
)
from trw_mcp.tools._client_detection import resolve_client_profile, resolve_tier_for_client

__all__ = [
    "_SCHEMA_VERSION_ACCEPTED",
    "BeforeEditHintResult",
    "BeforeEditHintStatus",
    "BeforeYouEditHintPayload",
    "LearningSummary",
    "_select_distill_hint",
    "compute_before_edit_hint",
    "register_before_edit_hint_tools",
    "resolve_client_profile",
]


def register_before_edit_hint_tools(server: FastMCP) -> None:
    """Register trw_before_edit_hint on the MCP server."""

    @server.tool()
    def trw_before_edit_hint(
        file_path: str,
        repo_root: str | None = None,
        cache_dir: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        """Return sidecar risk hints (paid tiers) + prior learnings (all tiers) for one file.

        Use when: about to edit file_path (trw_before_edit_hint_batch covers
        many files). Never raises; on failure, distill_status explains why.
        """
        result = compute_before_edit_hint(
            file_path=file_path,
            repo_root=repo_root,
            cache_dir=cache_dir,
        )
        # --- telemetry (fail-open) ---
        with suppress(Exception):  # justified: fail-open telemetry, never break the tool
            from trw_mcp.channels._distill_telemetry import emit_tool_call

            sidecar_sha = result.distill_sidecar_sha or ""
            record_ids = [f"hotspot:{file_path}@{sidecar_sha[:8]}"] if sidecar_sha else []
            emit_tool_call(
                tool_name="trw_before_edit_hint",
                file_path=file_path,
                tier=result.tier,
                record_ids=record_ids,
            )
        # --- tier-aware response enrichment (fail-open) ---
        base: dict[str, Any] = result.model_dump()
        with suppress(Exception):  # justified: fail-open enrichment never breaks the base response
            from trw_mcp.channels._tool_return_tiers import enrich_response

            client = resolve_client_profile(ctx=ctx)
            client_tier = resolve_tier_for_client(client)
            return enrich_response(base, client_tier=client_tier)
        return base
