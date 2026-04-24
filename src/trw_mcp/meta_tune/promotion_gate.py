"""Promotion gate — outcome + Goodhart + human sign-off.

PRD-HPO-SAFE-001 FR-2/FR-3/FR-11. A candidate may promote only when all
three hold: declared metric delta is positive (outcome-correlation proxy),
the declared delta is not an implausible spike versus the lookback history
(Goodhart detector), and a human reviewer has signed off.

Entry point: :class:`PromotionGate.evaluate(proposal)` returning
:class:`GateDecision` ∈ {approve, reject, needs_human_review}.

Kill switch (FR-7/FR-13): when ``config.meta_tune.enabled`` is False the
gate short-circuits with ``decision='reject', disabled=True``.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Literal

import structlog
from pydantic import BaseModel, ConfigDict, Field

from trw_mcp.telemetry.event_base import MetaTuneEvent

if TYPE_CHECKING:
    from trw_mcp.models.config._main import TRWConfig

logger = structlog.get_logger(__name__)


# Goodhart-check threshold: declared delta must be ≤ this × max of recent
# observed deltas. A value of 10 catches "10x larger than anything we've
# recently seen" as a likely reward-hacking shape.
_GOODHART_SPIKE_RATIO: float = 10.0

# Minimum lookback window for Goodhart to engage. With fewer than this many
# historical entries, we skip the check (insufficient baseline).
_GOODHART_MIN_HISTORY: int = 3


DecisionLiteral = Literal["approve", "reject", "needs_human_review"]


class PromotionProposal(BaseModel):
    """A candidate ready for the promotion gate."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    proposal_id: str
    declared_metric_delta: float = Field(
        ..., description="Candidate's declared metric improvement (≥0 means up-is-good)."
    )
    surface_classification: Literal["advisory", "control"] = Field(
        ..., description="Classifier verdict — control proposals are rejected."
    )
    surfaces: tuple[str, ...] = Field(
        default=(), description="Domains touched (prompt/config/policy/etc.)."
    )
    diff_lines_touched: int = Field(
        default=0, description="Number of lines changed; used for context only."
    )


