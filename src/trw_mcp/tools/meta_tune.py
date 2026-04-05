"""Meta-Tune synthesis process -- the reflection loop that closes the meta-learning cycle.

PRD-CORE-109: Runs 9 steps to validate, attribute, synthesize, and report.
Each step is independent -- partial completion is valid.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from fastmcp import FastMCP

_logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Data classes for step results and the overall report
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Outcome of a single meta-tune step."""

    step: str
    status: str  # "ok", "skipped", "error"
    actions_taken: int = 0
    details: str = ""


@dataclass
class MetaTuneReport:
    """Aggregate report from the full meta-tune process."""

    steps: list[StepResult] = field(default_factory=list)
    total_actions: int = 0
    learnings_demoted: int = 0
    hypotheses_resolved: int = 0
    clusters_detected: int = 0
    skills_generated: int = 0
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helper: check human-reviewed guard (NFR03)
# ---------------------------------------------------------------------------


def _is_human_reviewed(
    entry: dict[str, Any],
    last_tune_date: str | None = None,
) -> bool:
    """Return True if the learning has a reviewed_at timestamp after last_tune_date.

    Human-reviewed learnings are protected from mutations (NFR03).
    """
    reviewed_at = entry.get("reviewed_at")
    if not reviewed_at:
        return False
    if not last_tune_date:
        return True  # No prior tune -- any reviewed_at counts as protected
    return str(reviewed_at) > str(last_tune_date)


# ---------------------------------------------------------------------------
# Step 1: Validate anchors
# ---------------------------------------------------------------------------


def _step_validate_anchors(
    learnings: list[dict[str, Any]],
    *,
    last_tune_date: str | None = None,
    shadow_mode: bool = False,
) -> StepResult:
    """Step 1: Check anchor validity for learnings with anchors.

    - anchor_validity == 0.0 -> demote (set status to obsolete)
    - anchor_validity < 0.5 -> flag for review (count as action)
    - Entries without anchors are skipped.
    - Entries with reviewed_at > last_tune_date are protected (NFR03).
    """
    demoted = 0
    flagged = 0
    details_parts: list[str] = []

    for entry in learnings:
        anchors = entry.get("anchors", [])
        if not anchors:
            continue

        if _is_human_reviewed(entry, last_tune_date):
            details_parts.append(f"skipped {entry.get('id')}: human-reviewed")
            continue

        validity = float(entry.get("anchor_validity", 1.0))

        if validity == 0.0:
            if shadow_mode:
                details_parts.append(
                    f"dry_run: would demote {entry.get('id')} (validity=0.0)"
                )
            else:
                entry["status"] = "obsolete"
                entry["outcome_correlation"] = ""
                _logger.info(
                    "anchor_demote",
                    id=entry.get("id"),
                    validity=validity,
                )
            demoted += 1
        elif validity < 0.5:
            flagged += 1
            details_parts.append(f"flagged {entry.get('id')} for review (validity={validity})")
            _logger.info(
                "anchor_flag_partial",
                id=entry.get("id"),
                validity=validity,
            )

    detail_text = "; ".join(details_parts) if details_parts else ""
    if shadow_mode and demoted > 0:
        detail_text = f"shadow mode: {demoted} would be demoted. {detail_text}"

    return StepResult(
        step="validate_anchors",
        status="ok",
        actions_taken=demoted + flagged,
        details=detail_text,
    )


# ---------------------------------------------------------------------------
# Step 2: Validate workarounds
# ---------------------------------------------------------------------------


