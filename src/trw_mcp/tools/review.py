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
from fastmcp import Context, FastMCP

from trw_mcp.state._call_context import build_call_context as _build_call_context
from trw_mcp.state._paths import find_active_run
from trw_mcp.tools._review_helpers import (
    PRE_AUDIT_SELF_REVIEW_EVENT as PRE_AUDIT_SELF_REVIEW_EVENT,
)
from trw_mcp.tools._review_helpers import (
    PRE_IMPLEMENTATION_CHECKLIST_EVENT as PRE_IMPLEMENTATION_CHECKLIST_EVENT,
)
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
    _load_preflight_checks as _load_preflight_checks,
)
from trw_mcp.tools._review_helpers import (
    _log_preflight_events as _log_preflight_events,
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
    _register_review_tool(server)


def _register_review_tool(server: FastMCP) -> None:
    """Register the structured review tool."""

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_review(
        ctx: Context | None = None,
        findings: list[dict[str, str]] | None = None,
        run_path: str | None = None,
        mode: str | None = None,
        reviewer_findings: list[dict[str, object]] | None = None,
        prd_ids: list[str] | None = None,
        reviewer_source: str | None = None,
        reviewer_receipt_id: str | None = None,
        reviewer_run_id: str | None = None,
        reviewer_session_id: str | None = None,
        review_completed: bool = False,
    ) -> dict[str, object]:
        """Compute a structured code-review verdict and persist a review.yaml artifact.

        Use when:
        - Gating a PR or delivery and you need a pass/warn/block verdict with receipts.
        - You have pre-collected findings from a reviewer subagent (auto mode).
        - You want to detect spec-vs-code drift between a PRD and git diff (reconcile).

        Modes:
        - manual: caller passes ``findings=[...]`` directly. An empty manual
          invocation is persisted but marked non-substantive and does not
          satisfy REVIEW readiness.
        - auto: multi-reviewer analysis with confidence filtering.
        - cross_model: route diff to an external model family.
        - reconcile: compare PRD FRs against git diff.

        Input:
        - findings: list[{category, severity, description}] — triggers manual mode.
        - run_path: explicit run directory; auto-detected when None.
        - mode: explicit mode override; auto-detected when None.
        - reviewer_findings: pre-collected findings from subagent layer (auto).
        - prd_ids: explicit PRD IDs; reconcile mode auto-discovers when None.
        - reviewer_source: PRD-CORE-213-FR01 provenance override — one of
          self|subagent|cross_model|operator. When None the source is derived
          honestly from the effective mode (manual->self, auto->subagent,
          cross_model->cross_model). ``operator`` requires reviewer_receipt_id.
        - reviewer_receipt_id: operator sign-off token (required when
          reviewer_source='operator').
        - reviewer_run_id / reviewer_session_id: OQ-001 — the reviewing agent's
          own run/session identity. Verified against framework-recorded state
          (run.yaml under .trw/runs + the .trw/runtime/pins.json pin store);
          a verified distinct identity classifies the review ``independent``,
          an unverifiable claim falls back to the delivering run's identity
          (``asserted_independent`` at best). Never self-mintable.
        - review_completed: explicit manual-review completion assertion. This
          permits a fully covered zero-finding manual review to be substantive;
          an empty invocation remains non-substantive when false.

        Output: dict with fields
        {verdict: "pass"|"warn"|"block", findings_count: int, categories: dict,
         review_path: str, run_id: str, mode: str, substantive: bool}.

        Example:
            trw_review(findings=[{"category":"security","severity":"high","description":"..."}])
            → {"verdict": "block", "findings_count": 1, "review_path": ".../review.yaml",
               "mode": "manual"}
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

        # Resolve run directory (PRD-CORE-141 FR03/FR05).
        resolved_run: Path | None = None
        if run_path:
            resolved_run = Path(run_path).resolve()
        else:
            resolved_run = find_active_run(context=_build_call_context(ctx))

        # Auto-update phase to REVIEW
        from trw_mcp.models.run import Phase
        from trw_mcp.state.phase import try_update_phase

        try_update_phase(resolved_run, Phase.REVIEW)

        ts = datetime.now(timezone.utc).isoformat()
        review_id = "review-" + secrets.token_hex(4)

        # OQ-001: verify any caller-claimed reviewer identity against
        # framework-recorded state (run.yaml + pin store). Verification failure
        # is honest fallback, never an error — the block then carries the
        # delivering run's identity and classifies asserted_independent at best.
        verified_reviewer_identity = None
        identity_claimed = bool((reviewer_run_id or "").strip() or (reviewer_session_id or "").strip())
        if identity_claimed:
            from trw_mcp.state._paths import resolve_trw_dir
            from trw_mcp.tools._review_provenance import (
                read_run_identity,
                resolve_verified_reviewer_identity,
            )

            trw_dir = resolve_trw_dir()
            verified_reviewer_identity = resolve_verified_reviewer_identity(
                reviewer_run_id,
                reviewer_session_id,
                read_run_identity(resolved_run),
                runs_root=trw_dir / "runs",
                pins_path=trw_dir / "runtime" / "pins.json",
            )
            if verified_reviewer_identity is None:
                logger.warning(
                    "reviewer_identity_unverified",
                    claimed_run_id=reviewer_run_id or "",
                    claimed_session_id=reviewer_session_id or "",
                )

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
                    reviewer_source=reviewer_source,
                    reviewer_receipt_id=reviewer_receipt_id,
                    review_completed=review_completed,
                    verified_reviewer_identity=verified_reviewer_identity,
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
                    verified_reviewer_identity=verified_reviewer_identity,
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
                    verified_reviewer_identity=verified_reviewer_identity,
                ),
            )

        # OQ-001 honesty: tell the caller whether a claimed identity verified.
        if identity_claimed:
            response["reviewer_identity_verified"] = verified_reviewer_identity is not None

        # A spec-reconciliation report is useful evidence, but it is not a
        # code-quality review. Manual/auto handlers stamp their own substantive
        # status; other actual review modes remain substantive by default.
        if effective_mode == "reconcile":
            response["substantive"] = False
            response["non_substantive_reason"] = "spec reconciliation is not a code-quality review"
        substantive = bool(response.get("substantive", True))

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
            dimensions=list(response["dimensions"])
            if "dimensions" in response and isinstance(response["dimensions"], list)
            else [],
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

            mark_review(trw_dir, verdict=verdict, p0_count=p0_count, substantive=substantive)
            append_ceremony_status(response, trw_dir)
        except Exception:  # justified: fail-open, status decoration must not block review
            logger.debug("review_ceremony_status_skipped", exc_info=True)  # justified: fail-open

        return response
