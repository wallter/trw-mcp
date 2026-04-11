"""TRW review tool — structured code quality findings artifact.

PRD-QUAL-022: Accepts findings, computes verdict, writes review.yaml.
PRD-QUAL-026: Cross-model review mode — routes diff to external model.
PRD-QUAL-027: Multi-agent parallel review — confidence-scored findings.
Extracted from ceremony.py for single-responsibility.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import structlog
from fastmcp import FastMCP

from trw_mcp.state._paths import find_active_run
from trw_mcp.tools._review_helpers import (
    REVIEWER_ROLES as REVIEWER_ROLES,
)
from trw_mcp.tools._review_helpers import (
    _compute_verdict as _compute_verdict,
)
from trw_mcp.tools._review_helpers import (
    _get_git_diff as _get_git_diff,
)
from trw_mcp.tools._review_helpers import (
    _invoke_cross_model_review as _invoke_cross_model_review,
)
from trw_mcp.tools._review_helpers import (
    _normalize_severity as _normalize_severity,
)
from trw_mcp.tools._review_helpers import (
    _persist_review_artifact as _persist_review_artifact,
)
from trw_mcp.tools._review_helpers import (
    _run_multi_reviewer_analysis as _run_multi_reviewer_analysis,
)
from trw_mcp.tools.telemetry import log_tool_call

logger = structlog.get_logger(__name__)


def register_review_tools(server: FastMCP) -> None:
    """Register review tools on the MCP server."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_review(
        findings: list[dict[str, str]] | None = None,
        run_path: str | None = None,
        mode: str | None = None,
        reviewer_findings: list[dict[str, object]] | None = None,
        prd_ids: list[str] | None = None,
    ) -> dict[str, object]:
        """Review code quality and produce structured findings artifact (PRD-QUAL-022).

        Accepts a list of findings (category, severity, description) and computes
        a verdict (pass/warn/block). Writes review.yaml artifact to the run directory.

        Modes:
        - manual: findings=[...] provided directly (backward compatible)
        - auto: multi-reviewer analysis with confidence filtering (QUAL-027)
        - cross_model: routes diff to external model family (QUAL-026)
        - reconcile: compare PRD FRs against git diff to detect spec drift

        Args:
            findings: List of dicts with category, severity, description keys.
            run_path: Explicit run path. Auto-detected if None.
            mode: Review mode — 'manual', 'auto', 'cross_model', or 'reconcile'. Auto-detected.
            reviewer_findings: Pre-collected findings from subagent layer (QUAL-027).
            prd_ids: Explicit PRD IDs for reconcile mode. Auto-discovered if None.
        """
        from trw_mcp.models.config import get_config
        from trw_mcp.tools._review_auto import handle_auto_mode, handle_cross_model_mode
        from trw_mcp.tools._review_manual import handle_manual_mode, handle_reconcile_mode

        config = get_config()

        # Mode detection:
        # - mode="reconcile" explicitly set -> reconcile (check first)
        # - findings=[...] explicitly passed -> manual (backward compat)
        # - mode explicitly set -> use that mode
        # - reviewer_findings provided (no mode) -> auto
        # - nothing provided -> manual (backward compat with old callers)
        if mode == "reconcile":
            effective_mode = "reconcile"
        elif findings is not None:
            effective_mode = "manual"
        elif mode is not None:
            effective_mode = mode
        elif reviewer_findings is not None:
            effective_mode = "auto"
        else:
            effective_mode = "manual"

        # Resolve run directory
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run()

        # Auto-update phase to REVIEW
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(resolved_run, Phase.REVIEW)

        ts = datetime.now(timezone.utc).isoformat()
        review_id = "review-" + secrets.token_hex(4)

        response: dict[str, object]
        if effective_mode == "manual":
            response = cast(
                "dict[str, object]",
                handle_manual_mode(
                    findings or [],
                    resolved_run,
                    review_id,
                    ts,
                    prd_ids,
                ),
            )
        elif effective_mode == "reconcile":
            response = cast(
                "dict[str, object]",
                handle_reconcile_mode(
                    config,
                    resolved_run,
                    review_id,
                    ts,
                    prd_ids,
                ),
            )
        elif effective_mode == "cross_model":
            response = cast(
                "dict[str, object]",
                handle_cross_model_mode(
                    config,
                    resolved_run,
                    review_id,
                    ts,
                    prd_ids,
                ),
            )
        else:
            # Auto mode (QUAL-027)
            response = cast(
                "dict[str, object]",
                handle_auto_mode(
                    config,
                    resolved_run,
                    review_id,
                    ts,
                    reviewer_findings,
                    prd_ids,
                ),
            )

        _review_verdict = str(response.get("verdict", ""))
        _review_score = response.get("total_score", response.get("score", None))
        _review_run_id = str(resolved_run.name) if resolved_run else ""
        _review_phase = str(response.get("phase", effective_mode))
        if _review_verdict == "block":
            logger.warning("review_blocked", reason=_review_verdict, phase=_review_phase)
        else:
            logger.info(
                "review_ok",
                run_id=_review_run_id,
                score=_review_score,
                verdict=_review_verdict,
                phase=_review_phase,
            )
        logger.debug(
            "review_detail",
            dimensions=list(response["dimensions"]) if "dimensions" in response and isinstance(response["dimensions"], list) else [],
            run_dir=str(resolved_run) if resolved_run else "",
        )

        # Mark review in ceremony state and attach status summary.
        try:
            from trw_mcp.state._paths import resolve_trw_dir
            from trw_mcp.state.ceremony_progress import mark_review
            from trw_mcp.tools._ceremony_status import append_ceremony_status

            trw_dir = resolve_trw_dir()
            verdict = str(response.get("verdict", ""))
            p0_count = int(str(response.get("critical_count", 0)))

            # Normalize reconcile verdicts to standard set {pass, warn, block}
            if verdict == "drift_detected":
                verdict = "warn"
                # Use mismatch_count as p0_count if critical_count not set
                if p0_count == 0:
                    p0_count = int(str(response.get("mismatch_count", 0)))
            elif verdict == "clean":
                verdict = "pass"

            mark_review(trw_dir, verdict=verdict, p0_count=p0_count)
            append_ceremony_status(response, trw_dir)
        except Exception:  # justified: fail-open, status decoration must not block review
            logger.debug("review_ceremony_status_skipped", exc_info=True)  # justified: fail-open

        return response