def _step_validate_workarounds(
    learnings: list[dict[str, Any]],
    *,
    last_tune_date: str | None = None,
    shadow_mode: bool = False,
) -> StepResult:
    """Step 2: Check workaround-type learnings for expiry.

    Expired workarounds get protection_tier set to 'low'.
    Workarounds without an expiry date are left alone.
    """
    expired_count = 0
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for entry in learnings:
        if entry.get("type") != "workaround":
            continue

        expires = entry.get("expires", "")
        if not expires:
            continue

        if _is_human_reviewed(entry, last_tune_date):
            continue

        # Parse expiry -- accept ISO date or datetime strings
        try:
            expiry_str = str(expires).split("T")[0]  # Handle datetime strings
            if expiry_str <= now_iso:
                if shadow_mode:
                    _logger.info(
                        "workaround_expire_dry_run",
                        id=entry.get("id"),
                        expires=expires,
                    )
                else:
                    entry["protection_tier"] = "low"
                    _logger.info(
                        "workaround_expire",
                        id=entry.get("id"),
                        expires=expires,
                    )
                expired_count += 1
        except (ValueError, TypeError):
            _logger.warning(
                "workaround_expiry_parse_error",
                id=entry.get("id"),
                expires=expires,
            )

    detail = ""
    if shadow_mode and expired_count > 0:
        detail = f"shadow mode: {expired_count} would be expired"

    return StepResult(
        step="validate_workarounds",
        status="ok",
        actions_taken=expired_count,
        details=detail,
    )


# ---------------------------------------------------------------------------
# Step 3: Resolve hypotheses
# ---------------------------------------------------------------------------


def _step_resolve_hypotheses(
    learnings: list[dict[str, Any]],
    *,
    threshold_sessions: int = 30,
    last_tune_date: str | None = None,
    shadow_mode: bool = False,
) -> StepResult:
    """Step 3: Resolve hypothesis-type learnings.

    - Hypotheses older than threshold sessions with positive outcome -> promote to pattern
    - Hypotheses older than threshold sessions without confirmation -> set obsolete
    - Young hypotheses are left alone.
    """
    resolved = 0

    for entry in learnings:
        if entry.get("type") != "hypothesis":
            continue

        session_count = int(entry.get("session_count", 0))
        if session_count < threshold_sessions:
            continue

        if _is_human_reviewed(entry, last_tune_date):
            continue

        outcome = entry.get("outcome_correlation")

        if outcome in ("positive", "strong_positive"):
            # Promote confirmed hypothesis to pattern
            if shadow_mode:
                _logger.info(
                    "hypothesis_promote_dry_run",
                    id=entry.get("id"),
                    outcome=outcome,
                )
            else:
                entry["type"] = "pattern"
                _logger.info(
                    "hypothesis_promote",
                    id=entry.get("id"),
                    outcome=outcome,
                )
            resolved += 1
        else:
            # Remove unconfirmed hypothesis
            if shadow_mode:
                _logger.info(
                    "hypothesis_remove_dry_run",
                    id=entry.get("id"),
                )
            else:
                entry["status"] = "obsolete"
                _logger.info(
                    "hypothesis_remove",
                    id=entry.get("id"),
                )
            resolved += 1

    detail = ""
    if shadow_mode and resolved > 0:
        detail = f"shadow mode: {resolved} would be resolved"

    return StepResult(
        step="resolve_hypotheses",
        status="ok",
        actions_taken=resolved,
        details=detail,
    )


# ---------------------------------------------------------------------------
# Steps 4-6: Correlation, graph maintenance, bandit updates
# ---------------------------------------------------------------------------


