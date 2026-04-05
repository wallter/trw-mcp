"""Attribution pipeline orchestrator.

PRD-CORE-108-FR05: Runs at deliver time as a deferred step.
Coordinates IPS (tier 1), DML (tier 2, optional), selective
attribution, and phase eligibility traces.
"""

from __future__ import annotations

from typing import Any

import structlog

from trw_mcp.scoring.attribution._common import map_estimate_to_category
from trw_mcp.scoring.attribution.eligibility import compute_phase_weight
from trw_mcp.scoring.attribution.ips import compute_ips_attribution
from trw_mcp.scoring.attribution.selective import distribute_credit

logger = structlog.get_logger(__name__)


def _try_dml_attribution(
    learning_id: str,
    propensity_records: list[dict[str, object]],
    outcomes: list[dict[str, object]],
    client_profile: str = "",
    model_family: str = "",
) -> dict[str, object] | None:
    """Attempt DML attribution using EconML (optional dependency).

    Returns None if EconML is not installed or if estimation fails.
    """
    try:
        import numpy as np  # noqa: F811
        from econml.dml import LinearDML  # type: ignore[import-not-found]  # noqa: F811
    except ImportError:
        logger.debug(
            "dml_econml_not_installed",
            learning_id=learning_id,
        )
        return None

    # DML requires sufficient data
    n = min(len(propensity_records), len(outcomes))
    if n < 10:
        logger.debug(
            "dml_insufficient_data",
            learning_id=learning_id,
            observations=n,
        )
        return None

    try:
        # Build treatment (selection) and outcome arrays
        treatment = np.array([
            float(str(r.get("exploration", 0)))
            for r in propensity_records[:n]
        ]).reshape(-1, 1)
        outcome_arr = np.array([
            float(str(o.get("value", 0.0)))
            for o in outcomes[:n]
        ])
        covariates = np.ones((n, 1))  # Intercept-only for simple case

        model = LinearDML(discrete_treatment=True)
        model.fit(outcome_arr, treatment, X=covariates)
        effect = float(model.effect(covariates[:1]).flatten()[0])

        category = map_estimate_to_category(effect)

        logger.info(
            "dml_attribution_computed",
            learning_id=learning_id,
            effect=round(effect, 4),
            outcome_correlation=category,
        )
        return {
            "outcome_correlation": category,
            "estimate": effect,
            "tier": "dml",
            "observations": n,
        }
    except Exception:  # justified: fail-open, DML is best-effort optional tier
        logger.debug(
            "dml_estimation_failed",
            learning_id=learning_id,
            exc_info=True,
        )
        return None


def run_attribution(
    surfaces: list[dict[str, object]],
    outcomes: dict[str, object],
    propensity_records: list[dict[str, object]],
    graph_conn: Any = None,
    client_profile: str = "",
    model_family: str = "",
) -> list[dict[str, object]]:
    """Run the full attribution pipeline for surfaced learnings.

    For each surfaced learning:
    1. Compute phase weight (FR03)
    2. Try IPS attribution (FR01 tier 1)
    3. If insufficient propensity data, try DML (FR01 tier 2)
    4. If insufficient data for both, return "insufficient_data"
    5. Apply selective attribution (FR02) for credit splitting

    Args:
        surfaces: List of dicts with ``learning_id``, ``domain_match``,
            ``temporal_proximity``, ``source_phase``, ``target_phase``.
        outcomes: Dict mapping learning_id to outcome dict with ``value``.
        propensity_records: List of propensity dicts with ``learning_id``,
            ``selection_probability``, ``exploration``.
        graph_conn: Optional knowledge graph connection (reserved for future).
        client_profile: IDE/client profile for stratification.
        model_family: AI model family for stratification.

    Returns:
        List of attribution update dicts with ``learning_id``,
        ``outcome_correlation``, ``sessions_surfaced_delta``, ``credit_share``.
    """
    if not surfaces:
        return []

    results: list[dict[str, object]] = []

    # Step 1 & 2: Per-learning attribution (IPS or DML)
    attribution_map: dict[str, dict[str, object]] = {}
    for surface in surfaces:
        lid = str(surface.get("learning_id", ""))
        if not lid:
            continue

        # Phase weight (FR03)
        source_phase = str(surface.get("source_phase", ""))
        target_phase = str(surface.get("target_phase", ""))
        phase_weight = compute_phase_weight(source_phase, target_phase)

        # Filter propensity records for this learning
        lr_propensity = [
            r for r in propensity_records
            if str(r.get("learning_id", "")) == lid
        ]

        # Get outcome records for this learning
        outcome_data = outcomes.get(lid)
        lr_outcomes: list[dict[str, object]]
        if isinstance(outcome_data, dict):
            lr_outcomes = [outcome_data] * len(lr_propensity) if lr_propensity else [outcome_data]
        elif isinstance(outcome_data, list):
            lr_outcomes = outcome_data
        else:
            lr_outcomes = []

        # Try IPS first (tier 1)
        ips_result = compute_ips_attribution(
            lid, lr_propensity, lr_outcomes,
            client_profile=client_profile,
            model_family=model_family,
        )

        if ips_result.outcome_correlation != "insufficient_data":
            attribution_map[lid] = {
                "outcome_correlation": ips_result.outcome_correlation,
                "estimate": ips_result.estimate * phase_weight,
                "tier": ips_result.tier,
                "observations": ips_result.observations,
            }
        else:
            # Try DML fallback (tier 2) — only if EconML is installed
            dml_result = _try_dml_attribution(
                lid, lr_propensity, lr_outcomes,
                client_profile=client_profile,
                model_family=model_family,
            )
            if dml_result is not None:
                dml_result["estimate"] = float(str(dml_result.get("estimate", 0.0))) * phase_weight
                attribution_map[lid] = dml_result
            else:
                attribution_map[lid] = {
                    "outcome_correlation": "insufficient_data",
                    "estimate": 0.0,
                    "tier": "none",
                    "observations": ips_result.observations,
                }

    # Step 3: Credit splitting via selective attribution (FR02)
    credit_shares = distribute_credit(surfaces, outcome_value=1.0)
    share_map = {cs.learning_id: cs.share for cs in credit_shares}

    # Step 4: Assemble results
    for surface in surfaces:
        lid = str(surface.get("learning_id", ""))
        if not lid or lid not in attribution_map:
            continue

        attr = attribution_map[lid]
        results.append({
            "learning_id": lid,
            "outcome_correlation": str(attr.get("outcome_correlation", "insufficient_data")),
            "sessions_surfaced_delta": 1,
            "credit_share": share_map.get(lid, 0.0),
        })

    logger.info(
        "attribution_pipeline_completed",
        surface_count=len(surfaces),
        result_count=len(results),
    )

    return results