class GateDecision(BaseModel):
    """Result of a promotion-gate evaluation."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    decision: DecisionLiteral
    reason: str
    disabled: bool = False
    outcome_ok: bool = False
    goodhart_ok: bool = False
    human_signoff: bool = False
    vote_count: int = 0


class PromotionGate:
    """Evaluate promotion proposals against outcome/Goodhart/human checks."""

    def __init__(
        self,
        *,
        config: TRWConfig | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> None:
        if config is None:
            from trw_mcp.models.config._main import TRWConfig

            config = TRWConfig()
        self._config = config
        self._history: list[dict[str, object]] = list(history or [])

    # --- Individual checks ---------------------------------------------------

    def outcome_ok(self, proposal: PromotionProposal) -> bool:
        """Declared metric improvement must be strictly positive."""
        return proposal.declared_metric_delta > 0.0

    def goodhart_ok(self, proposal: PromotionProposal) -> bool:
        """Reject implausible spikes vs the recent history window."""
        if len(self._history) < _GOODHART_MIN_HISTORY:
            return True
        prior_deltas: list[float] = []
        for row in self._history:
            val = row.get("declared_metric_delta")
            if isinstance(val, (int, float)):
                prior_deltas.append(float(val))
        if not prior_deltas:
            return True
        max_prior = max(abs(d) for d in prior_deltas)
        if max_prior == 0.0:
            # No variation observed; any non-tiny delta is suspicious.
            return proposal.declared_metric_delta <= 0.05
        return proposal.declared_metric_delta <= _GOODHART_SPIKE_RATIO * max_prior

    def human_signoff(
        self,
        *,
        reviewer_id: str | None,
        approval_ts: datetime | None,
    ) -> bool:
        return bool(reviewer_id) and approval_ts is not None

    # --- Main entry point ----------------------------------------------------

    def evaluate(
        self,
        proposal: PromotionProposal,
        *,
        reviewer_id: str | None = None,
        approval_ts: datetime | None = None,
    ) -> GateDecision:
        """Produce a :class:`GateDecision` for ``proposal``."""
        if not self._config.meta_tune.enabled:
            logger.warning(
                "meta_tune_disabled",
                component="meta_tune.promotion_gate",
                op="evaluate",
                outcome="noop",
                reason="kill_switch_off",
            )
            return GateDecision(
                decision="reject",
                reason="meta-tune-disabled",
                disabled=True,
            )

        # FR-1: control-surface proposals are rejected before scoring.
        if proposal.surface_classification == "control":
            return self._record(
                proposal,
                GateDecision(
                    decision="reject",
                    reason="control-surface-violation",
                    outcome_ok=False,
                    goodhart_ok=False,
                    human_signoff=False,
                    vote_count=1,
                ),
            )

        outcome_ok = self.outcome_ok(proposal)
        goodhart_ok = self.goodhart_ok(proposal)
        human_ok = self.human_signoff(
            reviewer_id=reviewer_id, approval_ts=approval_ts
        )
        vote_count = int(outcome_ok) + int(goodhart_ok) + int(human_ok)

        if not outcome_ok:
            return self._record(
                proposal,
                GateDecision(
                    decision="reject",
                    reason="outcome-correlation-fail",
                    outcome_ok=False,
                    goodhart_ok=goodhart_ok,
                    human_signoff=human_ok,
                    vote_count=vote_count,
                ),
            )
        if not goodhart_ok:
            return self._record(
                proposal,
                GateDecision(
                    decision="reject",
                    reason="goodhart-flag",
                    outcome_ok=outcome_ok,
                    goodhart_ok=False,
                    human_signoff=human_ok,
                    vote_count=vote_count,
                ),
            )
        if not human_ok:
            return self._record(
                proposal,
                GateDecision(
                    decision="needs_human_review",
                    reason="awaiting-human-signoff",
                    outcome_ok=outcome_ok,
                    goodhart_ok=goodhart_ok,
                    human_signoff=False,
                    vote_count=vote_count,
                ),
            )
        return self._record(
            proposal,
            GateDecision(
                decision="approve",
                reason="promoted",
                outcome_ok=True,
                goodhart_ok=True,
                human_signoff=True,
                vote_count=vote_count,
            ),
        )

    # --- Emission ------------------------------------------------------------

    def _record(
        self,
        proposal: PromotionProposal,
        decision: GateDecision,
    ) -> GateDecision:
        try:
            from trw_mcp.telemetry.unified_events import emit as _emit_unified

            event = MetaTuneEvent(
                session_id=proposal.proposal_id,
                payload={
                    "action": "promotion_gate_evaluate",
                    "proposal_id": proposal.proposal_id,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "promotion_gate_vote_count": decision.vote_count,
                    "surface_classification_result": proposal.surface_classification,
                },
            )
            # PRD-HPO-SAFE-001 telemetry dispatch — emit via unified writer.
            # fallback_dir=None leaves emission best-effort when no run is
            # pinned; the audit log is the authoritative record either way.
            _emit_unified(event, run_dir=None, fallback_dir=None)
        except Exception:  # justified: telemetry_best_effort, gate must not raise
            logger.warning(
                "promotion_gate_telemetry_failed",
                component="meta_tune.promotion_gate",
                op="_record",
                outcome="degraded",
            )
        logger.info(
            "promotion_gate_decision",
            component="meta_tune.promotion_gate",
            op="evaluate",
            outcome="ok" if decision.decision == "approve" else decision.decision,
            proposal_id=proposal.proposal_id,
            decision=decision.decision,
            reason=decision.reason,
            vote_count=decision.vote_count,
        )
        return decision


__all__ = [
    "GateDecision",
    "PromotionGate",
    "PromotionProposal",
]