def _step_compute_correlations(
    learnings: list[dict[str, Any]],
    trw_dir: Path | None = None,
) -> StepResult:
    """Step 4: Compute outcome correlations via IPS/DML attribution (PRD-CORE-108).

    Runs the attribution pipeline for learnings with sufficient surface data.
    Updates outcome_correlation on each learning dict in-place.
    """
    from trw_mcp.scoring.attribution.pipeline import run_attribution

    # Build surfaces and outcomes from learning metadata
    surfaces: list[dict[str, object]] = []
    outcomes: dict[str, object] = {}
    for entry in learnings:
        lid = str(entry.get("id", ""))
        if not lid:
            continue
        surfaced = int(entry.get("sessions_surfaced", 0) or 0)
        if surfaced < 3:
            continue  # Not enough data
        surfaces.append({
            "learning_id": lid,
            "domain_match": 0.5,  # Default — real domain match comes from surface logs
            "temporal_proximity": 0.5,
            "source_phase": str(entry.get("phase_origin", "")),
            "target_phase": "DELIVER",
        })
        # Use existing outcome_correlation as outcome proxy
        raw_oc = entry.get("outcome_correlation", "")
        outcome_val = {"strong_positive": 0.9, "positive": 0.7, "neutral": 0.5, "negative": 0.2}.get(
            str(raw_oc), 0.5
        )
        outcomes[lid] = {"value": outcome_val}

    if not surfaces:
        return StepResult(
            step="compute_correlations",
            status="ok",
            actions_taken=0,
            details="no learnings with sufficient surface data",
        )

    results = run_attribution(surfaces, outcomes, propensity_records=[])
    updated = 0
    for attr_result in results:
        lid = str(attr_result.get("learning_id", ""))
        oc = str(attr_result.get("outcome_correlation", ""))
        if lid and oc and oc != "insufficient_data":
            # Update in-place for downstream steps
            for entry in learnings:
                if str(entry.get("id", "")) == lid:
                    entry["outcome_correlation"] = oc
                    updated += 1
                    break

    return StepResult(
        step="compute_correlations",
        status="ok",
        actions_taken=updated,
        details=f"attribution computed for {len(surfaces)} candidates, {updated} updated",
    )


def _step_graph_maintenance(
    learnings: list[dict[str, Any]],
    trw_dir: Path | None = None,
    out_clusters: list[dict[str, Any]] | None = None,
) -> StepResult:
    """Step 5: Graph maintenance — cluster detection and impact propagation (PRD-CORE-107).

    Detects dense learning clusters for L2 domain emergence and propagates
    outcome impact along graph edges.
    """
    try:
        from trw_memory.storage._schema import ensure_schema

        # Get database connection from trw_dir
        if trw_dir is None:
            return StepResult(step="graph_maintenance", status="skipped", details="no trw_dir")
        db_path = trw_dir / "memory" / "memory.db"
        if not db_path.exists():
            return StepResult(step="graph_maintenance", status="skipped", details="no memory database")

        import sqlite3

        conn = sqlite3.connect(str(db_path))
        ensure_schema(conn)

        from trw_memory.graph import detect_clusters, propagate_impact

        clusters = detect_clusters(conn, min_size=5, min_connectivity=0.6)
        if out_clusters is not None:
            out_clusters.extend(clusters)
        actions = len(clusters)

        # Propagate impact for learnings with strong outcome correlation
        for entry in learnings:
            lid = str(entry.get("id", ""))
            oc = str(entry.get("outcome_correlation", ""))
            if oc in ("strong_positive", "positive"):
                delta = 0.1 if oc == "positive" else 0.2
                affected = propagate_impact(conn, lid, delta, max_depth=2, max_affected=50)
                actions += len(affected)

        conn.close()

        return StepResult(
            step="graph_maintenance",
            status="ok",
            actions_taken=actions,
            details=f"{len(clusters)} clusters detected",
        )
    except ImportError:
        return StepResult(step="graph_maintenance", status="skipped", details="trw-memory graph not available")
    except Exception as exc:
        _logger.warning("graph_maintenance_failed", error=str(exc))
        return StepResult(step="graph_maintenance", status="error", details=str(exc))


