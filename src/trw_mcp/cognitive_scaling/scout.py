"""Cognitive Scaling Scout — PRD-SCALE-001 Sprint-97 (Phase 0 + Phase 1).

The Scout is a deterministic, fail-open, language-agnostic classifier. It:

  * computes three grounded signals (``blast_radius``, ``churn``,
    ``precedent_gap``) — FR01, delegated to ``_scout_signals``;
  * routes the session into a ``PlanningMode`` with a hard rule that mode >= 2
    requires >= 2 threshold hits — FR01/FR02;
  * degrades to DIRECT when fewer than two signals are computable, without
    ever silently escalating ceremony — FR12;
  * writes the session-layer overlay ``meta/session_profile.yaml`` that the H2
    profile resolver consumes (the dynamic-ceremony lever) — FR03;
  * sources the probe budget from the canonical CORE-144 table — FR07.

Sprint-98 deferrals (drafts/rubric/synthesizer/dissent/probe-execution) are
NOT implemented here. This module is the Scout-only Phase-1 surface.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from ruamel.yaml import YAML

from trw_mcp.cognitive_scaling._scout_signals import compute_signals
from trw_mcp.cognitive_scaling._scout_throttle import ThrottleDecision, evaluate_throttle
from trw_mcp.models.cognitive_scaling import (
    CEREMONY_TIER_BY_MODE,
    PlanningMode,
    ScoutClassification,
    ScoutSignals,
    SessionProfileOverlay,
)

logger = structlog.get_logger(__name__)

_yaml = YAML()
_yaml.default_flow_style = False

#: Minimum computable signals before Scout will classify above DIRECT (FR12).
_MIN_SIGNALS_FOR_ESCALATION = 2


def propose_probe_budget(planning_mode: PlanningMode) -> int:
    """Resolve the probe budget for a mode from the canonical CORE-144 table.

    FR07 source of truth: ``trw_mcp.probe.budget.PLANNING_MODE_BUDGETS`` keyed
    on the mode *name*. This is the single import point — Scout never
    re-declares budget values.
    """
    from trw_mcp.probe.budget import budget_for_mode

    return budget_for_mode(planning_mode.name)


def _mode_from_hits(hit_count: int) -> tuple[PlanningMode, str | None]:
    """Map threshold-hit count to a planning mode (FR01 hard rule).

    Hard rule (FR01): mode >= 2 requires >= 2 hits. 0 hits -> DIRECT, 1 hit ->
    DUAL_DRAFT, 2 hits -> TRIANGULATED, 3 hits -> TRIANGULATED_WITH_PROBE.
    Returns ``(mode, escalation_reason)``.
    """
    if hit_count <= 0:
        return PlanningMode.DIRECT, None
    if hit_count == 1:
        return PlanningMode.DUAL_DRAFT, "1 of 3 signals crossed threshold"
    if hit_count == 2:
        return PlanningMode.TRIANGULATED, "2 of 3 signals crossed threshold"
    return PlanningMode.TRIANGULATED_WITH_PROBE, "all 3 signals crossed threshold"


def _degraded_classification(reason: str, *, signals: ScoutSignals | None = None) -> ScoutClassification:
    """Build a fail-open DIRECT classification (FR12).

    MUST NOT silently escalate ceremony — degrade always lands DIRECT/MINIMAL.
    ``signals`` is passed through the constructor (not mutated post-build) so
    the degraded state survives a future ``frozen=True`` on ScoutClassification.
    """
    logger.warning("scout_degraded", component="cognitive_scaling.scout", reason=reason)
    return ScoutClassification(
        planning_mode=PlanningMode.DIRECT,
        ceremony_tier=CEREMONY_TIER_BY_MODE[PlanningMode.DIRECT],
        probe_budget=0,
        confidence=0.5,
        degraded=True,
        downgrade_reason=reason,
        signals=signals if signals is not None else ScoutSignals(),
    )


def classify(
    *,
    task_description: str,
    declared_paths: list[str] | None = None,
    project_root: Path,
    trw_dir: Path,
    blast_radius_threshold: int = 10,
    churn_commit_threshold: int = 8,
    override_mode: PlanningMode | None = None,
) -> ScoutClassification:
    """Classify a session into a ``PlanningMode`` (FR01/FR02/FR12/FR13).

    ``override_mode`` (FR13) forces the result regardless of signals; the
    original Scout-emitted mode is recorded for the dissent ledger. Otherwise
    signals are computed and the hard escalation rule applies. Never raises:
    any unexpected failure degrades to DIRECT (FR12).
    """
    paths = declared_paths or []
    try:
        signals = compute_signals(
            task_description=task_description,
            declared_paths=paths,
            project_root=project_root,
            trw_dir=trw_dir,
            blast_radius_threshold=blast_radius_threshold,
            churn_commit_threshold=churn_commit_threshold,
        )
    except Exception:  # justified: fail-open per FR12 — signal compute must not crash
        signals = ScoutSignals(
            blast_radius_available=False,
            churn_available=False,
            precedent_gap_available=False,
        )

    if override_mode is not None:
        return _apply_override(override_mode, signals)

    # FR12: too few computable signals -> degrade to DIRECT (no escalation).
    if signals.available_count() < _MIN_SIGNALS_FOR_ESCALATION:
        return _degraded_classification(
            f"only {signals.available_count()} of 3 signals computable",
            signals=signals,
        )

    mode, escalation_reason = _mode_from_hits(signals.hit_count())
    tier = CEREMONY_TIER_BY_MODE[mode]
    return ScoutClassification(
        planning_mode=mode,
        signals=signals,
        ceremony_tier=tier,
        probe_budget=propose_probe_budget(mode),
        confidence=0.9 if mode == PlanningMode.DIRECT else 0.8,
        escalation_reason=escalation_reason,
        downgrade_reason=("all signals below threshold" if mode == PlanningMode.DIRECT else None),
    )


def _apply_override(override_mode: PlanningMode, signals: ScoutSignals) -> ScoutClassification:
    """Honor a user planning-mode override (FR13), recording the original mode."""
    # Recompute what Scout WOULD have emitted, for the dissent record.
    if signals.available_count() < _MIN_SIGNALS_FOR_ESCALATION:
        scout_mode = PlanningMode.DIRECT
    else:
        scout_mode, _ = _mode_from_hits(signals.hit_count())
    return ScoutClassification(
        planning_mode=override_mode,
        signals=signals,
        ceremony_tier=CEREMONY_TIER_BY_MODE[override_mode],
        probe_budget=propose_probe_budget(override_mode),
        confidence=1.0,
        source="user_override",
        original_mode=scout_mode,
        escalation_reason=(f"user override to {override_mode.name}" if override_mode > scout_mode else None),
        downgrade_reason=(f"user override to {override_mode.name}" if override_mode < scout_mode else None),
    )


def _session_overlay(classification: ScoutClassification) -> SessionProfileOverlay:
    """Project a classification into the session-layer overlay (FR03)."""
    rationale: str | None
    if classification.source == "user_override":
        rationale = (
            f"user_override: {classification.planning_mode.name} "
            f"(scout proposed {classification.original_mode.name if classification.original_mode else 'n/a'})"
        )
    else:
        rationale = classification.escalation_reason or classification.downgrade_reason
    return SessionProfileOverlay(
        ceremony_tier=classification.ceremony_tier,
        planning_mode=int(classification.planning_mode),
        probe_budget=classification.probe_budget,
        rationale=rationale,
    )


def _h2_overlay_body(overlay: SessionProfileOverlay) -> dict[str, object]:
    """Render the H2-consumable ``session_profile.yaml`` body (FR03).

    The live H2 resolver (``profile/session_resolve.py::_session_layer``)
    validates every non-``rationale`` key against the ``Profile`` surface
    (``extra=forbid``) — so only ``ceremony_tier`` may appear as a top-level
    Profile key. ``planning_mode`` / ``probe_budget`` are SCALE-001 telemetry
    join keys that the resolver does NOT model, so they are carried inside the
    stripped ``rationale`` (auditable, never breaks H2 validation). The full
    classification is also returned to the caller + emitted to telemetry.
    """
    rationale_parts = [
        f"planning_mode={overlay.planning_mode}",
        f"probe_budget={overlay.probe_budget}",
    ]
    if overlay.rationale:
        rationale_parts.append(overlay.rationale)
    return {
        "ceremony_tier": overlay.ceremony_tier,
        "rationale": "; ".join(rationale_parts),
    }


def write_session_profile(classification: ScoutClassification, *, run_dir: Path) -> Path | None:
    """Write ``{run_dir}/meta/session_profile.yaml`` (FR03).

    This is the cross-PRD contract: the H2 profile resolver
    (``profile/session_resolve.py::_session_layer``) reads this file as the
    session-layer overlay that supersedes repo/user layers. The body is the
    H2-consumable Profile surface (``ceremony_tier`` + a stripped
    ``rationale`` carrying the SCALE-001 join keys). Fail-open: a write failure
    logs and returns None rather than crashing the session.
    """
    overlay = _session_overlay(classification)
    meta_dir = run_dir / "meta"
    path = meta_dir / "session_profile.yaml"
    try:
        meta_dir.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            _yaml.dump(_h2_overlay_body(overlay), fh)
    except OSError:
        logger.warning(
            "scout_session_profile_write_failed",
            component="cognitive_scaling.scout",
            path=str(path),
            exc_info=True,
        )
        return None
    logger.info(
        "scout_session_profile_written",
        component="cognitive_scaling.scout",
        op="write_session_profile",
        outcome="ok",
        planning_mode=classification.planning_mode.name,
        ceremony_tier=classification.ceremony_tier,
        path=str(path),
    )
    return path


__all__ = [
    "ThrottleDecision",
    "classify",
    "evaluate_throttle",
    "propose_probe_budget",
    "write_session_profile",
]