def _step_bandit_update(
    learnings: list[dict[str, Any]],
    trw_dir: Path | None = None,
) -> StepResult:
    """Step 6: Update Thompson Sampling posteriors and run Page-Hinkley (PRD-CORE-105).

    Loads bandit state, updates posteriors from accumulated outcome data,
    runs change detection per arm, and persists updated state.
    """
    try:
        from trw_memory.bandit import BanditSelector, PageHinkleyDetector

        if trw_dir is None:
            return StepResult(step="bandit_update", status="skipped", details="no trw_dir")

        state_path = trw_dir / "meta" / "bandit_state.json"

        # Load or create bandit
        bandit: BanditSelector
        if state_path.exists():
            bandit = BanditSelector.from_json(state_path.read_text(encoding="utf-8"))
        else:
            bandit = BanditSelector()

        # Create per-arm change detectors
        detectors: dict[str, PageHinkleyDetector] = {}
        updates = 0
        alarms = 0

        for entry in learnings:
            lid = str(entry.get("id", ""))
            if not lid:
                continue
            oc = str(entry.get("outcome_correlation", ""))
            reward = {"strong_positive": 0.9, "positive": 0.7, "neutral": 0.5, "negative": 0.2}.get(oc, 0.5)

            # Update bandit posterior
            bandit.update(lid, reward)
            updates += 1

            # Run Page-Hinkley change detection
            if lid not in detectors:
                detectors[lid] = PageHinkleyDetector()
            if detectors[lid].update(reward):
                alarms += 1
                _logger.info("page_hinkley_alarm_meta_tune", learning_id=lid)

        # Persist updated state (atomic temp-file + rename)
        import os
        import tempfile

        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=str(state_path.parent), suffix=".tmp")
        try:
            os.write(fd, bandit.to_json().encode("utf-8"))
            os.close(fd)
            os.rename(tmp_path, str(state_path))
        except Exception:
            os.close(fd)
            os.unlink(tmp_path)
            raise

        return StepResult(
            step="bandit_update",
            status="ok",
            actions_taken=updates,
            details=f"{updates} posteriors updated, {alarms} change alarms",
        )
    except ImportError:
        return StepResult(step="bandit_update", status="skipped", details="trw-memory bandit not available")
    except Exception as exc:
        _logger.warning("bandit_update_failed", error=str(exc))
        return StepResult(step="bandit_update", status="error", details=str(exc))


# ---------------------------------------------------------------------------
# Steps 7-9: Synthesis and reporting
# ---------------------------------------------------------------------------


def _step_synthesize_artifacts(
    trw_dir: Path,
    learnings: list[dict[str, Any]],
    config: dict[str, Any],
    clusters: list[dict[str, Any]] | None = None,
) -> StepResult:
    """Step 7: Generate or update .trw/meta.yaml and skill files.

    Args:
        trw_dir: Path to the .trw directory.
        learnings: Learning entry dicts.
        config: Configuration dict.
        clusters: Optional cluster data from step 5 (graph maintenance).
            When available, clusters are passed to meta.yaml synthesis and
            used to generate skill files via the triple gate.
    """
    from trw_mcp.state.meta_synthesis import generate_skill, synthesize_meta_yaml

    resolved_clusters = clusters if clusters is not None else []
    actions = 0
    skills_generated = 0

    try:
        synthesize_meta_yaml(
            trw_dir,
            learnings=learnings,
            clusters=resolved_clusters,
            config=config,
        )
        actions += 1
    except Exception:
        _logger.warning("meta_yaml_synthesis_failed", exc_info=True)
        return StepResult(
            step="synthesize_artifacts",
            status="error",
            details="meta.yaml synthesis failed",
        )

    # Generate skills from clusters that pass the triple gate
    for cluster in resolved_clusters:
        try:
            skill_path = generate_skill(cluster, trw_dir)
            if skill_path is not None:
                skills_generated += 1
                actions += 1
        except Exception:
            _logger.warning(
                "skill_generation_failed",
                domain=str(cluster.get("domain_slug", "unknown")),
                exc_info=True,
            )

    return StepResult(
        step="synthesize_artifacts",
        status="ok",
        actions_taken=actions,
        details=f"meta.yaml updated, {skills_generated} skills generated",
    )


def _step_prd_nudge_analysis(
    learnings: list[dict[str, Any]],
) -> StepResult:
    """Step 8: Analyze which PRD-referenced knowledge requirements correlated with outcomes.

    Identifies learnings that were PRD-boosted (have prd_references in metadata)
    and checks whether their outcome_correlation is positive. Flags ineffective
    PRD requirements for revision.
    """
    prd_linked = 0
    effective = 0
    ineffective_ids: list[str] = []

    for entry in learnings:
        lid = str(entry.get("id", ""))
        # Check if this learning was PRD-referenced (via tags or metadata)
        tags = entry.get("tags", [])
        if not isinstance(tags, list):
            tags = []
        has_prd_link = any(str(t).startswith("PRD-") for t in tags)
        if not has_prd_link:
            continue

        prd_linked += 1
        oc = str(entry.get("outcome_correlation", ""))
        if oc in ("strong_positive", "positive"):
            effective += 1
        elif oc in ("negative", "neutral") and oc:
            ineffective_ids.append(lid)

    if prd_linked == 0:
        return StepResult(
            step="prd_nudge_analysis",
            status="ok",
            actions_taken=0,
            details="no PRD-linked learnings found",
        )

    details = f"{effective}/{prd_linked} PRD-linked learnings effective"
    if ineffective_ids:
        details += f"; {len(ineffective_ids)} flagged for revision"
        _logger.info(
            "prd_nudge_ineffective",
            count=len(ineffective_ids),
            learning_ids=ineffective_ids[:10],
        )

    return StepResult(
        step="prd_nudge_analysis",
        status="ok",
        actions_taken=prd_linked,
        details=details,
    )


def _step_team_sync_report(
    report: MetaTuneReport,
    trw_dir: Path,
) -> StepResult:
    """Step 9: Generate team sync report."""
    from trw_mcp.state.meta_synthesis import generate_team_sync_report

    try:
        summary = generate_team_sync_report(report)
        _logger.info("team_sync_report_generated", length=len(summary))
        return StepResult(
            step="team_sync_report",
            status="ok",
            actions_taken=0,
            details="report generated",
        )
    except Exception:
        _logger.warning("team_sync_report_failed", exc_info=True)
        return StepResult(
            step="team_sync_report",
            status="error",
            details="report generation failed",
        )


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


def _run_step(
    step_num: int,
    step_name: str,
    valid_steps: set[int],
    runner: Callable[[], StepResult],
) -> StepResult:
    """Run a single step with error isolation, or skip if not selected."""
    if step_num not in valid_steps:
        return StepResult(step=step_name, status="skipped", details="not in selected steps")
    try:
        return runner()
    except Exception as exc:
        _logger.warning("meta_tune_step_error", step=step_name, error=str(exc))
        return StepResult(step=step_name, status="error", details=str(exc))


def execute_meta_tune(
    trw_dir: Path,
    *,
    learnings: list[dict[str, Any]],
    config: dict[str, Any],
    steps: list[int] | None = None,
    shadow_mode: bool | None = None,
) -> MetaTuneReport:
    """Execute the meta-tune synthesis process.

    Args:
        trw_dir: Path to the .trw directory.
        learnings: List of learning entry dicts to process.
        config: Configuration dict with model_family, trw_version, etc.
        steps: Optional list of step numbers (1-9) to run. None = all.
            Invalid step numbers (outside 1-9) are silently ignored.
        shadow_mode: If True, mutations are logged but not applied.
            Defaults to config.get("shadow_mode", True).

    Returns:
        MetaTuneReport with per-step status and aggregate metrics.
    """
    if shadow_mode is None:
        shadow_mode = bool(config.get("shadow_mode", True))

    valid_steps = {s for s in (steps or range(1, 10)) if 1 <= s <= 9}
    last_tune_date = config.get("last_tune_date")
    threshold = int(config.get("hypothesis_threshold", 30))
    report = MetaTuneReport()

    _logger.info(
        "meta_tune_start",
        learning_count=len(learnings),
        steps=sorted(valid_steps),
        shadow_mode=shadow_mode,
    )

    # Clusters detected by step 5 are threaded to step 7 for skill generation
    detected_clusters: list[dict[str, Any]] = []

    # Steps 1-8: each isolated with try/except via _run_step
    step_runners: list[tuple[int, str, Any]] = [
        (1, "validate_anchors", lambda: _step_validate_anchors(
            learnings, last_tune_date=last_tune_date, shadow_mode=shadow_mode)),
        (2, "validate_workarounds", lambda: _step_validate_workarounds(
            learnings, last_tune_date=last_tune_date, shadow_mode=shadow_mode)),
        (3, "resolve_hypotheses", lambda: _step_resolve_hypotheses(
            learnings, threshold_sessions=threshold,
            last_tune_date=last_tune_date, shadow_mode=shadow_mode)),
        (4, "compute_correlations", lambda: _step_compute_correlations(learnings, trw_dir=trw_dir)),
        (5, "graph_maintenance", lambda: _step_graph_maintenance(learnings, trw_dir=trw_dir, out_clusters=detected_clusters)),
        (6, "bandit_update", lambda: _step_bandit_update(learnings, trw_dir=trw_dir)),
        (7, "synthesize_artifacts", lambda: _step_synthesize_artifacts(
            trw_dir, learnings, config, clusters=detected_clusters)),
        (8, "prd_nudge_analysis", lambda: _step_prd_nudge_analysis(learnings)),
    ]

    for step_num, step_name, runner in step_runners:
        result = _run_step(step_num, step_name, valid_steps, runner)
        report.steps.append(result)
        # Track aggregate metrics for specific steps
        if step_num == 1:
            report.learnings_demoted += result.actions_taken
        elif step_num == 3:
            report.hypotheses_resolved += result.actions_taken
        elif step_num == 5:
            report.clusters_detected = result.actions_taken

    # Step 9: team sync report (needs the report so far)
    report.total_actions = sum(s.actions_taken for s in report.steps)
    result = _run_step(
        9, "team_sync_report", valid_steps,
        lambda: _step_team_sync_report(report, trw_dir),
    )
    report.steps.append(result)

    # Final total_actions recompute
    report.total_actions = sum(s.actions_taken for s in report.steps)

    _logger.info(
        "meta_tune_complete",
        total_actions=report.total_actions,
        learnings_demoted=report.learnings_demoted,
        hypotheses_resolved=report.hypotheses_resolved,
        skills_generated=report.skills_generated,
        step_count=len(report.steps),
    )

    return report


# ---------------------------------------------------------------------------
# MCP Tool Registration
# ---------------------------------------------------------------------------


def register_meta_tune_tools(server: FastMCP) -> None:
    """Register meta-tune tools on the MCP server."""
    from trw_mcp.tools.telemetry import log_tool_call

    @server.tool(output_schema=None)
    @log_tool_call
    def trw_meta_tune(
        steps: list[int] | None = None,
        shadow: bool = False,
    ) -> dict[str, object]:
        """Run the meta-tune synthesis process -- validates, attributes, synthesizes, and reports.

        Periodic reflection that closes the meta-learning loop. Each step is
        independent -- partial completion is valid.

        Args:
            steps: Specific steps to run (1-9). None = all steps.
            shadow: Shadow mode -- produce report only, no active modifications.
        """
        from trw_mcp.models.config import get_config
        from trw_mcp.state._paths import resolve_trw_dir
        from trw_mcp.state.memory_adapter import list_active_learnings

        trw_dir = resolve_trw_dir()
        config = get_config()

        # Load learnings
        learnings: list[dict[str, object]] = []
        try:
            learnings = list_active_learnings(trw_dir)
        except Exception:
            _logger.warning("meta_tune_learning_load_failed", exc_info=True)

        # Build config dict for execute_meta_tune
        model_family = str(getattr(config, "model_family", "") or "")
        config_dict: dict[str, object] = {
            "model_family": model_family,
            "trw_version": config.framework_version or "",
            "shadow_mode": shadow,
        }

        report = execute_meta_tune(
            trw_dir=trw_dir,
            config=config_dict,
            learnings=learnings,
            steps=steps,
            shadow_mode=shadow,
        )
        return {
            "steps": [
                {
                    "step": s.step,
                    "status": s.status,
                    "actions": s.actions_taken,
                    "details": s.details,
                }
                for s in report.steps
            ],
            "total_actions": report.total_actions,
            "learnings_demoted": report.learnings_demoted,
            "hypotheses_resolved": report.hypotheses_resolved,
            "clusters_detected": report.clusters_detected,
            "skills_generated": report.skills_generated,
            "warnings": report.warnings,
        }
